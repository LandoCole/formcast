"""Build FormCast Lite into one executable with PyInstaller.

Run on the platform you are building for (PyInstaller does not cross-compile): on Windows this makes
``dist/FormCastLite.exe``. From the ``lite`` folder:

    python -m pip install -r requirements.txt -r requirements-build.txt
    python build.py              # one file: dist/FormCastLite.exe
    python build.py --onedir     # a folder instead (starts faster, no unpacking on each launch)

The FFDNet-S weights (``models/FFDNet-S.onnx``, ~38 MB) are fetched once from Hugging Face if they are not
there yet, then bundled, so the exe works offline.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "FormCastLite"

# Big packages that sneak in through optional imports; the app needs none of them.
EXCLUDES = [
    "torch", "torchvision", "ultralytics", "rfdetr", "commonforms", "formcast", "tensorflow", "cv2",
    "matplotlib", "pandas", "scipy", "sklearn", "IPython", "jupyter", "notebook", "pytest",
    "tkinter", "sympy", "pydantic", "huggingface_hub", "transformers",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--onedir", action="store_true", help="build a folder instead of a single exe")
    parser.add_argument("--noconsole", action="store_true", help="hide the console window (then quit from Task Manager)")
    args = parser.parse_args()

    os.chdir(HERE)
    sys.path.insert(0, str(HERE))
    from formcast_lite.detector import MODEL_NAME, model_path

    bundled = HERE / "models" / MODEL_NAME
    if not bundled.is_file():
        found = model_path()  # the HF cache, or a download into models/
        if found != bundled:
            bundled.parent.mkdir(exist_ok=True)
            shutil.copyfile(found, bundled)
    print(f"model: {bundled} ({bundled.stat().st_size / 1e6:.1f} MB)")

    import PyInstaller.__main__

    sep = os.pathsep  # ';' on Windows, ':' elsewhere
    cmd = [
        str(HERE / "run.py"),
        "--name", NAME,
        "--noconfirm",
        "--clean",
        "--onedir" if args.onedir else "--onefile",
        "--add-data", f"{HERE / 'formcast_lite' / 'static'}{sep}formcast_lite/static",
        "--add-data", f"{bundled}{sep}models",
        "--collect-binaries", "onnxruntime",
        "--hidden-import", "onnxruntime",
        "--paths", str(HERE),
    ]
    if args.noconsole:
        cmd.append("--noconsole")
    icon = HERE / "icon.ico"
    if icon.is_file():
        cmd += ["--icon", str(icon)]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    PyInstaller.__main__.run(cmd)

    out = HERE / "dist" / (NAME if args.onedir else NAME + (".exe" if os.name == "nt" else ""))
    print(f"\nbuilt: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
