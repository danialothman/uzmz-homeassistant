"""Load the UZMZ client modules without Home Assistant.

custom_components/uzmz/__init__.py imports Home Assistant, but api.py and
const.py don't need it, so register a bare "uzmz" package pointing at the
integration folder and import those modules from it.
"""

import sys
import types
from pathlib import Path

_pkg = types.ModuleType("uzmz")
_pkg.__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components" / "uzmz")]
sys.modules.setdefault("uzmz", _pkg)
