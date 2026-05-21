"""Tests for SubtitleWindow."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import Qt

from rainyasr.config import SubtitleConfig
from rainyasr.gui.subtitle_window import SubtitleWindow, configure_macos_overlay_app


@pytest.fixture
def window(qtbot):
    """Create a SubtitleWindow for testing."""
    w = SubtitleWindow()
    qtbot.addWidget(w)
    return w


class TestSubtitleWindow:
    def test_window_has_frameless_and_topmost_flags(self, window: SubtitleWindow) -> None:
        flags = window.windowFlags()
        assert flags & Qt.WindowType.FramelessWindowHint
        assert flags & Qt.WindowType.WindowStaysOnTopHint
        assert flags & Qt.WindowType.NoDropShadowWindowHint
        assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
        assert flags & Qt.WindowType.Tool
        assert window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

    def test_update_subtitle_sets_text(self, window: SubtitleWindow) -> None:
        window.update_subtitle("Hello", "你好")

        assert window._original_label.text() == "Hello"
        assert window._translated_label.text() == "你好"

    def test_bilingual_mode_shows_both_lines(self, window: SubtitleWindow) -> None:
        config = SubtitleConfig(bilingual_mode=True)
        window.apply_config(config)
        window.update_subtitle("Hello", "你好")

        assert not window._original_label.isHidden()
        assert not window._translated_label.isHidden()

    def test_monolingual_mode_hides_original(self, window: SubtitleWindow) -> None:
        config = SubtitleConfig(bilingual_mode=False)
        window.apply_config(config)
        window.update_subtitle("Hello", "你好")

        assert window._original_label.isHidden()
        assert not window._translated_label.isHidden()

    def test_apply_config_updates_font_size(self, window: SubtitleWindow) -> None:
        config = SubtitleConfig(font_size=36)
        window.apply_config(config)

        assert window._original_label.font().pointSize() == 36
        assert window._translated_label.font().pointSize() == 36

    def test_empty_text_hides_labels(self, window: SubtitleWindow) -> None:
        window.update_subtitle("", "")

        assert window._original_label.isHidden()
        assert window._translated_label.isHidden()

    def test_partial_status_dot_yellow(self, window: SubtitleWindow) -> None:
        window.update_subtitle("Hello", "你好", is_partial=True)

        assert not window._status_label.isHidden()
        assert "FBBF24" in window._status_label.styleSheet()

    def test_final_status_dot_green(self, window: SubtitleWindow) -> None:
        window.update_subtitle("Hello", "你好", is_partial=False)

        assert not window._status_label.isHidden()
        assert "22C55E" in window._status_label.styleSheet()

    def test_empty_text_hides_status_dot(self, window: SubtitleWindow) -> None:
        window.update_subtitle("", "")

        assert window._status_label.isHidden()

    def test_size_constraints(self, window: SubtitleWindow) -> None:
        assert window.minimumWidth() == 200
        assert window.maximumWidth() == 800

    @pytest.mark.skipif(sys.platform != "darwin", reason="Requires macOS NSWindow")
    def test_macos_window_joins_all_spaces_and_fullscreen(self, window: SubtitleWindow) -> None:
        import AppKit
        import objc

        window.show()
        window._apply_platform_overlay_behavior()

        ns_view = objc.objc_object(c_void_p=int(window.winId()))
        ns_window = ns_view.window()
        behavior = ns_window.collectionBehavior()

        assert not behavior & AppKit.NSWindowCollectionBehaviorMoveToActiveSpace
        assert behavior & AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
        assert behavior & AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        assert behavior & AppKit.NSWindowCollectionBehaviorStationary
        assert ns_window.styleMask() & AppKit.NSWindowStyleMaskNonactivatingPanel
        assert not ns_window.hidesOnDeactivate()
        assert ns_window.level() == AppKit.NSStatusWindowLevel

    @pytest.mark.skipif(sys.platform != "darwin", reason="Requires macOS NSApplication")
    def test_macos_overlay_app_uses_accessory_activation_policy(self) -> None:
        import AppKit

        original_policy = AppKit.NSApplication.sharedApplication().activationPolicy()

        configure_macos_overlay_app()

        assert (
            AppKit.NSApplication.sharedApplication().activationPolicy()
            == AppKit.NSApplicationActivationPolicyAccessory
        )
        AppKit.NSApplication.sharedApplication().setActivationPolicy_(original_policy)
