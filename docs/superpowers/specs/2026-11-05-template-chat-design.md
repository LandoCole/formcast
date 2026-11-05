# Template chat: ask the AI for a fix while editing a template

Date: 2026-11-05. Status: agreed in conversation; calls marked *(builder's call made by Claude)* are open to the
person's correction.

## Intent

A template is still built in one pass (a page pass or an agent for a fill template, `build_setup` for an extraction
template). After a person has reviewed it, they can ask the AI for a second pass in plain words: a targeted fix
("it missed this one: add it", pointing at the place on the page) or a broad one ("put every box on its value, not
on its label", the failure of the first Closing Disclosure test). The AI answers with changes the person sees and
applies or discards.

The chat is a showcase of an agent-assisted tool, not an integrated agent the product depends on. A product that
embeds formcast later may drop the chat and keep everything under it: the chat only turns a message into the typed
edits the editors already apply.

## Goals

- A **Chat** tab in the right panel of both template editors:
  - the extraction setup screen (`demo/web/workbench/extract-setup.js`, the panel beside **Field**);
  - the fill template editor (`demo/web/workbench/workbench.js`, the panels `showLinkPanels` draws).
- The person can point at the page: an area dragged on the page goes with the message (sample, page, rectangle);
  the server adds the text in it.
- Every answer is a short reply and a list of changes, shown before anything changes. **Apply** applies them all
  (or none); **Discard** drops them.
- Targeted and broad requests go through the same flow; a broad one is a longer list of changes.
- Generic prompts: nothing in a prompt names a kind of document. Examples are invented, in the style of
  `src/formcast/extract/prompts.py`.

## Non-goals

- Fixing a finished run's values from run review. A run that misses something goes back to the template: add the
  document as a sample, or fix the template, and run again. (Run review already lets a person type a value.)
- Saved chat history, several chats per template, long-running chat sessions.
- Undo for an applied extraction change. Discard covers the case before applying; the fill editor keeps its
  existing undo stack, which an applied chat change goes onto like any other.
- Changing how templates are built.

## Anti-goals (a failure even if it works)

- New stored state: no chat file, no history table, nothing written to disk for the chat.
- Per-message cost that grows with the length of the chat.
- An edit applied that the person did not see, or that was not checked on the samples first.
- Document-specific prompting.

## Constraints (decided)

- **The server keeps no chat state.** Each chat request is self-contained: the current template (or setup id, see
  below), the message, the optional area, and at most the **last 3 exchanges** (each: the person's message, the AI's
  reply, and a one-line note of the changes and whether they were applied or discarded). The browser holds the
  conversation in memory; a **Clear** button empties it, and opening another template, form or setup empties it.
  Nothing survives a reload.
- **The current template is the memory.** A request never carries the history of edits, only the template as it is.
- One model call per extraction message (one retry when the answer cannot be used, as `formcast.data.ai._ask`
  does). A fill message is one `revise` agent run with its existing step budget.

## How it works: extraction setup

The in-progress setup already lives in server memory (`Setups`, `demo/server/extraction.py:784`; id is 32 hex);
that is the setup's own state, not chat state, and the chat does not change it.

### Request

`POST /api/jobs {kind: "extract-chat", setup, message, area?, history?}` starts a job, as `extract-line` does
(`_start_line`, `demo/server/extract_api.py:601`), so it shows progress and can be cancelled.

- `area`: `{sample, page, rect: [x0, y0, x1, y1]}` in that sample page's points (the frame `region` events of the
  page view give, `demo/web/core/pageview.js:707`).
- `history`: up to 3 items `{message, reply, changes, outcome: "applied" | "discarded" | "pending"}`; more are
  refused with 400.

### What the model is sent (new `src/formcast/extract/chat.py`, prompt `CHAT_SYSTEM` in `extract/prompts.py`)

1. The samples' views as `propose_readers` sends them (`sample_view`, with text lines as `t<page>_<n>`), within
   `TEXT_BUDGET`.
2. With vision on, at most 2 page images *(builder's call made by Claude)*: the area's page first, else the pages
   holding the most fields that are not ready; each drawn with the template's current boxes.
3. **TEMPLATE**: one line per concrete path that has a reader, and one line per repeated group's generic path for
   its rows that have none (`rows[4..31].col: no reader`), each with: title,
   type, category, absent or not, how it is read (form fields, box page and rectangle, labels and side, phrases,
   options), and per sample the proof's outcome, the value read and the model's opinion. Edits name concrete paths
   (`owners[2].percent`), as every edit op does; the schema edits already apply to every row of a group. Compact
   enough that a template of a few hundred read paths fits in about 25k characters; past that, the lines of the
   paths that are ready are cut first *(builder's call made by Claude)*.
4. **AREA** (when given): the sample, page and rectangle, and the text lines inside it with their ids.
5. **EARLIER** (when given): the exchanges, oldest first.
6. **MESSAGE**: the person's words.

### What the model answers

`{"reply": "<one or two sentences>", "edits": [<edit>, ...]}`: the existing edit ops of
`src/formcast/extract/edit.py` (`set_field`, `set_box`, `set_option_box`, `set_labels`, `set_clean`,
`mark_absent`, `restore`, `remove_field`, `rename_field`, `set_category`, `make_option_set`, `split_option_set`),
plus:

- **New op `add_field`** `{path, title, type, description?, category?, required?}`: adds a schema property with no
  reader yet; the edits after it in the same list give it its box, labels and clean steps. Today no edit adds a
  field (`apply_edit` refuses an unknown path, `edit.py:266`); this op is the one exception, refused for a path
  that exists. Paths inside a repeated group are refused in this first version *(builder's call made by Claude)*.
- **New op `set_reading`** `{path, sample, value}`: what the model reads at `path` on sample `sample` (its
  opinion, `SampleRecord.opinion`, `extract/model.py:261`), which `prove` checks the readers against. The chat
  answer gives one for every sample, for each path it adds or whose box, labels or clean steps it changes, read in
  the same model call (it sees the samples already): without it a new field could never be ready, and a moved box
  would be proven against a reading of the wrong place. These go to `/edit` with the rest, so an applied change
  carries the model's values *(builder's call made by Claude)*.
- **The person's area as the box**: `set_box` (and `set_option_box`) may give `area: true` instead of `rect`: the
  rectangle the person dragged, on its sample. It is the way to place a box on a fillable page, or anywhere the
  text has no line of its own (`text_lines` gives none for a fillable page) *(builder's call made by Claude)*. No
  op adds or changes a form field read in this version.
- **A box named by a text line**: `set_box` and `set_option_box` may give `line: "t2_14"` (or `"t2_14+t2_15"`) with
  `sample` instead of `rect`. The chat module turns it into a rectangle with the same code the build uses
  (`_Samples.text_box` / `text_cell` in `extract/readers.py`: the value's cell, bounded by its row neighbours and
  the table's column strokes) and into the layout's frame. The model never writes raw coordinates it made up.
  The `line` form exists only in the model's answer; the edits returned to the browser carry rectangles.

### Checking before the person sees it

The answer's check (the `check` of `_ask`) converts lines to rectangles, validates every edit with the `EDITS`
adapter, applies them in order to a **copy** of the setup's template (`apply_edit`, with the sample PDFs) and
raises on the first one refused, so the model is asked once more with what was wrong. The copy is then proven
(`prove`). The job's result:

`{reply, edits: [<edit with rect>], preview: {<path>: {ready, samples: [{outcome, value}]}}, usage}` for every
path an edit touched. The setup itself is unchanged.

### Apply and Discard

**Apply** posts the returned edits to the existing `POST /api/setups/{id}/edit` with `{edits, version}` (all or
none). `Setup` gets a `version`, a count of the edits and lines applied to it, in its summary; the chat job's
result carries the version it was proposed against; `/edit` given a `version` that is not the setup's answers 409
`stale_setup`, shown in the chat as "The template changed since this was proposed; ask again." So a proposal is
never applied to a template it was not proven on *(builder's call made by Claude)*. An `extract-chat` job does not
mark the setup busy: the person may keep editing while it runs, and a proposal made stale is refused at Apply.
**Discard** sends nothing.

## How it works: fill template editor

The fill side is already stateless (the browser sends the whole template on every call) and already has a
free-text revise: `POST /api/jobs {kind: "revise", doc, template, instruction, region?}` runs
`formcast.agent.api.revise` (`src/formcast/agent/api.py:200`) and returns the revised template; the floating
"Ask the AI about this area" box (`demo/web/workbench/jobs.js:355-397`) starts it after an area drag.

- Each chat message runs one `revise` job: `instruction` = the message, `region` = the area.
- New optional `history` on the revise payload (same shape and cap as above). `first_user_message`
  (`src/formcast/agent/prompts.py:293`) adds an `EARLIER IN THIS CHAT:` block after the reviewer instruction.
- The job's result is held as a proposal: the chat shows the agent's `summary` and the number of changes; **Apply**
  replaces the template on screen through the session, onto the undo stack (`demo/web/core/session.js`), so the
  editor's undo still works; **Discard** drops it. Apply is refused, in the chat, when the template on screen is not
  the one the job was sent (the person edited meanwhile): "The template changed since this was proposed; ask
  again." *(builder's call made by Claude)*
- The floating ask box is retired: an area dragged on the page goes to the chat as the message's area instead
  *(builder's call made by Claude)*.
- No page preview of a proposal on the fill side in this version: the page shows the template on screen until
  Apply *(builder's call made by Claude)*.

## The Chat tab (both editors)

As the mockup `.superpowers/brainstorm/96748-1793858737/content/chat-panel-v2.html` shows, in the workbench's style
(`demo/web/workbench/workbench.css` tokens: paper, ink, graphite, rule; EB Garamond; IBM Plex Mono labels):

- Tabs **Field** | **Chat** at the top of the right panel (fill editor: beside its existing panels).
- The thread: the person's messages (an area shown as a mono chip, "page 3 · [302, 214, 572, 226]" with the text),
  the AI's replies, each with its list of changes, the preview of what they read on the samples (extraction), and
  **Apply** / **Discard**. Once applied or discarded, the buttons give way to the outcome.
- The composer: a text box, **Send**, **Clear**, and a line saying an area dragged on the page goes with the next
  message. A pending area shows as a chip that can be removed.
- While a job runs: the existing job progress style, and a cancel.
- Extraction only: the proposed boxes are drawn dashed on the page, and new or changed fields are marked in the
  field list, until Apply or Discard.
- AI off (no model configured): the Chat tab says so and has no composer.

## Errors

- The model's answer unusable twice: the chat shows "The AI's answer could not be used" with a **Try again**.
- A model or network error: shown in the thread; nothing changes.
- An answer with no edits: the reply alone, no buttons.

## Testing

- Unit: `add_field` in `tests/extract/test_edit.py` (new file or the existing edit tests); the chat module with a
  scripted provider: line-to-rectangle conversion, a refused edit asked again, the preview, the history cap.
- Server: `extract-chat` jobs and `revise` with `history` in `tests/demo/test_server_extract.py` and
  `tests/demo/test_server_jobs.py`, with the existing fake providers.
- Workbench: Playwright tests for the Chat tab on both screens (send, area chip, Apply, Discard, Clear, AI off),
  in the style of `tests/demo/test_workbench_extract.py`.
- By hand: on the Closing Disclosure (`forms/realdocs/`), "it missed Payoff of First Mortgage Loan" with an area,
  and on a template built before the label fix, "put every box on its value".

## Base

Built on the extraction work in this worktree that is not yet committed: text line ids in `sample_view`,
`text_lines` (`extract/samples.py`), `_Samples.text_box` and `text_cell` (`extract/readers.py`), `DocText.walls`
(`extract/page.py`). That work lands first, as its own commit.

## Left to the builder

File layout inside the new module and the JS, the exact template summary format within the budget above, the
wording of `CHAT_SYSTEM` (generic, invented examples), job progress text, and how the field list marks new or
changed fields.
