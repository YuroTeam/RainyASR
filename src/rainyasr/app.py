"""QApplication + qasync event loop bridge for RainyASR.

Provides ``RainyASRApp`` which wires Qt's GUI lifecycle with asyncio
coroutines via ``qasync.QEventLoop``.
"""

from __future__ import annotations

import asyncio
import sys
from typing import ClassVar

import qasync
from PySide6.QtWidgets import QApplication

from rainyasr.config import SubtitleConfig
from rainyasr.gui.subtitle_window import SubtitleWindow, configure_macos_overlay_app


class RainyASRApp:
    """Application shell: QApplication, qasync loop, and subtitle window.

    Usage::

        app = RainyASRApp()
        # Optionally connect a worker before running:
        # worker.subtitle_changed.connect(app.window.update_subtitle)
        app.run()
    """

    _current_event_loop_owner: ClassVar[RainyASRApp | None] = None

    def __init__(
        self,
        subtitle_config: SubtitleConfig | None = None,
        *,
        configure_overlay_app: bool | None = None,
        quit_on_window_close: bool = True,
    ) -> None:
        existing = QApplication.instance()
        owns_qapplication = existing is None
        self._app = existing if existing else QApplication(sys.argv)
        self._quit_on_window_close = quit_on_window_close

        should_configure_overlay = (
            owns_qapplication if configure_overlay_app is None else configure_overlay_app
        )
        if should_configure_overlay:
            configure_macos_overlay_app()

        previous_event_loop_owner = RainyASRApp._current_event_loop_owner
        self._loop = qasync.QEventLoop(self._app)
        asyncio.set_event_loop(self._loop)
        RainyASRApp._current_event_loop_owner = self

        try:
            self._window = SubtitleWindow(subtitle_config or SubtitleConfig())
        except Exception:
            self._unset_event_loop()
            if not self._loop.is_closed():
                self._loop.close()
            if (
                previous_event_loop_owner is not None
                and not previous_event_loop_owner.loop.is_closed()
            ):
                asyncio.set_event_loop(previous_event_loop_owner.loop)
                RainyASRApp._current_event_loop_owner = previous_event_loop_owner
            raise
        if self._quit_on_window_close:
            self._window.closed.connect(self._handle_window_closed)

    @property
    def qapplication(self) -> QApplication:
        """The underlying QApplication instance."""
        return self._app

    @property
    def window(self) -> SubtitleWindow:
        """The main subtitle overlay window."""
        return self._window

    @property
    def loop(self) -> qasync.QEventLoop:
        """The running qasync event loop."""
        return self._loop

    def run(self) -> None:
        """Show the subtitle window and start the event loop."""
        self._window.show()
        try:
            with self._loop:
                self._loop.run_forever()
        finally:
            self._unset_event_loop()

    def quit(self) -> None:
        """Request the application to quit."""
        self._app.quit()

    def close(self) -> None:
        """Close the window and release event-loop resources."""
        # Closing emits SubtitleWindow.closed, which already requests QApplication quit.
        # The loop cleanup below keeps programmatic close() deterministic and idempotent.
        self._window.close()

        if self._loop.is_running():
            self._loop.stop()
            self._unset_event_loop()
            return
        elif not self._loop.is_closed():
            self._loop.close()

        self._unset_event_loop()

    def _handle_window_closed(self) -> None:
        """Quit the application when the main subtitle window closes."""
        self.quit()

    def _unset_event_loop(self) -> None:
        """Remove this app's loop from asyncio's current-event-loop slot."""
        if RainyASRApp._current_event_loop_owner is self:
            asyncio.set_event_loop(None)
            RainyASRApp._current_event_loop_owner = None
