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
from pathlib import Path

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
    def test_popen_does_not_request_text_mode(self):
        """Text mode would put newline handling back between the child and
        the watchdog, which is the coupling this removes."""
        src = (
            Path(__file__).resolve().parents[1] / "src" / "launcher_bridge.py"
        ).read_text(encoding="utf-8")
        start = src.index("self._state.proc = subprocess.Popen(")
        call = src[start:start + 700]
        assert "text=True" not in call
        assert "bufsize=0" in call
