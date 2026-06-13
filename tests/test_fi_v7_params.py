"""docs/finnish_grandmom.md must state the actual production v7 sampler values.

That doc's settings table documents the *production* Grandmom config (the
values scripts/generate_chatterbox_audiobook.py ships). They drifted once:
production was deliberately lowered to temperature 0.5 ("fix(chatterbox): lower
FI sampler temperature to 0.5") but the doc kept saying 0.8 — undetected
because nothing tied the prose to the code. This locks the numeric rows to the
production constants so a future change to one without the other fails.

Note: dev_chatterbox_fi.py intentionally uses a DIFFERENT temperature (the
model-card golden 0.8, locked by test_fi_temperature_matches_model_card) — it
is a script/UX iteration tool, not the production path, so it is deliberately
not compared here.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PROD = REPO_ROOT / "scripts" / "generate_chatterbox_audiobook.py"
DOC = REPO_ROOT / "docs" / "finnish_grandmom.md"

# doc settings-table row label -> production constant name
_ROW_TO_CONST = {
    "temperature": "FI_TEMPERATURE",
    "exaggeration": "FI_EXAGGERATION",
    "cfg_weight": "FI_CFG_WEIGHT",
}


def _prod_constants() -> dict[str, float]:
    tree = ast.parse(PROD.read_text(encoding="utf-8"))
    out: dict[str, float] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.startswith("FI_") and isinstance(
                    node.value.value, (int, float)
                ):
                    out[t.id] = float(node.value.value)
    return out


def _doc_value(label: str) -> float:
    """Pull the bold value from a `| `label` | **X** | ...` settings-table row."""
    text = DOC.read_text(encoding="utf-8")
    m = re.search(rf"\|\s*`{re.escape(label)}`\s*\|\s*\*\*([0-9.]+)\*\*", text)
    assert m, f"no settings-table row for `{label}` found in {DOC.name}"
    return float(m.group(1))


@pytest.mark.parametrize("label,const", sorted(_ROW_TO_CONST.items()))
def test_doc_matches_production_constant(label: str, const: str) -> None:
    prod = _prod_constants()
    assert const in prod, f"{PROD.name} no longer defines {const}"
    doc_val = _doc_value(label)
    assert doc_val == prod[const], (
        f"docs/finnish_grandmom.md says {label} = {doc_val} but the production "
        f"generator ships {const} = {prod[const]}. Update the doc row (it "
        f"documents production values) or the constant so they agree."
    )
