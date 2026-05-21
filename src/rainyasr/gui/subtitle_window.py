"""Borderless, always-on-top subtitle overlay window.

Design: Glassmorphism + OLED Dark
- Translucent dark background with subtle border
- Text shadow for readability against any background
- Hover close control for borderless-window exit
"""

from __future__ import annotations

import sys
from typing import override

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QEnterEvent, QFont, QMouseEvent, QResizeEvent
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QLabel, QPushButton, QVBoxLayout, QWidget

from rainyasr.config import SubtitleConfig

CONTROL_MARGIN = 8
CLOSE_BUTTON_SIZE = 24


def configure_macos_overlay_app() -> None:
    """Make the current macOS process behave like an overlay accessory app."""
    if sys.platform != "darwin":
        return

    try:
        import AppKit
    except ImportError:
        return

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)


class SubtitleWindow(QWidget):
    """A frameless, top-most subtitle window with drag-to-move support.

    Displays original text and translated text on separate lines.
    Appearance is controlled via :class:`~rainyasr.config.SubtitleConfig`.

    Design features:
        - Glassmorphism: translucent background + subtle border
        - Text shadow for readability on any video/game background
        - Hover close button for borderless-window exit
    """

    close_requested = Signal()

    def __init__(self, config: SubtitleConfig | None = None) -> None:
        super().__init__()
        self._config = config or SubtitleConfig()
        self._drag_pos: QPoint | None = None
        self._hidden_for_empty_subtitle = False

        self._setup_window()
        self._setup_labels()
        self._setup_close_button()
        self._apply_style()

    # -- Window setup ------------------------------------------------------

    def _setup_window(self) -> None:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.Tool
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # Constrain size to reasonable bounds
        self.setMinimumWidth(200)
        self.setMaximumWidth(800)

        self.setMouseTracking(True)
        self._apply_platform_overlay_behavior()

    def _apply_platform_overlay_behavior(self) -> None:
        """Apply platform-specific flags needed for true overlay behavior."""
        if sys.platform != "darwin":
            return

        self._apply_macos_overlay_behavior()

    def _apply_macos_overlay_behavior(self) -> None:
        """Allow the subtitle panel to appear over macOS full-screen Spaces."""
        try:
            import AppKit
            import objc
        except ImportError:
            return

        ns_view = objc.objc_object(c_void_p=int(self.winId()))
        ns_window = ns_view.window()
        if ns_window is None:
            return

        behavior = ns_window.collectionBehavior()
        behavior &= ~AppKit.NSWindowCollectionBehaviorMoveToActiveSpace
        behavior |= AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
        behavior |= AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        behavior |= AppKit.NSWindowCollectionBehaviorStationary
        ns_window.setCollectionBehavior_(behavior)
        ns_window.setHidesOnDeactivate_(False)
        ns_window.setStyleMask_(ns_window.styleMask() | AppKit.NSWindowStyleMaskNonactivatingPanel)
        ns_window.setLevel_(AppKit.NSStatusWindowLevel)

    def _setup_labels(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(6)

        self._original_label = QLabel(self)
        self._original_label.setObjectName("subtitleOriginalLabel")
        self._original_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._original_label.setWordWrap(True)
        layout.addWidget(self._original_label)

        self._translated_label = QLabel(self)
        self._translated_label.setObjectName("subtitleTranslatedLabel")
        self._translated_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._translated_label.setWordWrap(True)
        layout.addWidget(self._translated_label)

        self._update_label_visibility()

    def _setup_close_button(self) -> None:
        """Small hover-revealed close control for frameless windows."""
        self._close_button = QPushButton("×", self)
        self._close_button.setObjectName("subtitleCloseButton")
        self._close_button.setAccessibleName("Close subtitle window")
        self._close_button.setToolTip("Close")
        self._close_button.setFixedSize(CLOSE_BUTTON_SIZE, CLOSE_BUTTON_SIZE)
        self._close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._close_button.clicked.connect(self._request_close)
        self._close_button.hide()
        self._position_close_button()

    # -- Styling -----------------------------------------------------------

    def _apply_style(self) -> None:
        """Apply QSS based on current SubtitleConfig."""
        cfg = self._config

        # Font
        font = QFont()
        font.setFamily(cfg.font_family.split(",")[0].strip())
        font.setPointSize(cfg.font_size)
        font.setBold(True)

        self._original_label.setFont(font)
        self._translated_label.setFont(font)

        # Colors
        color = QColor(cfg.text_color).name()
        opacity = cfg.bg_opacity / 100.0

        # Glassmorphism: translucent black bg + subtle border
        bg_rgba = f"rgba(15, 23, 42, {opacity:.2f})"
        border_rgba = f"rgba(255, 255, 255, {min(opacity + 0.15, 0.35):.2f})"

        style = f"""
            QLabel#subtitleOriginalLabel,
            QLabel#subtitleTranslatedLabel {{
                color: {color};
                background-color: {bg_rgba};
                border: 1px solid {border_rgba};
                border-radius: 12px;
                padding: 6px 14px;
            }}

            QPushButton#subtitleCloseButton {{
                color: rgba(255, 255, 255, 0.88);
                background-color: rgba(15, 23, 42, 0.76);
                border: 1px solid rgba(255, 255, 255, 0.28);
                border-radius: 12px;
                padding: 0;
                font-size: 16px;
                font-weight: 600;
            }}

            QPushButton#subtitleCloseButton:hover {{
                color: #FFFFFF;
                background-color: rgba(220, 38, 38, 0.92);
                border-color: rgba(255, 255, 255, 0.46);
            }}

            QPushButton#subtitleCloseButton:pressed {{
                background-color: rgba(153, 27, 27, 0.96);
            }}
        """
        self.setStyleSheet(style)

        # Text shadow effect for readability on any video/game background
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(8)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(1, 1)
        self._original_label.setGraphicsEffect(shadow)

        shadow2 = QGraphicsDropShadowEffect(self)
        shadow2.setBlurRadius(8)
        shadow2.setColor(QColor(0, 0, 0, 180))
        shadow2.setOffset(1, 1)
        self._translated_label.setGraphicsEffect(shadow2)

    # -- Public API --------------------------------------------------------

    def update_subtitle(
        self,
        original: str,
        translated: str,
        *,
        is_partial: bool = False,
    ) -> None:
        """Update the displayed subtitle text.

        Args:
            original: Source-language text (hidden when bilingual_mode is False).
            translated: Target-language text.
            is_partial: Transcript state accepted for API compatibility.
        """
        self._original_label.setText(original.strip())
        self._translated_label.setText(translated.strip())
        self._update_label_visibility()

        self.adjustSize()
        self._position_close_button()
        self._sync_window_visibility()

    def apply_config(self, config: SubtitleConfig) -> None:
        """Re-apply appearance settings from a new config object."""
        self._config = config
        self._apply_style()
        self._update_label_visibility()
        self.adjustSize()
        self._position_close_button()
        self._sync_window_visibility()

    # -- Internal helpers --------------------------------------------------

    def _update_label_visibility(self) -> None:
        """Show/hide labels based on bilingual mode and content."""
        if self._config.bilingual_mode:
            self._original_label.setHidden(not self._original_label.text())
            self._translated_label.setHidden(not self._translated_label.text())
        else:
            self._original_label.hide()
            self._translated_label.setHidden(not self._translated_label.text())

    def _has_visible_subtitle_text(self) -> bool:
        """Return whether current text should visibly render as a subtitle."""
        if self._config.bilingual_mode:
            return bool(self._original_label.text() or self._translated_label.text())
        return bool(self._translated_label.text())

    def _sync_window_visibility(self) -> None:
        """Hide only empty subtitles, and restore only windows hidden for that reason."""
        if self._has_visible_subtitle_text():
            if self._hidden_for_empty_subtitle:
                self.show()
            self._hidden_for_empty_subtitle = False
            return

        if self.isVisible() or self._hidden_for_empty_subtitle:
            self.hide()
            self._hidden_for_empty_subtitle = True
            self._set_controls_visible(False)

    def _position_close_button(self) -> None:
        """Keep the close control anchored to the top-right corner."""
        if not hasattr(self, "_close_button"):
            return

        self._close_button.move(
            max(CONTROL_MARGIN, self.width() - CLOSE_BUTTON_SIZE - CONTROL_MARGIN),
            CONTROL_MARGIN,
        )

    def _set_controls_visible(self, visible: bool) -> None:
        """Show overlay controls only when useful and non-empty."""
        self._close_button.setVisible(visible and self._has_visible_subtitle_text())

    def _request_close(self) -> None:
        """Emit a close request before closing this borderless window."""
        self.close_requested.emit()
        self.close()

    # -- Drag to move ------------------------------------------------------

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        self._position_close_button()
        super().resizeEvent(event)

    @override
    def enterEvent(self, event: QEnterEvent) -> None:
        self._set_controls_visible(True)
        super().enterEvent(event)

    @override
    def leaveEvent(self, event: QEvent) -> None:
        self._set_controls_visible(False)
        super().leaveEvent(event)

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    @override
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_pos is not None and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = None
            event.accept()
