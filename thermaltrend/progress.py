"""Dependency-free terminal progress reporting for the CLI scripts.

Renders a single, self-overwriting progress line (bar, percent, count, elapsed
time and ETA) when the output stream is an interactive terminal. When output is
redirected to a file or captured by a test the live bar stays silent, so logs
and CI output stay clean; the caller's own per-item detail lines still print.

Callers keep printing detail messages through :meth:`Progress.log` so the bar is
cleared first and never gets clobbered, then call :meth:`Progress.update` after
each item to advance it.

Usage::

    progress = Progress(len(tickers), label="Updating")
    for i, ticker in enumerate(tickers, 1):
        progress.log(f"  {ticker} - updated")
        progress.update(i, note=ticker)
    progress.close()
"""

from __future__ import annotations

import sys
import time


def format_duration(seconds: float) -> str:
    """Format a duration as ``M:SS`` (or ``HhMM`` once past an hour)."""
    total = int(max(seconds, 0))
    minutes, secs = divmod(total, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}:{secs:02d}"


def human_size(num_bytes: int) -> str:
    """Format a byte count as ``861 KB`` / ``1.6 MB`` for summary lines."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


class Progress:
    """A live, single-line progress reporter.

    ``inline`` defaults to whether ``stream`` is a TTY: interactive terminals get
    a bar that redraws in place, everything else gets no bar (only the messages
    routed through :meth:`log`).
    """

    def __init__(self, total, label="", *, stream=None, width=28, inline=None):
        self.total = max(int(total), 0)
        self.label = label
        self.width = max(int(width), 1)
        self.stream = stream if stream is not None else sys.stdout
        self.done = 0
        self._start = time.monotonic()
        if inline is None:
            inline = bool(getattr(self.stream, "isatty", lambda: False)())
        self.inline = inline
        self._rendered_len = 0

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._start

    def _bar(self) -> str:
        if self.total <= 0:
            filled = self.width
        else:
            filled = int(self.width * self.done / self.total)
        return "[" + "#" * filled + "-" * (self.width - filled) + "]"

    def line(self, note="") -> str:
        """Return the current progress line (bar, percent, count, elapsed, ETA)."""
        elapsed = self.elapsed
        if self.total:
            pct = 100.0 * self.done / self.total
            count = f"{self.done}/{self.total}"
            eta = elapsed * (self.total - self.done) / self.done if self.done else 0.0
            eta_txt = f"  ETA {format_duration(eta)}"
        else:
            pct = 100.0
            count = str(self.done)
            eta_txt = ""
        prefix = f"{self.label} " if self.label else ""
        suffix = f"  {note}" if note else ""
        return (f"{prefix}{self._bar()} {pct:3.0f}% {count}  "
                f"{format_duration(elapsed)}{eta_txt}{suffix}")

    def _clear(self) -> None:
        if self._rendered_len:
            self.stream.write("\r" + " " * self._rendered_len + "\r")
            self._rendered_len = 0

    def _render(self, text: str) -> None:
        pad = " " * max(self._rendered_len - len(text), 0)
        self.stream.write("\r" + text + pad)
        self.stream.flush()
        self._rendered_len = len(text)

    def log(self, message="") -> None:
        """Print a detail message, keeping the live bar below it."""
        if self.inline:
            self._clear()
        self.stream.write(str(message) + "\n")
        self.stream.flush()
        if self.inline:
            self._render(self.line())

    def update(self, done=None, note="") -> None:
        """Advance the counter and (re)draw the bar when running inline."""
        self.done = self.done + 1 if done is None else int(done)
        if self.inline:
            self._render(self.line(note))

    def tick(self, note="") -> None:
        self.update(note=note)

    def close(self) -> None:
        """Finish the bar with a trailing newline so the next output is clean."""
        if self.inline:
            self._render(self.line())
            self.stream.write("\n")
            self.stream.flush()
            self._rendered_len = 0
