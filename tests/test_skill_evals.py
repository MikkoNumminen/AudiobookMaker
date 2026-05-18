"""Schema checks for `.claude/skills/*/evals/evals.json`.

The skills under `.claude/skills/` document themselves with an
`evals/evals.json` file. The evals describe expected behaviour for a
given prompt — they are the closest thing the skills have to a unit
test, but until now nothing asserted the files were even well-formed.

This module walks every skill directory, finds each `evals/evals.json`
that exists, and runs a small strict schema check against it:

- valid JSON
- top-level `skill_name` (string) equal to the parent skill dir name
- top-level `evals` array, non-empty
- every eval has `id` (int, unique), `name` (kebab-case string, unique),
  `prompt` (non-empty string), `expected_output` (non-empty string),
  `files` (array — empty is fine)

Skills that ship without an `evals/evals.json` are silently accepted —
not every skill has evals, and that is intentional.

Schema-only. We do NOT execute the prompts or grade outputs; that needs
a live model. The point here is to catch copy-paste mistakes
(`skill_name` not matching the folder), duplicate ids, or eval entries
that lost a required field during hand-editing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_ROOT = _REPO_ROOT / ".claude" / "skills"

# kebab-case: lowercase letters / digits, hyphen-separated, no leading
# or trailing hyphen, no double hyphens. The first character must be a
# letter — pure-digit slugs like "0" or "123" are almost always a
# copy-paste of the eval's `id` field and rarely what was intended.
_KEBAB_CASE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


def _discover_evals_files() -> list[Path]:
    """Return every `<skill>/evals/evals.json` that exists on disk.

    Walks `.claude/skills/*/evals/evals.json`. A skill without an
    `evals.json` is fine — it simply doesn't show up in the returned
    list, and pytest sees no parametrised case for it.
    """
    if not _SKILLS_ROOT.is_dir():
        return []
    found: list[Path] = []
    for skill_dir in sorted(_SKILLS_ROOT.iterdir()):
        if not skill_dir.is_dir():
            continue
        candidate = skill_dir / "evals" / "evals.json"
        if candidate.is_file():
            found.append(candidate)
    return found


_EVALS_FILES = _discover_evals_files()
_IDS = [p.parent.parent.name for p in _EVALS_FILES]


def test_skills_root_exists():
    """Sanity check — the skills tree itself must be present.

    If this fails, either the repo layout moved or the test is running
    from somewhere unexpected. Either way the rest of the module
    cannot produce meaningful results.
    """
    assert _SKILLS_ROOT.is_dir(), (
        f"Skills root not found: {_SKILLS_ROOT}. "
        "Expected `.claude/skills/` under the repo root."
    )


def test_at_least_one_evals_file_discovered():
    """If nobody has shipped an evals.json yet, the rest of the suite
    silently passes vacuously, which is worse than a clear signal.
    Pin a floor so a regression that hides every evals file fails."""
    assert _EVALS_FILES, (
        "No `evals/evals.json` files discovered under "
        f"{_SKILLS_ROOT}. Either the layout changed or every skill's "
        "evals file vanished."
    )


@pytest.mark.parametrize("evals_path", _EVALS_FILES, ids=_IDS)
def test_evals_file_is_valid_json(evals_path: Path):
    """The file must parse as JSON. A malformed evals.json should fail
    CI loudly rather than be silently ignored by skill-loading code."""
    try:
        json.loads(evals_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        pytest.fail(f"{evals_path} is not valid JSON: {exc}")


@pytest.mark.parametrize("evals_path", _EVALS_FILES, ids=_IDS)
def test_top_level_shape(evals_path: Path):
    """Top level must be an object with `skill_name` (string matching
    the parent dir) and `evals` (non-empty list)."""
    data = json.loads(evals_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), (
        f"{evals_path}: top level must be a JSON object, got "
        f"{type(data).__name__}"
    )

    # skill_name: present, string, equals parent skill directory name.
    assert "skill_name" in data, f"{evals_path}: missing `skill_name`"
    skill_name = data["skill_name"]
    assert isinstance(skill_name, str), (
        f"{evals_path}: `skill_name` must be a string, got "
        f"{type(skill_name).__name__}"
    )
    expected_name = evals_path.parent.parent.name
    assert skill_name == expected_name, (
        f"{evals_path}: `skill_name` is {skill_name!r} but the parent "
        f"skill directory is {expected_name!r}. Looks like a copy-paste "
        "from another skill — update `skill_name` to match the folder."
    )

    # evals: present, list, non-empty.
    assert "evals" in data, f"{evals_path}: missing `evals` array"
    evals = data["evals"]
    assert isinstance(evals, list), (
        f"{evals_path}: `evals` must be a JSON array, got "
        f"{type(evals).__name__}"
    )
    assert evals, (
        f"{evals_path}: `evals` array is empty. A skill that ships "
        "without any evals is allowed to omit the file entirely; an "
        "empty array is almost always an accident."
    )


@pytest.mark.parametrize("evals_path", _EVALS_FILES, ids=_IDS)
def test_every_eval_entry_has_required_fields(evals_path: Path):
    """Each eval entry must have the four required fields plus
    `files`. Field types are strict: id int, name string, prompt and
    expected_output non-empty strings, files a list (possibly empty)."""
    data = json.loads(evals_path.read_text(encoding="utf-8"))
    evals = data["evals"]

    for idx, entry in enumerate(evals):
        loc = f"{evals_path}[evals[{idx}]]"
        assert isinstance(entry, dict), (
            f"{loc}: each eval must be a JSON object, got "
            f"{type(entry).__name__}"
        )

        # id: int. Reject bool, which is technically an int in Python.
        assert "id" in entry, f"{loc}: missing `id`"
        eid = entry["id"]
        assert isinstance(eid, int) and not isinstance(eid, bool), (
            f"{loc}: `id` must be an integer, got {type(eid).__name__}"
        )

        # name: kebab-case string.
        assert "name" in entry, f"{loc}: missing `name`"
        name = entry["name"]
        assert isinstance(name, str), (
            f"{loc}: `name` must be a string, got {type(name).__name__}"
        )
        assert _KEBAB_CASE.match(name), (
            f"{loc}: `name` {name!r} is not kebab-case "
            "(lowercase letters/digits separated by single hyphens)."
        )

        # prompt: non-empty string.
        assert "prompt" in entry, f"{loc}: missing `prompt`"
        prompt = entry["prompt"]
        assert isinstance(prompt, str) and prompt.strip(), (
            f"{loc}: `prompt` must be a non-empty string"
        )

        # expected_output: non-empty string.
        assert "expected_output" in entry, (
            f"{loc}: missing `expected_output`"
        )
        expected = entry["expected_output"]
        assert isinstance(expected, str) and expected.strip(), (
            f"{loc}: `expected_output` must be a non-empty string"
        )

        # files: list (may be empty).
        assert "files" in entry, f"{loc}: missing `files`"
        files = entry["files"]
        assert isinstance(files, list), (
            f"{loc}: `files` must be a JSON array (use [] if none), "
            f"got {type(files).__name__}"
        )


@pytest.mark.parametrize("evals_path", _EVALS_FILES, ids=_IDS)
def test_ids_and_names_are_unique(evals_path: Path):
    """Within one file, both `id` and `name` must be unique. Duplicates
    point at a copy-paste that forgot to renumber/rename."""
    data = json.loads(evals_path.read_text(encoding="utf-8"))
    evals = data["evals"]

    ids = [e["id"] for e in evals]
    dup_ids = sorted({i for i in ids if ids.count(i) > 1})
    assert not dup_ids, (
        f"{evals_path}: duplicate `id` values within one file: {dup_ids}"
    )

    names = [e["name"] for e in evals]
    dup_names = sorted({n for n in names if names.count(n) > 1})
    assert not dup_names, (
        f"{evals_path}: duplicate `name` values within one file: "
        f"{dup_names}"
    )
