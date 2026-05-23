"""Global hotkey management for RainyASR."""

from __future__ import annotations

import sys
from collections.abc import Callable
from importlib import import_module
from typing import Protocol

import logfire
from PySide6.QtCore import QMetaObject, QObject, Qt, Slot
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

HotkeyCallback = Callable[[], None]


class HotkeyListener(Protocol):
    """Minimal protocol implemented by pynput global hotkey listeners."""

    def start(self) -> object:
        """Start listening for global keyboard events."""

    def stop(self) -> object:
        """Stop listening for global keyboard events."""


HotkeysFactory = Callable[[dict[str, HotkeyCallback]], HotkeyListener]
PermissionNotifier = Callable[[str], None]
AccessibilityChecker = Callable[[], bool]

MACOS_ACCESSIBILITY_PERMISSION_MESSAGE = (
    "Global hotkeys on macOS require Accessibility permission. Enable the app or the "
    "terminal running RainyASR in System Settings > Privacy & Security > Accessibility, "
    "then start hotkeys again."
)

_MODIFIER_ALIASES = {
    "alt": "alt",
    "cmd": "cmd",
    "command": "cmd",
    "control": "ctrl",
    "ctrl": "ctrl",
    "meta": "cmd",
    "option": "alt",
    "shift": "shift",
    "super": "cmd",
    "win": "cmd",
    "windows": "cmd",
}
_MODIFIER_TOKENS = frozenset(f"<{name}>" for name in {"alt", "cmd", "ctrl", "shift"})
_NAMED_KEY_ALIASES = {
    "backspace": "backspace",
    "del": "delete",
    "delete": "delete",
    "down": "down",
    "end": "end",
    "enter": "enter",
    "esc": "esc",
    "escape": "esc",
    "home": "home",
    "left": "left",
    "pagedown": "page_down",
    "pageup": "page_up",
    "pgdn": "page_down",
    "pgup": "page_up",
    "return": "enter",
    "right": "right",
    "space": "space",
    "tab": "tab",
    "up": "up",
}


class HotkeyRegistrationError(RuntimeError):
    """Raised when a global hotkey listener cannot be registered."""


class HotkeyPermissionError(HotkeyRegistrationError):
    """Raised when the platform requires permissions before hotkeys can run."""


class GlobalHotkeyManager(QObject):
    """Register a global shortcut and toggle a Qt subtitle window safely."""

    def __init__(
        self,
        target_window: QWidget,
        hotkey: str,
        *,
        hotkeys_factory: HotkeysFactory | None = None,
        accessibility_checker: AccessibilityChecker | None = None,
        permission_notifier: PermissionNotifier | None = None,
    ) -> None:
        super().__init__(target_window)
        self._target_window = target_window
        self._registered_hotkey = normalize_hotkey_text(hotkey)
        self._pynput_hotkey = format_pynput_hotkey(self._registered_hotkey)
        self._hotkeys_factory = hotkeys_factory or _pynput_hotkeys_factory
        self._accessibility_checker = accessibility_checker or macos_accessibility_is_trusted
        self._permission_notifier = permission_notifier or (
            lambda message: show_macos_accessibility_prompt(message, self._target_window)
        )
        self._listener: HotkeyListener | None = None

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop)

    @property
    def registered_hotkey(self) -> str:
        """The normalized config hotkey, for example ``ctrl+shift+r``."""
        return self._registered_hotkey

    @property
    def pynput_hotkey(self) -> str:
        """The pynput hotkey string, for example ``<ctrl>+<shift>+r``."""
        return self._pynput_hotkey

    @property
    def is_running(self) -> bool:
        """Return whether the underlying listener has been started."""
        return self._listener is not None

    def start(self) -> None:
        """Start the global hotkey listener."""
        if self._listener is not None:
            return

        if sys.platform == "darwin" and not self._accessibility_checker():
            self._permission_notifier(MACOS_ACCESSIBILITY_PERMISSION_MESSAGE)
            raise HotkeyPermissionError(MACOS_ACCESSIBILITY_PERMISSION_MESSAGE)

        try:
            listener = self._hotkeys_factory({self._pynput_hotkey: self._request_toggle})
            listener.start()
        except ValueError as exc:
            msg = f"Invalid global hotkey {self._registered_hotkey!r}: {exc}"
            raise HotkeyRegistrationError(msg) from exc
        except Exception as exc:
            msg = f"Failed to register global hotkey {self._registered_hotkey!r}: {exc}"
            raise HotkeyRegistrationError(msg) from exc

        self._listener = listener

    @Slot()
    def stop(self) -> None:
        """Stop the global hotkey listener. Safe to call more than once."""
        listener = self._listener
        if listener is None:
            return

        self._listener = None
        listener.stop()
        join = getattr(listener, "join", None)
        if callable(join):
            # pynput join() has no timeout status; shutdown remains best-effort.
            join(timeout=1.0)

    def _request_toggle(self) -> None:
        """Queue the actual GUI toggle onto the Qt event loop."""
        QMetaObject.invokeMethod(
            self,
            "_toggle_window_visibility",
            Qt.ConnectionType.QueuedConnection,
        )

    @Slot()
    def _toggle_window_visibility(self) -> None:
        """Show or hide the target window from the Qt GUI thread."""
        if self._target_window.isVisible():
            self._target_window.hide()
        else:
            self._target_window.show()


def normalize_hotkey_text(hotkey: str) -> str:
    """Normalize a config hotkey into lowercase ``+`` separated parts."""
    parts = _split_hotkey(hotkey)
    return "+".join(parts)


def format_pynput_hotkey(hotkey: str) -> str:
    """Convert a config hotkey into the string format expected by pynput."""
    parts = _split_hotkey(hotkey)
    tokens = [_pynput_token(part) for part in parts]

    if len(tokens) != len(set(tokens)):
        raise ValueError("Hotkey contains duplicate keys.")
    if not any(token in _MODIFIER_TOKENS for token in tokens):
        raise ValueError("Hotkey must include at least one modifier.")
    if not any(token not in _MODIFIER_TOKENS for token in tokens):
        raise ValueError("Hotkey must include a non-modifier key.")

    return "+".join(tokens)


def macos_accessibility_is_trusted() -> bool:
    """Return whether macOS has granted keyboard accessibility permission."""
    if sys.platform != "darwin":
        return True

    for module_name in ("ApplicationServices", "Quartz"):
        try:
            module = import_module(module_name)
        except ImportError:
            continue

        checker = getattr(module, "AXIsProcessTrusted", None)
        if callable(checker):
            return bool(checker())

    logfire.warning("macOS accessibility API unavailable; skipping macOS accessibility check")
    return True


def show_macos_accessibility_prompt(
    message: str = MACOS_ACCESSIBILITY_PERMISSION_MESSAGE,
    parent: QWidget | None = None,
) -> None:
    """Show a user-facing macOS Accessibility permission hint."""
    QMessageBox.warning(parent, "Accessibility permission required", message)


def _split_hotkey(hotkey: str) -> list[str]:
    if not isinstance(hotkey, str):
        raise ValueError("Hotkey must be a string.")

    parts = [part.strip().lower() for part in hotkey.strip().split("+")]
    if not parts or any(not part for part in parts):
        raise ValueError("Hotkey segments cannot be empty.")
    return parts


def _pynput_token(part: str) -> str:
    if part in _MODIFIER_ALIASES:
        return f"<{_MODIFIER_ALIASES[part]}>"
    if part in _NAMED_KEY_ALIASES:
        return f"<{_NAMED_KEY_ALIASES[part]}>"
    if part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 20:
        return f"<{part}>"
    if len(part) == 1 and part.isascii() and part.isalnum():
        return part
    msg = f"Unsupported hotkey key {part!r}."
    raise ValueError(msg)


def _pynput_hotkeys_factory(hotkeys: dict[str, HotkeyCallback]) -> HotkeyListener:
    from pynput import keyboard

    return keyboard.GlobalHotKeys(hotkeys)


__all__ = [
    "GlobalHotkeyManager",
    "HotkeyPermissionError",
    "HotkeyRegistrationError",
    "format_pynput_hotkey",
    "macos_accessibility_is_trusted",
    "normalize_hotkey_text",
    "show_macos_accessibility_prompt",
]
