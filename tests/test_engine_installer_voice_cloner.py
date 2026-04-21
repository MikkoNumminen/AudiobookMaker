"""Tests for :mod:`src.engine_installer_voice_cloner`.

All I/O is stubbed. Pip runner, HF verify, smoke test, and the HF
token-prompt modal are injected as test doubles, so these tests never
hit the network, the disk beyond tmp_path, or a real subprocess.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import pytest

from src.engine_installer import InstallProgress
from src.engine_installer_voice_cloner import (
    HF_PYANNOTE_MODEL_URL,
    HF_SIGNUP_URL,
    HF_TOKENS_URL,
    VOICE_CLONER_ID,
    VOICE_CLONER_PIP_PACKAGES,
    HfVerifyResult,
    VoiceClonerInstaller,
    _verify_failure_copy,
    get_capability_installer,
    list_capability_installers,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _collecting_progress() -> tuple[list[InstallProgress], callable]:
    events: list[InstallProgress] = []
    return events, events.append


def _build_installer(
    tmp_path: Path,
    *,
    verify_results: Optional[list[HfVerifyResult]] = None,
    prompted_tokens: Optional[list[Optional[str]]] = None,
    pip_returncode: int = 0,
    smoke_test_ok: bool = True,
    existing_token: Optional[str] = None,
    venv_exists: bool = True,
) -> VoiceClonerInstaller:
    """Build an installer wired to fakes. Sensible defaults for happy path."""
    verify_iter = iter(verify_results or [HfVerifyResult(ok=True, reason="ok")])
    prompted_iter = iter(prompted_tokens or ["hf_testtoken"])

    def _verify(token: str) -> HfVerifyResult:
        return next(verify_iter)

    def _prompt() -> Optional[str]:
        return next(prompted_iter)

    def _pip(venv, packages, progress_cb, cancel_event):
        progress_cb(InstallProgress(message="pip: Downloading faster-whisper…"))
        progress_cb(InstallProgress(message="pip: Successfully installed"))
        return pip_returncode

    def _smoke(venv, progress_cb, cancel_event):
        return smoke_test_ok

    fake_venv = tmp_path / "venv" / "Scripts" / "python.exe"
    if venv_exists:
        fake_venv.parent.mkdir(parents=True, exist_ok=True)
        fake_venv.write_text("fake-python", encoding="utf-8")
    # When venv_exists=False we still pass a Path (one that doesn't
    # exist) so the installer does not fall back to the real-box
    # ``resolve_chatterbox_python()`` and accidentally find the dev
    # machine's venv.
    token_path = tmp_path / "hf_cache" / "token"
    if existing_token is not None:
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(existing_token, encoding="utf-8")

    return VoiceClonerInstaller(
        venv_python=fake_venv,
        hf_token_prompt_fn=_prompt,
        hf_verify_fn=_verify,
        pip_runner=_pip,
        smoke_test_fn=_smoke,
        token_path=token_path,
    )


# ---------------------------------------------------------------------------
# Registry and URLs
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_list_capability_installers_returns_voice_cloner(self) -> None:
        items = list_capability_installers()
        assert len(items) == 1
        assert items[0].engine_id == VOICE_CLONER_ID

    def test_get_capability_installer_by_id(self) -> None:
        inst = get_capability_installer(VOICE_CLONER_ID)
        assert isinstance(inst, VoiceClonerInstaller)

    def test_get_capability_installer_unknown_returns_none(self) -> None:
        assert get_capability_installer("does_not_exist") is None


class TestPublicUrls:
    def test_hf_urls_point_to_huggingface(self) -> None:
        # These literal URLs are exposed in the Barney-style modal copy
        # and shipped in release notes — if they change someone must
        # consciously update the modal too.
        assert HF_SIGNUP_URL.startswith("https://huggingface.co/")
        assert HF_PYANNOTE_MODEL_URL.startswith("https://huggingface.co/")
        assert HF_TOKENS_URL.startswith("https://huggingface.co/")

    def test_pip_packages_are_the_expected_two(self) -> None:
        assert set(VOICE_CLONER_PIP_PACKAGES) == {"faster-whisper", "pyannote.audio"}


# ---------------------------------------------------------------------------
# check_prerequisites / get_steps
# ---------------------------------------------------------------------------


class TestCheckPrerequisites:
    def test_no_chatterbox_venv_raises_issue(self, tmp_path: Path) -> None:
        inst = _build_installer(tmp_path, venv_exists=False)
        issues = inst.check_prerequisites()
        assert any("Chatterbox is not installed" in i for i in issues)

    def test_venv_present_and_disk_ok_no_issues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inst = _build_installer(tmp_path, venv_exists=True)
        # Disk check returns whatever the real system says, but we
        # don't actually care about anything except "not below threshold"
        # here — on any CI or dev box with >2 GB free we expect empty.
        from src import engine_installer_voice_cloner as mod

        class _FakeDisk:
            free_gb = 999

        monkeypatch.setattr(mod, "check_disk_space", lambda _p: _FakeDisk())
        assert inst.check_prerequisites() == []

    def test_disk_below_threshold_raises_issue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inst = _build_installer(tmp_path, venv_exists=True)
        from src import engine_installer_voice_cloner as mod

        class _FakeDisk:
            free_gb = 0

        monkeypatch.setattr(mod, "check_disk_space", lambda _p: _FakeDisk())
        issues = inst.check_prerequisites()
        assert any("GB free" in i for i in issues)


class TestGetSteps:
    def test_returns_five_steps(self, tmp_path: Path) -> None:
        steps = _build_installer(tmp_path).get_steps()
        assert [s.name for s in steps] == [
            "disk",
            "pip",
            "whisper_warm",
            "hf_setup",
            "smoke",
        ]


# ---------------------------------------------------------------------------
# install() happy path and failure modes
# ---------------------------------------------------------------------------


class TestInstallHappyPath:
    def test_fresh_install_writes_token_and_reports_done(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inst = _build_installer(tmp_path)
        # Bypass real disk check.
        from src import engine_installer_voice_cloner as mod

        class _FakeDisk:
            free_gb = 999

        monkeypatch.setattr(mod, "check_disk_space", lambda _p: _FakeDisk())

        events, cb = _collecting_progress()
        inst.install(cb, threading.Event())

        # Token should now be on disk.
        token_file = tmp_path / "hf_cache" / "token"
        assert token_file.exists()
        assert token_file.read_text(encoding="utf-8").strip() == "hf_testtoken"

        # Final event should flag done=True with no error.
        assert any(e.done for e in events)
        assert not any(e.error for e in events)

    def test_existing_valid_token_is_reused_without_prompt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompt_calls: list[int] = []

        def _prompt() -> Optional[str]:
            prompt_calls.append(1)
            return "never_called"

        fake_venv = tmp_path / "venv" / "Scripts" / "python.exe"
        fake_venv.parent.mkdir(parents=True, exist_ok=True)
        fake_venv.write_text("fake", encoding="utf-8")
        token_path = tmp_path / "hf_cache" / "token"
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text("hf_existing", encoding="utf-8")

        inst = VoiceClonerInstaller(
            venv_python=fake_venv,
            hf_token_prompt_fn=_prompt,
            hf_verify_fn=lambda t: HfVerifyResult(ok=True, reason="ok"),
            pip_runner=lambda *a, **k: 0,
            smoke_test_fn=lambda *a, **k: True,
            token_path=token_path,
        )

        from src import engine_installer_voice_cloner as mod

        class _FakeDisk:
            free_gb = 999

        monkeypatch.setattr(mod, "check_disk_space", lambda _p: _FakeDisk())

        events, cb = _collecting_progress()
        inst.install(cb, threading.Event())

        assert prompt_calls == [], "existing valid token must be reused"
        assert any(e.done for e in events)
        # Log mentions reuse.
        assert any("reusing" in e.message.lower() for e in events if e.message)


class TestInstallFailures:
    def test_no_chatterbox_venv_aborts_with_error(self, tmp_path: Path) -> None:
        inst = _build_installer(tmp_path, venv_exists=False)
        events, cb = _collecting_progress()
        inst.install(cb, threading.Event())
        assert any(e.error for e in events)
        assert not any(e.done for e in events)

    def test_pip_nonzero_exit_aborts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inst = _build_installer(tmp_path, pip_returncode=1)
        from src import engine_installer_voice_cloner as mod

        class _FakeDisk:
            free_gb = 999

        monkeypatch.setattr(mod, "check_disk_space", lambda _p: _FakeDisk())
        events, cb = _collecting_progress()
        inst.install(cb, threading.Event())
        assert any("exit 1" in (e.error or "") for e in events)
        assert not any(e.done for e in events)

    def test_user_cancels_hf_modal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inst = _build_installer(tmp_path, prompted_tokens=[None])
        from src import engine_installer_voice_cloner as mod

        class _FakeDisk:
            free_gb = 999

        monkeypatch.setattr(mod, "check_disk_space", lambda _p: _FakeDisk())
        events, cb = _collecting_progress()
        inst.install(cb, threading.Event())
        assert any("cancelled" in (e.error or "").lower() for e in events)

    def test_unauthorised_then_success_second_try(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # First verify says 401, second says ok. Prompt is called twice.
        inst = _build_installer(
            tmp_path,
            verify_results=[
                HfVerifyResult(ok=False, reason="unauthorised"),
                HfVerifyResult(ok=True, reason="ok"),
            ],
            prompted_tokens=["hf_wrong", "hf_right"],
        )
        from src import engine_installer_voice_cloner as mod

        class _FakeDisk:
            free_gb = 999

        monkeypatch.setattr(mod, "check_disk_space", lambda _p: _FakeDisk())
        events, cb = _collecting_progress()
        inst.install(cb, threading.Event())
        token_file = tmp_path / "hf_cache" / "token"
        assert token_file.read_text(encoding="utf-8").strip() == "hf_right"
        assert any(e.done for e in events)
        # User-facing copy mentions the refusal.
        assert any("refused" in e.message for e in events if e.message)

    def test_two_failed_tries_gives_up(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inst = _build_installer(
            tmp_path,
            verify_results=[
                HfVerifyResult(ok=False, reason="unauthorised"),
                HfVerifyResult(ok=False, reason="unauthorised"),
            ],
            prompted_tokens=["hf_wrong_a", "hf_wrong_b"],
        )
        from src import engine_installer_voice_cloner as mod

        class _FakeDisk:
            free_gb = 999

        monkeypatch.setattr(mod, "check_disk_space", lambda _p: _FakeDisk())
        events, cb = _collecting_progress()
        inst.install(cb, threading.Event())
        assert any("after two tries" in (e.error or "") for e in events)
        assert not any(e.done for e in events)

    def test_smoke_test_failure_aborts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inst = _build_installer(tmp_path, smoke_test_ok=False)
        from src import engine_installer_voice_cloner as mod

        class _FakeDisk:
            free_gb = 999

        monkeypatch.setattr(mod, "check_disk_space", lambda _p: _FakeDisk())
        events, cb = _collecting_progress()
        inst.install(cb, threading.Event())
        assert any("did not import cleanly" in (e.error or "") for e in events)
        assert not any(e.done for e in events)

    def test_existing_invalid_token_triggers_prompt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inst = _build_installer(
            tmp_path,
            existing_token="hf_stale",
            verify_results=[
                # Stale token fails.
                HfVerifyResult(ok=False, reason="forbidden"),
                # Prompted-for-new token works.
                HfVerifyResult(ok=True, reason="ok"),
            ],
            prompted_tokens=["hf_fresh"],
        )
        from src import engine_installer_voice_cloner as mod

        class _FakeDisk:
            free_gb = 999

        monkeypatch.setattr(mod, "check_disk_space", lambda _p: _FakeDisk())
        events, cb = _collecting_progress()
        inst.install(cb, threading.Event())
        token_file = tmp_path / "hf_cache" / "token"
        assert token_file.read_text(encoding="utf-8").strip() == "hf_fresh"

    def test_disk_too_small_aborts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inst = _build_installer(tmp_path)
        from src import engine_installer_voice_cloner as mod

        class _FakeDisk:
            free_gb = 0

        monkeypatch.setattr(mod, "check_disk_space", lambda _p: _FakeDisk())
        events, cb = _collecting_progress()
        inst.install(cb, threading.Event())
        assert any("Only 0 GB" in (e.error or "") for e in events)


class TestCancellation:
    def test_cancel_after_disk_aborts_before_pip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pip_called: list[int] = []

        def _pip(venv, packages, progress_cb, cancel_event):
            pip_called.append(1)
            return 0

        fake_venv = tmp_path / "venv" / "Scripts" / "python.exe"
        fake_venv.parent.mkdir(parents=True, exist_ok=True)
        fake_venv.write_text("fake", encoding="utf-8")

        inst = VoiceClonerInstaller(
            venv_python=fake_venv,
            hf_token_prompt_fn=lambda: "hf_whatever",
            hf_verify_fn=lambda t: HfVerifyResult(ok=True, reason="ok"),
            pip_runner=_pip,
            smoke_test_fn=lambda *a, **k: True,
            token_path=tmp_path / "hf_cache" / "token",
        )

        from src import engine_installer_voice_cloner as mod

        class _FakeDisk:
            free_gb = 999

        monkeypatch.setattr(mod, "check_disk_space", lambda _p: _FakeDisk())

        cancel = threading.Event()
        cancel.set()

        events, cb = _collecting_progress()
        inst.install(cb, cancel)
        assert pip_called == []


# ---------------------------------------------------------------------------
# _verify_failure_copy
# ---------------------------------------------------------------------------


class TestVerifyFailureCopy:
    def test_unauthorised_mentions_key(self) -> None:
        msg = _verify_failure_copy(HfVerifyResult(ok=False, reason="unauthorised"))
        assert "wrong" in msg.lower()

    def test_forbidden_mentions_model_terms(self) -> None:
        msg = _verify_failure_copy(HfVerifyResult(ok=False, reason="forbidden"))
        assert "terms" in msg.lower() or "agree" in msg.lower()

    def test_network_mentions_internet(self) -> None:
        msg = _verify_failure_copy(HfVerifyResult(ok=False, reason="network"))
        assert "internet" in msg.lower()

    def test_other_passes_detail_through(self) -> None:
        msg = _verify_failure_copy(
            HfVerifyResult(ok=False, reason="other", detail="HTTP 500")
        )
        assert "HTTP 500" in msg


# ---------------------------------------------------------------------------
# HF verify — exercise _default_hf_verify via URL/request monkeypatching
# ---------------------------------------------------------------------------


class TestDefaultHfVerify:
    def test_200_is_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import urllib.request

        from src import engine_installer_voice_cloner as mod

        class _FakeResp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _fake_urlopen(req, timeout):
            return _FakeResp()

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
        out = mod._default_hf_verify("hf_x")
        assert out.ok is True
        assert out.reason == "ok"

    def test_401_is_unauthorised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import urllib.error
        import urllib.request

        from src import engine_installer_voice_cloner as mod

        def _fake_urlopen(req, timeout):
            raise urllib.error.HTTPError(
                url="x", code=401, msg="nope", hdrs=None, fp=None
            )

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
        out = mod._default_hf_verify("hf_x")
        assert out.ok is False
        assert out.reason == "unauthorised"

    def test_403_is_forbidden(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import urllib.error
        import urllib.request

        from src import engine_installer_voice_cloner as mod

        def _fake_urlopen(req, timeout):
            raise urllib.error.HTTPError(
                url="x", code=403, msg="nope", hdrs=None, fp=None
            )

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
        out = mod._default_hf_verify("hf_x")
        assert out.reason == "forbidden"

    def test_network_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import urllib.error
        import urllib.request

        from src import engine_installer_voice_cloner as mod

        def _fake_urlopen(req, timeout):
            raise urllib.error.URLError("dns fail")

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
        out = mod._default_hf_verify("hf_x")
        assert out.reason == "network"
