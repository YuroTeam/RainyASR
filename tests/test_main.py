"""Tests for the RainyASR main controller."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QDialog

from rainyasr import main as main_module
from rainyasr.audio.capture import AudioDeviceInfo, NoLoopbackDeviceError
from rainyasr.config import AppConfig
from rainyasr.gui.settings_dialog import ApiKeyValues
from rainyasr.gui.subtitle_window import SubtitleWindow
from rainyasr.main import RainyASRController, configure_logfire, install_terminal_signal_handlers


def _sample_config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "audio": {
                "sample_rate": 24000,
                "channels": 2,
                "frame_ms": 50,
                "audio_queue_max_frames": 120,
                "silence_rms_threshold": 0.0007,
            },
            "asr": {
                "asr_model": "custom-asr",
                "asr_format": "pcm",
                "asr_language": "en",
            },
            "subtitle": {
                "font_family": "Inter, Arial, sans-serif",
                "font_size": 30,
                "text_color": "#12ABEF",
                "bg_opacity": 65,
                "bilingual_mode": False,
            },
            "hotkey": {"toggle_hotkey": "ctrl+alt+r"},
            "language": {"target_lang": "en"},
        }
    )


class FakeApp:
    def __init__(
        self,
        *,
        qapplication: QApplication,
        window: SubtitleWindow,
        loop: Any,
    ) -> None:
        self.qapplication = qapplication
        self.window = window
        self.loop = loop


class FakeLoop:
    def __init__(self) -> None:
        self.tasks: list[Any] = []

    def is_running(self) -> bool:
        return False

    def is_closed(self) -> bool:
        return False

    def create_task(self, coroutine: Any) -> None:
        self.tasks.append(coroutine)


class FakeAudioDeviceDetector:
    def __init__(
        self,
        device: AudioDeviceInfo | None = None,
        error: Exception | None = None,
    ) -> None:
        self._device = device or AudioDeviceInfo(
            device_id=7,
            name="Fake Loopback",
            sample_rate=48000,
            channels=2,
        )
        self._error = error
        self.calls = 0

    def find_loopback_device(self) -> AudioDeviceInfo:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._device


class RecordingProviderFactory:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((args, kwargs))
        return {"name": self.name, "args": args, "kwargs": kwargs}


class FakeWorker(QObject):
    subtitle_changed = Signal(str, str, bool)
    error_occurred = Signal(str)
    state_changed = Signal(str)

    instances: list[FakeWorker] = []

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        self.kwargs = kwargs
        self.started = 0
        self.stopped = 0
        self.capture_audio: bool | None = None
        self.instances.append(self)

    async def start(self, *, capture_audio: bool = True) -> None:
        self.started += 1
        self.capture_audio = capture_audio
        self.state_changed.emit("running")

    async def stop(self) -> None:
        self.stopped += 1


class FakeHotkeyManager:
    instances: list[FakeHotkeyManager] = []

    def __init__(self, window: SubtitleWindow, hotkey: str) -> None:
        self.window = window
        self.hotkey = hotkey
        self.started = 0
        self.stopped = 0
        self.instances.append(self)

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1


class BrokenHotkeyManager(FakeHotkeyManager):
    def start(self) -> None:
        raise RuntimeError("hotkey backend exploded")


class FakeSignalController(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.quit_requests = 0

    def request_quit(self) -> None:
        self.quit_requests += 1


class FakeAcceptedSettingsDialog:
    instances: list[FakeAcceptedSettingsDialog] = []

    def __init__(
        self,
        config: AppConfig,
        *,
        dashscope_api_key: str,
        deepseek_api_key: str,
        parent: SubtitleWindow,
    ) -> None:
        self.config = config
        self.dashscope_api_key = dashscope_api_key
        self.deepseek_api_key = deepseek_api_key
        self.parent = parent
        self.instances.append(self)

    def exec(self) -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted

    def current_config(self) -> AppConfig:
        return self.config

    def api_key_values(self) -> ApiKeyValues:
        return ApiKeyValues("dash-key", "deep-key")


class FakeRejectedSettingsDialog(FakeAcceptedSettingsDialog):
    instances: list[FakeRejectedSettingsDialog] = []

    def exec(self) -> QDialog.DialogCode:
        return QDialog.DialogCode.Rejected


@pytest.fixture(autouse=True)
def clear_fakes(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(
        main_module.EnvConfig,
        "dashscope_compatible_base_url",
        staticmethod(lambda: "dash-compatible-base"),
    )
    monkeypatch.setattr(
        main_module.EnvConfig,
        "translate_api_key",
        staticmethod(lambda: ""),
    )
    monkeypatch.setattr(
        main_module.EnvConfig,
        "translate_base_url",
        staticmethod(lambda: ""),
    )
    monkeypatch.setattr(
        main_module.EnvConfig,
        "translate_model",
        staticmethod(lambda: "qwen-mt-flash"),
    )
    FakeWorker.instances.clear()
    FakeHotkeyManager.instances.clear()
    FakeAcceptedSettingsDialog.instances.clear()
    FakeRejectedSettingsDialog.instances.clear()
    yield
    FakeWorker.instances.clear()
    FakeHotkeyManager.instances.clear()
    FakeAcceptedSettingsDialog.instances.clear()
    FakeRejectedSettingsDialog.instances.clear()


@pytest.fixture
def fake_app(qapp: QApplication, qtbot) -> Iterator[FakeApp]:
    window = SubtitleWindow()
    qtbot.addWidget(window)
    app = FakeApp(
        qapplication=qapp,
        window=window,
        loop=FakeLoop(),
    )
    yield app
    window.close()


def _message_recorder() -> tuple[
    list[tuple[str, str, str]],
    main_module.MessagePresenter,
]:
    messages: list[tuple[str, str, str]] = []

    def record(_parent, level: str, title: str, message: str) -> None:
        messages.append((level, title, message))

    return messages, record


def test_configure_logfire_uploads_only_when_token_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(main_module.EnvConfig, "logfire_token", staticmethod(lambda: "token-1"))
    monkeypatch.setattr(main_module.logfire, "configure", lambda **kwargs: calls.append(kwargs))

    configure_logfire()

    assert calls == [
        {
            "send_to_logfire": "if-token-present",
            "token": "token-1",
            "service_name": "rainyasr",
        }
    ]


def test_terminal_signal_handlers_request_graceful_quit(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered_handlers = {}

    def fake_signal(signum, handler):
        registered_handlers[signum] = handler

    monkeypatch.setattr(main_module.signal, "signal", fake_signal)
    controller = FakeSignalController()
    timer = install_terminal_signal_handlers(controller, poll_interval_ms=25)

    try:
        assert timer.isActive()
        assert timer.interval() == 25
        registered_handlers[main_module.signal.SIGINT](main_module.signal.SIGINT, None)
        assert controller.quit_requests == 1

        signum = getattr(main_module.signal, "SIGTERM", None)
        if signum is not None:
            registered_handlers[signum](signum, None)
            assert controller.quit_requests == 2
    finally:
        timer.stop()
        controller.deleteLater()


@pytest.mark.asyncio
async def test_controller_start_wires_audio_worker_providers_and_hotkey(
    fake_app: FakeApp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _sample_config()
    detector = FakeAudioDeviceDetector()
    asr_factory = RecordingProviderFactory("asr")
    translation_factory = RecordingProviderFactory("translation")
    messages, message_presenter = _message_recorder()
    saved_configs: list[AppConfig] = []
    monkeypatch.setattr(main_module.EnvConfig, "deepseek_base_url", staticmethod(lambda: "base"))
    monkeypatch.setattr(main_module.EnvConfig, "translate_model", staticmethod(lambda: "model"))

    controller = RainyASRController(
        fake_app,
        config,
        dashscope_api_key="dash-key",
        deepseek_api_key="deep-key",
        audio_device_detector=detector,
        asr_provider_factory=asr_factory,
        translation_provider_factory=translation_factory,
        worker_factory=FakeWorker,
        hotkey_manager_factory=FakeHotkeyManager,
        tray_available=lambda: False,
        message_presenter=message_presenter,
        config_saver=saved_configs.append,
    )

    assert await controller.start() is True

    assert messages == []
    assert detector.calls == 1
    assert len(FakeHotkeyManager.instances) == 1
    assert FakeHotkeyManager.instances[0].hotkey == "ctrl+alt+r"
    assert FakeHotkeyManager.instances[0].started == 1

    assert len(FakeWorker.instances) == 1
    worker = FakeWorker.instances[0]
    assert worker.started == 1
    assert worker.capture_audio is True
    assert fake_app.window._playback_active is True
    assert worker.kwargs["target_lang"] == "en"
    assert worker.kwargs["sample_rate"] == 24000
    assert worker.kwargs["channels"] == 2
    assert worker.kwargs["frame_ms"] == 50
    assert worker.kwargs["audio_queue_max_frames"] == 120
    assert worker.kwargs["silence_rms_threshold"] == 0.0007
    assert worker.kwargs["audio_device_detector"].find_loopback_device().name == "Fake Loopback"

    assert asr_factory.calls == [
        (
            ("dash-key",),
            {"model": "custom-asr", "sample_rate": 24000, "language": "en"},
        )
    ]
    assert translation_factory.calls == [
        (
            ("deep-key",),
            {"base_url": "base", "model": "model"},
        )
    ]

    await controller.shutdown()

    assert worker.stopped == 1
    assert fake_app.window._playback_active is False
    assert FakeHotkeyManager.instances[0].stopped == 1
    assert saved_configs == [config]


@pytest.mark.asyncio
async def test_controller_start_uses_dashscope_for_default_qwen_translation(
    fake_app: FakeApp,
) -> None:
    config = _sample_config()
    detector = FakeAudioDeviceDetector()
    translation_factory = RecordingProviderFactory("translation")

    controller = RainyASRController(
        fake_app,
        config,
        dashscope_api_key="dash-key",
        deepseek_api_key="",
        audio_device_detector=detector,
        translation_provider_factory=translation_factory,
        worker_factory=FakeWorker,
        hotkey_manager_factory=FakeHotkeyManager,
        tray_available=lambda: False,
        config_saver=lambda _config: None,
    )

    assert await controller.start() is True

    assert translation_factory.calls == [
        (
            ("dash-key",),
            {"base_url": "dash-compatible-base", "model": "qwen-mt-flash"},
        )
    ]

    await controller.shutdown()


@pytest.mark.asyncio
async def test_controller_start_keeps_running_when_hotkey_backend_fails(
    fake_app: FakeApp,
) -> None:
    config = _sample_config()
    messages, message_presenter = _message_recorder()
    controller = RainyASRController(
        fake_app,
        config,
        dashscope_api_key="dash-key",
        deepseek_api_key="deep-key",
        audio_device_detector=FakeAudioDeviceDetector(),
        worker_factory=FakeWorker,
        hotkey_manager_factory=BrokenHotkeyManager,
        tray_available=lambda: False,
        message_presenter=message_presenter,
        config_saver=lambda _config: None,
    )

    assert await controller.start() is True

    assert len(FakeWorker.instances) == 1
    assert FakeWorker.instances[0].started == 1
    assert messages == [
        (
            "warning",
            "Global shortcut unavailable",
            "hotkey backend exploded",
        )
    ]

    await controller.shutdown()


@pytest.mark.asyncio
async def test_controller_start_reports_audio_detection_failure(fake_app: FakeApp) -> None:
    config = _sample_config()
    messages, message_presenter = _message_recorder()
    controller = RainyASRController(
        fake_app,
        config,
        dashscope_api_key="dash-key",
        deepseek_api_key="deep-key",
        audio_device_detector=FakeAudioDeviceDetector(
            error=NoLoopbackDeviceError("no monitor source")
        ),
        worker_factory=FakeWorker,
        hotkey_manager_factory=FakeHotkeyManager,
        tray_available=lambda: False,
        message_presenter=message_presenter,
        config_saver=lambda _config: None,
    )

    assert await controller.start() is False

    assert FakeWorker.instances == []
    assert messages
    assert messages[0][0] == "critical"
    assert messages[0][1] == "Audio loopback device not found"
    assert "no monitor source" in messages[0][2]

    await controller.shutdown()


@pytest.mark.asyncio
async def test_controller_start_opens_settings_when_api_keys_are_missing(
    fake_app: FakeApp,
) -> None:
    config = _sample_config()
    messages, message_presenter = _message_recorder()
    detector = FakeAudioDeviceDetector()
    controller = RainyASRController(
        fake_app,
        config,
        dashscope_api_key="",
        deepseek_api_key="",
        audio_device_detector=detector,
        worker_factory=FakeWorker,
        hotkey_manager_factory=FakeHotkeyManager,
        settings_dialog_factory=FakeAcceptedSettingsDialog,
        tray_available=lambda: False,
        message_presenter=message_presenter,
        config_saver=lambda _config: None,
    )

    assert await controller.start() is True

    assert len(FakeAcceptedSettingsDialog.instances) == 1
    dialog = FakeAcceptedSettingsDialog.instances[0]
    assert dialog.dashscope_api_key == ""
    assert dialog.deepseek_api_key == ""
    assert detector.calls == 1
    assert len(FakeWorker.instances) == 1
    assert FakeWorker.instances[0].started == 1
    assert messages == []

    await controller.shutdown()


@pytest.mark.asyncio
async def test_controller_start_stays_idle_when_missing_api_key_settings_are_cancelled(
    fake_app: FakeApp,
) -> None:
    config = _sample_config()
    messages, message_presenter = _message_recorder()
    detector = FakeAudioDeviceDetector()
    controller = RainyASRController(
        fake_app,
        config,
        dashscope_api_key="",
        deepseek_api_key="",
        audio_device_detector=detector,
        worker_factory=FakeWorker,
        hotkey_manager_factory=FakeHotkeyManager,
        settings_dialog_factory=FakeRejectedSettingsDialog,
        tray_available=lambda: False,
        message_presenter=message_presenter,
        config_saver=lambda _config: None,
    )

    assert await controller.start() is False

    assert len(FakeRejectedSettingsDialog.instances) == 1
    assert detector.calls == 0
    assert FakeWorker.instances == []
    assert messages == []

    await controller.shutdown()


def test_window_settings_signal_opens_settings(fake_app: FakeApp) -> None:
    config = _sample_config()
    controller = RainyASRController(
        fake_app,
        config,
        dashscope_api_key="dash-key",
        deepseek_api_key="deep-key",
        audio_device_detector=FakeAudioDeviceDetector(),
        worker_factory=FakeWorker,
        hotkey_manager_factory=FakeHotkeyManager,
        settings_dialog_factory=FakeAcceptedSettingsDialog,
        tray_available=lambda: False,
        config_saver=lambda _config: None,
    )

    fake_app.window.settings_requested.emit()

    assert len(FakeAcceptedSettingsDialog.instances) == 1
    assert FakeAcceptedSettingsDialog.instances[0].parent is fake_app.window
    assert fake_app.window._config == config.subtitle

    controller.deleteLater()


@pytest.mark.asyncio
async def test_window_playback_signal_pauses_and_resumes_worker(fake_app: FakeApp) -> None:
    config = _sample_config()
    controller = RainyASRController(
        fake_app,
        config,
        dashscope_api_key="dash-key",
        deepseek_api_key="deep-key",
        audio_device_detector=FakeAudioDeviceDetector(),
        worker_factory=FakeWorker,
        hotkey_manager_factory=FakeHotkeyManager,
        tray_available=lambda: False,
        config_saver=lambda _config: None,
    )

    assert await controller.start() is True
    old_worker = FakeWorker.instances[0]

    fake_app.window.playback_toggle_requested.emit()
    await asyncio.sleep(0)

    assert controller.worker is None
    assert old_worker.stopped == 1
    assert fake_app.window._playback_active is False

    fake_app.window.playback_toggle_requested.emit()
    await asyncio.sleep(0)

    assert len(FakeWorker.instances) == 2
    assert controller.worker is FakeWorker.instances[1]
    assert FakeWorker.instances[1].started == 1
    assert fake_app.window._playback_active is True

    await controller.shutdown()


@pytest.mark.asyncio
async def test_apply_settings_restarts_hotkey_and_worker(fake_app: FakeApp) -> None:
    config = _sample_config()
    new_config = config.model_copy(deep=True)
    new_config.audio.sample_rate = 44100
    new_config.subtitle.font_size = 36
    new_config.hotkey.toggle_hotkey = "ctrl+shift+s"
    messages, message_presenter = _message_recorder()
    controller = RainyASRController(
        fake_app,
        config,
        dashscope_api_key="dash-key",
        deepseek_api_key="deep-key",
        audio_device_detector=FakeAudioDeviceDetector(),
        worker_factory=FakeWorker,
        hotkey_manager_factory=FakeHotkeyManager,
        tray_available=lambda: False,
        message_presenter=message_presenter,
        config_saver=lambda _config: None,
    )

    assert await controller.start() is True
    old_worker = FakeWorker.instances[0]
    old_hotkey = FakeHotkeyManager.instances[0]

    await controller.apply_settings(new_config, ApiKeyValues("dash-new", "deep-new"))

    assert messages == []
    assert fake_app.window._config.font_size == 36
    assert old_hotkey.stopped == 1
    assert FakeHotkeyManager.instances[1].hotkey == "ctrl+shift+s"
    assert FakeHotkeyManager.instances[1].started == 1
    assert old_worker.stopped == 1
    assert len(FakeWorker.instances) == 2
    assert FakeWorker.instances[1].started == 1
    assert FakeWorker.instances[1].kwargs["sample_rate"] == 44100

    await controller.shutdown()


@pytest.mark.asyncio
async def test_apply_settings_keeps_worker_paused(fake_app: FakeApp) -> None:
    config = _sample_config()
    new_config = config.model_copy(deep=True)
    new_config.audio.sample_rate = 44100
    controller = RainyASRController(
        fake_app,
        config,
        dashscope_api_key="dash-key",
        deepseek_api_key="deep-key",
        audio_device_detector=FakeAudioDeviceDetector(),
        worker_factory=FakeWorker,
        hotkey_manager_factory=FakeHotkeyManager,
        tray_available=lambda: False,
        config_saver=lambda _config: None,
    )

    assert await controller.start() is True
    old_worker = FakeWorker.instances[0]
    await controller._toggle_playback()

    assert controller.worker is None
    assert old_worker.stopped == 1

    await controller.apply_settings(new_config, ApiKeyValues("dash-key", "deep-key"))

    assert controller.worker is None
    assert len(FakeWorker.instances) == 1
    assert fake_app.window._playback_active is False

    await controller.shutdown()
