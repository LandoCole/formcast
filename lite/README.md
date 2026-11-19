# formcast lite

Static PDFs in, fillable PDFs out. One job, no AI, nothing stored.

- Add one PDF or many (button or drag and drop); they convert one after another.
- **Skip prose pages**: pages that read as running text are not searched, so only likely form pages run
  through the detector. FormCast's prose rule, plus one addition: a page with two or more check boxes is
  always treated as a form.
- Fields come from CommonForms **FFDNet-S** (its official ONNX export, run on CPU with onnxruntime): a text
  field per text or signature blank, a check box per choice blank, named `text_<page>_<n>`,
  `check_<page>_<n>` and `signature_<page>_<n>` in reading order. Pages that already have form fields are
  left as they are.
- Document queue and page queue on screen; download one PDF or all of them as a zip; **Reset** stops the
  current conversion and forgets everything.
- Everything lives in memory. Quitting the app (closing its window) wipes it all.

It is a small local web app: a standard-library HTTP server on `127.0.0.1` that opens the page in your
default browser. It does not import the `formcast` package, torch or ultralytics.

## Run from source

Python 3.10 or later.

```sh
cd lite
python -m pip install -r requirements.txt
python -m formcast_lite            # opens http://127.0.0.1:8790/
```

`--port N` picks another port (a free one is used if it is taken) and `--no-browser` skips opening the page.

The FFDNet-S weights (~38 MB) are found in `models/FFDNet-S.onnx`, in the Hugging Face cache if commonforms
or FormCast downloaded them before, or downloaded once into `models/`. `FORMCAST_LITE_MODEL=/path/FFDNet-S.onnx`
points at a copy elsewhere.

## Build the Windows exe

On a Windows machine (PyInstaller does not cross-compile), from the `lite` folder:

```bat
build.bat
```

That makes a virtual environment (`.venv-build`), installs the requirements and PyInstaller, fetches the
model if needed and writes **`dist\FormCastLite.exe`**: one file with the model bundled, so it runs offline.
Double-click it; a console window shows the address, and closing that window quits the app.

`build.bat --onedir` builds a folder instead (starts faster, since a one-file exe unpacks itself on every
launch); `build.bat --noconsole` hides the console (then quit from Task Manager). Without the batch file:

```sh
python -m pip install -r requirements.txt -r requirements-build.txt
python build.py
```

## Tests

```sh
cd lite
python -m pytest tests
```

The test with the real model runs when the weights are on the machine and `forms/dev/fw4.pdf` is in the repo.
