"""Settings dialog for editing RainyASR configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import override

from pydantic import ValidationError
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QDoubleValidator, QFont, QIntValidator, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFontDialog,
    QFormLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from rainyasr.config import AppConfig, EnvConfig, save_env_api_keys

LANGUAGE_OPTIONS = (
    ("zh", "Chinese (zh)"),
    ("en", "English (en)"),
    ("ja", "Japanese (ja)"),
    ("ko", "Korean (ko)"),
    ("fr", "French (fr)"),
    ("de", "German (de)"),
    ("es", "Spanish (es)"),
    ("ru", "Russian (ru)"),
)

ASR_LANGUAGE_OPTIONS = (
    ("auto", "Auto"),
    ("zh", "Chinese (zh)"),
    ("en", "English (en)"),
    ("ja", "Japanese (ja)"),
    ("ko", "Korean (ko)"),
)

ASR_FORMAT_OPTIONS = ("pcm", "wav", "mp3")
CHANNEL_OPTIONS = ((1, "Mono"), (2, "Stereo"))
CHANNEL_VALUES = frozenset(value for value, _label in CHANNEL_OPTIONS)
CONTROL_HEIGHT = 44


@dataclass(frozen=True)
class ApiKeyValues:
    """API key values entered in the settings dialog."""

    dashscope_api_key: str
    deepseek_api_key: str


class SettingsDialog(QDialog):
    """Dialog that edits AppConfig without depending on runtime components."""

    settings_saved = Signal(object)
    api_keys_submitted = Signal(str, str)

    def __init__(
        self,
        config: AppConfig | None = None,
        *,
        dashscope_api_key: str | None = None,
        deepseek_api_key: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = (config or AppConfig.load()).model_copy(deep=True)
        self._text_color = self._normalized_color(self._config.subtitle.text_color)

        if dashscope_api_key is None:
            dashscope_api_key = EnvConfig.dashscope_api_key()
        if deepseek_api_key is None:
            deepseek_api_key = EnvConfig.deepseek_api_key()

        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumSize(520, 380)

        self._setup_ui(dashscope_api_key, deepseek_api_key)

    def _setup_ui(self, dashscope_api_key: str, deepseek_api_key: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        tabs = QTabWidget(self)
        tabs.addTab(self._build_credentials_tab(dashscope_api_key, deepseek_api_key), "API Keys")
        tabs.addTab(self._build_audio_tab(), "Audio")
        tabs.addTab(self._build_recognition_tab(), "Recognition")
        tabs.addTab(self._build_appearance_tab(), "Appearance")
        root.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._apply_dialog_style()

    def _build_credentials_tab(self, dashscope_api_key: str, deepseek_api_key: str) -> QWidget:
        tab = QWidget(self)
        form = QFormLayout(tab)
        self._configure_form_layout(form)

        self._dashscope_key_edit = QLineEdit(tab)
        self._dashscope_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._dashscope_key_edit.setText(dashscope_api_key)
        self._add_form_row(form, "DashScope API key", self._dashscope_key_edit)

        self._deepseek_key_edit = QLineEdit(tab)
        self._deepseek_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._deepseek_key_edit.setText(deepseek_api_key)
        self._add_form_row(form, "Translation fallback API key", self._deepseek_key_edit)

        return tab

    def _build_audio_tab(self) -> QWidget:
        tab = QWidget(self)
        form = QFormLayout(tab)
        self._configure_form_layout(form)

        sample_rate_row, self._sample_rate_edit = self._number_field_row(
            tab,
            value=self._config.audio.sample_rate,
            minimum=8000,
            maximum=48000,
            unit="Hz",
            accessible_name="Sample rate value",
        )
        self._add_form_row(form, "Sample rate", sample_rate_row)

        self._channels_group = QButtonGroup(self)
        self._channels_group.setExclusive(True)
        channels_row = self._segmented_buttons(
            tab,
            self._channels_group,
            CHANNEL_OPTIONS,
            checked_id=self._config.audio.channels,
        )
        self._add_form_row(form, "Channels", channels_row)

        frame_ms_row, self._frame_ms_edit = self._number_field_row(
            tab,
            value=self._config.audio.frame_ms,
            minimum=20,
            maximum=500,
            unit="ms",
            accessible_name="Frame length value",
        )
        self._add_form_row(form, "Frame length", frame_ms_row)

        audio_queue_row, self._audio_queue_edit = self._number_field_row(
            tab,
            value=self._config.audio.audio_queue_max_frames,
            minimum=10,
            maximum=1000,
            unit="frames",
            accessible_name="Queue max value",
        )
        self._add_form_row(form, "Queue max", audio_queue_row)

        silence_threshold_row, self._silence_threshold_edit = self._float_field_row(
            tab,
            value=self._config.audio.silence_rms_threshold,
            minimum=0.0,
            maximum=1.0,
            decimals=6,
            unit="RMS",
            accessible_name="Silence threshold value",
        )
        self._add_form_row(form, "Silence threshold", silence_threshold_row)

        return tab

    def _build_recognition_tab(self) -> QWidget:
        tab = QWidget(self)
        form = QFormLayout(tab)
        self._configure_form_layout(form)

        self._asr_model_edit = QLineEdit(tab)
        self._asr_model_edit.setText(self._config.asr.asr_model)
        self._asr_model_edit.setCursorPosition(0)
        self._add_form_row(form, "Model", self._asr_model_edit)

        self._asr_language_combo = QComboBox(tab)
        self._asr_language_combo.setEditable(True)
        self._add_combo_options(self._asr_language_combo, ASR_LANGUAGE_OPTIONS)
        self._set_combo_value(self._asr_language_combo, self._config.asr.asr_language)
        self._add_form_row(form, "Language", self._asr_language_combo)

        self._asr_format_group = QButtonGroup(self)
        self._asr_format_group.setExclusive(True)
        format_row = self._segmented_buttons(
            tab,
            self._asr_format_group,
            tuple((index, fmt) for index, fmt in enumerate(ASR_FORMAT_OPTIONS)),
            checked_id=self._format_id(self._config.asr.asr_format),
        )
        self._add_form_row(form, "Audio format", format_row)

        self._target_lang_combo = QComboBox(tab)
        self._add_combo_options(self._target_lang_combo, LANGUAGE_OPTIONS)
        self._set_combo_value(self._target_lang_combo, self._config.language.target_lang)
        self._add_form_row(form, "Translation target", self._target_lang_combo)

        return tab

    def _build_appearance_tab(self) -> QWidget:
        tab = QWidget(self)
        form = QFormLayout(tab)
        self._configure_form_layout(form)

        font_row = QWidget(tab)
        font_layout = QHBoxLayout(font_row)
        font_layout.setContentsMargins(0, 0, 0, 0)
        font_layout.setSpacing(8)
        self._font_family_edit = QLineEdit(font_row)
        self._font_family_edit.setText(self._config.subtitle.font_family)
        self._font_family_edit.setCursorPosition(0)
        self._choose_font_button = QPushButton("Choose...", font_row)
        self._choose_font_button.clicked.connect(self._choose_font)
        font_layout.addWidget(self._font_family_edit, 1)
        font_layout.addWidget(self._choose_font_button)
        self._add_form_row(form, "Font", font_row)

        font_size_row, self._font_size_slider, self._font_size_edit = self._linked_slider_field(
            tab,
            minimum=8,
            maximum=72,
            value=self._config.subtitle.font_size,
            accessible_name="Font size value",
        )
        self._add_form_row(form, "Size", font_size_row)

        window_width_row, self._window_width_edit = self._number_field_row(
            tab,
            value=self._config.subtitle.window_width,
            minimum=400,
            maximum=1800,
            unit="px",
            accessible_name="Window width value",
        )
        self._add_form_row(form, "Window width", window_width_row)

        self._text_color_button = QPushButton(tab)
        self._text_color_button.clicked.connect(self._choose_text_color)
        self._refresh_color_button()
        self._add_form_row(form, "Text color", self._text_color_button)

        opacity_row, self._bg_opacity_slider, self._bg_opacity_edit = self._linked_slider_field(
            tab,
            minimum=0,
            maximum=100,
            value=self._config.subtitle.bg_opacity,
            accessible_name="Background opacity value",
        )
        self._add_form_row(form, "Background", opacity_row)

        self._bilingual_mode_check = QCheckBox(tab)
        self._bilingual_mode_check.setChecked(self._config.subtitle.bilingual_mode)
        self._add_form_row(form, "Bilingual mode", self._bilingual_mode_check)

        self._hotkey_edit = QKeySequenceEdit(tab)
        self._hotkey_edit.setKeySequence(QKeySequence(self._config.hotkey.toggle_hotkey))
        self._add_form_row(form, "Toggle shortcut", self._hotkey_edit)

        return tab

    def current_config(self) -> AppConfig:
        """Return a validated AppConfig built from the current form values."""
        data = self._config.model_dump(mode="python")
        data["audio"].update(
            {
                "sample_rate": self._number_value(
                    self._sample_rate_edit,
                    label="Sample rate",
                    minimum=8000,
                    maximum=48000,
                ),
                "channels": self._channel_value(),
                "frame_ms": self._number_value(
                    self._frame_ms_edit,
                    label="Frame length",
                    minimum=20,
                    maximum=500,
                ),
                "audio_queue_max_frames": self._number_value(
                    self._audio_queue_edit,
                    label="Queue max",
                    minimum=10,
                    maximum=1000,
                ),
                "silence_rms_threshold": self._float_value(
                    self._silence_threshold_edit,
                    label="Silence threshold",
                    minimum=0.0,
                    maximum=1.0,
                ),
            }
        )
        data["asr"].update(
            {
                "asr_model": self._asr_model_edit.text().strip(),
                "asr_format": self._asr_format_value(),
                "asr_language": self._combo_value(self._asr_language_combo),
            }
        )
        data["subtitle"].update(
            {
                "font_family": self._font_family_edit.text().strip(),
                "font_size": self._number_value(
                    self._font_size_edit,
                    label="Font size",
                    minimum=8,
                    maximum=72,
                ),
                "window_width": self._number_value(
                    self._window_width_edit,
                    label="Window width",
                    minimum=400,
                    maximum=1800,
                ),
                "text_color": self._text_color,
                "bg_opacity": self._number_value(
                    self._bg_opacity_edit,
                    label="Background opacity",
                    minimum=0,
                    maximum=100,
                ),
                "bilingual_mode": self._bilingual_mode_check.isChecked(),
            }
        )
        data["hotkey"].update({"toggle_hotkey": self._hotkey_text()})
        data["language"].update({"target_lang": self._combo_value(self._target_lang_combo)})
        return AppConfig.model_validate(data)

    def api_key_values(self) -> ApiKeyValues:
        """Return API keys entered by the user."""
        return ApiKeyValues(
            dashscope_api_key=self._dashscope_key_edit.text().strip(),
            deepseek_api_key=self._deepseek_key_edit.text().strip(),
        )

    @override
    def accept(self) -> None:
        try:
            config = self.current_config()
            self._validate_required_fields(config)
            api_keys = self.api_key_values()
            config.save()
            save_env_api_keys(
                dashscope_api_key=api_keys.dashscope_api_key,
                deepseek_api_key=api_keys.deepseek_api_key,
            )
        except ValidationError as exc:
            self._show_validation_error(str(exc))
            return
        except ValueError as exc:
            self._show_validation_error(str(exc))
            return
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return

        self._config = config
        self.settings_saved.emit(config)
        self.api_keys_submitted.emit(api_keys.dashscope_api_key, api_keys.deepseek_api_key)
        super().accept()

    def _choose_font(self) -> None:
        initial = QFont(self._font_family_edit.text().split(",")[0].strip())
        font, ok = QFontDialog.getFont(initial, self, "Choose subtitle font")
        if ok:
            self._font_family_edit.setText(font.family())

    def _choose_text_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._text_color), self, "Choose text color")
        if color.isValid():
            self._text_color = color.name(QColor.NameFormat.HexRgb).upper()
            self._refresh_color_button()

    def _refresh_color_button(self) -> None:
        text_color = self._contrast_text_color(self._text_color)
        self._text_color_button.setText(self._text_color)
        self._text_color_button.setStyleSheet(
            "QPushButton {"
            f"background-color: {self._text_color};"
            f"color: {text_color};"
            "border: 1px solid rgba(15, 23, 42, 0.35);"
            "padding: 4px 10px;"
            "border-radius: 4px;"
            "}"
        )

    def _hotkey_text(self) -> str:
        return self._hotkey_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)

    def _validate_required_fields(self, config: AppConfig) -> None:
        if not config.asr.asr_model:
            raise ValueError("ASR model is required.")
        if not config.asr.asr_format:
            raise ValueError("ASR audio format is required.")
        if not config.subtitle.font_family:
            raise ValueError("Subtitle font family is required.")
        if not config.hotkey.toggle_hotkey:
            raise ValueError("Toggle hotkey is required.")

    def _show_validation_error(self, message: str) -> None:
        QMessageBox.warning(self, "Invalid settings", message)

    @staticmethod
    def _configure_form_layout(form: QFormLayout) -> None:
        form.setContentsMargins(20, 20, 20, 20)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(14)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

    @staticmethod
    def _add_form_row(form: QFormLayout, label: str, field: QWidget) -> None:
        label_widget = QLabel(label)
        label_widget.setProperty("formLabel", True)
        label_widget.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.addRow(label_widget, field)

    def _apply_dialog_style(self) -> None:
        self.setStyleSheet(
            """
            QDialog {
                background-color: #F7F5EF;
                color: #272625;
            }
            QTabWidget::pane {
                border: 1px solid #DDD8CE;
                border-radius: 14px;
                background-color: #FFFDF8;
                top: -8px;
            }
            QTabBar::tab {
                background-color: transparent;
                color: #6F6B65;
                border: 1px solid transparent;
                border-radius: 12px;
                padding: 8px 13px;
                margin: 0 3px 10px 0;
            }
            QTabBar::tab:selected {
                background-color: #FFFFFF;
                border-color: #E2DED5;
                color: #252422;
                font-weight: 600;
            }
            QTabBar::tab:hover:!selected {
                background-color: #ECE8DE;
                color: #3F3C38;
            }
            QLabel {
                color: #504B45;
            }
            QLabel[formLabel="true"] {
                min-height: 44px;
                font-size: 14px;
            }
            QLineEdit,
            QComboBox,
            QKeySequenceEdit {
                min-height: 44px;
                max-height: 44px;
                border: 1px solid #D7D2C8;
                border-radius: 10px;
                padding: 0 12px;
                background-color: #FFFDF8;
                color: #252422;
                selection-background-color: #D8F7EE;
            }
            QLineEdit:focus,
            QComboBox:focus,
            QKeySequenceEdit:focus {
                border-color: #9F9A90;
                background-color: #FFFFFF;
            }
            QLineEdit[numericField="true"] {
                max-width: 170px;
            }
            QComboBox::drop-down {
                border: none;
                width: 26px;
            }
            QPushButton {
                min-height: 44px;
                max-height: 44px;
                border: 1px solid #D7D2C8;
                border-radius: 10px;
                padding: 0 12px;
                background-color: #FFFDF8;
                color: #252422;
            }
            QPushButton:hover {
                background-color: #F1EEE7;
            }
            QPushButton:pressed {
                background-color: #E9E4DA;
            }
            QPushButton[segment="true"] {
                min-width: 82px;
                min-height: 44px;
                max-height: 44px;
                border-radius: 12px;
                background-color: #ECE8DE;
                color: #625D56;
                border-color: #ECE8DE;
            }
            QPushButton[segment="true"]:checked {
                background-color: #FFFFFF;
                color: #252422;
                border-color: #D7D2C8;
                font-weight: 600;
            }
            QCheckBox {
                color: #3F3C38;
                min-height: 44px;
            }
            QLabel[unitLabel="true"] {
                min-height: 44px;
                color: #77716A;
                padding-left: 2px;
            }
            QSlider::groove:horizontal {
                height: 6px;
                border-radius: 3px;
                background-color: #E4DED2;
            }
            QSlider::sub-page:horizontal {
                border-radius: 3px;
                background-color: #8E8A82;
            }
            QSlider::handle:horizontal {
                width: 18px;
                height: 18px;
                margin: -7px 0;
                border-radius: 9px;
                background-color: #FFFFFF;
                border: 1px solid #C8C2B8;
            }
            QSlider::handle:horizontal:hover {
                border-color: #8E8A82;
            }
            """
        )

    @staticmethod
    def _number_field_row(
        parent: QWidget,
        *,
        value: int,
        minimum: int,
        maximum: int,
        unit: str,
        accessible_name: str,
    ) -> tuple[QWidget, QLineEdit]:
        row = QWidget(parent)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        row.setMinimumHeight(CONTROL_HEIGHT)

        edit = QLineEdit(row)
        edit.setProperty("numericField", True)
        edit.setValidator(QIntValidator(minimum, maximum, edit))
        edit.setText(str(value))
        edit.setPlaceholderText(f"{minimum}-{maximum} {unit}")
        edit.setAccessibleName(accessible_name)
        edit.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        unit_label = QLabel(unit, row)
        unit_label.setProperty("unitLabel", True)

        layout.addWidget(edit)
        layout.addWidget(unit_label)
        layout.addStretch(1)
        return row, edit

    @staticmethod
    def _float_field_row(
        parent: QWidget,
        *,
        value: float,
        minimum: float,
        maximum: float,
        decimals: int,
        unit: str,
        accessible_name: str,
    ) -> tuple[QWidget, QLineEdit]:
        row = QWidget(parent)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        row.setMinimumHeight(CONTROL_HEIGHT)

        edit = QLineEdit(row)
        edit.setProperty("numericField", True)
        validator = QDoubleValidator(minimum, maximum, decimals, edit)
        validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        edit.setValidator(validator)
        edit.setText(f"{value:.6g}")
        edit.setPlaceholderText(f"{minimum:.0f}-{maximum:.0f} {unit}")
        edit.setAccessibleName(accessible_name)
        edit.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        unit_label = QLabel(unit, row)
        unit_label.setProperty("unitLabel", True)

        layout.addWidget(edit)
        layout.addWidget(unit_label)
        layout.addStretch(1)
        return row, edit

    @staticmethod
    def _segmented_buttons(
        parent: QWidget,
        group: QButtonGroup,
        options: tuple[tuple[int, str], ...],
        *,
        checked_id: int,
    ) -> QWidget:
        row = QWidget(parent)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        row.setMinimumHeight(CONTROL_HEIGHT)

        for value, label in options:
            button = QPushButton(label, row)
            button.setCheckable(True)
            button.setProperty("segment", True)
            group.addButton(button, value)
            layout.addWidget(button)
            if value == checked_id:
                button.setChecked(True)

        layout.addStretch(1)
        return row

    @staticmethod
    def _linked_slider_field(
        parent: QWidget,
        *,
        minimum: int,
        maximum: int,
        value: int,
        accessible_name: str,
    ) -> tuple[QWidget, QSlider, QLineEdit]:
        row = QWidget(parent)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        row.setMinimumHeight(CONTROL_HEIGHT)

        slider = QSlider(Qt.Orientation.Horizontal, row)
        slider.setRange(minimum, maximum)
        slider.setValue(value)

        edit = QLineEdit(row)
        edit.setProperty("numericField", True)
        edit.setValidator(QIntValidator(minimum, maximum, edit))
        edit.setText(str(value))
        edit.setFixedWidth(72)
        edit.setAccessibleName(accessible_name)

        def apply_edit_value() -> None:
            value_text = edit.text().strip()
            if not value_text or not edit.hasAcceptableInput():
                edit.setText(str(slider.value()))
                return
            new_value = min(max(int(value_text), minimum), maximum)
            edit.setText(str(new_value))
            slider.setValue(new_value)

        def sync_slider_from_edit(value_text: str) -> None:
            if not value_text.strip() or not edit.hasAcceptableInput():
                return
            slider.setValue(int(value_text))

        slider.valueChanged.connect(lambda next_value: edit.setText(str(next_value)))
        edit.textChanged.connect(sync_slider_from_edit)
        edit.editingFinished.connect(apply_edit_value)

        layout.addWidget(slider, 1)
        layout.addWidget(edit)
        return row, slider, edit

    def _number_value(
        self,
        edit: QLineEdit,
        *,
        label: str,
        minimum: int,
        maximum: int,
    ) -> int:
        text = edit.text().strip()
        if not text:
            raise ValueError(f"{label} is required.")
        try:
            value = int(text)
        except ValueError:
            raise ValueError(f"{label} must be a number.") from None
        if not minimum <= value <= maximum:
            raise ValueError(f"{label} must be between {minimum} and {maximum}.")
        return value

    def _float_value(
        self,
        edit: QLineEdit,
        *,
        label: str,
        minimum: float,
        maximum: float,
    ) -> float:
        text = edit.text().strip()
        if not text:
            raise ValueError(f"{label} is required.")
        try:
            value = float(text)
        except ValueError:
            raise ValueError(f"{label} must be a number.") from None
        if not minimum <= value <= maximum:
            raise ValueError(f"{label} must be between {minimum} and {maximum}.")
        return value

    def _channel_value(self) -> int:
        checked_id = self._channels_group.checkedId()
        if checked_id not in CHANNEL_VALUES:
            raise ValueError("Audio channel mode is required.")
        return checked_id

    def _asr_format_value(self) -> str:
        checked_id = self._asr_format_group.checkedId()
        if not 0 <= checked_id < len(ASR_FORMAT_OPTIONS):
            raise ValueError("ASR audio format is required.")
        return ASR_FORMAT_OPTIONS[checked_id]

    @staticmethod
    def _format_id(value: str) -> int:
        try:
            return ASR_FORMAT_OPTIONS.index(value)
        except ValueError:
            return -1

    @staticmethod
    def _add_combo_options(combo: QComboBox, options: tuple[tuple[str, str], ...]) -> None:
        for value, label in options:
            combo.addItem(label, value)

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)
        elif combo.isEditable():
            combo.setEditText(value)

    @staticmethod
    def _combo_value(combo: QComboBox) -> str:
        data = combo.currentData()
        if combo.isEditable():
            current_index = combo.currentIndex()
            # Editable combos can keep the last item data while showing custom typed text.
            if current_index < 0 or combo.currentText() != combo.itemText(current_index):
                return combo.currentText().strip()
        if data is not None:
            return str(data).strip()
        return combo.currentText().strip()

    @staticmethod
    def _normalized_color(value: str) -> str:
        color = QColor(value)
        if not color.isValid():
            return "#FFFFFF"
        return color.name(QColor.NameFormat.HexRgb).upper()

    @staticmethod
    def _contrast_text_color(value: str) -> str:
        color = QColor(value)
        if not color.isValid():
            return "#000000"
        luminance = (0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()) / 255
        return "#000000" if luminance > 0.5 else "#FFFFFF"
