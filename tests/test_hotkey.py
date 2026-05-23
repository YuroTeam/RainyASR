"""Tests for global hotkey management."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from rainyasr import hotkey as hotkey_module
from rainyasr.hotkey import (
    GlobalHotkeyManager,
    HotkeyPermissionError,
    HotkeyRegistrationError,
    format_pynput_hotkey,
    normalize_hotkey_text,
)


class FakeGlobalHotKeys:
    """In-memory replacement for pynput.keyboard.GlobalHotKeys."""

    instances: list[FakeGlobalHotKeys] = []

    def __init__(self, hotkeys: dict[str, Callable[[], None]]) -> None:
        self.hotkeys = hotkeys
        self.started = 0
        self.stopped = 0
        self.joined_timeout: float | None = None
        self.instances.append(self)

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def join(self, timeout: float | None = None) -> None:
        self.joined_timeout = timeout

    def trigger(self) -> None:
        next(iter(self.hotkeys.values()))()


@pytest.fixture(autouse=True)
def clear_fake_hotkeys() -> Iterator[None]:
    FakeGlobalHotKeys.instances.clear()
    yield
    FakeGlobalHotKeys.instances.clear()


@pytest.fixture
def window(qtbot) -> QWidget:
    widget = QWidget()
    qtbot.addWidget(widget)
    return widget


def test_normalize_hotkey_text() -> None:
    assert normalize_hotkey_text(" Ctrl + Alt + R ") == "ctrl+alt+r"


@pytest.mark.parametrize(
    ("hotkey", "expected"),
    [
        ("ctrl+shift+r", "<ctrl>+<shift>+r"),
        ("Ctrl+Alt+F8", "<ctrl>+<alt>+<f8>"),
        ("command+space", "<cmd>+<space>"),
        ("control+option+return", "<ctrl>+<alt>+<enter>"),
        ("win+pgup", "<cmd>+<page_up>"),
    ],
)
def test_format_pynput_hotkey(hotkey: str, expected: str) -> None:
    assert format_pynput_hotkey(hotkey) == expected


@pytest.mark.parametrize(
    "hotkey",
    [
        "r",
        "ctrl",
        "ctrl++r",
        "ctrl+control+r",
        "ctrl+audio",
        "ctrl+>",
        "ctrl+-",
        "ctrl+é",
    ],
)
def test_format_pynput_hotkey_rejects_invalid_values(hotkey: str) -> None:
    with pytest.raises(ValueError):
        format_pynput_hotkey(hotkey)


def test_start_registers_normalized_hotkey_and_is_idempotent(window: QWidget) -> None:
    manager = GlobalHotkeyManager(
        window,
        " Ctrl + Shift + R ",
        hotkeys_factory=FakeGlobalHotKeys,
        accessibility_checker=lambda: True,
    )

    manager.start()
    manager.start()

    assert manager.registered_hotkey == "ctrl+shift+r"
    assert manager.pynput_hotkey == "<ctrl>+<shift>+r"
    assert manager.is_running
    assert len(FakeGlobalHotKeys.instances) == 1
    listener = FakeGlobalHotKeys.instances[0]
    assert list(listener.hotkeys) == ["<ctrl>+<shift>+r"]
    assert listener.started == 1


def test_stop_unregisters_listener_and_is_idempotent(window: QWidget) -> None:
    manager = GlobalHotkeyManager(
        window,
        "ctrl+shift+r",
        hotkeys_factory=FakeGlobalHotKeys,
        accessibility_checker=lambda: True,
    )
    manager.start()
    listener = FakeGlobalHotKeys.instances[0]

    manager.stop()
    manager.stop()

    assert not manager.is_running
    assert listener.stopped == 1
    assert listener.joined_timeout == 1.0


def test_stop_allows_listener_without_join(window: QWidget) -> None:
    class NoJoinGlobalHotKeys:
        def __init__(self, hotkeys: dict[str, Callable[[], None]]) -> None:
            self.started = 0
            self.stopped = 0

        def start(self) -> None:
            self.started += 1

        def stop(self) -> None:
            self.stopped += 1

    listener = NoJoinGlobalHotKeys({})

    def factory(hotkeys: dict[str, Callable[[], None]]) -> NoJoinGlobalHotKeys:
        return listener

    manager = GlobalHotkeyManager(
        window,
        "ctrl+shift+r",
        hotkeys_factory=factory,
        accessibility_checker=lambda: True,
    )

    manager.start()
    manager.stop()

    assert listener.started == 1
    assert listener.stopped == 1
    assert not manager.is_running


def test_hotkey_callback_queues_qt_visibility_toggle(
    window: QWidget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = GlobalHotkeyManager(
        window,
        "ctrl+shift+r",
        hotkeys_factory=FakeGlobalHotKeys,
        accessibility_checker=lambda: True,
    )
    calls = []

    class FakeMetaObject:
        @staticmethod
        def invokeMethod(obj, method: str, connection: Qt.ConnectionType) -> bool:  # noqa: N802
            calls.append((obj, method, connection))
            return True

    monkeypatch.setattr(hotkey_module, "QMetaObject", FakeMetaObject)

    manager._request_toggle()

    assert calls == [
        (
            manager,
            "_toggle_window_visibility",
            Qt.ConnectionType.QueuedConnection,
        )
    ]


def test_hotkey_callback_toggles_window_visibility(qtbot, window: QWidget) -> None:
    manager = GlobalHotkeyManager(
        window,
        "ctrl+shift+r",
        hotkeys_factory=FakeGlobalHotKeys,
        accessibility_checker=lambda: True,
    )
    manager.start()
    listener = FakeGlobalHotKeys.instances[0]

    window.show()
    qtbot.waitUntil(window.isVisible)

    listener.trigger()
    qtbot.waitUntil(lambda: not window.isVisible())

    listener.trigger()
    qtbot.waitUntil(window.isVisible)


def test_start_prompts_and_fails_when_macos_accessibility_is_missing(
    window: QWidget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr(hotkey_module.sys, "platform", "darwin")
    manager = GlobalHotkeyManager(
        window,
        "ctrl+shift+r",
        hotkeys_factory=FakeGlobalHotKeys,
        accessibility_checker=lambda: False,
        permission_notifier=prompts.append,
    )

    with pytest.raises(HotkeyPermissionError):
        manager.start()

    assert prompts == [hotkey_module.MACOS_ACCESSIBILITY_PERMISSION_MESSAGE]
    assert FakeGlobalHotKeys.instances == []


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_start_skips_macos_permission_check_on_linux_and_windows(
    platform: str,
    window: QWidget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hotkey_module.sys, "platform", platform)

    def fail_if_checked() -> bool:
        raise AssertionError("macOS accessibility check should not run")

    def fail_if_prompted(message: str) -> None:
        raise AssertionError(f"macOS permission prompt should not show: {message}")

    manager = GlobalHotkeyManager(
        window,
        "ctrl+shift+r",
        hotkeys_factory=FakeGlobalHotKeys,
        accessibility_checker=fail_if_checked,
        permission_notifier=fail_if_prompted,
    )

    manager.start()

    assert manager.is_running
    assert len(FakeGlobalHotKeys.instances) == 1
    assert FakeGlobalHotKeys.instances[0].started == 1


def test_default_macos_permission_prompt_uses_target_window_parent(
    window: QWidget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings = []
    monkeypatch.setattr(hotkey_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        hotkey_module.QMessageBox,
        "warning",
        lambda parent, title, message: warnings.append((parent, title, message)),
    )
    manager = GlobalHotkeyManager(
        window,
        "ctrl+shift+r",
        hotkeys_factory=FakeGlobalHotKeys,
        accessibility_checker=lambda: False,
    )

    with pytest.raises(HotkeyPermissionError):
        manager.start()

    assert warnings == [
        (
            window,
            "Accessibility permission required",
            hotkey_module.MACOS_ACCESSIBILITY_PERMISSION_MESSAGE,
        )
    ]


def test_macos_accessibility_check_uses_application_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_import_module(name: str) -> object:
        if name == "ApplicationServices":
            return SimpleNamespace(AXIsProcessTrusted=lambda: False)
        raise ImportError(f"{name} unavailable")

    monkeypatch.setattr(hotkey_module.sys, "platform", "darwin")
    monkeypatch.setattr(hotkey_module, "import_module", fake_import_module)

    assert hotkey_module.macos_accessibility_is_trusted() is False


def test_macos_accessibility_check_logs_when_api_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings = []

    def fake_import_module(name: str) -> object:
        if name == "ApplicationServices":
            return SimpleNamespace()
        raise ImportError(f"{name} unavailable")

    monkeypatch.setattr(hotkey_module.sys, "platform", "darwin")
    monkeypatch.setattr(hotkey_module, "import_module", fake_import_module)
    monkeypatch.setattr(hotkey_module.logfire, "warning", lambda message: warnings.append(message))

    assert hotkey_module.macos_accessibility_is_trusted() is True
    assert warnings == ["macOS accessibility API unavailable; skipping macOS accessibility check"]


def test_start_wraps_listener_value_errors(window: QWidget) -> None:
    def broken_factory(hotkeys: dict[str, Callable[[], None]]) -> FakeGlobalHotKeys:
        raise ValueError("backend rejected hotkey")

    manager = GlobalHotkeyManager(
        window,
        "ctrl+shift+r",
        hotkeys_factory=broken_factory,
        accessibility_checker=lambda: True,
    )

    with pytest.raises(HotkeyRegistrationError, match="Invalid global hotkey"):
        manager.start()


def test_start_wraps_listener_runtime_errors(window: QWidget) -> None:
    def broken_factory(hotkeys: dict[str, Callable[[], None]]) -> FakeGlobalHotKeys:
        raise RuntimeError("keyboard backend unavailable")

    manager = GlobalHotkeyManager(
        window,
        "ctrl+shift+r",
        hotkeys_factory=broken_factory,
        accessibility_checker=lambda: True,
    )

    with pytest.raises(HotkeyRegistrationError, match="Failed to register global hotkey"):
        manager.start()
