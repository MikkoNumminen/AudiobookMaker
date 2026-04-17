"""Single import point for every bundled TTS engine.

Each engine module registers its ``TTSEngine`` subclass via the
``@register_engine`` decorator on import, so simply importing this
module populates the registry. The GUI, launcher, and tests can then
enumerate engines via ``src.tts_base.list_engines`` without worrying
which engine modules have been imported yet.

Before this module existed, ``src/gui.py``, ``src/gui_unified.py``, and
``src/launcher.py`` each maintained their own copy of the engine
imports, which made "add a new engine" an edit in four places. Now the
list lives here and nowhere else.

Optional / developer-only engines (currently VoxCPM2, which isn't
bundled in frozen installs) are guarded so their registration failure
does not crash the rest of the app on older Python versions or
missing-optional-dep machines.
"""

from __future__ import annotations

import sys

# Always available — these three are the officially supported engines.
from src import tts_edge  # noqa: F401  (registers EdgeTTSEngine)
from src import tts_piper  # noqa: F401  (registers PiperTTSEngine)
from src import tts_chatterbox_bridge  # noqa: F401  (registers ChatterboxFiEngine)

# VoxCPM2 is a developer-only engine — it depends on torch and a GPU
# that we don't want to pull into frozen installs. Skip it when running
# inside a PyInstaller bundle; in dev, try to import it but keep going
# if the optional dependency is absent.
if not getattr(sys, "frozen", False):
    try:
        from src import tts_voxcpm  # noqa: F401  (registers VoxCPM2Engine)
    except Exception:
        pass
