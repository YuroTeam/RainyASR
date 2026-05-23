"""Tests for the subtitle demo script."""

from __future__ import annotations

from collections.abc import Callable

from scripts.demo_subtitle import quit_after_all_windows_close


class _FakeSignal:
    def __init__(self) -> None:
        self._callback: Callable[[], None] | None = None

    def connect(self, callback: Callable[[], None]) -> None:
        self._callback = callback

    def emit(self) -> None:
        assert self._callback is not None
        self._callback()


class _FakeWindow:
    def __init__(self) -> None:
        self.closed = _FakeSignal()


class _FakeApp:
    def __init__(self) -> None:
        self.quit_count = 0

    def quit(self) -> None:
        self.quit_count += 1


def test_demo_quits_only_after_all_windows_are_closed() -> None:
    app = _FakeApp()
    windows = [_FakeWindow(), _FakeWindow(), _FakeWindow()]

    quit_after_all_windows_close(app, windows)

    windows[1].closed.emit()
    assert app.quit_count == 0

    windows[0].closed.emit()
    assert app.quit_count == 0

    windows[2].closed.emit()
    assert app.quit_count == 1
