# Vision Page Mapping — Design

Date: 2026-10-23. Status: approved to build (the user asked for design, plan and build in one go, without a review stop).

## 1. Intent

The weakest part of formcast is mapping on flat and scanned forms. The agent reads a text-and-geometry map of the page and has to work out from coordinates which label belongs to which blank. It gets that wrong often, mostly on tables, on scans and on boxes far from their labels.

A person reads a form by looking at it. The spike test (§9) did that: one model call per page, given the page image with every seeded box outlined and numbered, plus the same text map. That one call beat the full agent loop on every kind of page, at a fraction of the cost and with almost no run-to-run variance. An earlier line of work tried to rebuild page structure from geometry and layout models. It was dropped as over-engineered.

This design puts that call into formcast. Each page is mapped once from its image. The agent then does what one call can't: it adds boxes the detector missed, splits boxes, and fixes record blocks.

## 2. Goals

- A **page pass**. For each page, one call gets the marked page image and that page's text map, and returns mappings and tables for the seeded boxes. formcast applies them as ordinary, validated edit ops.
- `build_template` runs the page pass, then the agent. The agent starts from the page pass's template and also sees the page images.
- **Image input** in `formcast.llm` for both API formats (Chat Completions and Responses).
- A **text-only fallback** (`vision=False`) that behaves exactly as formcast does today.
- **Eval support:** run and score `agent` (today), `pages` (page pass only) and `pages+agent` on dev, with the existing scorers. Then run held-out once.

## 3. Non-goals

- No new detection or layout models. Boxes still come from widgets and the CommonForms detectors.
- No change to the template format, the edit ops, fill or the browser engine (`demo/web`).
- The page pass draws no new boxes. It maps only the boxes it is given. Adding boxes stays the agent's job.
- No prompt caching or provider-specific SDKs. Calls stay on formcast's OpenAI-compatible HTTP layer.
- No tuning against held-out pages.

## 4. Anti-goals

These would make the work a failure even if it ran:

- Rebuilding a structured page outline (Markdown, reading order, layout regions) for the model to read. That path was tried and dropped.
- The agent undoing the page pass's correct work. `pages+agent` must not score below `pages` on reachable boxes. If it does, the agent's brief is wrong and must be fixed before shipping.
- Images or base64 data ending up in logs, step reports, result JSON or templates.
- Breaking text-only users. `vision=False` must give today's prompts and behaviour, byte for byte in the first message.

## 5. Constraints

- **This replaces the "No vision model assumed" constraint** in the main design (`2026-10-20-form-projection-design.md` §5). The user decided this after the spike. Text-only stays supported through `vision=False`.
- Any OpenAI-compatible endpoint, both formats. The reference models are Haiku 5.5 through Anthropic's OpenAI-compatible Chat Completions endpoint, and Luna 6 through Responses.
- Spend:
  - Dev eval runs on Haiku only, with a hard stop of **$6** estimated at Haiku prices ($0.10 / $0.50 per M input / output tokens).
  - Held-out runs once, Haiku, after dev passes §8, with a hard stop of **$3**.
  - Luna runs are out of scope for this round (OpenAI credits).
- Held-out stays untouched until the dev numbers are final. Nothing in prompts or code is changed after looking at held-out results.

## 6. Design

### 6.1 Image input in `formcast.llm`

- `Message.content` becomes `str | list[ContentPart]`. A part is either `TextPart(text)` or `ImagePart(data: bytes, media_type="image/png")`.
- Wire format for a user message whose content is a list:
  - Chat Completions: `[{"type": "text", "text": ...}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}]`.
  - Responses: `[{"type": "input_text", ...}, {"type": "input_image", "image_url": "data:..."}]`.
  - A plain string is sent as today.
- Images are allowed only in user messages. Anything that turns messages into text for logs uses a helper, `content_text(content)`, which writes an image as `[image: N bytes]`. Redaction and the eval's `RecordingProvider` use it.
- If a provider rejects images, the call fails as an HTTP error, as other failures do. The error message suggests `vision=False`.

### 6.2 Marked page images (`formcast.pagepass.marks`)

- Render each page of the input PDF as it is: scans are not deskewed. Seeded boxes are already in page space, so they line up.
- Resolution is 140 dpi, lowered as needed so the long edge is at most 1568 px. Above that the APIs downscale and the marks blur.
- Each box is outlined in its type's colour: text blue, checkbox green, radio teal, signature purple.
- Each box gets a **mark**, a short label drawn in a white tag inside the box's top-left corner, or just left of the box when it is too small. The mark is the part of the box id after the underscore. If two boxes on the page would get the same mark, the page uses full ids instead. (Builder's call: any rule works as long as a mark names exactly one box on its page.)
- This drawing code is the spike's `draw_ids` (scratchpad `spike/vision2/prep.py`), moved into the library. Fonts: try a system bold sans and fall back to Pillow's default at a fixed size.

### 6.3 The page pass (`formcast.pagepass`)

Public entry point:

```python
map_pages(template, form_map, pdf, llm, *, pages=None, max_parallel=4) -> PagePassResult
```

- **Input:** the seeded template (`new_template(schema, fm, candidates=True)`), the form map and the PDF.
- **One call per page** that has boxes. Pages with no seeded boxes are skipped.
- **Concurrency:** every page's marked image is rendered first, one page at a time, under the library's MuPDF lock (`formcast.pagemap.extract.PDF_LOCK`). MuPDF is not safe to call from several threads. Then the calls run in parallel, up to `max_parallel`. A page that fails to render is an `error`.
- **Output tokens and timeout:** the call asks for 32,000 output tokens at most, with a 300 s timeout. The spike's pages used up to 22,800 tokens, which the default `LLMConfig.max_output_tokens` (8,000) can't hold, and a page took 97 s even with streaming. These are per-call overrides on `Provider.complete`; the agent's settings stay untouched. They are passed only when the provider's `complete` accepts them (a named parameter or `**kwargs`). A provider written against the two-argument `complete(messages, tools)` is called without them, and its own limits apply.
- **Prompt:** the spike's `vision2` prompt, moved into `formcast/pagepass/prompts.py`:
  - System: the mapping rules (taken from the agent prompt) plus the table rule.
  - User message, in order:
    - the image;
    - the note explaining the marks;
    - schema paths, in the agent's `_schema_line` form;
    - the transform reference;
    - the page's box list (`id: type`);
    - that page's whole page map (`summarize_page`): the summary preamble and the page's section, not a slice of the whole-form summary. Only a page longer than 24,000 characters on its own is cut, and the cut note does not mention the query helpers (the page-pass model has no tools).
- **Answer:** text JSON, `{"mappings": [...], "tables": [...]}`. It is parsed leniently: take the outermost `{...}` and ignore anything unparsable.
- **No tool calls.** The spike got valid JSON on all 68 vision calls.

**Turning answers into ops.** These are the spike's `score2.build` rules, now library code:

- **Mapping:**
  - If the mapping has a `when` or `bool_to_check` and its box is typed text, first `set_type` the box to checkbox.
  - Then `set_source`.
  - A box that is also named in a table is left to the table.
- **Table:** one `add_table` per answer table, with one segment.
  - The segment rect is the bounding box of the linked boxes, padded by 1 pt.
  - `row_top` is the top of that rect, `row_height` = height / rows, and `max_rows` = rows.
  - Each column links its boxes through `Column.fields` and spans the x range of its boxes.
  - Linked boxes have their own source cleared first.
  - Boxes that are not on the page, or that an earlier table already used, are dropped from the rows.
- **Tables across pages:** answer tables on different pages that have the same `source_path` and share at least one column key are one table that continues. They are merged into one `add_table` with one segment per page, in page order. Tables with disjoint column keys stay separate: that is the "record printed as a block" pattern, one table per printed line of the block.
- **Validation:** every op goes through `formcast.template.ops.apply`, with author `"agent"` and note `"page pass p<n>"`. A rejected op is recorded and skipped. The page pass never raises on a bad answer.
- **Errors:** a page whose call fails after the provider's retries, or whose answer doesn't parse, keeps its boxes unmapped and is listed in the result. The agent sees those boxes as unmapped.

`PagePassResult`:

- `template`
- `pages`: per page,
  - page number;
  - status (`ok`, `error` or `unparsed`);
  - counts of applied and rejected ops;
  - the rejected reasons;
  - token usage;
  - seconds;
  - the raw answer text.
- `usage`: the total.

### 6.4 `build_template` and the agent

New signature (new parameters in bold):

```python
build_template(schema, pdf, llm, *, start_from=None, form_map=None, budget=..., seed_candidates=True,
               on_step=None, **vision=True, agent=True**)
```

| Case | What runs |
|---|---|
| `vision=False` | Exactly today's behaviour. No page pass, no images. |
| `vision=True`, fresh template | Seed → page pass → agent session that starts from the page pass's template. |
| `agent=False` | Stops after the page pass. Returns a `SessionResult` with `stop_reason="finish"`, no steps, and summary `"page pass only"`. |
| `start_from` | No page pass: the template is already mapped. The agent gets images when `vision=True`. |

Other rules:

- `revise` gets `vision=True` too, and the agent gets images when it is on.
- **What the agent sees with `vision=True`:** its first user message carries one marked image per page, with marks for the template's current fields. Each image follows a one-line `PAGE <n>` note and comes before the existing first-message text.
  - The first message is resent every turn. That is the cost of images in the agent: about 1.6k input tokens per page per step on Haiku.
  - Images are drawn once, when the session starts. They show the boxes as they were then, and the prompt says so.
- **The agent's brief after a page pass:** a new paragraph replaces the fresh-template note. It says:
  - A page pass has already mapped the boxes from the page images.
  - The agent's job is to:
    - check that work against the images;
    - map the schema paths still unmapped;
    - add boxes for blanks that have none;
    - split boxes that hold several answers;
    - make sure every printed line of a record block is in a table.
  - It should keep page-pass mappings it can't show to be wrong.
  - The current field list (`_template_note`) follows as it does for any non-fresh start.
  - When no page's call succeeded (every page failed, could not be parsed, or had no boxes), the agent gets the fresh-template note instead, as if no page pass ran. It still gets the images.
- `SessionResult` gains `page_pass: PagePassResult | None`. `usage` includes the page pass's tokens.
- **Progress and cancel:** `on_step` is not called during the page pass. When the pass is done, and before the agent's first call, `on_step` gets one event for it: `step=0`, summary `"page pass"`, the pass's applied and rejected op counts, the coverage it left, and its tokens. `finished` is true only with `agent=False`. Raising `SessionCancelled` from that event stops the run there, with `stop_reason="cancelled"`, summary `"cancelled"`, the page pass's template, `page_pass` set, and the pass's usage. So a cancel takes effect only once the whole pass is done. On a dense form that can take minutes: each call may run up to 300 s, plus the provider's retries.
- **Budget:** `SessionBudget`'s token caps count the agent's tokens only, not the page pass's. The page pass's output is bounded by pages × 32,000 tokens.
- `formcast.map_pages` and `PagePassResult` are exported lazily, like the other public names.
- **Demo server:** it calls `build_template` and gets the new default. A build job's progress starts with the page pass's step-0 event (the workbench shows it as "Page pass"). The server's `PYMUPDF_LOCK` is the library's `formcast.pagemap.extract.PDF_LOCK`. The page pass renders its page images one at a time under that lock, so those renders and the server's own MuPDF work exclude each other.

### 6.5 Prompt fix carried in from the spike

On Grandview page 2 (employment history printed as repeating blocks), the page pass built the block tables but left out the phone and start/end date lines in both runs. The table rule gets one more sentence: every printed line of a record block that has boxes gets its own table, and a block is not finished until each line with boxes for that record is covered.

Check this on that dev page. It is the only prompt change made before the eval.

### 6.6 Eval (`eval/agent_eval.py`)

- **New `--pipeline` option:**
  - `agent` (default): today's run, `vision=False`.
  - `pages`: `vision=True, agent=False`.
  - `pages+agent`: `vision=True`.
- Results files and directories carry the pipeline in the tag. Row fields gain:
  - `pipeline`;
  - `page_pass_input_tokens` and `page_pass_output_tokens`;
  - `page_errors`.
- **Logs:** the session log adds the page pass's per-page answers. Images are not stored; `first_user_message` goes through `content_text`.
- **Spend guard:** a `--max-usd` option with Haiku prices built into a small table (other models: no guard unless prices are given). Before each job the harness adds up the spend of the run's finished rows and skips jobs once the total passes the limit.
- **Scoring is unchanged:** detection, mapping, fill and the seed comparison.

## 7. Left to the builder

- Module layout inside `formcast.pagepass`, names of private helpers, and the exact mark-collision rule.
- Thread pool versus a sequential loop with `max_parallel=1` in tests. The provider must work from several threads: `httpx.Client` is thread-safe for requests.
- Whether `ContentPart` types are dataclasses or tuples. They must be importable from `formcast.llm`.
- Wording of the agent's new paragraph, within §6.4.
- Unit tests:
  - all of them offline, using the fake providers the tests already use;
  - one `-m llm` smoke test that sends a single image to the configured endpoint.

## 8. Done means

Measured on the whole dev split (all scored pages, both variants), Haiku, one run per pipeline. A fresh `agent` run on today's form maps is the baseline, because the archive used the 21 October maps. "Reachable" means a seeded box has IoU ≥ 0.3 with the GT box; it is the harness's `mapping_reachable`.

1. **Reachable boxes:**
   - `pages` path accuracy is at least the fresh `agent` run's + 0.05.
   - On the spike's 34 pages (the spike's `cases.json`, analysed by the controller from the saved templates), `pages` is within 0.03 of the spike's 0.96.
   - `pages+agent` is at least `pages` − 0.01.
2. **All GT boxes.** `pages+agent` path accuracy is higher than both the fresh `agent` run and `pages`.
3. **Fill.** `fill_acc` of `pages+agent` is at least the fresh `agent` run's.
4. **Behaviour and tests:**
   - `vision=False` gives the same first message as before. A unit test checks this.
   - All tests pass.
5. **Held-out, once,** after 1–4 hold:
   - run `pages+agent` on Haiku;
   - record the result in this doc;
   - change nothing in response.

**When a target fails:**
- If `pages` misses item 1, stop and report. Don't tune it.
- If only the `pages+agent` parts of items 1–3 fail, one fix is allowed:
  - either reword `PAGE_PASS_NOTE`, or turn off images in the agent;
  - then run `pages+agent` once more.
  - If it still fails, stop and report, with `pages` (`agent=False`) as the measured fallback.
- **Grandview check (§6.5):** it passes when, in the `pages` run, the phone and date lines of Grandview page 2 are in tables. One reword of that sentence is allowed, tested with a single-form `pages` run.
- **Spend:** the smoke test, these checks and all retries count toward the $6 dev cap.

## 9. Evidence (spike, 2026-10-23, throwaway)

- **Setup:**
  - 34 scoreable dev pages (published and fake scans), covering 1,908 GT boxes, 800 of them table cells;
  - Haiku 5.5, conditions text vs text + image, two runs each;
  - 136 calls, $0.44.
- **Path accuracy on reachable boxes (1,442),** run 1 / run 2:

  | Group | Text + image | Text only | Agent round3-gen | Agent latest |
  |---|---|---|---|---|
  | Fillable | 1.00 / 1.00 | 0.99 / 0.97 | 1.00 | 0.96 |
  | Digital | 0.93 / 0.93 | 0.67 / 0.77 | 0.85 | 0.81 |
  | Scanned | 0.95 / 0.96 | 0.72 / 0.69 | 0.79 | 0.76 |
  | **All** | **0.96 / 0.96** | 0.80 / 0.82 | 0.89 | 0.85 |

  Joint (path + transforms): 0.92 / 0.93 for text + image, against 0.83 for the agent.
- **Table cells:** 0.93 for text + image, against 0.81 for the agent.
- **All GT:** 0.74 for text + image, 0.71 for the agent.
- **What no single call can map:** 466 GT boxes (24%) have no seeded box. That ceiling is the agent's structural work.
- **Files:** `scratchpad/spike/vision2` (prep, call, score scripts, all responses, `scores2.json`).
- **Caveat:** the archived agent ran on the form maps of 21 October.

## 10. Calls made by Claude (the user can override)

- **Page pass then agent, rather than images inside the agent loop alone.** The spike measured the single call; it did not measure an agent with images.
- **Agent sees images too, by default.** The structural work (missed blanks) is the part that most needs sight. The cost is about 1.6k tokens per page per step on Haiku.
- **`vision=True` by default.** Both reference models take images. Text-only users pass `vision=False`.
- **Plain text JSON answers, not a tool call.** That is the format the spike tested.
- **Haiku only for this round.** Spend caps: $6 dev, $3 held-out.
- **Merge rule for tables across pages** (§6.3).
- **Targets measured against a fresh `agent` run** on the full dev split, not against the spike's subset or the archive (§8). The spike's 0.96 is checked only on its own 34 pages.
- **One run per pipeline, one fix allowed** for the `pages+agent` brief, all within $6. Estimated cost: `agent` about $1.65, `pages+agent` about $2, `pages` about $0.15.
- **`Provider.complete` gains keyword-only `max_output_tokens` and `timeout_s` overrides.** This is a public protocol change; existing two-argument implementations keep working for the agent. The page pass asks for 32,000 tokens and a 300 s timeout, because a dense page's reply can take more than 120 s without streaming.

## 11. Results (2026-10-24)

All runs on Haiku 5.5 with the final code (`39cfb74`), scored by the harness. Every pipeline completed every session.

Results files, under `eval/results/`:
- dev `agent`: `…dev_2026-10-23_final-agent` plus `…dev_2026-10-24_final-rest{,-cms,-i9}-agent`, for the 14 sessions the usage limit cut off on 10-23;
- dev `pages`: `…dev_2026-10-24_v3-pages`;
- dev `pages+agent`: `…dev_2026-10-24_v3-pages-agent`;
- held-out: `…heldout_2026-10-24_heldout-vision-pages-agent`.

### Dev, 24 of 24 sessions per pipeline

| | All-GT path | All-GT joint | Reachable path | Reachable joint | Fill | Cost | Agent steps |
|---|---|---|---|---|---|---|---|
| `agent` (text only, today's maps) | 0.630 | 0.581 | 0.793 | 0.729 | 0.538 | $1.64 | 486 |
| `pages` | 0.739 | 0.696 | 0.947 | 0.890 | 0.573 | $0.15 | 0 |
| **`pages+agent`** | **0.791** | **0.720** | **0.955** | 0.869 | **0.662** | $1.46 | 308 |

By variant:

| Variant | Pipeline | All-GT path | Reachable path | Fill |
|---|---|---|---|---|
| Published | `agent` | 0.785 | 0.878 | 0.669 |
| Published | `pages` | 0.837 | 0.952 | 0.699 |
| Published | `pages+agent` | 0.816 | 0.934 | 0.692 |
| Fake scans | `agent` | 0.475 | 0.685 | 0.408 |
| Fake scans | `pages` | 0.642 | 0.940 | 0.448 |
| Fake scans | `pages+agent` | 0.765 | 0.982 | 0.633 |

**§8 verdicts:**
- **8.1 passes.**
  - `pages` reachable is 0.947, at least the agent's 0.793 + 0.05.
  - `pages+agent` is 0.955, at least `pages` − 0.01.
  - On the spike's 34 pages, `pages` reachable is 0.959 (spike: 0.96).
- **8.2 passes.** `pages+agent` all-GT path is 0.791, above the agent's 0.630 and `pages`' 0.739.
- **8.3 passes.** `pages+agent` fill is 0.662, against the agent's 0.538.
- **8.4 passes.** 1,751 tests pass, and the `vision=False` first-message test is in place.

**The biggest gains are on scans:**

| Session | Agent fill | `pages+agent` fill |
|---|---|---|
| I-9 published | 0.37 | 0.94 |
| I-9 scan | 0.27 | 0.91 |
| CSUDH scan | 0.32 | 0.86 |
| cms1500 scan | 0.03 | 0.43 |

**Weak spots left:**
- **cms1500 published:** fill 0.22 against the agent's 0.35. The page's answer runs near the 32,000-token limit, mostly on hidden reasoning.
- **8949 scan:** fill 0.47 against the agent's 0.91. The detector draws boxes in the empty space of the column-heading cells, and the model still takes them as table row 1 despite a prompt rule. The agent didn't fix it either.
- **Run-to-run variance in the page pass:** one Columbus page returned 43 mappings in one run and 64 in another.

### Held-out, once (9 of 9 sessions, $0.94, nothing changed in response)

| | All-GT path | All-GT joint | Reachable path | Reachable joint | Fill | Detection recall @0.5 (bare detector) |
|---|---|---|---|---|---|---|
| All | 0.804 | 0.777 | 0.986 | 0.954 | 0.695 | 0.691 (0.587) |
| Published | 0.897 | 0.878 | 0.994 | 0.974 | 0.794 | 0.824 (0.686) |
| Fake scans | 0.700 | 0.663 | 0.974 | 0.927 | 0.588 | 0.538 (0.474) |

- Held-out tracks dev (all-GT path 0.804 against 0.791), so the result generalises.
- The earlier held-out agent run (10-21) ended in errors on 8 of 9 sessions, so there is no held-out agent number to compare with.
- Against the v1 finish line (main design §6):
  - "(b) at least 90% of detected fields mapped right" is met: reachable path 0.986.
  - Detection recall (a) is not met. Detection is outside this work's scope; the page pass adds no boxes.

### Changes made after the first dev eval (all on dev; none after held-out)

- **The data-rows rule:** table rows are printed record rows only, and boxes in heading cells don't count.
- **Agent brief:** the agent is told to split joined boxes into one per label, and to add boxes for unmapped paths. This was the one allowed agent-brief fix (§8). It closed ND Guard's fill gap: 0.49 → 0.78 / 0.82.
- **Bare marks:** the model sometimes names boxes by their image mark ("89"). These now resolve to box ids; cms1500's whole table had been dropped.
- **Cut-off answers:** every complete item of an answer cut off at the output limit is kept.
- **Bugs fixed earlier:**
  - fractional dpi on large pages;
  - a silent page-pass failure reported to the agent as done;
  - MuPDF called from several threads;
  - a truncated page map;
  - no cancel during the page pass.

**Spend:** about $4.3 on dev evals and checks, against the $6 cap, and $0.94 on held-out, against $3.
