# Showcase guided flow and fill preview (2026-10-22)

The portfolio demo (demo/web/showcase/) becomes a guided run of the three steps: a blank form,
Detect under a visible scan, Auto map, Fill shown on the page, Download. The workbench gets the
same fill preview. This note records the owner's decisions and the calls made while building it.

## Owner's decisions (as built)

1. **Detect runs the real model.** A sample opens blank: form.pdf (fields stripped) and a template
   with no boxes. Detect runs FFDNet in the browser while a scan plays over the page sheet
   (showcase/scan.js): a 1 px line with a soft light above it sweeps down, looping while the model
   downloads and runs; the status line keeps the download progress ("Downloading the model: 19.0 of
   38.0 MB"). When the boxes arrive the line makes one last 1.4 s pass and each box fades in as the
   line reaches it (delay from its top edge). Under `prefers-reduced-motion` there is no sweep, no
   stagger; the status says what is happening.
2. **Where live detection is off** (`detectLiveOff`: touch phones, under 4 GB memory), Detect plays
   the same scan over the sample's saved detections.json. The caption says "Saved results from an
   earlier run of FFDNet-S" and the note under Detect says "Saved results from an earlier run of the
   detector: live detection is off on this device." Try anyway runs the detector live. Before the
   click the status says Detect will play back saved results. A visitor's flat PDF has no saved
   results: it shows the old "Live detection is off" note with Try anyway.
3. **Auto map** (Link step, first group, the step's primary button). Pure logic in
   showcase/automap.js: per page, greedy best-IoU pairs, one to one, IoU >= 0.3 (`MATCH_IOU`),
   between the boxes on screen and the agent template's fields that have a source. A paired box
   takes the agent field's source (paths, transforms, join, when) and its whole style (font size,
   alignment, comb, multiline), and keeps its own id, label and rect. Each change goes through
   core/ops.js (`set_type` when the types differ, `set_source`, `set_style`), so a mapping the
   template's checks refuse is skipped, not forced. The result goes through
   `session.replaceTemplate` as one undo step; the boxes turn linked one after another
   (scan.js `land`). The readout says "Mapped N of M boxes. The rest are yours to link by hand."
   The note under the button names the model from agent-scores.json (`model_label`). On a visitor's
   PDF, or when the template's schema is not the sample's (a pasted schema), the button is disabled
   and the readout gives the one-line reason.
4. **Fill preview.** Fill fills from the template and the blank form, opens the result with the
   engine (pdf.js, AnnotationMode.ENABLE, so the fields' appearances show) and the page view draws
   those pages with the boxes hidden (`.fc-pageview.is-filled` in parts.css). Download saves the
   same bytes. Clear goes back to the boxes. Picking another record while the preview is up fills
   again from the template. Any template event, a new document, or leaving the Fill step ends the
   preview. Each sample has two or three made-up records (demo/tools/sample_records/, bundle file
   records.json). A visitor's PDF or a pasted schema gets one placeholder record and no picker.
5. **Removed from the showcase:** fillable empty PDF, Download/Load template, the fill report, the
   written/left counts, the History panel (replaced by plain Undo and Redo buttons, so Auto map
   stays undoable), the "Load the AI agent's template" group and its scores line. Code only those
   used is gone: `scoresLine`, `matchCount` (now `pairByOverlap`), store.js `downloadTemplate`,
   `readTemplateFile`, `checkTemplate`, `remember`, `recall` and their tests. The write-up (lead,
   How it works, the diagram's "in this page" box and caption, What the AI agent adds, a new Limits
   paragraph) describes the new flow.
6. **Workbench:** the fill drawer's Fill shows the filled PDF on the page (pdf.js in the browser,
   engines/browser/document.js imported on the first fill; the server serves demo/web/vendor/),
   Download saves those bytes, Clear goes back. Changing the record or the record source fills
   again (with the same fingerprint option, so after Fill anyway it keeps filling anyway). Any
   template change, a new document, and closing the drawer end the preview. Rebind (one undo step)
   ends it and fills again. The report, the fillable PDF and the rest stay.

## Calls made here

- **Saved edits are no longer restored.** The showcase used to remember each sample's template in
  localStorage and reopen it ("Your earlier edits", Start over). That contradicts "a sample opens
  blank", so the guided page starts every visit at the blank form. The store.js helpers went with it.
- **published.pdf and record.json left the bundles.** Nothing on the page loads the published form
  any more; dropping it saves 130-500 KB per sample on the portfolio. records.json replaces
  record.json. The tests that need a fillable PDF make it from forms/dev/fw9.pdf; the bundle test
  now checks the agent template's page geometry against form.pdf (Auto map depends on it). The
  bundles were regenerated with build_showcase.py; every other file came out byte-identical.
- **The "found N of M" readout and the compare checkbox stay**, shown only once there are boxes.
- **The model choice (S or L) stays** on desktop; it is not on the removal list.
- **A visitor's flat PDF is still detected as it opens** (no extra Detect click), under the same scan.
- **A failed live run on a sample offers "Show the saved results".**
- **Auto map overwrites a box's hand link** when the box pairs with an agent field; Undo restores it.
- **Agent tables are not carried** (they have no single rect to pair); the Limits copy says so.
- **Primary buttons** (Detect, Auto map, Fill) are filled ink (`.btn.go`), the site's tokens.
- **Disclaimer wording**: "in the formcast eval" instead of "our eval" (the page has no first
  person, as the site's copy rules require).

## After review (same day)

- **SSN/EIN split correction (owner's decision).** The archived W-9 agent template splits the SSN
  and EIN boxes with `slice` written for dashless digits ([0:3], [3:5], [5:9]; [0:2], [2:9]), while
  the schema's values are dashed, so a dashed EIN filled as "12" | "-345678". build_showcase.py's
  `correct_digit_splits` runs when it writes agent-template.json: where a field's only path has a
  dashed-digits `pattern` and a slice covers exactly one digit group, the slice becomes
  `split("-", index)`. Only the five W-9 transforms change (the W-4 and I-9 templates have no
  position split and are written byte for byte). The page does not mention the correction.
- **Auto map history entries carry the author "agent"** (the template model's other author value).
  ops.js applies browser edits only as "human" (an agent's own guards need the library), so the
  ops are applied as "human" and the entries Auto map added are relabelled, with a note saying
  they are the agent's mapping paired by position.
- `scan.land` runs after a successful replace (the boxes are redrawn before the browser paints).
- The Auto map readout clears on undo, redo and any template replace Auto map didn't make.
- A fill still running when the visitor leaves the Fill step is never shown; in the workbench,
  Clear or closing the drawer drops a running fill, and a filled PDF pdf.js can't open (over 50
  pages) still downloads, with a message.
- Button hover styles apply only under `@media (hover: hover)`.
- `pairByOverlap` takes its threshold as a required argument.
- How it works says the detector runs in the browser where the device allows.
