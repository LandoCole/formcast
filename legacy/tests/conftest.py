import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("DSPY_CACHEDIR", str(ROOT / ".dspy_cache"))
(ROOT / ".dspy_cache").mkdir(parents=True, exist_ok=True)
