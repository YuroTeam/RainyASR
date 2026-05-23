"""Main application entry point for RainyASR."""

from __future__ import annotations

import contextlib
import platform
import signal
from collections.abc import Callable
from dataclasses import dataclass

import logfire
from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QDialog,
    QMenu,
    QMessageBox,
    QStyle,
    QSystemTrayIcon,
    QWidget,
)

from rainyasr.app import RainyASRApp
from rainyasr.audio.capture import AudioDeviceDetector, AudioDeviceInfo, NoLoopbackDeviceError
from rainyasr.config import AppConfig, EnvConfig
from rainyasr.gui.settings_dialog import ApiKeyValues, SettingsDialog
from rainyasr.hotkey import GlobalHotkeyManager, HotkeyPermissionError, HotkeyRegistrationError
from rainyasr.providers import DeepSeekTranslationProvider, QwenRealtimeASRProvider
from rainyasr.worker import SubtitleWorker

ProviderFactory = Callable[..., object]
WorkerFactory = Callable[..., SubtitleWorker]
HotkeyManagerFactory = Callable[..., GlobalHotkeyManager]
SettingsDialogFactory = Callable[..., SettingsDialog]
MessagePresenter = Callable[[QWidget, str, str, str], None]
ConfigSaver = Callable[[AppConfig], None]


@dataclass(frozen=True)
class _FixedAudioDeviceDetector:
    """Worker detector wrapper for a device already checked during startup."""

    device: AudioDeviceInfo

    def find_loopback_device(self) -> AudioDeviceInfo:
        """Return the pre-detected loopback device."""
        return self.device


def configure_logfire() -> None:
    """Configure Logfire for local output, uploading only when a token exists."""
    logfire.configure(
        send_to_logfire="if-token-present",
        token=EnvConfig.logfire_token(),
        service_name="rainyasr",
    )


class RainyASRController(QObject):
    """Own the top-level RainyASR runtime objects and lifecycle."""

    def __init__(
        self,
        app: RainyASRApp,
        config: AppConfig,
        *,
        dashscope_api_key: str | None = None,
        deepseek_api_key: str | None = None,
        audio_device_detector: AudioDeviceDetector | None = None,
        asr_provider_factory: ProviderFactory = QwenRealtimeASRProvider,
        translation_provider_factory: ProviderFactory = DeepSeekTranslationProvider,
        worker_factory: WorkerFactory = SubtitleWorker,
        hotkey_manager_factory: HotkeyManagerFactory = GlobalHotkeyManager,
        settings_dialog_factory: SettingsDialogFactory = SettingsDialog,
        tray_available: Callable[[], bool] | None = None,
        message_presenter: MessagePresenter | None = None,
        config_saver: ConfigSaver | None = None,
    ) -> None:
        super().__init__(app.window)
        self._app = app
        self._config = config
        self._dashscope_api_key = (
            dashscope_api_key if dashscope_api_key is not None else EnvConfig.dashscope_api_key()
        )
        self._deepseek_api_key = (
            deepseek_api_key if deepseek_api_key is not None else EnvConfig.deepseek_api_key()
        )
        self._audio_device_detector = audio_device_detector or AudioDeviceDetector()
        self._asr_provider_factory = asr_provider_factory
        self._translation_provider_factory = translation_provider_factory
        self._worker_factory = worker_factory
        self._hotkey_manager_factory = hotkey_manager_factory
        self._settings_dialog_factory = settings_dialog_factory
        self._tray_available = tray_available or QSystemTrayIcon.isSystemTrayAvailable
        self._message_presenter = message_presenter or self._default_message_presenter
        self._config_saver = config_saver or (lambda current_config: current_config.save())

        self._worker: SubtitleWorker | None = None
        self._hotkey_manager: GlobalHotkeyManager | None = None
        self._tray_icon: QSystemTrayIcon | None = None
        self._tray_menu: QMenu | None = None
        self._show_hide_action: QAction | None = None
        self._settings_action: QAction | None = None
        self._quit_action: QAction | None = None
        self._shutdown_requested = False
        self._shutting_down = False
        self._previous_quit_on_last_window_closed = self._app.qapplication.quitOnLastWindowClosed()

        self._app.qapplication.setQuitOnLastWindowClosed(False)
        self._app.window.closed.connect(self.request_quit)

    @property
    def worker(self) -> SubtitleWorker | None:
        """The current subtitle worker, exposed for focused tests."""
        return self._worker

    @property
    def hotkey_manager(self) -> GlobalHotkeyManager | None:
        """The current global hotkey manager, exposed for focused tests."""
        return self._hotkey_manager

    async def start(self) -> bool:
        """Start tray, hotkey, and the subtitle worker."""
        self._setup_tray()
        self._start_hotkey()
        return await self._start_worker()

    async def shutdown(self) -> None:
        """Stop runtime components and persist the latest configuration."""
        if self._shutting_down:
            return

        self._shutting_down = True
        try:
            self._stop_hotkey()
            await self._stop_worker()
            self._hide_tray()
            try:
                self._config_saver(self._config)
            except Exception as exc:
                logfire.warning("Failed to save RainyASR config during shutdown", error=str(exc))
        finally:
            self._app.qapplication.setQuitOnLastWindowClosed(
                self._previous_quit_on_last_window_closed
            )
            self._shutting_down = False

    def request_quit(self) -> None:
        """Gracefully shut down, then quit QApplication."""
        if self._shutdown_requested:
            return

        self._shutdown_requested = True
        loop = self._app.loop
        if loop.is_running() and not loop.is_closed():
            loop.create_task(self._shutdown_then_quit())
            return

        self._app.qapplication.quit()

    def open_settings(self) -> None:
        """Open the settings dialog and apply accepted changes."""
        dialog = self._settings_dialog_factory(
            self._config,
            dashscope_api_key=self._dashscope_api_key,
            deepseek_api_key=self._deepseek_api_key,
            parent=self._app.window,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        api_keys = dialog.api_key_values()
        new_config = dialog.current_config()
        loop = self._app.loop
        if loop.is_running() and not loop.is_closed():
            loop.create_task(self.apply_settings(new_config, api_keys))
        else:
            self._config = new_config
            self._dashscope_api_key = api_keys.dashscope_api_key
            self._deepseek_api_key = api_keys.deepseek_api_key
            self._app.window.apply_config(new_config.subtitle)

    async def apply_settings(self, new_config: AppConfig, api_keys: ApiKeyValues) -> None:
        """Apply accepted settings and restart affected runtime components."""
        old_config = self._config
        old_dashscope_api_key = self._dashscope_api_key
        old_deepseek_api_key = self._deepseek_api_key

        self._config = new_config
        self._dashscope_api_key = api_keys.dashscope_api_key
        self._deepseek_api_key = api_keys.deepseek_api_key
        self._app.window.apply_config(new_config.subtitle)

        if old_config.hotkey.toggle_hotkey != new_config.hotkey.toggle_hotkey:
            self._start_hotkey()

        if self._worker_restart_required(
            old_config,
            new_config,
            old_dashscope_api_key,
            old_deepseek_api_key,
            self._dashscope_api_key,
            self._deepseek_api_key,
        ):
            await self._restart_worker()

    async def _shutdown_then_quit(self) -> None:
        await self.shutdown()
        self._app.qapplication.quit()

    async def _restart_worker(self) -> None:
        await self._stop_worker()
        await self._start_worker()

    async def _start_worker(self) -> bool:
        if self._worker is not None:
            return True

        if not self._has_api_keys() and not self._prompt_for_missing_api_keys():
            return False

        try:
            device = self._audio_device_detector.find_loopback_device()
        except NoLoopbackDeviceError as exc:
            self._present_message(
                "critical",
                "Audio loopback device not found",
                self._audio_setup_hint(str(exc)),
            )
            return False
        except Exception as exc:
            self._present_message(
                "critical",
                "Audio device detection failed",
                f"RainyASR could not inspect audio devices: {exc}",
            )
            return False

        worker = self._create_worker(device)
        worker.error_occurred.connect(self._handle_worker_error)
        worker.state_changed.connect(self._handle_worker_state_changed)

        try:
            await worker.start(capture_audio=True)
        except Exception as exc:
            with contextlib.suppress(Exception):
                await worker.stop()
            self._present_message(
                "critical",
                "RainyASR failed to start",
                str(exc),
            )
            return False

        self._worker = worker
        return True

    async def _stop_worker(self) -> None:
        worker = self._worker
        if worker is None:
            return

        self._worker = None
        with contextlib.suppress(Exception):
            await worker.stop()

    def _create_worker(self, device: AudioDeviceInfo) -> SubtitleWorker:
        asr_provider = self._asr_provider_factory(
            self._dashscope_api_key,
            model=self._config.asr.asr_model,
            sample_rate=self._config.audio.sample_rate,
            language=self._config.asr.asr_language,
        )
        translation_provider = self._translation_provider_factory(
            self._deepseek_api_key,
            base_url=EnvConfig.deepseek_base_url(),
            model=EnvConfig.translate_model(),
        )
        return self._worker_factory(
            asr_provider=asr_provider,
            translation_provider=translation_provider,
            subtitle_window=self._app.window,
            target_lang=self._config.language.target_lang,
            sample_rate=self._config.audio.sample_rate,
            channels=self._config.audio.channels,
            frame_ms=self._config.audio.frame_ms,
            audio_queue_max_frames=self._config.audio.audio_queue_max_frames,
            audio_device_detector=_FixedAudioDeviceDetector(device),
        )

    def _has_api_keys(self) -> bool:
        return bool(self._dashscope_api_key and self._deepseek_api_key)

    def _prompt_for_missing_api_keys(self) -> bool:
        dialog = self._settings_dialog_factory(
            self._config,
            dashscope_api_key=self._dashscope_api_key,
            deepseek_api_key=self._deepseek_api_key,
            parent=self._app.window,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False

        new_config = dialog.current_config()
        api_keys = dialog.api_key_values()
        self._config = new_config
        self._dashscope_api_key = api_keys.dashscope_api_key
        self._deepseek_api_key = api_keys.deepseek_api_key
        self._app.window.apply_config(new_config.subtitle)

        if self._has_api_keys():
            return True

        self._present_message(
            "warning",
            "API keys required",
            (
                "Set DASHSCOPE_API_KEY and DEEPSEEK_API_KEY in the environment or .env file, "
                "or enter them in Settings for this run."
            ),
        )
        return False

    def _start_hotkey(self) -> None:
        self._stop_hotkey()
        try:
            manager = self._hotkey_manager_factory(
                self._app.window,
                self._config.hotkey.toggle_hotkey,
            )
            manager.start()
        except HotkeyPermissionError as exc:
            logfire.warning("Global hotkey permission missing", error=str(exc))
            self._hotkey_manager = None
        except HotkeyRegistrationError as exc:
            self._present_message("warning", "Global shortcut unavailable", str(exc))
            self._hotkey_manager = None
        except Exception as exc:
            logfire.warning(
                "Unexpected global hotkey startup failure",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            self._present_message("warning", "Global shortcut unavailable", str(exc))
            self._hotkey_manager = None
        else:
            self._hotkey_manager = manager

    def _stop_hotkey(self) -> None:
        manager = self._hotkey_manager
        self._hotkey_manager = None
        if manager is not None:
            with contextlib.suppress(Exception):
                manager.stop()

    def _setup_tray(self) -> None:
        if self._tray_icon is not None or not self._tray_available():
            return

        parent = self._app.window
        menu = QMenu(parent)
        self._show_hide_action = QAction("Hide subtitles", menu)
        self._settings_action = QAction("Settings", menu)
        self._quit_action = QAction("Quit", menu)

        self._show_hide_action.triggered.connect(self._toggle_window_visibility)
        self._settings_action.triggered.connect(self.open_settings)
        self._quit_action.triggered.connect(self.request_quit)

        menu.addAction(self._show_hide_action)
        menu.addAction(self._settings_action)
        menu.addSeparator()
        menu.addAction(self._quit_action)

        icon = self._tray_icon_image()
        tray = QSystemTrayIcon(icon, parent)
        tray.setToolTip("RainyASR")
        tray.setContextMenu(menu)
        tray.activated.connect(self._handle_tray_activated)
        tray.show()

        self._tray_menu = menu
        self._tray_icon = tray

    def _hide_tray(self) -> None:
        if self._tray_icon is not None:
            self._tray_icon.hide()
        self._tray_icon = None
        self._tray_menu = None

    def _tray_icon_image(self) -> QIcon:
        icon = self._app.qapplication.windowIcon()
        if not icon.isNull():
            return icon
        return self._app.qapplication.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)

    def _toggle_window_visibility(self) -> None:
        window = self._app.window
        if window.isVisible():
            window.hide()
            if self._show_hide_action is not None:
                self._show_hide_action.setText("Show subtitles")
        else:
            window.show()
            if self._show_hide_action is not None:
                self._show_hide_action.setText("Hide subtitles")

    def _handle_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self._toggle_window_visibility()

    def _handle_worker_error(self, message: str) -> None:
        logfire.error("Subtitle worker error", error=message)
        if self._tray_icon is not None and self._tray_icon.isVisible():
            self._tray_icon.showMessage("RainyASR error", message)

    def _handle_worker_state_changed(self, state: str) -> None:
        if self._tray_icon is not None:
            self._tray_icon.setToolTip(f"RainyASR - {state}")

    def _present_message(self, level: str, title: str, message: str) -> None:
        self._message_presenter(self._app.window, level, title, message)

    @staticmethod
    def _default_message_presenter(
        parent: QWidget,
        level: str,
        title: str,
        message: str,
    ) -> None:
        if level == "critical":
            QMessageBox.critical(parent, title, message)
        else:
            QMessageBox.warning(parent, title, message)

    @staticmethod
    def _worker_restart_required(
        old_config: AppConfig,
        new_config: AppConfig,
        old_dashscope_api_key: str,
        old_deepseek_api_key: str,
        new_dashscope_api_key: str,
        new_deepseek_api_key: str,
    ) -> bool:
        return (
            old_config.audio != new_config.audio
            or old_config.asr != new_config.asr
            or old_config.language != new_config.language
            or old_dashscope_api_key != new_dashscope_api_key
            or old_deepseek_api_key != new_deepseek_api_key
        )

    @staticmethod
    def _audio_setup_hint(error_message: str) -> str:
        system = platform.system()
        if system == "Darwin":
            guidance = "Install and select a BlackHole loopback device, then restart RainyASR."
        elif system == "Windows":
            guidance = "Use a Windows WASAPI loopback-capable output device, then restart RainyASR."
        elif system == "Linux":
            guidance = (
                "Enable a PulseAudio/PipeWire monitor source and make sure PortAudio can see it."
            )
        else:
            guidance = "RainyASR needs a supported system loopback audio device."
        return f"{error_message}\n\n{guidance}"


def install_terminal_signal_handlers(
    controller: RainyASRController,
    *,
    poll_interval_ms: int = 200,
) -> QTimer:
    """Let Ctrl+C/SIGTERM request the same graceful shutdown as the tray menu."""

    def handle_signal(signum: int, _frame: object) -> None:
        logfire.info("Received terminal shutdown signal", signal=signum)
        controller.request_quit()

    for signal_name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, signal_name, None)
        if signum is None:
            continue
        with contextlib.suppress(ValueError):
            signal.signal(signum, handle_signal)

    signal_poll_timer = QTimer(controller)
    signal_poll_timer.setInterval(poll_interval_ms)
    signal_poll_timer.timeout.connect(lambda: None)
    signal_poll_timer.start()
    return signal_poll_timer


def main() -> int:
    """Run the RainyASR desktop application."""
    configure_logfire()
    config = AppConfig.load()
    app = RainyASRApp(config.subtitle, quit_on_window_close=False)
    controller = RainyASRController(app, config)
    signal_poll_timer = install_terminal_signal_handlers(controller)
    app.loop.create_task(controller.start())
    app.window.show()
    app.run()
    signal_poll_timer.stop()
    return 0


__all__ = [
    "RainyASRController",
    "configure_logfire",
    "install_terminal_signal_handlers",
    "main",
]
