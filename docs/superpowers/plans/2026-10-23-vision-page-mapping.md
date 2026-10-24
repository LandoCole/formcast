# Vision Page Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Map each page of a form with one model call that sees the page image (seeded boxes outlined and numbered) plus the page's text map, then let the agent do the structural work starting from that template, also with the images.

**Architecture:**
- `formcast.llm` messages gain image parts, sent in both API formats.
- A new package, `formcast.pagepass`, does three things:
  - renders marked page images;
  - makes one call per page;
  - turns each JSON answer into validated edit ops (`set_type`, `set_source`, `add_table`).
- `build_template` runs seed → page pass → agent (`vision=True`, the default). `vision=False` is today's path, unchanged.
- The eval gains `--pipeline agent|pages|pages+agent`, a reachable-box mapping score and a spend guard.

**Tech Stack:** Python 3.11+, httpx, PyMuPDF, Pillow, pydantic, pytest.

**Spec:** `docs/superpowers/specs/2026-10-23-vision-page-mapping-design.md`. Read it first. The spike code it cites is in `/private/tmp/claude-501/-Users-landonthornton-Documents-Projects-RLMayonnaise--claude-worktrees-formcast-weaknesses-4a0a3a/dd3b2be6-4795-42c7-8260-87c042386ca6/scratchpad/spike/vision2/` (`prep.py`, `call.py`, `score2.py`). Port from it; don't import it.

## Global Constraints

- **Python:** run everything with the main checkout's venv: `../../../.venv/bin/python` from the worktree root (the worktree has no `.venv`). Tests: `../../../.venv/bin/python -m pytest -q`. The default addopts skip `slow` and `llm`.
- **Credentials:** they live in the main checkout's `.env` (`../../../.env`). Load only the `*_EVAL` prefixes. Never print key values.
- **`vision=False`:** must produce the agent's first message and behaviour exactly as today.
- **No base64 or image bytes** in logs, step reports, result JSON or templates. Use `content_text` (Task 1).
- **The page pass maps only seeded boxes.** It never adds boxes.
- **Page-pass ops** are applied through `formcast.template.ops.apply` with `author="agent"` and `note=f"page pass p{page}"`. A rejected op is recorded, never raised.
- **Marked images:**
  - 140 dpi, lowered so the long edge is ≤ 1568 px;
  - outline colours: text (31,119,180), checkbox (44,160,44), radio_option (23,160,180), signature (148,103,189), anything else (200,60,60).
- **Page-pass output limit:** 32,000 tokens per call. The agent's `LLMConfig.max_output_tokens` stays untouched.
- **Spend:** dev eval ≤ $6, held-out ≤ $3, both at Haiku prices ($0.10 / $0.50 per M input / output tokens). Haiku (`ANTHROPIC_EVAL`) only.
- **Held-out:** don't run it until Task 8. Never change prompts or code after seeing held-out results.
- **Model-facing text:** text in `src/formcast/pagepass/prompts.py` must not name corpus labels, schema properties or widgets. `tests/agent/test_prompts.py` checks the files in `MODEL_FACING`, so add this file to that list.
- **Style:** match the surrounding code: docstrings with a one-line summary, `# fmt: skip` where the repo uses it, `from __future__ import annotations`.
- **Commits:** commit on the current branch `claude/formcast-weaknesses-4a0a3a`, one commit per task. End each message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A page with zero seeded boxes** (an instructions-only page). Expected: no call is made, and the page is listed with status `skipped`. Test in Task 4.
2. **An answer naming box ids that don't exist, are on another page, or appear twice across tables.** Expected: those entries are dropped, the rest still applies, and nothing raises. Test in Task 3.
3. **A model that answers with prose around the JSON, or with truncated JSON** (`stop_reason` at the token limit). Expected: the outermost `{...}` is parsed; when parsing fails, the page is `unparsed` and its boxes stay unmapped. Test in Task 3.
4. **A provider that rejects image input** (HTTP 400). Expected: the `FormcastError` message mentions `vision=False`. `build_template` with the page pass returns a result rather than crashing for pages that failed. Test in Task 4 (page error) and Task 1 (error hint).
5. **Two boxes on one page whose mark would collide** (ids `w0_3` and `c0_3`). Expected: the page draws full ids, and each mark names exactly one box. Test in Task 2.

---

### Task 1: Image content parts in `formcast.llm`

**Files:**
- Modify: `src/formcast/llm/types.py`, `src/formcast/llm/chat_completions.py`, `src/formcast/llm/responses.py`, `src/formcast/llm/base.py` (Provider protocol, error hint), `src/formcast/llm/__init__.py`
- Test: `tests/llm/test_providers.py`

**Interfaces:**
- Produces:
  - `TextPart(text: str)` and `ImagePart(data: bytes, media_type: str = "image/png")`: frozen dataclasses in `formcast.llm.types`, exported from `formcast.llm`.
  - `ContentPart = TextPart | ImagePart`.
  - `Message.content: str | list[ContentPart]`.
  - `content_text(content: str | list[ContentPart]) -> str`: joins text parts with `"\n\n"` and writes each image as `[image: <N> bytes]`. Exported.
  - `Provider.complete(messages, tools, *, max_output_tokens: int | None = None, timeout_s: float | None = None) -> LLMResponse`.
    - `None` means `cfg.max_output_tokens` / `cfg.timeout_s`.
    - Both built-in providers honour both overrides. `HTTPProvider.post_json(path, body, *, timeout_s=None)` passes `timeout=` to `httpx.Client.post`.

- [ ] **Step 1: Write failing tests**
  - `test_chat_completions_sends_image_parts_as_data_urls`. A user `Message(content=[TextPart("look"), ImagePart(b"\x89PNG")])`. Assert that `body["messages"][0]["content"] == [{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(b"\x89PNG").decode()}}]`.
  - `test_responses_sends_image_parts_as_input_image`. Same message. Assert that `body["input"][0] == {"role": "user", "content": [{"type": "input_text", "text": "look"}, {"type": "input_image", "image_url": "data:image/png;base64,..."}]}`.
  - `test_plain_string_content_is_sent_unchanged`. Both formats: a string message gives the same wire dict as before (`{"role": "user", "content": "hi"}`).
  - `test_max_output_tokens_override_per_call`. Chat: `body["max_completion_tokens"] == 32000` when `complete(..., max_output_tokens=32000)`, and `cfg.max_output_tokens` otherwise. Responses: `body["max_output_tokens"]` likewise.
  - `test_timeout_override_per_call`. `complete(..., timeout_s=300)` sends the request with `request.extensions["timeout"]` read timeout 300. Without the override it is `cfg.timeout_s`.
  - `test_content_text_replaces_images_with_a_placeholder`. `content_text([TextPart("a"), ImagePart(b"12345")]) == "a\n\n[image: 5 bytes]"` and `content_text("x") == "x"`.
  - `test_http_400_on_a_request_with_images_suggests_vision_false`. The Recorder returns `httpx.Response(400, text="image not supported")` for a message with an `ImagePart`. Assert that the `FormcastError` message contains `"vision=False"`. Also assert that the same 400 on a text-only message does not contain it.
  - `@pytest.mark.llm test_live_endpoint_accepts_an_image(eval_env)`. Send a 64×64 red PNG made with Pillow plus "What colour is this square? One word." to `LLMConfig.from_env("ANTHROPIC_EVAL")`. Assert that `"red"` is in the lowercased text.

- [ ] **Step 2: Run them to verify they fail**
  Run: `../../../.venv/bin/python -m pytest tests/llm/test_providers.py -q`
  Expected: the new tests fail with ImportError / AssertionError.

- [ ] **Step 3: Implement**
  - Serialisers branch on `isinstance(m.content, list)`.
  - The error hint is appended in `HTTPProvider.post_json` when `status_code` is 400–499 and the body contains `"image_url"` or `"input_image"`. Have `complete` pass a flag, or check the body dict: builder's call.
  - The text is: `" (the model or endpoint may not accept images: pass vision=False to build without page images)"`.

- [ ] **Step 4: Run the full test suite**
  Run: `../../../.venv/bin/python -m pytest -q`
  Expected: all pass. Existing fakes taking `(messages, tools)` still work because the session doesn't pass the new keyword.

- [ ] **Step 5: Commit**: `git commit -m "llm: image content parts and per-call output limit"`

### Task 2: Marked page images (`formcast.pagepass.marks`)

**Files:**
- Create: `src/formcast/pagepass/__init__.py` (lazy exports, filled in by Task 4; empty docstring module for now), `src/formcast/pagepass/marks.py`
- Test: `tests/pagepass/test_marks.py`

**Interfaces:**
- Consumes: `formcast.template.model.Field`, `formcast.pagemap.extract.open_pdf`.
- Produces:
  - `marks_for(fields: list[Field]) -> dict[str, str]`: field id → mark, for one page's fields. The mark is the id's part after the first `_`. If any two fields would share a mark, every mark on the page is the full id.
  - `render_marked_page(pdf: str | Path | bytes, page: int, fields: list[Field]) -> bytes`: a PNG.
  - `page_dpi(width_pt: float, height_pt: float) -> float`: `min(140, 1568 / (max(width_pt, height_pt) / 72))`.

- [ ] **Step 1: Write failing tests**
  - `test_marks_are_id_suffixes`: fields `w0_3`, `w0_10` → `{"w0_3": "3", "w0_10": "10"}`.
  - `test_colliding_suffixes_fall_back_to_full_ids`: `w0_3`, `c0_3` → `{"w0_3": "w0_3", "c0_3": "c0_3"}`.
  - `test_letter_page_renders_at_140_dpi`: `page_dpi(612, 792) == 140`. On `flat_form_pdf` (tests/conftest.py) with one field, the PNG size is `(round(612*140/72), round(792*140/72))` ± 1 px.
  - `test_legal_page_long_edge_is_capped`: `page_dpi(612, 1008)` gives a long edge ≤ 1568 px.
  - `test_box_outline_uses_the_type_colour`: render one checkbox field at a known rect on `flat_form_pdf`. The pixel on the outline's left edge, mid-height, is `(44, 160, 44)`.
  - `test_fields_without_rect_are_skipped`: a field with `rect=None` doesn't raise.

- [ ] **Step 2: Run to verify they fail**: `../../../.venv/bin/python -m pytest tests/pagepass/test_marks.py -q` → ImportError.

- [ ] **Step 3: Implement.** Port `draw_ids` from the spike's `prep.py`:
  - tag inside the top-left corner when the box is ≥ 17 px tall and wider than text + 5 px, else just to the left;
  - white fill, outline in the box colour, font size 13.
  - Font: try `"/System/Library/Fonts/Supplemental/Arial Bold.ttf"`, then `"DejaVuSans-Bold.ttf"`, then `ImageFont.load_default(size=13)`.
  - Render with `page.get_pixmap(dpi=...)`, through `open_pdf`.

- [ ] **Step 4: Run tests**: they pass. The full suite passes.

- [ ] **Step 5: Commit**: `git commit -m "pagepass: marked page images"`

### Task 3: Page-pass prompt and answers → ops

**Files:**
- Create: `src/formcast/pagepass/prompts.py`, `src/formcast/pagepass/answers.py`
- Modify: `tests/agent/test_prompts.py` (add `src/formcast/pagepass/prompts.py` to `MODEL_FACING`)
- Test: `tests/pagepass/test_answers.py`, `tests/pagepass/test_prompts.py`

**Interfaces:**
- Consumes: `formcast.agent.prompts._schema_line`, `formcast.schema.flatten_schema`, `formcast.transforms._REGISTRY`, `formcast.pagemap.summarize.summarize`, `formcast.template.ops.apply`, `formcast.errors.OpError`.
- Produces:
  - `SYSTEM_PROMPT: str`.
  - `IMAGE_NOTE: str`: a template with `{example}`, filled with one real mark → id pair from the page.
  - `page_messages(template: Template, form_map: FormMap, page: int, image: bytes, marks: dict[str, str]) -> list[Message]`: `[system, user]`. The user content is `[ImagePart, TextPart(note), TextPart(page text)]`.
  - `parse_answer(text: str) -> dict | None`: returns `{"mappings": list, "tables": list}`, or None when no JSON object parses.
  - `TableSpec`: a dataclass with `page: int`, `source_path: str`, `transforms: dict[str, list]` (column key → transforms) and `rows: list[dict[str, str]]`.
  - `mapping_ops(template: Template, answer: dict, page: int) -> tuple[list[dict], list[TableSpec]]`: ops for the plain mappings, plus the page's table specs with rows cleaned (see the rules below).
  - `table_ops(template: Template, specs: list[TableSpec]) -> list[dict]`: merges continuing tables across pages (see the rules below), then emits, for each table, `set_source(id, None)` for linked boxes that have a source, then one `add_table` op.
  - `apply_ops(template: Template, ops: list[dict], page: int | None, context: OpContext | None) -> tuple[Template, int, list[dict]]`: returns (template, applied count, rejected `[{"op": op, "reason": str}]`). The note is `f"page pass p{page}"`, or `"page pass"` for merged tables.

**Prompt (exact copy):**
- `SYSTEM_PROMPT` is the spike's `vision2/prep.py` `SYSTEM` string with two changes:
  - drop the parenthetical "(the same ones the formcast agent follows)";
  - append to the table rule: `Every printed line of a record block that has boxes gets its own table; a block is done only when every line with boxes for that record is in a table.`
- `IMAGE_NOTE` is the spike's `call.py` `IMAGE_NOTE` text, with the example taken from the page's own first mark.
- The page text is the spike's `user_text`, built the same way:
  - `SCHEMA PATHS:`;
  - `TRANSFORMS:`, using the spike's `transforms_ref`;
  - `BOXES ON THIS PAGE (n; ids as in the page map):`, one `- id: type` line per box;
  - `PAGE MAP (extracted text and geometry, page n):`, using the spike's `page_section(summarize(fm), page)`.

**Answer rules (port of the spike's `score2.build`):**
- **Mappings:**
  - Skip a mapping whose id is in one of the page's tables, isn't a field on this page, or has no `source.paths`.
  - When the source has `when`, or a `bool_to_check` transform, and the field's type is `"text"`, emit `{"op": "set_type", "id": id, "type": "checkbox"}` first.
  - Then `{"op": "set_source", "id": id, "source": source}`.
- **Table rows:** keep only values that are field ids on this page and not used by an earlier table, on this page or earlier pages. Drop empty rows and tables with no rows.
- **Merge:** specs on different pages with the same `source_path` and at least one shared column key are one table, ordered by page. Specs on the same page are never merged.
- **Segment per spec:**
  - `rect` = bbox of its linked rects ± 1 pt; `row_top` = rect y0; `row_height` = (y1 − y0) / len(rows); `max_rows` = len(rows).
  - Columns follow first-seen key order. Each column has:
    - `x = [min x0, max(max x1, min x0 + 4)]`;
    - `transforms` from the spec;
    - `fields` = that key's box per row, or None.
- **The `add_table` op:** `{"op": "add_table", "source_path": ..., "segments": [...]}`. Leave `id` to the op's default. Check `AddTable` in `src/formcast/template/op_models.py` for required fields.

- [ ] **Step 1: Write failing tests.** Build a small template by hand (Field objects on pages 0 and 1, with known rects) and a schema with a table path `rows[]` (`a`, `b`), a boolean `ok` and a string `name`.
  - `test_parse_answer_takes_the_outermost_json_object`: `'Sure:\n{"mappings": [], "tables": []}\nDone'` parses. `'{"mappings": [' ` → None. `"no json"` → None. Missing keys default to `[]`.
  - `test_choice_on_a_text_box_sets_type_checkbox_first`: a mapping on a text field with `when` → ops `[set_type, set_source]`.
  - `test_mapping_with_unknown_or_other_page_id_is_dropped`.
  - `test_box_in_a_table_is_not_also_mapped`.
  - `test_table_rows_drop_unknown_and_reused_ids`.
  - `test_table_segment_geometry_from_linked_boxes`. Two rows × two columns at known rects. Assert:
    - the exact `rect`, `row_top`, `row_height` and `max_rows`;
    - column `x`;
    - `fields`.
  - `test_continuing_table_across_pages_becomes_one_add_table_with_two_segments`: same source_path, shared key, pages 0 and 1.
  - `test_block_tables_with_disjoint_keys_stay_separate`: same source_path, keys `{a}` and `{b}`, same page → two `add_table` ops.
  - `test_apply_ops_records_rejections_and_keeps_going`: a `set_source` with a path not in the schema is rejected with a reason, and the next op still applies.
  - `test_end_to_end_answer_applies_and_scores`: `mapping_ops` + `table_ops` + `apply_ops` on the hand template. The resulting template has the table field with linked cells, and the mapped fields' `source.paths`.
  - `tests/pagepass/test_prompts.py::test_page_messages_carry_image_note_and_page_text`:
    - the user content types are `[ImagePart, TextPart, TextPart]`;
    - the note contains a real `mark → id` example;
    - the text contains `BOXES ON THIS PAGE` and only that page's box ids.

- [ ] **Step 2: Run to verify they fail.** `../../../.venv/bin/python -m pytest tests/pagepass -q`

- [ ] **Step 3: Implement** `prompts.py` and `answers.py` as specified.

- [ ] **Step 4: Run tests.** Run `tests/pagepass` and the full suite, including `tests/agent/test_prompts.py`. All pass.

- [ ] **Step 5: Commit.** `git commit -m "pagepass: prompt and answer-to-ops"`

### Task 4: `map_pages` runner

**Files:**
- Create: `src/formcast/pagepass/run.py`
- Modify: `src/formcast/pagepass/__init__.py`, `src/formcast/__init__.py` (lazy `map_pages`, `PagePassResult`; add both to `__all__`), `tests/agent/test_session.py::test_package_exports_the_public_api` if it lists names
- Test: `tests/pagepass/test_run.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `PageOutcome`, a dataclass with these fields:
    - `page: int`;
    - `status: Literal["ok", "error", "unparsed", "skipped"]`;
    - `applied: int`;
    - `rejected: list[dict]`;
    - `usage: Usage`;
    - `seconds: float`;
    - `text: str`;
    - `error: str | None`.
  - `PagePassResult`: a dataclass with `template: Template`, `pages: list[PageOutcome]` and `usage: Usage`.
  - `map_pages(template: Template, form_map: FormMap, pdf: PdfInput, llm: LLMConfig | Provider, *, pages: list[int] | None = None, max_parallel: int = 4) -> PagePassResult`.
  - Each call passes `max_output_tokens=PAGE_PASS_MAX_OUTPUT_TOKENS` and `timeout_s=PAGE_PASS_TIMEOUT_S`, where `PAGE_PASS_MAX_OUTPUT_TOKENS = 32000` and `PAGE_PASS_TIMEOUT_S = 300`.

- [ ] **Step 1: Write failing tests.** Use a `ScriptedProvider`-like fake that maps the page in the user text to a canned reply and records the `max_output_tokens` kwarg. Use `flat_form_pdf` with a hand-built template.
  - `test_one_call_per_page_with_boxes_and_skipped_pages`: a 2-page PDF; page 1 has no fields. One call is made, and page 1 is `skipped`.
  - `test_calls_ask_for_32000_output_tokens_and_300s_timeout`.
  - `test_page_answers_are_applied_with_page_pass_note`: the template history's last entries have `author == "agent"` and note `"page pass p0"`.
  - `test_provider_error_marks_the_page_and_keeps_its_boxes_unmapped`: the fake raises `FormcastError` for page 0. Status is `error`, the error text is kept, and no exception escapes.
  - `test_unparsable_answer_marks_the_page_unparsed`.
  - `test_usage_is_summed_over_pages`.
  - `test_results_do_not_depend_on_completion_order`: with `max_parallel=4` and the fake sleeping longer on page 0, the outcomes come back in page order and table merging gives the same template as `max_parallel=1`.
  - `test_llm_config_provider_is_created_and_closed`: monkeypatch `make_provider`.

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement.**
  - Render and call per page in a `ThreadPoolExecutor(max_parallel)`, each page call wrapped in `try/except Exception`.
  - Then apply, sequentially in page order: page 0 mapping ops, page 1 mapping ops, … then `table_ops` over all specs.
  - Make the `OpContext` the way `Session.__init__` does. Extract a small helper `op_context(form_map, schema_paths) -> OpContext` in `formcast/template/ops.py` and use it in both places.

- [ ] **Step 4: Run tests.** Run `tests/pagepass` and the full suite.

- [ ] **Step 5: Commit.** `git commit -m "pagepass: map_pages runner"`

### Task 5: `build_template` / `revise` / `Session` with vision

**Files:**
- Modify: `src/formcast/agent/api.py`, `src/formcast/agent/session.py`, `src/formcast/agent/prompts.py`, `README.md` (a short "Page images" section: what `vision` and `agent` do, and the `vision=False` fallback)
- Test: `tests/agent/test_session.py`, `tests/agent/test_prompts.py`

**Interfaces:**
- Consumes: `map_pages`, `PagePassResult` (Task 4); `render_marked_page`, `marks_for` (Task 2); `TextPart`, `ImagePart` (Task 1).
- Produces:
  - `build_template(..., vision: bool = True, agent: bool = True) -> SessionResult`.
  - `revise(..., vision: bool = True) -> SessionResult`.
  - `Session(..., page_images: list[tuple[int, bytes]] | None = None, after_page_pass: bool = False)`.
  - `SessionResult.page_pass: PagePassResult | None = None`.
  - `prompts.PAGE_PASS_NOTE: str`.
  - `prompts.page_image_parts(images: list[tuple[int, bytes]]) -> list[ContentPart]`: for each page, `TextPart(f"PAGE {n}: marked image (boxes as at the start of this session)")` then its `ImagePart`.

**Behaviour:**
- **`vision=False`:** `Session._first.content` is exactly the string `prompts.first_user_message(...)` returns today.
- **`vision=True`:** `Session._first.content` is `page_image_parts(images) + [TextPart(first_user_message(...))]`.
- **First-message text after a page pass:** with `after_page_pass=True`, the message uses `PAGE_PASS_NOTE` and then the `_template_note` listing, instead of `starting_note`. Add `after_page_pass` to `first_user_message`: when set, it emits `PAGE_PASS_NOTE` + `_template_note(template)`.
- **`PAGE_PASS_NOTE`** covers spec §6.4's points, in plain words:
  - a page pass already mapped boxes from the page images;
  - check its work against the images;
  - map the schema paths still unmapped;
  - add boxes for blanks with none;
  - split boxes holding several answers;
  - make sure every printed line of a record block is in a table;
  - keep page-pass mappings you cannot show are wrong.
- **`build_template`, fresh template with vision:**
  1. `seed = new_template(schema, fm, candidates=seed_candidates)`
  2. `pp = map_pages(seed, fm, pdf, provider)`
  3. If `agent=False`: return `SessionResult(template=pp.template, finished=True, summary="page pass only", steps=[], usage=pp.usage, stop_reason="finish", page_pass=pp)`.
  4. Otherwise: render images of `pp.template`'s fields per page, run `Session(fm, schema, provider, template=pp.template, page_images=..., after_page_pass=True, ...)`, set `result.page_pass = pp`, and add `pp.usage` into `result.usage`.
- **`start_from` with vision:** no page pass. Images of the start template, and the existing non-fresh note.
- **`revise` with vision:** images of the given template.
- **Provider lifetime:** when `llm` is an `LLMConfig`, `build_template` creates one provider, uses it for the page pass and the session, and closes it. Pass the Provider into `Session` so the session doesn't close it.

- [ ] **Step 1: Write failing tests.** Use the `ScriptedProvider`. Page-pass calls have `tools == []`; route them by a fake that answers page-pass calls with canned JSON and session calls from its script.
  - `test_vision_false_first_message_is_unchanged`. Compare `Session(..., page_images=None)._first.content` with `prompts.first_user_message(summarize(fm), paths, None, None, template=t, fresh=True, api_reference=api_text())`. Assert it is a `str` and equal.
  - `test_vision_first_message_has_page_images_then_text`.
  - `test_build_template_runs_page_pass_then_agent_from_its_template`. The page pass maps `name` to a field. The session's first message contains `PAGE_PASS_NOTE`, and the field's source is in `_template_note`'s listing. `result.page_pass` is set, and `result.usage` = page pass + session.
  - `test_build_template_agent_false_stops_after_page_pass`: no session call, `stop_reason == "finish"`, summary `"page pass only"`.
  - `test_build_template_vision_false_makes_no_page_pass_call`.
  - `test_start_from_skips_the_page_pass_but_sends_images`.
  - `test_revise_sends_images_when_vision`.
  - `test_config_llm_uses_one_provider_and_closes_it`: monkeypatch `make_provider`.
  - In `tests/agent/test_prompts.py`, `test_page_pass_note_is_in_the_after_page_pass_message`.

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement** as specified. Keep `Session` free of PDF access: `api.py` renders the images.

- [ ] **Step 4: Run the full suite.** Run the demo tests too (`tests/demo`). Demo fakes may need the new `vision` path handled. If a demo test drives `build_template` with a scripted provider that doesn't expect page-pass calls, pass `vision=False` in that test only, and leave the demo server code on the default.

- [ ] **Step 5: Commit.** `git commit -m "agent: page pass before the agent, page images in the agent"`

### Task 6: Eval pipelines, reachable score, spend guard

**Files:**
- Modify: `eval/agent_eval.py`, `eval/score.py`
- Test: `tests/eval/test_agent_eval.py`, `tests/eval/test_score.py` (create it if it doesn't exist; check `tests/eval/` first)

**Interfaces:**
- Consumes: `build_template(..., vision, agent)`, `SessionResult.page_pass`, `content_text`.
- Produces:
  - `score.mapping_verdicts(gt, template, iou_thr=0.3) -> list[tuple[bool, bool]]`: (path_ok, joint_ok) per GT field with a path, in GT order, using exactly `score_mapping`'s matching. Port the spike's `per_field` and `joint_per_field`.
  - `score.reachable(gt, seed, iou_thr=0.3) -> list[bool]`: per GT field with a path, whether some seed field with a rect on that page has IoU ≥ 0.3 with it. Table fields of the seed don't count.
  - Row key `mapping_reachable: {"n", "path_ok", "joint_ok", "path_acc", "joint_acc"}`, micro-averaged in `totals()[...]["mapping_reachable"]`.
  - CLI `--pipeline {agent,pages,pages+agent}`, default `agent`, mapped to `vision`/`agent` flags:
    - `agent` → `vision=False`;
    - `pages` → `vision=True, agent=False`;
    - `pages+agent` → `vision=True`.
  - CLI `--max-usd FLOAT`, default None.
  - `PRICES = {"claude-haiku-5-5": (0.10, 0.50)}` (USD per M input / output tokens).
  - `run_cost(rows) -> float | None`: None when any row's model has no price.
  - Row keys: `pipeline`, `page_pass_input_tokens`, `page_pass_output_tokens`, `page_errors` (pages with status `error` or `unparsed`). `meta["pipeline"]`.
  - The markdown title includes the pipeline.
  - `RecordingProvider.complete(messages, tools, **kw)` forwards `kw`. `first_user_message` is stored as `content_text(...)`. It records page-pass calls (`tools == []`) separately in `self.page_calls` (text, usage, seconds), so `reports()` and step logs still line up with the session's steps.
  - The step log JSON gains `"page_pass": [{"page", "status", "applied", "rejected", "usage", "seconds", "text", "error"}]`.

**Spend guard:** in `main`, jobs run in a process pool. Before submitting each job, check the running cost of the rows finished so far (`run_cost`). Once it exceeds `--max-usd`, stop submitting: the remaining jobs get rows with `error="skipped: spend limit"`. Submit jobs in a way that makes this possible (e.g. `as_completed` with a bounded number in flight equal to `--jobs`). Read the current `main` (around lines 845–940) before changing it.

- [ ] **Step 1: Write failing tests.**
  - `test_mapping_verdicts_agree_with_score_mapping`: on the existing `_template` helper's fixtures, `sum(p for p, _ in verdicts) == score_mapping(...)["path_ok"]`, and likewise for joint.
  - `test_reachable_uses_seed_boxes_on_the_same_page`.
  - `test_totals_micro_average_mapping_reachable`.
  - `test_parse_args_pipeline_and_max_usd`: choices are enforced, and the default is `agent`.
  - `test_pipeline_flags`: a pure helper `pipeline_flags(name) -> dict` returns `{"vision": False}`, `{"vision": True, "agent": False}` or `{"vision": True}`.
  - `test_run_cost_haiku_and_unknown_model`.
  - `test_recording_provider_text_for_image_messages_and_page_calls`: the first message with an `ImagePart` is stored with `[image: N bytes]`, a page-pass call goes to `page_calls`, and `reports()` is unaffected.

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement.** Also add a note to `NOTES`: "mapping_reachable: path/joint accuracy over GT fields that some seeded box (widget or detector candidate, before any edit) overlaps at IoU ≥ 0.3 — what a mapping-only step can reach."

- [ ] **Step 4: Run the full suite.**

- [ ] **Step 5: Commit.** `git commit -m "eval: pipelines, reachable mapping score, spend guard"`

### Task 7: Dev eval run (controller, not a subagent)

- [ ] **Step 1: Smoke test.** Run the llm smoke test on one image: `../../../.venv/bin/python -m pytest -m llm tests/llm/test_providers.py -k image -q` → PASS.
All spend in this task counts toward the $6 dev cap. Keep a running total from each results file's tokens at Haiku prices.

- [ ] **Step 2: Pages-only run.** It is cheap, about $0.15.
  `../../../.venv/bin/python eval/agent_eval.py --split dev --model-env ANTHROPIC_EVAL --variant both --pipeline pages --tag pages --max-usd 1 --jobs 4`
- [ ] **Step 3: Check against the spike.** Using the saved templates, score the spike's 34 pages (`cases.json` in the spike's directory) with the spike's reachability. Compare with its 0.96 (spec §8.1, within 0.03). Check Grandview page 2: the phone and date lines must be in tables.
  - If it is low, find out why before spending more: the prompt port, the marks, or the op conversion.
  - One reword of the Grandview sentence is allowed, tested with a single-form `pages` run.
- [ ] **Step 4: Fresh `agent` baseline.** About $1.65.
  `../../../.venv/bin/python eval/agent_eval.py --split dev --model-env ANTHROPIC_EVAL --variant both --pipeline agent --tag agent-baseline --max-usd 2.5 --jobs 3`
- [ ] **Step 5: Pages + agent run.** About $2.
  `../../../.venv/bin/python eval/agent_eval.py --split dev --model-env ANTHROPIC_EVAL --variant both --pipeline pages+agent --tag pages-agent --max-usd 3 --jobs 3`
- [ ] **Step 6: Judge against spec §8 items 1–3.**
  - If `pages` misses, stop and report.
  - If only the `pages+agent` parts miss, make one fix: reword `PAGE_PASS_NOTE`, or turn off agent images. Then re-run Step 5, if the remaining budget allows it.
- [ ] **Step 7: Commit the results files.** Commit `eval/results/*.json|md` for each run, and update the templates archive the way the repo does for committed runs. Record the results in the spec's new §11 "Results".

### Task 8: Held-out run (once) and write-up

- [ ] **Step 1: Run held-out.** Only if spec §8 items 1–4 hold on dev:
  `../../../.venv/bin/python eval/agent_eval.py --split heldout --model-env ANTHROPIC_EVAL --variant both --pipeline pages+agent --tag heldout-pages-agent --max-usd 3 --jobs 3 --baseline-stamp 2026-10-21_heldout`
- [ ] **Step 2: Record and commit.** Record the held-out numbers in spec §11 and commit. Change nothing in response.
