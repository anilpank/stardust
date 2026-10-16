"""Tests for thermaltrend/progress.py — the shared CLI progress reporter."""

from __future__ import annotations

import io

from thermaltrend.progress import Progress, format_duration, human_size


class TestFormatDuration:
    def test_under_a_minute(self):
        assert format_duration(5) == "0:05"

    def test_minutes(self):
        assert format_duration(125) == "2:05"

    def test_over_an_hour(self):
        assert format_duration(3725) == "1h02m"

    def test_negative_clamps_to_zero(self):
        assert format_duration(-3) == "0:00"


class TestHumanSize:
    def test_bytes(self):
        assert human_size(512) == "512 B"

    def test_kilobytes(self):
        assert human_size(2048) == "2.0 KB"

    def test_megabytes(self):
        assert human_size(1_600_000) == "1.5 MB"


def _progress(**kwargs):
    stream = io.StringIO()
    return Progress(4, label="Work", stream=stream, width=4, **kwargs), stream


class TestProgress:
    def test_non_tty_is_silent_by_default(self):
        progress, stream = _progress()
        assert progress.inline is False
        progress.update(1)
        progress.log("hello")
        progress.close()
        assert stream.getvalue() == "hello\n"

    def test_inline_reports_percent_and_count(self):
        progress, stream = _progress(inline=True)
        progress.update(2)
        out = stream.getvalue()
        assert progress.done == 2
        assert "50%" in out and "2/4" in out and "[" in out

    def test_inline_log_prints_above_the_bar(self):
        progress, stream = _progress(inline=True)
        progress.update(1)
        progress.log("  AAPL - updated")
        assert "  AAPL - updated\n" in stream.getvalue()

    def test_update_without_argument_increments(self):
        progress, _ = _progress(inline=False)
        progress.update()
        progress.tick()
        assert progress.done == 2

    def test_inline_line_includes_eta(self):
        progress, _ = _progress(inline=True)
        progress.update(1)
        assert "ETA" in progress.line()

    def test_close_ends_with_newline_when_inline(self):
        progress, stream = _progress(inline=True)
        progress.update(4)
        progress.close()
        assert stream.getvalue().endswith("\n")

    def test_zero_total_does_not_crash(self):
        stream = io.StringIO()
        progress = Progress(0, stream=stream, inline=True)
        progress.update(0)
        progress.close()
        assert "100%" in stream.getvalue()
