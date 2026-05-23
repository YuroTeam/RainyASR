"""Tests for SubtitleWindow."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication

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

    def test_source_only_update_preserves_previous_translation(
        self, window: SubtitleWindow
    ) -> None:
        window.update_subtitle("Hello", "你好")

        window.update_subtitle("Hello wor", "", is_partial=True)

        assert window._original_label.text() == "Hello wor"
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

    def test_apply_config_keeps_hover_controls_visible(self, window: SubtitleWindow) -> None:
        window.update_subtitle("Hello", "你好")
        window.show()
        window._set_controls_visible(True)

        window.apply_config(SubtitleConfig(font_size=36))

        assert not window._settings_button.isHidden()
        assert not window._close_button.isHidden()

    def test_cursor_sync_restores_controls_when_hover_event_is_missed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        window: SubtitleWindow,
    ) -> None:
        window.update_subtitle("Hello", "你好")
        window.show()
        window._set_controls_visible(False)
        monkeypatch.setattr(window, "_mouse_is_inside_window", lambda: True)

        window._sync_controls_with_cursor()

        assert not window._settings_button.isHidden()
        assert not window._close_button.isHidden()

    def test_apply_config_uses_font_fallbacks(self, window: SubtitleWindow) -> None:
        config = SubtitleConfig(font_family="Inter, Arial, sans-serif")
        window.apply_config(config)

        assert window._original_label.font().families() == ["Inter", "Arial", "sans-serif"]
        assert window._translated_label.font().families() == ["Inter", "Arial", "sans-serif"]

    def test_apply_config_reuses_shadow_effects(self, window: SubtitleWindow) -> None:
        original_shadow = window._original_shadow
        translated_shadow = window._translated_shadow

        window.apply_config(SubtitleConfig(font_size=36))

        assert window._original_shadow is original_shadow
        assert window._translated_shadow is translated_shadow
        assert window._original_label.graphicsEffect() is original_shadow
        assert window._translated_label.graphicsEffect() is translated_shadow

    def test_apply_config_adjusts_window_size(self, window: SubtitleWindow) -> None:
        window.update_subtitle("Hello world", "你好世界")
        before = window.size()

        window.apply_config(SubtitleConfig(font_size=72))

        assert window.size().height() > before.height()

    def test_empty_text_hides_labels(self, window: SubtitleWindow) -> None:
        window.update_subtitle("", "")

        assert window._original_label.isHidden()
        assert window._translated_label.isHidden()

    def test_empty_text_hides_visible_window(self, window: SubtitleWindow) -> None:
        window.update_subtitle("Hello", "你好")
        window.show()
        window._set_controls_visible(True)

        window.update_subtitle("", "")

        assert not window.isVisible()
        assert window._close_button.isHidden()

    def test_text_restores_window_hidden_by_empty_text(self, window: SubtitleWindow) -> None:
        window.update_subtitle("Hello", "你好")
        window.show()
        window.update_subtitle("", "")

        window.update_subtitle("Hello again", "再次你好")

        assert window.isVisible()

    def test_update_subtitle_does_not_show_never_shown_window(self, window: SubtitleWindow) -> None:
        window.update_subtitle("Hello", "你好")

        assert not window.isVisible()

    def test_close_button_starts_hidden(self, window: SubtitleWindow) -> None:
        window.update_subtitle("Hello", "你好")

        assert window._close_button.isHidden()
        assert window._settings_button.isHidden()

    def test_control_buttons_show_only_when_controls_visible(self, window: SubtitleWindow) -> None:
        window.update_subtitle("Hello", "你好")

        window._set_controls_visible(True)

        assert not window._close_button.isHidden()
        assert not window._settings_button.isHidden()

    def test_control_buttons_are_icon_only_and_same_size(self, window: SubtitleWindow) -> None:
        assert window._settings_button.text() == ""
        assert window._close_button.text() == ""
        assert window._settings_button.size() == window._close_button.size()

    def test_hovering_label_shows_control_buttons(self, window: SubtitleWindow) -> None:
        window.update_subtitle("Hello", "你好")

        QApplication.sendEvent(window._original_label, QEvent(QEvent.Type.Enter))

        assert not window._close_button.isHidden()
        assert not window._settings_button.isHidden()

    def test_control_buttons_remain_hidden_when_monolingual_mode_has_no_visible_text(
        self, window: SubtitleWindow
    ) -> None:
        window.apply_config(SubtitleConfig(bilingual_mode=False))

        window.update_subtitle("partial source only", "", is_partial=True)
        window._set_controls_visible(True)

        assert window._original_label.isHidden()
        assert window._translated_label.isHidden()
        assert window._close_button.isHidden()
        assert window._settings_button.isHidden()

    def test_control_buttons_are_positioned_top_right(self, window: SubtitleWindow) -> None:
        window.update_subtitle("Hello", "你好", is_partial=True)

        assert window._close_button.x() == window.width() - window._close_button.width() - 8
        assert window._close_button.y() == 8
        assert window._settings_button.x() == window._close_button.x() - 30
        assert window._settings_button.y() == 8

    def test_settings_button_click_emits_signal(self, qtbot, window: SubtitleWindow) -> None:
        window.update_subtitle("Hello", "你好")
        window.show()
        window._set_controls_visible(True)

        settings_requested = []
        window.settings_requested.connect(lambda: settings_requested.append(True))

        qtbot.mouseClick(window._settings_button, Qt.MouseButton.LeftButton)

        assert settings_requested == [True]

    def test_close_button_click_closes_window_and_emits_signal(
        self, qtbot, window: SubtitleWindow
    ) -> None:
        window.update_subtitle("Hello", "你好")
        window.show()
        window._set_controls_visible(True)

        close_requested = []
        closed = []
        window.close_requested.connect(lambda: close_requested.append(True))
        window.closed.connect(lambda: closed.append(True))

        qtbot.mouseClick(window._close_button, Qt.MouseButton.LeftButton)

        assert close_requested == [True]
        assert closed == [True]
        assert not window.isVisible()

    def test_programmatic_close_emits_closed_without_close_requested(
        self, window: SubtitleWindow
    ) -> None:
        window.update_subtitle("Hello", "你好")
        window.show()

        close_requested = []
        closed = []
        window.close_requested.connect(lambda: close_requested.append(True))
        window.closed.connect(lambda: closed.append(True))

        window.close()

        assert close_requested == []
        assert closed == [True]
        assert not window.isVisible()

    def test_size_constraints(self, window: SubtitleWindow) -> None:
        assert window.minimumWidth() == 1000
        assert window.maximumWidth() == 1000
        assert window.minimumHeight() == 120

    def test_apply_config_updates_fixed_width(self, window: SubtitleWindow) -> None:
        window.apply_config(SubtitleConfig(window_width=1280))

        assert window.width() == 1280
        assert window.minimumWidth() == 1280
        assert window.maximumWidth() == 1280

    def test_text_updates_do_not_change_fixed_width(self, window: SubtitleWindow) -> None:
        window.apply_config(SubtitleConfig(window_width=900))
        before = window.width()

        window.update_subtitle("short", "短")
        short_width = window.width()
        window.update_subtitle("This is a much longer subtitle that should wrap.", "长译文")

        assert before == 900
        assert short_width == 900
        assert window.width() == 900

    def test_long_text_grows_vertically_only(self, window: SubtitleWindow) -> None:
        window.apply_config(SubtitleConfig(window_width=500))
        window.update_subtitle("short", "短")
        short_size = window.size()

        long_text = " ".join(["This subtitle should wrap inside the fixed width"] * 8)
        window.update_subtitle(long_text, " ".join(["这段译文应该换行"] * 8))

        assert window.width() == 500
        assert window.height() > short_size.height()

    def test_label_height_grows_one_line_at_a_time(self, window: SubtitleWindow) -> None:
        window.apply_config(SubtitleConfig(window_width=500, bilingual_mode=False))
        label = window._translated_label
        line_height = label.fontMetrics().lineSpacing()

        one_line_text = "short subtitle"
        two_line_text = self._text_that_wraps_to_lines(window, label, 2)
        three_line_text = self._text_that_wraps_to_lines(window, label, 3)

        one_line_height = window._label_height_for_text(label, one_line_text)
        two_line_height = window._label_height_for_text(label, two_line_text)
        three_line_height = window._label_height_for_text(label, three_line_text)

        assert two_line_height == one_line_height + line_height
        assert three_line_height == one_line_height + line_height * 2

    @staticmethod
    def _text_that_wraps_to_lines(window: SubtitleWindow, label, lines: int) -> str:
        words: list[str] = []
        for _ in range(200):
            words.append("wrapped")
            text = " ".join(words)
            if window._wrapped_line_count(label, text) == lines:
                return text
        pytest.fail(f"Could not build {lines}-line subtitle text")

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

        app = AppKit.NSApplication.sharedApplication()
        original_policy = app.activationPolicy()

        try:
            configure_macos_overlay_app()

            assert app.activationPolicy() == AppKit.NSApplicationActivationPolicyAccessory
        finally:
            app.setActivationPolicy_(original_policy)
