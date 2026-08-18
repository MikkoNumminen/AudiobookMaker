"""Tests for reading the runner's output as bytes.

Liveness used to be stamped per LINE, on a text-mode pipe. That made "is the
child alive" depend on a property of the line parser: it worked only because
``text=True`` turns a lone carriage return into a line terminator, so a
dependency's ``\\r``-updated progress bar still produced lines. Verified true
at the time, and silently breakable by any later change to encoding,
buffering or newline handling — at which point a working runner looks wedged
and the 45-minute idle watchdog terminates it mid-download.

Reading bytes and splitting lines separately makes the two questions
independent: a byte arriving means the child is alive, whatever it turns out
to mean.
"""
from __future__ import annotations

import io
import subprocess
import sys
import textwrap
import time

import pytest

from src.launcher_bridge import iter_lines


class TestLineSplitting:
    def test_newline_terminated_lines(self):
        assert list(iter_lines(io.BytesIO(b"eka\ntoka\n"))) == ["eka", "toka"]

    def test_a_lone_carriage_return_ends_a_line(self):
        """A progress bar emits these and never a newline."""
        assert list(iter_lines(io.BytesIO(b"10%\r20%\r"))) == ["10%", "20%"]

    def test_crlf_yields_an_empty_line_between(self):
        """Callers already skip empty lines; pinning it so nobody 'fixes' it
        into swallowing a real blank line instead."""
        assert list(iter_lines(io.BytesIO(b"eka\r\ntoka\r\n"))) == [
            "eka", "", "toka", "",
        ]

    def test_unterminated_tail_is_still_yielded(self):
        """A runner that dies mid-line must not have its last words dropped."""
        assert list(iter_lines(io.BytesIO(b"eka\nkesken"))) == ["eka", "kesken"]

    def test_empty_stream(self):
        assert list(iter_lines(io.BytesIO(b""))) == []

    def test_utf8_survives_the_round_trip(self):
        assert list(iter_lines(io.BytesIO("hyvä ää\n".encode()))) == ["hyvä ää"]

    def test_invalid_utf8_does_not_raise(self):
        """Strict decoding here would kill the reader thread and starve the
        event queue, which the GUI reads as a hang."""
        out = list(iter_lines(io.BytesIO(b"hyv\xe4\xe4\nseuraava\n")))
        assert len(out) == 2
        assert out[1] == "seuraava"

    def test_a_character_split_across_chunks_is_not_mangled(self):
        """The buffer holds bytes until the line is complete, so a chunk
        boundary landing mid-character cannot corrupt it."""
        class _Split:
            def __init__(self):
                data = "ää\n".encode()
                self._chunks = iter([data[:1], data[1:], b""])

            def read(self, size=-1):
                return next(self._chunks, b"")

        assert list(iter_lines(_Split())) == ["ää"]


class TestLivenessCallback:
    def test_called_for_every_chunk(self):
        class _Chunks:
            def __init__(self):
                self._chunks = iter([b"a\n", b"b\n", b"c\n", b""])

            def read(self, size=-1):
                return next(self._chunks, b"")

        calls = []
        list(iter_lines(_Chunks(), lambda: calls.append(1)))
        assert len(calls) == 3

    def test_called_before_any_line_is_complete(self):
        """The point of the refactor: output that never forms a line still
        counts as the child being alive."""
        class _NoNewline:
            def __init__(self):
                self._chunks = iter([b"no terminator here", b""])

            def read(self, size=-1):
                return next(self._chunks, b"")

        calls = []
        list(iter_lines(_NoNewline(), lambda: calls.append(1)))
        assert calls, "a chunk with no line terminator did not count as alive"

    def test_not_called_on_an_empty_stream(self):
        calls = []
        list(iter_lines(io.BytesIO(b""), lambda: calls.append(1)))
        assert calls == []

    def test_optional(self):
        assert list(iter_lines(io.BytesIO(b"x\n"), None)) == ["x"]


class TestAgainstARealSubprocess:
    """The end-to-end property, against a real pipe rather than a fake."""

    def _run(self, body: str) -> list[str]:
        script = textwrap.dedent(body)
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
        )
        try:
            return list(iter_lines(proc.stdout))
        finally:
            proc.stdout.close()
            # kill() first: a wait() that times out inside `finally` raises
            # over the original failure and leaves a live child for the rest
            # of the session.
            proc.kill()
            proc.wait(timeout=30)

    def test_a_carriage_return_progress_bar_produces_lines(self):
        """The exact shape a model download emits. If this ever stops
        producing output, the idle watchdog kills long first runs."""
        out = self._run("""
            import sys
            for i in range(3):
                sys.stdout.write("\\rProgress %d%%" % (i * 33))
                sys.stdout.flush()
            sys.stdout.write("\\ndone\\n")
        """)
        assert any("Progress" in line for line in out)
        assert "done" in out

    def test_ordinary_lines_still_work(self):
        out = self._run("""
            import sys
            for i in range(3):
                print("[chapter 1/1] chunk %d/3" % i, flush=True)
        """)
        assert len([line for line in out if line.startswith("[chapter")]) == 3

    def test_non_ascii_output(self):
        out = self._run("""
            import sys
            sys.stdout.buffer.write("hyvä yö\\n".encode("utf-8"))
            sys.stdout.flush()
        """)
        assert "hyvä yö" in out


class TestThePipeIsBinary:
    """Guards the invariant the whole change rests on, by inspecting the
    ACTUAL Popen kwargs rather than scanning the source for a substring.

    A source scan could only see `text=True`. CPython enables text mode if ANY
    of text / universal_newlines / encoding / errors is passed, and `encoding`
    is literally what this change removed, so re-adding it would restore the
    TextIOWrapper with the scan still green.

    `bufsize` matters even more. At the default -1 the pipe is a
    BufferedReader whose read() blocks until 64 KB accumulates or the child
    exits, so a runner emitting a line a minute would refresh liveness roughly
    once every ten hours and the 45-minute idle watchdog would terminate every
    healthy run. That is precisely the incident this exists to prevent.
    """

    def _captured_kwargs(self, monkeypatch, tmp_path):
        import subprocess as _sp
        from src.launcher_bridge import ChatterboxRunner

        captured: dict = {}

        class _FakeProc:
            stdout = io.BytesIO(b"")

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        def _fake_popen(argv, **kw):
            captured.update(kw)
            return _FakeProc()

        monkeypatch.setattr(_sp, "Popen", _fake_popen)
        runner = ChatterboxRunner(
            python_exe=sys.executable,
            script_path="scripts/generate_chatterbox_audiobook.py",
            pdf_path=str(tmp_path / "book.pdf"),
            out_dir=str(tmp_path / "out"),
        )
        runner.start()
        return captured

    @pytest.mark.parametrize(
        "kwarg", ["text", "universal_newlines", "encoding", "errors"]
    )
    def test_no_text_mode_switch_is_passed(self, kwarg, monkeypatch, tmp_path):
        """Any one of these turns the pipe back into text mode."""
        assert self._captured_kwargs(monkeypatch, tmp_path).get(kwarg) is None

    def test_the_pipe_is_unbuffered(self, monkeypatch, tmp_path):
        """Buffering delays read() until the buffer fills, which starves the
        liveness stamp and gets a healthy runner terminated."""
        assert self._captured_kwargs(monkeypatch, tmp_path)["bufsize"] == 0


class TestOutputArrivesIncrementally:
    """Liveness is worthless if the bytes only arrive at the end.

    Every other real-subprocess test here calls list() after the child has
    exited, so they would pass identically under a fully-blocking read.
    """

    def test_lines_arrive_while_the_child_is_still_running(self):
        script = textwrap.dedent(r"""
            import sys, time
            for i in range(3):
                sys.stdout.write("tick %d\n" % i)
                sys.stdout.flush()
                time.sleep(0.4)
        """)
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
        )
        try:
            started = time.monotonic()
            first_at = None
            for line in iter_lines(proc.stdout):
                if line.startswith("tick") and first_at is None:
                    first_at = time.monotonic() - started
                    break
        finally:
            proc.kill()
            proc.wait(timeout=30)

        assert first_at is not None, "no output arrived at all"
        # The child runs for ~1.2 s. A buffered pipe would deliver nothing
        # until it exited; unbuffered delivers the first line immediately.
        assert first_at < 1.0, f"first line took {first_at:.2f}s: pipe buffered?"


class TestBufferBounds:
    """Output that never contains a terminator must not grow without bound."""

    def test_a_terminator_free_payload_is_flushed(self):
        from src.launcher_bridge import _MAX_LINE_BYTES

        class _Endless:
            def __init__(self):
                self.sent = 0

            def read(self, size=-1):
                if self.sent >= _MAX_LINE_BYTES + 1024:
                    return b""
                self.sent += 4096
                return b"x" * 4096

        out = list(iter_lines(_Endless()))
        assert out, "nothing was ever yielded"
        assert max(len(line) for line in out) <= _MAX_LINE_BYTES + 4096

    def test_a_none_read_is_not_end_of_stream(self):
        """A raw non-blocking stream returns None when nothing is ready.
        Treating it as EOF closes the pipe and reports a clean drain while
        the child is still producing output."""
        class _Hiccup:
            def __init__(self):
                self._chunks = iter([b"eka\n", None, b"toka\n", b""])

            def read(self, size=-1):
                return next(self._chunks, b"")

        assert list(iter_lines(_Hiccup())) == ["eka", "toka"]


class TestReaderStampsLivenessWithoutALine:
    """End-to-end wiring: on_bytes -> _mark_alive -> state.last_output_at.

    The existing watchdog test feeds a newline-terminated chunk, which is the
    case that already worked before any of this. Nothing covered the case the
    change exists for.
    """

    def test_bytes_that_never_form_a_line_still_refresh_the_marker(self):
        from unittest.mock import patch
        import src.launcher_bridge as lb

        runner = lb.ChatterboxRunner.__new__(lb.ChatterboxRunner)
        runner._state = lb._RunnerState()
        before = runner._state.last_output_at

        class _Parser:
            def rewrite_upstream_noise(self, line):
                return line

            def parse(self, line):
                return lb.ProgressEvent(kind="log", raw_line=line)

        class _NoTerminator:
            def __init__(self):
                self._chunks = iter([b"no terminator at all", b""])

            def read(self, size=-1):
                return next(self._chunks, b"")

            def close(self):
                pass

        runner._state.proc = type("P", (), {"stdout": _NoTerminator()})()
        with patch("src.launcher_bridge.time.monotonic", return_value=before + 900.0):
            runner._reader_loop(_Parser())

        assert runner._state.last_output_at == before + 900.0
