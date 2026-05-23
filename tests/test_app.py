"""Tests for RainyASRApp (Task 11: qasync bridge)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Protocol

import pytest
import qasync
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from rainyasr.app import RainyASRApp
from rainyasr.config import SubtitleConfig
from rainyasr.gui.subtitle_window import SubtitleWindow


class AppFactory(Protocol):
    """Callable fixture that tracks RainyASRApp instances for cleanup."""

    def __call__(
        self,
        config: SubtitleConfig | None = None,
        *,
        configure_overlay_app: bool | None = None,
    ) -> RainyASRApp: ...


@pytest.fixture
def app_factory(qapp: QApplication) -> Iterator[AppFactory]:
    """Create RainyASRApp instances and clean up their qasync loops."""
    apps: list[RainyASRApp] = []

    def create(
        config: SubtitleConfig | None = None,
        *,
        configure_overlay_app: bool | None = None,
    ) -> RainyASRApp:
        app = RainyASRApp(config, configure_overlay_app=configure_overlay_app)
        apps.append(app)
        return app

    yield create

    for app in apps:
        app.close()


def _event_loop_or_none() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        return None


class TestRainyASRApp:
    def test_creates_qapplication(self, app_factory: AppFactory) -> None:
        app = app_factory()
        assert app is not None

    def test_window_is_subtitle_window(self, app_factory: AppFactory) -> None:
        app = app_factory()

        assert isinstance(app.window, SubtitleWindow)

    def test_window_uses_provided_config(self, app_factory: AppFactory) -> None:
        config = SubtitleConfig(font_size=42, bilingual_mode=False)
        app = app_factory(config)

        assert app.window._config.font_size == 42
        assert app.window._config.bilingual_mode is False

    def test_window_uses_default_config_when_none_provided(self, app_factory: AppFactory) -> None:
        app = app_factory()

        assert app.window._config.font_size == 24  # default
        assert app.window._config.bilingual_mode is True  # default

    def test_loop_is_qasync_event_loop(self, app_factory: AppFactory) -> None:
        app = app_factory()
        assert isinstance(app.loop, qasync.QEventLoop)

    def test_reused_qapplication_does_not_configure_overlay_by_default(
        self,
        app_factory: AppFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = []
        monkeypatch.setattr(
            "rainyasr.app.configure_macos_overlay_app",
            lambda: calls.append(True),
        )

        app_factory()

        assert calls == []

    def test_reused_qapplication_can_opt_into_overlay_config(
        self,
        app_factory: AppFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = []
        monkeypatch.setattr(
            "rainyasr.app.configure_macos_overlay_app",
            lambda: calls.append(True),
        )

        app_factory(configure_overlay_app=True)

        assert calls == [True]

    def test_close_unsets_registered_event_loop(self, app_factory: AppFactory) -> None:
        app = app_factory()
        assert asyncio.get_event_loop() is app.loop

        app.close()

        current_loop = _event_loop_or_none()
        assert current_loop is not app.loop

        if current_loop is not None:
            current_loop.close()
            asyncio.set_event_loop(None)

    def test_closing_old_app_does_not_unset_newer_event_loop(self, app_factory: AppFactory) -> None:
        old_app = app_factory()
        new_app = app_factory()
        assert asyncio.get_event_loop() is new_app.loop

        old_app.close()

        assert asyncio.get_event_loop() is new_app.loop

    def test_init_failure_restores_previous_event_loop(
        self,
        app_factory: AppFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        old_app = app_factory()
        assert asyncio.get_event_loop() is old_app.loop

        class BrokenSubtitleWindow:
            def __init__(self, config: SubtitleConfig) -> None:
                raise RuntimeError("window creation failed")

        monkeypatch.setattr("rainyasr.app.SubtitleWindow", BrokenSubtitleWindow)

        with pytest.raises(RuntimeError, match="window creation failed"):
            RainyASRApp()

        assert asyncio.get_event_loop() is old_app.loop

    def test_window_close_quits_run_loop(self, app_factory: AppFactory) -> None:
        app = app_factory()
        fell_back_to_forced_stop = False

        def force_stop() -> None:
            nonlocal fell_back_to_forced_stop
            fell_back_to_forced_stop = True
            app.loop.stop()

        QTimer.singleShot(0, app.window.close)
        QTimer.singleShot(1000, force_stop)

        app.run()

        assert fell_back_to_forced_stop is False
