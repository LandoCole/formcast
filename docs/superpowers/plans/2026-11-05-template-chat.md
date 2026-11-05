# Template Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Chat tab in the extraction setup screen and the fill template editor where a person asks the AI for a
fix, sees the proposed changes, and applies or discards them.

**Architecture:** The chat is a thin layer over the edits each editor already applies. Extraction: a new
`extract-chat` job makes one model call (`formcast.extract.chat.propose_fix`) that answers with typed edits, proven
on a copy of the setup's template; Apply posts them to the existing `/edit` route with a version check. Fill: each
message runs the existing `revise` job with up to 3 earlier exchanges folded into its instruction; Apply puts the
returned template on the session's undo stack. The browser keeps the conversation in memory; the server keeps none.

**Tech Stack:** Python 3.12 (pydantic, PyMuPDF), stdlib HTTP server (`demo/server`), vanilla JS modules
(`demo/web`), pytest + Playwright.

**Spec:** `docs/superpowers/specs/2026-11-05-template-chat-design.md`

## Global Constraints

- The server stores nothing for the chat: no file, no table, no in-memory conversation.
- A request carries at most **3** earlier exchanges; more is a 400 `bad_request`.
- An exchange is `{message: str, reply: str, changes: str, outcome: "applied" | "discarded" | "pending"}`.
- Extraction: one model call per message through `formcast.data.ai._ask` (one retry on an unusable answer).
- Fill: one `revise` job per message, its existing budget (`SessionBudget(max_steps=15)`).
- No prompt names a kind of document; examples are invented (style of `src/formcast/extract/prompts.py`).
- Vision: at most **2** page images per extraction chat message.
- The TEMPLATE summary is cut to **25,000** characters, ready paths' lines cut first.
- Copy in the UI: stale proposal → "The template changed since this was proposed; ask again."; unusable answer →
  "The AI's answer could not be used." with **Try again**; AI off → the Chat tab says the AI is not configured and
  has no composer.
- Workbench style tokens only (`demo/web/workbench/workbench.css`: `--paper`, `--ink`, `--graphite`, `--rule`,
  `--serif`, `--mono`); proposals drawn dashed in `#2a78d6` (the review's `not_located` blue).
- Every repo commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A proposal that names a text line on a fillable page** (no `t` ids there) → the edit is refused inside the
   check and the model is asked again; never a 500. Test in Task 3.
2. **Apply after the person edited the setup meanwhile** → 409 `stale_setup`, the chat shows the stale copy, the
   setup is unchanged. Test in Task 2 (route) and Task 6 (UI).
3. **A broad request touching many paths** (every box moved) → all edits apply together or none; the preview
   lists every touched path. Test in Task 3.
4. **The person sends a 4th exchange of history** (a client bug or a crafted request) → 400, no model call. Test in
   Task 4 and Task 5.
5. **AI not configured** → `extract-chat` 400 `ai_not_configured` (existing check), the Chat tab shows no composer.
   Test in Task 4 and Task 6.

---

### Task 1: `add_field` and `set_reading` edits

**Files:**
- Modify: `src/formcast/extract/edit.py` (op models, `Edit` union at :239, `apply_edit` at :248, `_HANDLERS`,
  module docstring's op list)
- Test: `tests/extract/test_edit.py`

**Interfaces:**
- Produces:
  - `AddField(op="add_field", path: str, title: str, type: Literal["text","number","integer","date","yes/no"] = "text", description: str = "", category: str | None = None, required: bool = False)`
  - `SetReading(op="set_reading", path: str, sample: int, value: Any)`
  - both in `Edit`; `apply_edit` accepts an `AddField` whose path is **not** in the schema (every other op still
    refuses an unknown path).

- [ ] **Step 1: Write the failing tests**

```python
def test_add_field_adds_a_property_with_no_reader():
    t = apply_edit(hand_template(), AddField(path="investor.nickname", title="Nickname", type="text"))
    assert "investor.nickname" in concrete_paths(t.schema_) and "investor.nickname" not in t.readers
    assert schema_at(t.schema_, "investor.nickname")["title"] == "Nickname"

def test_add_field_then_a_box_in_one_list_reads_on_the_sample(): ...
    # apply AddField then SetBox(path, page=0, rect=FIELD_RECTS["tax_id"]); extract() on a corpus copy finds the tax id

def test_add_field_refuses_an_existing_path_an_item_path_and_a_reserved_name():
    for path in ("investor.tax_id", "owners[0].nickname", "file"):
        with pytest.raises(FormcastError):
            apply_edit(hand_template(), AddField(path=path, title="X"))

def test_set_reading_sets_the_samples_opinion_and_prove_uses_it():
    t = apply_edit(template_with_samples, SetReading(path="investor.tax_id", sample=0, value="11-1111111"))
    assert t.samples[0].opinion["investor.tax_id"] == "11-1111111"

def test_set_reading_refuses_a_sample_the_template_does_not_have(): ...
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/extract/test_edit.py -k "add_field or set_reading" -q`
Expected: FAIL (`AddField` not defined)

- [ ] **Step 3: Implement the two ops in `edit.py`**

`add_field` creates the property under its parent object (top level, or an existing object; a missing parent object
is created with `category`'s title when given), sets `title`, `description`, the type as `set_field` maps it, and
`required`; it refuses an existing path, a path with `[`, and a top-level name in `RESERVED_NAMES`. `set_reading`
needs `template.samples[sample]`. In `apply_edit`, check the path against the schema for every op but `AddField`.

- [ ] **Step 4: Run the edit tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/extract/test_edit.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/formcast/extract/edit.py tests/extract/test_edit.py
git commit -m "An extraction template edit can add a field and set the model's reading on a sample"
```

### Task 2: A setup's version, checked by `/edit`

**Files:**
- Modify: `demo/server/extraction.py` (`Setup` at :618: field, `summary`, `edit`, `learn`, `end_line`)
- Modify: `demo/server/extract_api.py` (`edit_setup` at :672, route docs at the top)
- Test: `tests/demo/test_server_extract.py`

**Interfaces:**
- Produces: `Setup.version: int` (0 at creation, +1 on every `edit`, `learn` and kept line); `summary()["version"]`;
  `POST /api/setups/{id}/edit` body may carry `version: int`; a different version → 409 `stale_setup`.

- [ ] **Step 1: Write the failing tests** — `test_an_edit_raises_the_setups_version`,
  `test_an_edit_sent_against_an_old_version_is_refused_and_changes_nothing` (409 `stale_setup`, template and
  version unchanged), `test_an_edit_with_no_version_still_applies` (the review screen's own edits are unchanged).
- [ ] **Step 2: Run them to see them fail** — `pytest tests/demo/test_server_extract.py -k version -q`; FAIL.
- [ ] **Step 3: Implement** — the version is compared and raised under the setup's lock in `Setup.edit(*edits,
  version: int | None = None)`, which raises a new `StaleSetup(Exception)`; `edit_setup` maps it to 409.
- [ ] **Step 4: Run** `pytest tests/demo/test_server_extract.py -q`; PASS.
- [ ] **Step 5: Commit** — "A setup has a version, and an edit can name the one it was made for".

### Task 3: `propose_fix`: one model call from a message to proven edits

**Files:**
- Create: `src/formcast/extract/chat.py`
- Modify: `src/formcast/extract/prompts.py` (add `CHAT_SYSTEM`; module docstring list)
- Modify: `src/formcast/extract/__init__.py` (export the public names)
- Test: `tests/extract/test_chat.py`

**Interfaces:**
- Consumes: `AddField`, `SetReading` (Task 1); `EDITS` shape of `edit.Edit`; `sample_view`, `sample_images`,
  `text_lines` (`extract/samples.py`); `_Samples.text_box` / `text_cell` (`extract/readers.py`); `prove`;
  `_ask` (`data/ai.py:190`).
- Produces:
  - `class ChatArea(BaseModel): sample: int; page: int; rect: tuple[float, float, float, float]`
  - `class ChatTurn(BaseModel): message: str; reply: str; changes: str; outcome: Literal["applied","discarded","pending"]`
  - `MAX_HISTORY = 3`
  - `@dataclass class ChatAnswer: reply: str; edits: list[Edit]; preview: dict[str, FieldProof]; usage: Usage`
  - `propose_fix(template: ExtractionTemplate, samples: list[SampleDoc], message: str, llm: LLMConfig | Provider, *, area: ChatArea | None = None, history: Sequence[ChatTurn] = (), vision: bool = True) -> ChatAnswer`
    — raises `FormcastError` for more than `MAX_HISTORY` turns, an empty message, an area on a sample or page the
    template lacks, or an answer unusable twice.

The model's answer is `{"reply": str, "edits": [...]}`; inside it, `set_box`/`set_option_box` may carry
`line: "t2_14"` (or `"t2_14+t2_15"`) with `sample`, or `area: true`, instead of `rect`. The check turns those into
`rect` (layout frame, `sample` dropped: a `line` through `_Samples(...).text_box(BoxRef(sample, line))`; `area: true`
moved into the layout frame as `apply_edit` does a sample's box), validates with a `TypeAdapter(list[Edit])`,
applies them in order to the template with `apply_edit(..., pdfs=...)` (raising `ValueError` on the first refusal so
`_ask` asks again), proves the result, and keeps `preview` = the `FieldProof` of every path an edit names.
The returned `edits` carry rectangles only.

The user message (one `Message("user", parts)`): page images (vision; ≤2: the area's page, else the pages with the
most fields not ready; `render_marked_page` with the template's boxes), then the samples' views
(`sample_view`, `TEXT_BUDGET` shared), then `TEMPLATE:` (`_template_lines(template, proof) -> list[str]`, one line
per concrete path that has a reader plus one per repeated group's rows with none, cut to 25,000 characters ready
lines first), `AREA:` (sample, page, rect, the text lines inside it with ids), `EARLIER:` (turns, oldest first),
`MESSAGE:`.

`CHAT_SYSTEM` (generic) says: what the samples and TEMPLATE lines are; answer one JSON object; the edit ops and
their fields (list them as `READERS_SYSTEM` lists reader fields); point at an answer by its text line, never by
inventing coordinates; use `area: true` for the person's area when no line fits; for every path it adds or whose
box, labels or clean steps it changes, give a `set_reading` per sample with the value read there; an empty `edits`
when nothing should change (the reply says why); keep the reply to one or two sentences.

- [ ] **Step 1: Write the failing tests** (a `Scripted` provider as in `tests/data/test_ai.py:19`, corpus samples
  from `tests/extract/corpus.py`, flattened `make_form` copies):
  - `test_a_line_id_becomes_the_answers_cell_and_the_preview_reads_it` — answer `add_field` + `set_box` with
    `line` + `set_reading`; `edits[1].rect` is a rectangle; `preview[path].ready`.
  - `test_the_person_s_area_is_the_box_when_the_answer_says_area` — `area: true` → the area's rect in the layout frame.
  - `test_an_edit_the_template_refuses_is_asked_again_with_what_was_wrong` — first answer names `t9_999`, second
    is good; the second call's text holds the refusal; 2 calls.
  - `test_a_line_on_a_fillable_page_is_refused_then_asked_again` (Review Focus 1).
  - `test_many_edits_are_proven_together_and_the_preview_has_every_path` (Review Focus 3).
  - `test_more_than_three_turns_is_refused_before_any_model_call`.
  - `test_the_template_lines_stay_within_their_budget_ready_lines_cut_first`.
  - `test_the_message_sends_area_text_lines_and_earlier_turns` — assert the `AREA:` block lists the ids of the
    lines inside the rect and `EARLIER:` the turns in order.
  - `test_the_setup_template_is_not_changed` — the template passed in compares equal afterwards.
- [ ] **Step 2: Run** `pytest tests/extract/test_chat.py -q`; FAIL (module missing).
- [ ] **Step 3: Implement `chat.py` and `CHAT_SYSTEM`.**
- [ ] **Step 4: Run** `pytest tests/extract/test_chat.py tests/extract/test_edit.py -q`; PASS.
- [ ] **Step 5: Commit** — "The AI proposes proven edits to an extraction template from a person's message".

### Task 4: The `extract-chat` job

**Files:**
- Modify: `demo/server/extract_api.py` (`start_extract_job` :245, new `_start_chat`, route docs)
- Modify: `demo/server/jobs.py` (`EXTRACT_KINDS` :83)
- Test: `tests/demo/test_server_extract.py`

**Interfaces:**
- Consumes: `propose_fix`, `ChatArea`, `ChatTurn`, `MAX_HISTORY` (Task 3); `Setup.snapshot()`, `Setup.version` (Task 2).
- Produces: `POST /api/jobs {kind: "extract-chat", setup, message, area?, history?}` → `{id}`; job result
  `{reply, edits: [Edit as JSON], preview: {path: FieldProof as JSON}, version}`. It does not mark the setup busy.
  400 `bad_request` for no message, a bad area or history (incl. more than 3 turns), before the job starts.

- [ ] **Step 1: Write the failing tests** — with `start_demo_server(provider_factory=...)` and a scripted fake:
  `test_a_chat_job_returns_proven_edits_and_leaves_the_setup_alone`, `test_a_chat_job_refuses_four_turns_of_history`
  (Review Focus 4), `test_a_chat_job_without_a_model_is_refused` (Review Focus 5),
  `test_applying_a_chat_answer_after_another_edit_is_stale` (job → `/edit` with another edit → `/edit` with the
  job's edits and version → 409).
- [ ] **Step 2: Run** `pytest tests/demo/test_server_extract.py -k chat -q`; FAIL.
- [ ] **Step 3: Implement `_start_chat(api, payload) -> dict`** in the style of `_start_line` (:601): validate,
  `snapshot()` and `version` taken before the job starts, provider opened and closed in the job, usage added,
  `_stop_if_cancelled` after the call.
- [ ] **Step 4: Run** `pytest tests/demo/test_server_extract.py -q`; PASS.
- [ ] **Step 5: Commit** — "An extract-chat job answers a message with proven edits for a setup".

### Task 5: Revise with earlier turns (fill side)

**Files:**
- Modify: `src/formcast/agent/prompts.py` (new `chat_instruction`)
- Modify: `demo/server/api.py` (`create_job` :557: read `history` for `revise`)
- Modify: `demo/web/engines/server/client.js` (`revise` :667 takes `history`)
- Test: `tests/agent/test_prompts.py` (or the existing prompts test file), `tests/demo/test_server_jobs.py`

**Interfaces:**
- Produces: `chat_instruction(message: str, history: Sequence[Mapping[str, str]] = ()) -> str` — the message alone
  with no history; else `EARLIER IN THIS CHAT:` then each turn as `Person: …` / `You: …` / `Changes: … (outcome)`,
  oldest first, then `NOW:` and the message. `revise` job payload `history?` (≤3, same shape as the extraction one);
  the API folds it into `instruction` with `chat_instruction`. `engine.revise(template, doc, instruction, region = null, history = [])`.

- [ ] **Step 1: Write the failing tests** — `test_chat_instruction_is_the_message_alone_with_no_history`,
  `test_chat_instruction_lists_earlier_turns_oldest_first`, and in `test_server_jobs.py`
  `test_a_revise_job_folds_history_into_its_instruction` (the fake provider sees `EARLIER IN THIS CHAT:`),
  `test_a_revise_job_refuses_four_turns_of_history` (Review Focus 4).
- [ ] **Step 2: Run** them; FAIL.
- [ ] **Step 3: Implement** the helper and the payload field.
- [ ] **Step 4: Run** `pytest tests/agent tests/demo/test_server_jobs.py -q`; PASS.
- [ ] **Step 5: Commit** — "A revise job can carry the last turns of a chat".

### Task 6: The chat panel, and the Chat tab of the extraction setup

**Files:**
- Create: `demo/web/core/chat.js` (the panel, shared by both editors)
- Modify: `demo/web/core/parts.css` (chat styles) — or `demo/web/workbench/workbench.css` if parts.css is the
  showcase's only
- Modify: `demo/web/workbench/index.html` (setup `col-side` at :209: tabs **Field** | **Chat**, a `#setup-chat` host)
- Modify: `demo/web/workbench/extract-setup.js` (PageView `regionSelect: true` when AI is on; `region` → chat area;
  proposed boxes drawn dashed; Apply with `{edits, version}`)
- Modify: `demo/web/engines/server/client.js` (`extract.chat(setupId, message, area, history)` → job handle;
  `extract.edits(id, edits, version?)`)
- Test: `tests/demo/test_workbench_extract.py`

**Interfaces:**
- Produces (`chat.js`): `export class ChatPanel { constructor(el, { send, available }) ; clear(); setArea(area | null); }`
  - `send(message, area, history) -> Promise<{ reply: string, changes: string[], apply: (() => Promise<void>) | null, discard: () => void }>`
    supplied by each editor; `history` is the last ≤3 exchanges the panel keeps (`{message, reply, changes, outcome}`).
  - The panel draws the thread (the person's messages with an area chip `page N · [x0, y0, x1, y1]`, replies,
    the list of changes, **Apply** / **Discard**, then the outcome), the composer (text box, **Send**, **Clear**,
    a removable pending area chip), progress with **Cancel** while waiting, and the error copy of Global
    Constraints. `available: false` shows the AI-off copy and no composer.

- [ ] **Step 1: Write the failing Playwright tests** (fixtures of `test_workbench_extract.py`: `extract_server`,
  `corpus_model` at :158-165; a corpus model that answers the chat prompt):
  `test_the_chat_tab_sends_a_message_and_applies_the_proposal` (field list gains the field; proof shows it ready),
  `test_a_dragged_area_goes_with_the_message` (the request body carries the area),
  `test_discard_changes_nothing`, `test_clear_empties_the_thread_and_history`,
  `test_a_stale_proposal_says_ask_again` (edit a field between send and Apply; Review Focus 2),
  `test_with_ai_off_the_chat_tab_has_no_composer` (Review Focus 5).
- [ ] **Step 2: Run** `pytest tests/demo/test_workbench_extract.py -k chat -q`; FAIL.
- [ ] **Step 3: Implement** `chat.js`, the tab markup and styles (mockup
  `.superpowers/brainstorm/96748-1793858737/content/chat-panel-v2.html`), and the setup wiring. Proposed boxes:
  the `preview`'s places for each touched path, drawn dashed on the shown sample until Apply/Discard; the field
  list marks touched paths. Opening another setup or template calls `clear()`.
- [ ] **Step 4: Run** `pytest tests/demo/test_workbench_extract.py -q`; PASS.
- [ ] **Step 5: Commit** — "The extraction setup has a Chat tab: ask the AI for a fix, then apply or discard it".

### Task 7: The Chat tab of the fill editor

**Files:**
- Modify: `demo/web/workbench/index.html` (`col-side` at :136: tabs **Box** | **Chat** over the Selected box and
  History groups, a `#fill-chat` host; remove the `#ask` popover at :126)
- Modify: `demo/web/workbench/jobs.js` (retire `closeAsk`/`ask-go`; `region` → `chat.setArea`; a `revise` run per
  message whose finished template is held, not loaded)
- Modify: `demo/web/workbench/workbench.js` (create the `ChatPanel` for the fill editor; `clear()` on reset)
- Test: `tests/demo/test_workbench_page.py`

**Interfaces:**
- Consumes: `ChatPanel` (Task 6), `engine.revise(..., history)` (Task 5), `session.replace`/undo
  (`demo/web/core/session.js`).
- Produces: Apply replaces the template on screen through the session (onto its undo stack) only when
  `session.template` is still the template the job was sent; else the stale copy.

- [ ] **Step 1: Write the failing Playwright tests** (the `text_only_agent` fixture of `tests/demo/conftest.py`):
  `test_the_fill_chat_runs_a_revise_and_applies_its_template_onto_undo` (Undo brings back the old template),
  `test_a_dragged_area_goes_to_the_chat_not_a_popover`, `test_the_fill_chat_refuses_a_stale_apply`,
  `test_discard_leaves_the_template`.
- [ ] **Step 2: Run** `pytest tests/demo/test_workbench_page.py -k chat -q`; FAIL.
- [ ] **Step 3: Implement** the wiring.
- [ ] **Step 4: Run** `pytest tests/demo/test_workbench_page.py tests/demo/test_server_jobs.py -q`; PASS.
- [ ] **Step 5: Commit** — "The fill editor has a Chat tab over the revise job; the area popover goes".

### Task 8: Documentation and a check by hand

**Files:**
- Modify: `README.md` (the workbench section: the Chat tab, what it sends, that nothing is stored, the 3-turn cap)
- Modify: `demo/server/extract_api.py` and `demo/server/api.py` module docstrings if not done in their tasks

- [ ] **Step 1:** Run the whole suite in the background: `PYTHONPATH=src:. .venv/bin/python -m pytest -q -p no:cacheprovider`;
  expected: all pass (the 3 deselected/4 skipped as before).
- [ ] **Step 2:** In the running workbench (`formcast-workbench-ai`, port 8767), open the saved **Test 4**,
  drag around "Payoff of First Mortgage Loan $100,000.00" on page 3, send "It missed this one. Add it.", check the
  proposal reads `100000` on the sample, Apply, and the field is ready. Open **Test 3**, send "Put every box on its
  value, not its label", check the preview, Discard. Screenshot both.
- [ ] **Step 3:** In the fill editor, open a form with a template, drag an area, send a revise request, Apply, Undo.
- [ ] **Step 4: Commit** — "The README describes the Chat tab".
