"""Live evaluation of the virtual attendant's customer turn (AC23 and AC24).

Every case goes through the helper's own path: the runner cycle builds the pack, starts the
tool-less ``claude`` child that ``profiles.build_child`` describes, parses the reply and runs the
egress check. Only the Turbo Notify API is replaced by an in-memory fake.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The helper is not installed as a package; the plugin runs it from its own folder, so do we.
_HELPER = Path(__file__).resolve().parents[2] / "skills" / "virtual-attendant" / "scripts"
if str(_HELPER) not in sys.path:
    sys.path.insert(0, str(_HELPER))
