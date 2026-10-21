# formcast Plan 1: Page Map, Template, Agent Layer, Eval — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Given a blank PDF and a JSON Schema, an LLM agent (no vision) produces a reviewed-ready template: field boxes placed, labeled and mapped to schema paths. We measure how much it improves on the CommonForms detectors alone.

**Architecture:** A deterministic page map comes first. It combines PyMuPDF text, widgets and rules, OCR when the text layer is missing, and CommonForms detections. An agent session (our own loop, Chat Completions or Responses) has the model write Python in a persistent sandbox worker. The page map, schema and edit-op functions are preloaded there. Edit ops are recorded in the sandbox and applied and validated by the parent against the authoritative pydantic `Template`. An eval harness scores detection and mapping against ground truth for dev and held-out forms.

**Tech Stack:**
- Python ≥ 3.11
- PyMuPDF 2.x
- commonforms (FFDNet-L, FFDetr)
- pytesseract + Tesseract (`[ocr]` extra)
- pydantic 2, httpx, jsonschema
- pytest
- uv for environments

**Spec:** `docs/superpowers/specs/2026-10-20-form-projection-design.md`. Read §6–§8, §11 and §14 before starting.

**Out of scope (later plans):**
- **Plan 2:** `fill`, `to_smart_pdf`, fill report, text fitting.
- **Plan 3:** `revise` UI flows, `similarity`, the vanilla-JS demo app.

This plan includes `revise()` at the API level, because it's the same loop as `build_template` and costs nothing extra.

## Global Constraints

- Package name `formcast`, in `src/formcast/`. Python `>=3.11`. *Spec deviation:* the spec says ≥3.10, but PyMuPDF 2.0 requires 3.11.
- License: AGPL-compatible. PyMuPDF and ultralytics are AGPL, which the user accepted.
- Coordinates everywhere outside the detector wrapper: **PDF points, origin top-left, unrotated page space**, as `(x0, y0, x1, y1)`.
- No vision model. The agent sees text and geometry only.
- Frontier models only. Tool calling is required.
- LLM config is `base_url`, `api_key`, `model`, and `api_format` in `{"chat_completions", "responses"}`.
- **No form-specific code or prompt text.** Nothing may branch on a form's name, label text or layout to improve a score. Dev ground truth is for scoring only. Every reviewer checks this.
- `forms/heldout/` is scored only in Task 15, step "held-out run". It is never used for tuning.
- Template JSON is the source of truth. The agent's code is not saved.
- Secrets live in a git-ignored `.env` and are never printed or logged.

## Review Focus

1. **Rotated pages** (`/Rotate 90`) and crop boxes offset from the media box. Words, rules, widgets and detections must all land in the same unrotated point space. Pinned in Task 2 and Task 4.
2. **Encrypted or broken PDFs.** `build_form_map` raises `FormcastError("encrypted PDF")` or `FormcastError("cannot open PDF")` instead of crashing deep inside a library. Pinned in Task 5.
3. **Runaway agent code:** infinite loops, huge stdout, `import os; os.remove(...)`. A step times out and returns an error, stdout is truncated, and the import is refused, all without killing the session. Pinned in Task 10.
4. **Model misbehavior:** a reply with no tool call, malformed JSON tool arguments, or an unknown tool name. The session sends a corrective message, counts the step against the budget, and never raises out of `run()`. Pinned in Task 12.
5. **Schema shapes:** nested objects, arrays of objects (table rows), arrays of scalars, enums, missing descriptions, property names with dots or spaces. Path flattening is unambiguous (`a.b`, `rows[].col`, quoted segments like `["first name"]`) and ops validate against it. Pinned in Task 6.

---

## File Structure

```
pyproject.toml                      # new; formcast package, extras [ocr], [dev]
.gitignore                          # .env, .venv, caches, eval/results/overlays/
legacy/                             # old rlm_lab project, moved untouched (Task 0)
src/formcast/
  __init__.py                       # public API re-exports
  errors.py                         # FormcastError, OpError
  geometry.py                       # Rect helpers, iou, norm<->points, rotation mapping
  schema.py                         # JSON Schema -> SchemaPath list; data validation
  transforms.py                     # registry + built-in transforms + chain validate/apply
  match.py                          # fingerprint(pdf)
  pagemap/
    model.py                        # Word, TextLine, Rule, Widget, Candidate, PageMap, FormMap
    extract.py                      # PyMuPDF: words, lines, rules, widgets, text-layer coverage
    ocr.py                          # Tesseract words + deskew angle (optional extra)
    detect.py                       # Detector protocol, FFDNet/FFDetr wrappers, merge
    build.py                        # build_form_map(): assemble + label links
    query.py                        # FormMapQuery helpers exposed to agent code
    summarize.py                    # compact text rendering for the model
  template/
    model.py                        # Template, Field, Source, Style, TableSegment, Column, HistoryEntry
    ops.py                          # op models, apply(), replay(), diff()
    coverage.py                     # coverage report
  llm/
    config.py                       # LLMConfig
    types.py                        # Message, ToolSpec, ToolCall, LLMResponse, Usage
    base.py                         # Provider protocol + make_provider()
    chat_completions.py
    responses.py
  sandbox/
    protocol.py                     # JSON-lines request/response models
    worker.py                       # subprocess entry: persistent namespace, op recording
    client.py                       # Sandbox class
  agent/
    prompts.py                      # SYSTEM_PROMPT + message builders (generic only)
    report.py                       # step report text
    session.py                      # Session loop
    api.py                          # build_template(), revise()
  render/
    preview.py                      # preview(): page PNGs with field boxes drawn
tests/                              # mirrors src; conftest builds fixture PDFs with PyMuPDF
eval/
  score.py                          # shared IoU matching + mapping scoring
  baseline_detection.py             # exists; refactored onto formcast.pagemap.detect (Task 13)
  gt_draft.py                       # draft GT for flat forms (Task 14)
  agent_eval.py                     # runs build_template over a split, scores, writes results
  results/
forms/
  manifest.json                     # exists
  dev/  heldout/                    # exist
  gt/<id>.json                      # ground truth boxes (+ schema paths)
  schemas/<id>.json                 # JSON Schema per form
```

---

### Task 0: Repository reset and package skeleton

**Files:**
- Move: `rlm_lab/`, `src/rlm_lab/`, `app.py`, `sitecustomize.py`, `README.md`, `pyproject.toml`, `gutenburg books/`, `runs/`, `tests/` → `legacy/` (keep their relative layout)
- Delete: `.dspy_cache/`, `.pytest_cache/`, `.venv/` (old py3.10 env)
- Create: `pyproject.toml`, `.gitignore`, `README.md` (short: what formcast is, setup, link to spec), `src/formcast/__init__.py`, `src/formcast/errors.py`, `tests/conftest.py`, `tests/test_smoke.py`

**Interfaces:**
- Produces:
  - `formcast.errors.FormcastError(Exception)`
  - `formcast.errors.OpError(FormcastError)` with attributes `op: dict` and `reason: str`
  - pytest markers `slow` (downloads models) and `llm` (calls a real model). Both are skipped unless `-m slow` / `-m llm` is passed.

- [ ] **Step 1:** `git init` (only if the user approved it at plan review). Then move the legacy files with `mkdir legacy && mv ... legacy/` and delete the caches and the old `.venv`.
- [ ] **Step 2:** Write `pyproject.toml`:
  - `name="formcast"`, `requires-python=">=3.11"`, src layout
  - dependencies `pymupdf>=1.24`, `commonforms>=0.2.1`, `pydantic>=2.11`, `httpx>=0.27`, `jsonschema>=4.22`, `numpy`, `pillow`
  - extras `ocr=["pytesseract>=0.3.13"]` and `dev=["pytest>=8"]`
  - pytest `addopts = "-m 'not slow and not llm'"`, with the markers registered
- [ ] **Step 3:** Write `.gitignore`: `.env`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `eval/results/overlays/`, `*.egg-info/`.
- [ ] **Step 4:** `uv venv -p 3.12 .venv && uv pip install -e ".[ocr,dev]"`.
- [ ] **Step 5:** Copy `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` from `../ontoo 2/backend/.env` into `.env` with a script that never echoes the values. Then confirm `.env` is ignored with `git check-ignore .env`.
- [ ] **Step 6:** Write `tests/test_smoke.py::test_import` asserting `import formcast` works and `formcast.errors.OpError(op={}, reason="x").reason == "x"`. Run `.venv/bin/pytest -q`. Expected: 1 passed.
- [ ] **Step 7:** Commit: `chore: move rlm_lab to legacy, scaffold formcast package`.

### Task 1: Geometry helpers

**Files:**
- Create: `src/formcast/geometry.py`
- Test: `tests/test_geometry.py`

**Interfaces:**
- Produces:
  - `Rect = tuple[float, float, float, float]`
  - `iou(a: Rect, b: Rect) -> float`
  - `area(r: Rect) -> float`
  - `intersect(a: Rect, b: Rect) -> Rect | None`
  - `norm_to_points(box: Rect, page_rect: Rect) -> Rect`: `box` is normalized to the rendered *crop box* image
  - `points_to_norm(r: Rect, page_rect: Rect) -> Rect`
  - `horizontal_overlap(a: Rect, b: Rect) -> float` (0..1 of the smaller width)
  - `vertical_gap(upper: Rect, lower: Rect) -> float`

- [ ] **Step 1:** Write tests:
  - `iou((0,0,10,10),(5,0,15,10)) == pytest.approx(1/3)`
  - `iou` of disjoint rects is `0.0`
  - `norm_to_points((0.5,0.5,1,1),(0,0,612,792)) == (306,396,612,792)`
  - with a crop box offset `(36,36,576,756)`, `norm_to_points((0,0,1,1), crop)` equals the crop box
  - `points_to_norm` round-trips
  - `horizontal_overlap((0,0,10,1),(5,0,30,1)) == 0.5`
- [ ] **Step 2:** Run `pytest tests/test_geometry.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3:** Implement `geometry.py` (pure functions, no PyMuPDF import).
- [ ] **Step 4:** Run tests. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: geometry helpers`.

### Task 2: Page map model and PyMuPDF extraction

**Files:**
- Create: `src/formcast/pagemap/__init__.py`, `src/formcast/pagemap/model.py`, `src/formcast/pagemap/extract.py`
- Modify: `tests/conftest.py` (fixture PDF builders)
- Test: `tests/pagemap/test_extract.py`

**Interfaces:**
- Consumes: `formcast.geometry`
- Produces (pydantic models, all rects in points):
  - `Word(text: str, rect: Rect, line_id: int, source: Literal["pdf","ocr"], conf: float = 1.0)`
  - `TextLine(id: int, text: str, rect: Rect, word_ids: list[int], font_size: float | None, bold: bool = False)`
  - `Rule(rect: Rect, orientation: Literal["h","v"])`: thin drawn lines, plus `___` underscore runs in text converted to `h` rules
  - `Widget(name: str, kind: Literal["text","checkbox","radio","combo","list","signature","button"], rect: Rect, options: list[str] = [], tooltip: str | None = None)`
  - `Candidate(id: str, kind: Literal["text","choice","signature"], rect: Rect, conf: float, detector: str, label_line_id: int | None = None, label_relation: Literal["left","above","inside","below"] | None = None)`
  - `PageMap(index: int, width: float, height: float, rotation: int, kind: Literal["fillable","digital","scanned"], text_source: Literal["pdf","ocr","pdf+ocr"], words: list[Word], lines: list[TextLine], rules: list[Rule], widgets: list[Widget], candidates: list[Candidate] = [])`
  - `FormMap(pages: list[PageMap], fingerprint: str = "", source_name: str = "")`
  - In `extract.py`:
    - `extract_page(page: pymupdf.Page) -> PageMap`: candidates empty, `kind` provisional
    - `text_layer_coverage(page: pymupdf.Page, rules: list[Rule]) -> float`: the fraction of page "ink" that has extractable text over it (compute it by rendering at low dpi and comparing ink pixels inside word boxes against all ink outside rules)
    - `needs_ocr(page) -> bool`: `True` when the page is image-dominated (one image covering more than 80%) or coverage is below 0.35

- [ ] **Step 1:** In `conftest.py`, add builders that write a PDF to `tmp_path` with PyMuPDF:
  - `flat_form_pdf`: "Name:" text, a horizontal line to its right, a 10×10 square box with "Yes" to its right, "Date" text above a line
  - `fillable_form_pdf`: the same page plus a text widget named `applicant_name` and a checkbox `agree`
  - `rotated_form_pdf`: flat_form with `page.set_rotation(90)`
  - `cropped_form_pdf`: flat_form with the crop box inset by 36pt
  - `image_only_pdf`: flat_form rendered to a 150-dpi PNG and inserted as a full-page image
- [ ] **Step 2:** Write tests:
  - flat form: one `TextLine` whose text starts with "Name"; at least one `h` rule right of it with a vertical gap under 4pt to the "Name" line's baseline; `widgets == []`
  - fillable: two widgets, kinds `{"text","checkbox"}`, names `{"applicant_name","agree"}`
  - rotated: the "Name" word rect equals the unrotated flat-form rect within 1pt
  - cropped: the word rect is relative to the crop box origin
  - `needs_ocr` is `False` for flat and `True` for image-only
  - underscores `"Phone ________"` produce an `h` rule spanning the underscore run
- [ ] **Step 3:** Run tests. Expected: FAIL.
- [ ] **Step 4:** Implement the models and `extract.py`:
  - use `page.get_text("rawdict")` for words, lines, font size and bold
  - use `page.get_drawings()` for rules: items with height ≤ 2pt and width ≥ 20pt are `h`; the reverse are `v`; rectangles become their 4 edges
  - use `page.widgets()` for widgets
  - convert everything to unrotated crop-box space (`page.rotation_matrix` inverse, offset by `page.cropbox.tl`)
- [ ] **Step 5:** Run tests. Expected: PASS.
- [ ] **Step 6:** Commit: `feat: page map model and PDF extraction`.

### Task 3: OCR words and deskew

**Files:**
- Create: `src/formcast/pagemap/ocr.py`
- Test: `tests/pagemap/test_ocr.py`

**Interfaces:**
- Consumes: `Word`, `TextLine`, `geometry.norm_to_points`
- Produces:
  - `ocr_page(page: pymupdf.Page, dpi: int = 300) -> tuple[list[Word], list[TextLine]]`: words have `source="ocr"` and `conf` in 0..1, and rects are in points
  - `estimate_skew(image: PIL.Image.Image) -> float`: degrees; implement with a projection-profile search over −3..3° in 0.1° steps on a downscaled binarized image
  - `deskew(image, angle) -> PIL.Image.Image`
  - `ocr_available() -> bool`
  - When `pytesseract` or the binary is missing, `ocr_page` raises `FormcastError("OCR requested but Tesseract is not installed; pip install formcast[ocr] and install tesseract")`.

- [ ] **Step 1:** Write tests, skipped when `not ocr_available()`:
  - on `image_only_pdf`, OCR finds a word matching `/Name/` whose rect is within 3pt of the flat-form word rect
  - `estimate_skew` of an image rotated by 1.5° returns 1.5 ± 0.3
  - missing Tesseract (monkeypatch `ocr_available` to False) raises `FormcastError` with that exact message
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement with `pytesseract.image_to_data(output_type=DICT)`. Group lines by `(block_num, par_num, line_num)`. Map pixel coordinates back to points through the rotation and crop handling from Task 2.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: OCR and deskew for pages without a usable text layer`.

### Task 4: Detector wrapper

**Files:**
- Create: `src/formcast/pagemap/detect.py`
- Test: `tests/pagemap/test_detect.py`

**Interfaces:**
- Consumes: `Candidate`, `geometry.norm_to_points`, `geometry.iou`
- Produces:
  - `class Detector(Protocol): name: str; def detect(self, image: PIL.Image.Image) -> list[tuple[str, Rect, float]]`: kind, normalized box, confidence
  - `FFDNetDetector(model="FFDNet-L", confidence=0.3, image_size=1216)`
  - `FFDetrDetector(confidence=0.3)`: calls `model.predict` directly and handles both list and single returns
  - `merge_detections(lists: list[list[Candidate]], iou_threshold=0.5) -> list[Candidate]`: keeps the higher-confidence box of each overlapping pair, irrespective of class
  - `detect_page(page: pymupdf.Page, page_kind: str, detectors: dict[str, Detector], policy: DetectPolicy) -> list[Candidate]`
  - `DetectPolicy(digital=("FFDNet-L",), scanned=("FFDetr",), merge_iou=0.5, dpi=144, deskew_scanned=True)`
  - `load_default_detectors() -> dict[str, Detector]` (lazy, cached)
  - Candidate ids are `"c{page}_{n}"`, in reading order (top-to-bottom, then left-to-right, with 0.01-height row bucketing like commonforms' `sort_widgets`).

- [ ] **Step 1:** Write tests with a `FakeDetector` returning fixed normalized boxes:
  - `detect_page` on `cropped_form_pdf` converts `(0,0,1,1)` to the crop box in point space
  - on `rotated_form_pdf`, a box detected on the rendered (rotated) image maps back to the unrotated point rect of the fixture line within 2pt
  - `merge_detections` of two lists with a 0.8-IoU pair keeps one, with the higher confidence
  - the ids are in reading order
  - `@pytest.mark.slow`: real `FFDNetDetector` on `flat_form_pdf` returns at least 1 `text` candidate overlapping the drawn line
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement. Render with `page.get_pixmap(dpi=policy.dpi)` (the pixmap respects rotation and crop). Map normalized image boxes to the rotated page rect, then through the inverse rotation matrix to unrotated crop-box space. For scanned pages, detect on the deskewed image and rotate box centers back by the angle.
- [ ] **Step 4:** Run `pytest tests/pagemap/test_detect.py -q`, then `-m slow`. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: CommonForms detector wrapper with per-kind policy and merge`.

### Task 5: Build the form map, label links, query helpers, summary

**Files:**
- Create: `src/formcast/pagemap/build.py`, `src/formcast/pagemap/query.py`, `src/formcast/pagemap/summarize.py`, `src/formcast/match.py`
- Test: `tests/pagemap/test_build.py`, `tests/pagemap/test_query.py`, `tests/pagemap/test_summarize.py`

**Interfaces:**
- Consumes: Tasks 2–4
- Produces:
  - `build_form_map(pdf: str | Path | bytes, *, detectors=None, policy=DetectPolicy(), ocr="auto"|"never"|"always") -> FormMap`. The page kind is `fillable` if the page has widgets, `scanned` if image-dominated, otherwise `digital`. OCR runs when `needs_ocr`. Detection is skipped on fillable pages. Raises `FormcastError("encrypted PDF")` and `FormcastError("cannot open PDF")`.
  - `link_labels(page: PageMap) -> None`: sets `label_line_id` and `label_relation` per candidate. Rule: nearest line to the left on the same row (vertical-center distance < 0.6 × line height), else the nearest line whose bottom is within 18pt above and which overlaps horizontally by at least 30%, else a line inside, else a line directly below within 14pt (sub-labels).
  - `fingerprint(pdf) -> str`: sha256 of page count + rounded page sizes + the first 2000 normalized characters of text per page (OCR text not used), as hex[:16]
  - `FormMapQuery(form_map)` with methods:
    - `words(page)`, `lines(page)`
    - `find_text(pattern: str, page=None, regex=False) -> list[TextLine]`
    - `right_of(rect_or_item, page, max_dist=300)`, `left_of`, `above`, `below`
    - `near(rect, page, radius)`
    - `candidates(page=None, kind=None, min_conf=0.0)`, `rules(page, orientation=None)`, `widgets(page=None)`
    - `region(page, rect) -> dict` with keys `lines`, `rules`, `candidates`, `widgets`
    - `label_for(candidate_id) -> TextLine | None`
    - `sublabels_under(rect, page) -> list[TextLine]` (small-font lines within 14pt below)

    All of them return plain dicts or lists of dicts, so they serialize into the sandbox.
  - `summarize(form_map, schema_paths, *, max_chars=24000) -> str`. Per page, a header with the kind, size and text source, then one line per candidate in reading order: `c0_3 text conf=.91 [x0,y0,x1,y1] label(left)="Name:" sub="Street|City|State|Zip"`. Then unlabeled text lines that look like field prompts (ending in `:`, `#`, `?`, or followed by a rule) with no candidate nearby. Then widgets for fillable pages. Truncate per page proportionally when over `max_chars`, and say so.

- [ ] **Step 1:** Write tests:
  - `build_form_map(flat_form_pdf, detectors={"FFDNet-L": Fake})` yields `kind == "digital"`, candidates present, and the "Name" candidate's `label_relation == "left"`
  - `fillable_form_pdf` yields `kind == "fillable"`, 0 candidates (detection skipped) and 2 widgets
  - an encrypted fixture (`doc.save(..., encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x")`) raises `FormcastError` with `"encrypted PDF"`
  - garbage bytes raise `"cannot open PDF"`
  - `fingerprint` is stable across a re-save with `garbage=4`
  - `sublabels_under` finds `["Street","City"]` under a fixture address line
  - the `summarize` output contains `label(left)="Name:"` and stays under `max_chars`
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: form map assembly, label links, query helpers, summary`.

### Task 6: Schema paths

**Files:**
- Create: `src/formcast/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces:
  - `SchemaPath(path: str, type: Literal["string","integer","number","boolean","date","enum","array","object"], description: str = "", enum: list[str] | None = None, item_type: str | None = None, in_table: str | None = None)`
  - `flatten_schema(schema: dict) -> list[SchemaPath]`
  - `resolve(schema_paths, path) -> SchemaPath` (raises `KeyError`)
  - `validate_data(schema: dict, data: dict) -> list[str]` (jsonschema error messages, empty when valid)
  - **Path syntax:**
    - dots for object properties
    - `[]` for array items (`medications[].dose`)
    - `["..."]` for property names containing `.`, `[`, `"` or spaces (`applicant["first name"]`)
    - `format: date` → `date`; `enum` → `enum`
    - arrays of objects emit the array path (`type="array"`) and each column path with `in_table=<array path>`

- [ ] **Step 1:** Write tests:
  - nested object → `applicant.dob` with type `date`
  - array of objects → `medications` (array) plus `medications[].name` with `in_table == "medications"`
  - array of strings → `aliases`, `item_type == "string"`
  - an enum carries its values
  - a property `"first name"` → `applicant["first name"]`, and `resolve` finds it
  - a missing description gives `""`
  - `validate_data` reports the wrong type for `dob: 5`
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement (local `$ref`s within `$defs` are resolved; remote refs raise `FormcastError`).
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: JSON Schema flattening to field paths`.

### Task 7: Transforms

**Files:**
- Create: `src/formcast/transforms.py`
- Test: `tests/test_transforms.py`

**Interfaces:**
- Produces:
  - `Transform(op: str, args: dict = {})` (pydantic)
  - `register_transform(name: str, fn: Callable[..., Any], arg_schema: dict) -> None`
  - `validate_chain(chain: list[Transform]) -> list[str]` (errors for unknown ops and bad args, via each op's `arg_schema`)
  - `apply_chain(values: list[Any], chain: list[Transform]) -> Any`. The input is the list of source values (one per source path). `join` consumes the list; other ops apply to a single value (the list's only element) or map over it.
  - `list_transforms() -> list[dict]` (name, args schema and a one-line doc, for the system prompt)
  - **Built-ins (exact names):**
    - `split{sep, index}`, `join{sep}`, `slice{start, end}`, `char_at{index}`
    - `upper`, `lower`, `title`, `strip`
    - `date_format{format}` (strftime; input ISO date string or date) and `date_part{part: month|day|year|year2, pad: int=0}`
    - `number_format{decimals=2, thousands=",", prefix=""}`
    - `bool_to_check`, `equals{value}`, `contains{value}`
    - `default{value}`, `prefix{value}`, `suffix{value}`

- [ ] **Step 1:** Write tests:
  - `apply_chain(["2024-03-07"], [date_part(month, pad=2)]) == "03"`
  - `apply_chain(["Ada","Lovelace"], [join(" "), upper]) == "ADA LOVELACE"`
  - `apply_chain(["123-45-6789"], [split("-", 2)]) == "6789"`
  - `number_format(1234.5) == "1,234.50"`
  - `equals("married")` on `"married"` is `True`
  - `contains("email")` on `["email","sms"]` is `True`
  - `validate_chain([Transform(op="nope")])` returns `["unknown transform 'nope'"]`
  - a registered custom transform is usable
  - bad args (`date_part{part:"week"}`) produce an error
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: named chainable transforms`.

### Task 8: Template model

**Files:**
- Create: `src/formcast/template/__init__.py`, `src/formcast/template/model.py`
- Test: `tests/template/test_model.py`

**Interfaces:**
- Consumes: `Transform`, `Rect`
- Produces (pydantic):
  - `Source(paths: list[str], transforms: list[Transform] = [], join: str | None = None, when: Transform | None = None)`
  - `Style(font_size: float | Literal["auto"] = "auto", min_font_size: float = 6, align: Literal["left","center","right"] = "left", comb: int | None = None, multiline: bool = False)`
  - `Column(key: str, x: tuple[float, float], transforms: list[Transform] = [], wrap: int | None = None)`
  - `TableSegment(page: int, rect: Rect, row_top: float, row_height: float, max_rows: int, columns: list[Column])`
  - `Field(id: str, type: Literal["text","checkbox","radio_option","signature","choice_mark","table"], label: str = "", page: int | None = None, rect: Rect | None = None, source: Source | None = None, style: Style = Style(), existing_widget: str | None = None, segments: list[TableSegment] = [], confidence: float | None = None, origin: Literal["detector","agent","human","widget"] = "agent")`. Validators: non-table fields need `page` and `rect`; tables need at least one segment.
  - `FormInfo(fingerprint: str, page_count: int, page_sizes: list[tuple[float,float]], kinds: list[str])`
  - `HistoryEntry(op: dict, author: Literal["agent","human"], at: str, note: str = "")`
  - `Template(formcast_version: Literal["1"] = "1", template_id: str, schema_: dict = Field(alias="schema"), form: FormInfo, fields: list[Field] = [], history: list[HistoryEntry] = [])`, with `to_json() -> str` and `from_json(s) -> Template`
  - `new_template(schema: dict, form_map: FormMap) -> Template`. It is created empty. For fillable pages, one field per widget with `origin="widget"` and `existing_widget` set, unmapped.

- [ ] **Step 1:** Write tests:
  - JSON round-trip equality
  - a text field without `rect` raises `ValidationError`
  - a table without segments raises
  - `new_template` on the fillable fixture produces 2 fields with `existing_widget` names
  - the `schema` key appears in the JSON as `"schema"`
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: template model`.

### Task 9: Edit ops, validation, history, coverage

**Files:**
- Create: `src/formcast/template/ops.py`, `src/formcast/template/coverage.py`
- Test: `tests/template/test_ops.py`, `tests/template/test_coverage.py`

**Interfaces:**
- Consumes: Tasks 6–8, `FormMap`
- Produces:
  - **Op models**, a discriminated union on `op`, with exact names:
    - `add_field{id?, type, page, rect, label?, source?, style?}`
    - `remove_field{id}`
    - `move{id, rect? | dx, dy}`, `resize{id, rect}`
    - `set_label{id, label}`, `set_type{id, type}`
    - `set_source{id, source}`, `set_style{id, style}`
    - `add_table{id?, label?, source_path, segments}`
    - `set_table_columns{id, segment, columns}`, `set_table_rows{id, segment, row_top, row_height, max_rows}`, `add_table_segment{id, segment}`
    - `link_widget{id, widget}`
  - `Op = Annotated[Union[...], Field(discriminator="op")]`
  - `apply(template: Template, op: Op | dict, *, author: str, note: str = "", context: OpContext) -> tuple[Template, list[str]]`. It returns a new template and warnings, and raises `OpError` with a reason. `OpContext(schema_paths: list[SchemaPath], page_sizes: list[tuple[float,float]])`.
  - **Errors:**
    - unknown id
    - duplicate id
    - rect off page (more than 2pt outside) or degenerate (width or height < 2pt)
    - a source path not in the schema
    - a table field whose `source_path` isn't a `type="array"` path
    - `when` on a non-choice field
    - an invalid transform chain
    - `set_type` to `table`
  - **Warnings:**
    - overlap IoU > 0.5 with another field on the same page
    - a checkbox source path whose type isn't boolean/enum/array without a `when`/`bool_to_check`
  - Generated ids are `f{n}`, with `n` = 1 + the max existing numeric suffix.
  - `replay(base: Template, history: list[HistoryEntry], context) -> Template`
  - `diff(a: Template, b: Template) -> list[dict]` (added, removed and changed field ids with the changed keys)
  - `coverage(template, form_map, schema_paths) -> Coverage` with fields:
    - `unmapped_paths: list[str]` (leaf paths and table arrays not referenced by any field source)
    - `unused_candidates: list[str]` (candidate ids with IoU < 0.3 against every field)
    - `unmapped_fields: list[str]` (fields without a source)
    - `low_confidence: list[str]`

- [ ] **Step 1:** Write tests:
  - `add_field` then `move(dx=5)` shifts the rect by 5 and appends 2 history entries with the given author
  - `move` off page raises `OpError` with `"off page"` in the reason
  - `set_source` with an unknown path raises `"unknown schema path"`
  - `add_table` on a string path raises
  - overlap produces a warning, not an error
  - `replay` of the history on the base equals the final template
  - `diff` reports a moved field's `rect`
  - coverage lists an unmapped path, then empties after `set_source`
  - `unused_candidates` drops a candidate once a field covers it
  - applying an op as a plain dict works
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement (templates are immutable; `apply` returns `template.model_copy(deep=True)` with the change).
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: edit ops with validation, history, replay, diff, coverage`.

### Task 10: Sandbox worker

**Files:**
- Create: `src/formcast/sandbox/__init__.py`, `src/formcast/sandbox/protocol.py`, `src/formcast/sandbox/worker.py`, `src/formcast/sandbox/client.py`
- Test: `tests/sandbox/test_sandbox.py`

**Interfaces:**
- Consumes: `FormMapQuery` (worker side), template JSON, schema paths
- Produces:
  - `Sandbox(timeout_s: float = 20, max_stdout: int = 8000, memory_mb: int = 1024)`, a context manager
  - `.start(form_map: FormMap, schema_paths: list[SchemaPath], template: Template)`
  - `.update_template(template: Template)`
  - `.exec(code: str) -> ExecResult`
  - `.close()`
  - `ExecResult(stdout: str, truncated: bool, ops: list[dict], error: str | None, timed_out: bool)`
  - **Worker namespace,** which persists across `exec` calls:
    - `fm`: a `FormMapQuery`
    - `schema`: a list of SchemaPath dicts
    - `tpl`: a read-only dict snapshot of the template, refreshed on `update_template`
    - one function per op name from Task 9 (`add_field(...)`, `move(...)`, …): each records `{"op": name, **kwargs}` and returns the field id (pre-assigned in the worker as `f{n}` from the snapshot plus a counter, so later calls in the same exec can refer to it)
    - `transforms()`, which lists the transforms
    - safe builtins
    - pre-imported `re`, `math`, `statistics`, `itertools`, `collections`, `json`
  - **Restrictions:** a custom `__import__` allows only `re`, `math`, `statistics`, `itertools`, `collections`, `json`, `functools`, `string`, `dataclasses`, `typing`; `open`, `exec`, `eval`, `compile`, `input`, `__import__` overrides and `globals` are removed; `resource.setrlimit(RLIMIT_AS)` where supported; a timeout kills and restarts the worker, after which the namespace is lost and the error says so.
  - Transport is JSON lines over stdin/stdout of `python -m formcast.sandbox.worker`.

- [ ] **Step 1:** Write tests:
  - `x = 1` then `print(x + 1)` across two execs prints `2`
  - `add_field(page=0, rect=[10,10,100,30], type="text")` returns `"f1"` and `ops == [{"op":"add_field", "id":"f1", ...}]`
  - `print(fm.find_text("Name"))` returns the fixture line
  - `import os` gives an `error` containing `"import of 'os' is not allowed"`
  - `while True: pass` gives `timed_out=True`, and a following `exec("print(1)")` works and reports that the namespace was reset
  - `print("x"*100000)` gives `truncated=True` and `len(stdout) <= 8000 + 100`
  - `open("/etc/passwd")` gives a `NameError`
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: persistent sandbox worker with op recording`.

### Task 11: LLM providers

**Files:**
- Create: `src/formcast/llm/__init__.py`, `config.py`, `types.py`, `base.py`, `chat_completions.py`, `responses.py`
- Test: `tests/llm/test_providers.py`

**Interfaces:**
- Produces:
  - `LLMConfig(base_url: str, api_key: str, model: str, api_format: Literal["chat_completions","responses"] = "chat_completions", temperature: float | None = None, max_output_tokens: int = 8000, timeout_s: float = 120, max_retries: int = 3, extra_headers: dict[str,str] = {}, reasoning_effort: str | None = None)`, plus `LLMConfig.from_env(prefix: str)` reading `{prefix}_BASE_URL`, `_API_KEY`, `_MODEL` and `_API_FORMAT`
  - `Message(role: Literal["system","user","assistant","tool"], content: str = "", tool_calls: list[ToolCall] = [], tool_call_id: str | None = None)`
  - `ToolSpec(name: str, description: str, parameters: dict)`
  - `ToolCall(id: str, name: str, arguments: dict | None, raw_arguments: str)`: `arguments` is `None` when the JSON is malformed
  - `Usage(input_tokens: int, output_tokens: int)`
  - `LLMResponse(text: str, tool_calls: list[ToolCall], usage: Usage, raw: dict)`
  - `Provider` protocol with `complete(messages: list[Message], tools: list[ToolSpec]) -> LLMResponse`
  - `make_provider(cfg, *, transport: httpx.BaseTransport | None = None) -> Provider`
  - **Chat Completions:** POST `{base_url}/chat/completions` with `messages`, `tools=[{"type":"function","function":{...}}]` and `tool_choice:"auto"`.
  - **Responses:** POST `{base_url}/responses`, with `input` items built from messages (system → `instructions`, assistant tool calls → `function_call` items, tool results → `function_call_output` items) and `tools=[{"type":"function","name":...,"parameters":...}]`. It parses `output` items of type `message` (`output_text`) and `function_call`.
  - Both retry 429/5xx with exponential backoff up to `max_retries` and raise `FormcastError` with the status and a body excerpt otherwise.

- [ ] **Step 1:** Write tests with `httpx.MockTransport`:
  - Chat Completions request body shape: tools wrapped in `function`, tool result messages carry `tool_call_id`
  - parse a tool-call reply
  - Responses request body shape: `instructions` set from the system message, `function_call_output` items for tool results
  - parse a `function_call` output item
  - malformed arguments `"{bad"` give `arguments is None` with `raw_arguments` preserved
  - a 429 followed by a 200 retries once
  - a 400 raises `FormcastError` containing `"400"`
  - `@pytest.mark.llm`: one real round-trip per configured eval model with a trivial `echo` tool
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement. Verify the Anthropic OpenAI-compatible endpoint (base URL `https://api.anthropic.com/v1/`, Chat Completions) and the OpenAI Responses endpoint during the `llm` test. Note any field quirks in comments, not in branching logic keyed on provider names where avoidable.
- [ ] **Step 4:** Run unit tests, then `-m llm`. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: Chat Completions and Responses providers`.

### Task 12: Agent session, prompts, API

**Files:**
- Create: `src/formcast/agent/__init__.py`, `prompts.py`, `report.py`, `session.py`, `api.py`; `src/formcast/render/preview.py`
- Modify: `src/formcast/__init__.py` (exports `LLMConfig`, `build_form_map`, `build_template`, `revise`, `preview`, `apply`, `fingerprint`, `Template`)
- Test: `tests/agent/test_session.py`, `tests/agent/test_prompts.py`, `tests/render/test_preview.py`

**Interfaces:**
- Consumes: everything above
- Produces:
  - `SessionBudget(max_steps: int = 40, max_input_tokens: int = 1_500_000, max_output_tokens: int = 200_000, wall_clock_s: float = 900)`
  - `StepRecord(n: int, code: str, exec: ExecResult, applied: list[dict], rejected: list[dict], warnings: list[str], usage: Usage)`
  - `SessionResult(template: Template, finished: bool, summary: str, steps: list[StepRecord], usage: Usage, stop_reason: Literal["finish","budget","error"])`
  - `Session(form_map, schema, llm: LLMConfig | Provider, *, template: Template | None = None, instruction: str | None = None, region: tuple[int, Rect] | None = None, budget=SessionBudget(), sandbox_factory=Sandbox)` with `.run() -> SessionResult`
  - **Tools given to the model:**
    - `run_python{code: str}`
    - `finish{summary: str, unplaceable: list[{path, reason}]}`
  - `build_template(schema: dict, pdf, llm, *, start_from: Template | None = None, form_map: FormMap | None = None, budget=SessionBudget()) -> SessionResult`
  - `revise(template, pdf, instruction: str, llm, *, region=None, budget=SessionBudget(max_steps=15)) -> SessionResult`
  - `preview(template, pdf, *, dpi=110, show_candidates: FormMap | None = None) -> list[bytes]`: PNG per page, with field boxes coloured by type, id + label text, and optional faint candidate boxes. Used for human review and debugging, never sent to the model.
  - **`prompts.SYSTEM_PROMPT` must contain these sections, written generically** (no form names or example labels from the corpus):
    1. the role: turn a blank form into a field template for a data schema
    2. coordinates and the page-map vocabulary
    3. the sandbox API: `fm` methods, `schema`, `tpl`, op functions, `transforms()`
    4. the working method: start from detector candidates; confirm, reject or reinterpret them; add missed fields using text cues; split lines that have sub-labels; mark choices; build tables from repeated rows or cells; map every schema path you can; never map a path to a field you can't justify from nearby text
    5. the step report and how to use it
    6. "call `finish` when coverage can't improve; list unplaceable paths with reasons"
    7. what not to do: no guessing a position without text or geometry evidence; don't print whole page maps; keep steps small
  - `prompts.first_user_message(summary_text, schema_paths, instruction, region) -> str`
  - **The step report** (`report.step_report(record, coverage) -> str`) lists:
    - stdout (truncated)
    - applied ops (id + op)
    - rejected ops with reasons
    - warnings
    - counts of fields / mapped fields / schema paths
    - unmapped paths (up to 40)
    - unused candidates (up to 40, with their labels)
  - **Context management:** keep the system prompt, the first message and the last 6 steps verbatim. Older steps are compressed to one line each (`step n: k ops applied, j rejected`).
  - **Malformed replies:**
    - no tool call → corrective user message `"Reply with a run_python or finish tool call."`
    - unknown tool or `arguments is None` → a tool result with the error

    Each counts as a step. `run()` never raises for model misbehaviour. Provider errors after retries end with `stop_reason="error"` and the template so far.

- [ ] **Step 1:** Write tests with a `ScriptedProvider` that returns a queued list of `LLMResponse`s and a fake detector:
  - a script that adds a field, sets its source, then calls finish yields `finished=True`, 1 field, `history` authors all `"agent"`, `stop_reason == "finish"`
  - a reply with plain text and no tool call gets a corrective message and the run continues
  - malformed tool args get an error tool result
  - an op rejected in the sandbox appears in the next step report with its reason
  - the budget (`max_steps=2`) gives `stop_reason == "budget"` with the partial template
  - a `revise` with `region` puts the region in the first message
  - `SYSTEM_PROMPT` contains none of the corpus labels (a test loads every `forms/gt/*.json` label longer than 4 characters and asserts none appear verbatim in the prompt, ignoring a small allowlist of generic words: Name, Date, Signature, Address, Phone, City, State, Zip, Email)
  - `preview` returns one PNG per page
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: agent session, build_template, revise, preview`.

### Task 13: Shared scoring and baseline on formcast

**Files:**
- Create: `eval/score.py`
- Modify: `eval/baseline_detection.py` (use `formcast.pagemap.detect` + `build_form_map` and `eval/score.py`, and drop its private copies)
- Test: `tests/eval/test_score.py`

**Interfaces:**
- Produces:
  - `GTField(page: int, rect: Rect, type: Literal["text","choice","signature"], label: str = "", path: str | None = None, transforms: list[str] = [])`
  - `load_gt(form: dict, pdf_path) -> list[GTField] | None`: fillable forms use widgets, enriched with `path` from `forms/gt/<id>.json` when present (matched by widget name); flat and scanned forms use `forms/gt/<id>.json`
  - `score_detection(gt: list[GTField], boxes: list[tuple[str, int, Rect]], iou_thr) -> dict` (recall, precision, type accuracy, misses by type)
  - `score_mapping(gt, template: Template, iou_thr=0.3) -> dict`: for GT fields with `path`, the share whose matched template field has `path` in its `source.paths` (`path_acc`) and whose transform op names include the GT `transforms` (`transform_acc`); table GT is matched per cell against table rows and columns
  - **Type mapping** from template to detection types: `text|table cell → text`, `checkbox|radio_option|choice_mark → choice`, `signature → signature`

- [ ] **Step 1:** Write tests: synthetic GT and predictions give exact recall/precision values (for example 3 GT, 2 matched, 1 extra → recall 0.667, precision 0.667), and mapping accuracy counts a correct path and a wrong transform separately.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement and refactor the baseline onto it. Re-run `python eval/baseline_detection.py --split dev` and confirm the dev numbers match the 2026-10-20 baseline within ±0.01 (same detectors, same seeds).
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: shared eval scoring; baseline on formcast`.

### Task 14: Ground truth and schemas for the corpus

**Files:**
- Create: `eval/gt_draft.py`, `forms/gt/<id>.json` for every form, `forms/schemas/<id>.json` for every form
- Test: `tests/eval/test_gt_files.py`

**Interfaces:**
- Produces:
  - **GT file format:** `{"form": id, "fields": [{"page", "rect", "type", "label", "path"?, "transforms"?}], "notes": ""}`, with rects in PDF points
  - **Schema files:** realistic JSON Schemas a business would plausibly own for that form, *not* derived from the form's widget names. They are written from a reading of the form, and some of them deliberately differ in shape from the form:
    - split versus combined names
    - ISO dates where the form wants MM/DD/YYYY
    - booleans where the form has Yes/No boxes
    - enums for choice groups
    - arrays of objects for tables
  - `python eval/gt_draft.py <id>` writes `forms/gt/<id>.draft.json` from the union of both detectors plus nearest-label text, and an overlay PNG for correction

- [ ] **Step 1:** Write `tests/eval/test_gt_files.py`:
  - every manifest form has a schema that passes `jsonschema` meta-validation and a GT file whose rects are on-page
  - every GT `path` resolves in that form's schema
  - flat and scanned GT have at least 1 field per scored page
- [ ] **Step 2:** For flat and scanned forms, run `gt_draft.py`. Correct each draft into `forms/gt/<id>.json`: add missed blanks (signature and date lines, underline-checkboxes, option words that need marks, sub-label splits), delete false positives, fix types. Do this by reviewing overlays page by page. **Labelers record *what the form asks for*, not what the detector found.**
- [ ] **Step 3:** Write the schemas, then add `path` and `transforms` to the GT fields that a schema covers. Fillable forms: keyed by widget name in `{"widget_paths": {name: {"path", "transforms"}}}` inside the GT file.
- [ ] **Step 4:** Run `pytest tests/eval/test_gt_files.py -q`. Expected: PASS.
- [ ] **Step 5:** Commit: `data: ground truth and schemas for test corpus`.

### Task 15: Agent eval, dev iteration, held-out run

**Files:**
- Create: `eval/agent_eval.py`, `eval/results/agent_<model>_<split>_<date>.{json,md}`
- Modify: the design doc §14 (append results)

**Interfaces:**
- Consumes: `build_template`, `score_detection`, `score_mapping`, `load_gt`
- Produces:
  - `python eval/agent_eval.py --split dev --model-env OPENAI_EVAL|ANTHROPIC_EVAL [--forms id,...] [--variant as_published|fake_scan]`
  - It writes, per form:
    - detection scores of the final template's fields against GT, next to the baseline detector scores for the same form and variant
    - mapping scores
    - steps, tokens and wall time
    - a saved template JSON
    - preview PNGs under `eval/results/overlays/`
  - Model envs: `OPENAI_EVAL_*` (api_format `responses`, model = OpenAI Luna 6) and `ANTHROPIC_EVAL_*` (api_format `chat_completions`, base URL `https://api.anthropic.com/v1/`, model `claude-haiku-5-5`). Confirm the exact Luna 6 model id from the provider's model list in Task 11's `llm` test.

- [ ] **Step 1:** Implement `agent_eval.py`. Smoke-run on `coleman_pir` and `wadoc_auth` with one model. Expected: completes, writes results, and detection recall is at least the baseline.
- [ ] **Step 2: Dev iteration.** Run all dev forms on both models and read the failure cases (overlay + step log). Improve **only** generic things:
  - page-map signals (Task 5 helpers)
  - query helpers
  - the summary format
  - op validation messages
  - system prompt method sections

  Each change gets a one-line rationale stating the *general* failure it addresses, in the commit message. Re-run the dev set after each change and keep the per-run results files.
- [ ] **Step 3: Generalization check before held-out.** A reviewer subagent reads `prompts.py`, `summarize.py` and `query.py` against `forms/gt/*.json` and flags any rule that only makes sense for one corpus form. Remove whatever it flags.
- [ ] **Step 4: Held-out run (once).** `--split heldout` on both models, plus `fake_scan` variants for non-scanned held-out forms. Record the results. No changes after this run in this plan.
- [ ] **Step 5:** Append the results to the design doc §14 as baseline vs agent per split and model, against the v1 finish line (spec §6). Commit: `eval: agent layer results on dev and held-out`.

---

## Self-review notes

- **Spec coverage:** this plan implements spec §7.1–7.7, the preview part of §7.8, fingerprint from §7.10, §8 (partly) and §11. §7.8 fill/smart PDF, §7.9 the fill report, §7.10 similarity and §10 the demo are deferred to Plans 2–3, as the header says.
- **Spec deviation recorded:** Python ≥ 3.11.
- **Interface names checked across tasks:**
  - `FormMap`, `Candidate`, `FormMapQuery`
  - `SchemaPath`, `Transform`, `Template`, `Field`, `apply`, `OpContext`, `coverage`
  - `Sandbox`, `ExecResult`
  - `LLMConfig`, `Provider`, `Session`, `build_template`
  - `GTField`, `score_detection`, `score_mapping`
