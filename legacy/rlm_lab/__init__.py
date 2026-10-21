from pathlib import Path
from pkgutil import extend_path


__path__ = extend_path(__path__, __name__)
SRC_PACKAGE = Path(__file__).resolve().parent.parent / "src" / "rlm_lab"
if SRC_PACKAGE.exists():
    __path__.append(str(SRC_PACKAGE))
