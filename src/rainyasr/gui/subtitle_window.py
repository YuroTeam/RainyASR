"""Borderless, always-on-top subtitle overlay window.

Design: Glassmorphism + OLED Dark
- Translucent dark background with subtle border
- Text shadow for readability against any background
- Hover close control for borderless-window exit
"""

from __future__ import annotations

import sys
from math import cos, pi, sin
from typing import override

from PySide6.QtCore import QEvent, QLineF, QObject, QPoint, QPointF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QCursor,
    QEnterEvent,
    QFont,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QResizeEvent,
)
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QLabel, QPushButton, QVBoxLayout, QWidget

from rainyasr.config import SubtitleConfig

CONTROL_MARGIN = 8
CONTROL_GAP = 6
CLOSE_BUTTON_SIZE = 24
LAYOUT_MARGIN_X = 20
LAYOUT_MARGIN_Y = 12
LAYOUT_SPACING = 6
LABEL_PADDING_X = 14
LABEL_PADDING_Y = 6
LABEL_BORDER_WIDTH = 1
SUBTITLE_WINDOW_MIN_HEIGHT = 120


class _OverlayControlButton(QPushButton):
    """Icon-only control button drawn consistently across platforms."""

    def __init__(self, icon_name: str, parent: QWidget) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        self.setText("")

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        icon_color = QColor(255, 255, 255, 255 if self.underMouse() or self.isDown() else 224)
        pen = QPen(icon_color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if self._icon_name == "settings":
            self._paint_settings_icon(painter)
        else:
            self._paint_close_icon(painter)
        painter.end()

    def _paint_close_icon(self, painter: QPainter) -> None:
        center = QPointF(self.width() / 2, self.height() / 2)
        radius = 5.7
        painter.drawLine(
            QLineF(
                center.x() - radius,
                center.y() - radius,
                center.x() + radius,
                center.y() + radius,
            )
        )
        painter.drawLine(
            QLineF(
                center.x() + radius,
                center.y() - radius,
                center.x() - radius,
                center.y() + radius,
            )
        )

    def _paint_settings_icon(self, painter: QPainter) -> None:
        center = QPointF(self.width() / 2, self.height() / 2)
        for step in range(8):
            angle = step * pi / 4
            painter.drawLine(
                QLineF(
                    center.x() + cos(angle) * 6.0,
                    center.y() + sin(angle) * 6.0,
                    center.x() + cos(angle) * 7.7,
                    center.y() + sin(angle) * 7.7,
                )
            )
        painter.drawEllipse(center, 5.0, 5.0)
        painter.drawEllipse(center, 1.8, 1.8)


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
    settings_requested = Signal()
    closed = Signal()

    def __init__(self, config: SubtitleConfig | None = None) -> None:
        super().__init__()
        self._config = config or SubtitleConfig()
        self._drag_pos: QPoint | None = None
        self._hidden_for_empty_subtitle = False
        self._hover_sync_timer = QTimer(self)

        self._setup_window()
        self._setup_labels()
        self._setup_control_buttons()
        self._install_hover_event_filters()
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

        self._apply_window_size_constraints()

        self.setMouseTracking(True)
        self._apply_platform_overlay_behavior()
        self._setup_hover_sync_timer()

    def _setup_hover_sync_timer(self) -> None:
        """Recover hover controls when platform enter/leave events are missed."""
        self._hover_sync_timer.setInterval(100)
        self._hover_sync_timer.timeout.connect(self._sync_controls_with_cursor)
        self._hover_sync_timer.start()

    def _apply_window_size_constraints(self) -> None:
        """Keep the overlay width stable while allowing vertical growth."""
        width = self._config.window_width
        self.setFixedWidth(width)
        self.setMinimumHeight(SUBTITLE_WINDOW_MIN_HEIGHT)
        if self.height() < SUBTITLE_WINDOW_MIN_HEIGHT:
            self.resize(width, SUBTITLE_WINDOW_MIN_HEIGHT)

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
        self._subtitle_layout = QVBoxLayout(self)
        self._subtitle_layout.setContentsMargins(
            LAYOUT_MARGIN_X,
            LAYOUT_MARGIN_Y,
            LAYOUT_MARGIN_X,
            LAYOUT_MARGIN_Y,
        )
        self._subtitle_layout.setSpacing(LAYOUT_SPACING)

        self._original_label = QLabel(self)
        self._original_label.setObjectName("subtitleOriginalLabel")
        self._original_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._original_label.setWordWrap(True)
        self._subtitle_layout.addWidget(self._original_label)

        self._translated_label = QLabel(self)
        self._translated_label.setObjectName("subtitleTranslatedLabel")
        self._translated_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._translated_label.setWordWrap(True)
        self._subtitle_layout.addWidget(self._translated_label)

        self._original_shadow = QGraphicsDropShadowEffect(self._original_label)
        self._translated_shadow = QGraphicsDropShadowEffect(self._translated_label)
        self._original_label.setGraphicsEffect(self._original_shadow)
        self._translated_label.setGraphicsEffect(self._translated_shadow)

        self._update_label_visibility()
        self._refresh_subtitle_geometry()

    def _setup_control_buttons(self) -> None:
        """Small hover-revealed controls for frameless windows."""
        self._settings_button = _OverlayControlButton("settings", self)
        self._settings_button.setObjectName("subtitleSettingsButton")
        self._settings_button.setAccessibleName("Open settings")
        self._settings_button.setToolTip("Settings")
        self._settings_button.setFixedSize(CLOSE_BUTTON_SIZE, CLOSE_BUTTON_SIZE)
        self._settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._settings_button.clicked.connect(self._request_settings)
        self._settings_button.hide()

        self._close_button = _OverlayControlButton("close", self)
        self._close_button.setObjectName("subtitleCloseButton")
        self._close_button.setAccessibleName("Close subtitle window")
        self._close_button.setToolTip("Close")
        self._close_button.setFixedSize(CLOSE_BUTTON_SIZE, CLOSE_BUTTON_SIZE)
        self._close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._close_button.clicked.connect(self._request_close)
        self._close_button.hide()
        self._position_control_buttons()

    def _install_hover_event_filters(self) -> None:
        """Track hover over the label children as well as the parent widget."""
        for widget in (
            self,
            self._original_label,
            self._translated_label,
            self._settings_button,
            self._close_button,
        ):
            widget.setMouseTracking(True)
            widget.installEventFilter(self)

    # -- Styling -----------------------------------------------------------

    def _apply_style(self) -> None:
        """Apply QSS based on current SubtitleConfig."""
        cfg = self._config

        # Font
        font = QFont()
        font_families = [family.strip() for family in cfg.font_family.split(",") if family.strip()]
        if font_families:
            font.setFamilies(font_families)
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

            QPushButton#subtitleSettingsButton,
            QPushButton#subtitleCloseButton {{
                color: rgba(255, 255, 255, 0.88);
                background-color: rgba(15, 23, 42, 0.76);
                border: 1px solid rgba(255, 255, 255, 0.28);
                border-radius: 12px;
                padding: 0;
                font-size: 16px;
                font-weight: 600;
            }}

            QPushButton#subtitleSettingsButton:hover {{
                color: #FFFFFF;
                background-color: rgba(37, 99, 235, 0.92);
                border-color: rgba(255, 255, 255, 0.46);
            }}

            QPushButton#subtitleSettingsButton:pressed {{
                background-color: rgba(30, 64, 175, 0.96);
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
        for shadow in (self._original_shadow, self._translated_shadow):
            shadow.setBlurRadius(8)
            shadow.setColor(QColor(0, 0, 0, 180))
            shadow.setOffset(1, 1)

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
        original_text = original.strip()
        translated_text = translated.strip()
        self._original_label.setText(original_text)
        if translated_text or not original_text:
            self._translated_label.setText(translated_text)
        self._update_label_visibility()

        self._refresh_subtitle_geometry()
        self._position_control_buttons()
        self._sync_window_visibility()

    def apply_config(self, config: SubtitleConfig) -> None:
        """Re-apply appearance settings from a new config object."""
        controls_were_visible = (
            not self._close_button.isHidden() or not self._settings_button.isHidden()
        )
        self._config = config
        self._apply_window_size_constraints()
        self._apply_style()
        self._update_label_visibility()
        self._refresh_subtitle_geometry()
        self._position_control_buttons()
        self._sync_window_visibility()
        if self._has_visible_subtitle_text():
            self._set_controls_visible(controls_were_visible or self._mouse_is_inside_window())

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

    def _refresh_subtitle_geometry(self) -> None:
        """Resize subtitles by wrapped line count while keeping width fixed."""
        label_width = self._label_width()
        visible_labels = [
            label
            for label in (self._original_label, self._translated_label)
            if not label.isHidden()
        ]

        total_label_height = 0
        for label in visible_labels:
            label.setFixedWidth(label_width)
            label_height = self._label_height_for_text(label, label.text())
            label.setFixedHeight(label_height)
            total_label_height += label_height

        spacing = LAYOUT_SPACING * max(0, len(visible_labels) - 1)
        target_height = max(
            SUBTITLE_WINDOW_MIN_HEIGHT,
            LAYOUT_MARGIN_Y * 2 + total_label_height + spacing,
        )
        self.setFixedHeight(target_height)

    def _label_width(self) -> int:
        return max(1, self.width() - LAYOUT_MARGIN_X * 2)

    def _label_height_for_text(self, label: QLabel, text: str) -> int:
        line_height = label.fontMetrics().lineSpacing()
        line_count = self._wrapped_line_count(label, text)
        return line_count * line_height + 2 * (LABEL_PADDING_Y + LABEL_BORDER_WIDTH)

    def _wrapped_line_count(self, label: QLabel, text: str) -> int:
        text = text.strip() or " "
        text_width = max(
            1,
            self._label_width() - 2 * (LABEL_PADDING_X + LABEL_BORDER_WIDTH),
        )
        metrics = label.fontMetrics()
        flags = Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere
        bounds = metrics.boundingRect(QRect(0, 0, text_width, 100000), flags, text)
        return max(1, round(bounds.height() / max(1, metrics.lineSpacing())))

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

    def _position_control_buttons(self) -> None:
        """Keep overlay controls anchored to the top-right corner."""
        if not hasattr(self, "_close_button") or not hasattr(self, "_settings_button"):
            return

        close_x = max(CONTROL_MARGIN, self.width() - CLOSE_BUTTON_SIZE - CONTROL_MARGIN)
        settings_x = max(CONTROL_MARGIN, close_x - CLOSE_BUTTON_SIZE - CONTROL_GAP)
        self._settings_button.move(settings_x, CONTROL_MARGIN)
        self._close_button.move(close_x, CONTROL_MARGIN)
        self._raise_control_buttons()

    def _set_controls_visible(self, visible: bool) -> None:
        """Show overlay controls only when useful and non-empty."""
        should_show = visible and self._has_visible_subtitle_text()
        self._settings_button.setVisible(should_show)
        self._close_button.setVisible(should_show)
        if should_show:
            self._raise_control_buttons()

    def _raise_control_buttons(self) -> None:
        self._settings_button.raise_()
        self._close_button.raise_()

    def _hide_controls_if_mouse_left(self) -> None:
        if self._mouse_is_inside_window():
            return
        self._set_controls_visible(False)

    def _sync_controls_with_cursor(self) -> None:
        if not hasattr(self, "_close_button") or not hasattr(self, "_settings_button"):
            return
        if not self.isVisible():
            self._set_controls_visible(False)
            return
        self._set_controls_visible(self._mouse_is_inside_window())

    def _mouse_is_inside_window(self) -> bool:
        return self.rect().contains(self.mapFromGlobal(QCursor.pos()))

    def _request_settings(self) -> None:
        """Ask the application shell to open settings."""
        self.settings_requested.emit()

    def _request_close(self) -> None:
        """Emit a close request before closing this borderless window."""
        self.close_requested.emit()
        self.close()

    # -- Drag to move ------------------------------------------------------

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        self._set_controls_visible(False)
        self.closed.emit()
        super().closeEvent(event)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Enter:
            self._set_controls_visible(True)
        elif event.type() == QEvent.Type.Leave:
            QTimer.singleShot(0, self._hide_controls_if_mouse_left)
        return super().eventFilter(watched, event)

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        self._position_control_buttons()
        super().resizeEvent(event)

    @override
    def enterEvent(self, event: QEnterEvent) -> None:
        self._set_controls_visible(True)
        super().enterEvent(event)

    @override
    def leaveEvent(self, event: QEvent) -> None:
        QTimer.singleShot(0, self._hide_controls_if_mouse_left)
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
