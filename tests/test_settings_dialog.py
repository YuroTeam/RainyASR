"""Tests for SettingsDialog."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QFont, QKeySequence
from PySide6.QtWidgets import QColorDialog, QFontDialog, QLineEdit, QSpinBox, QTabWidget

from rainyasr import config as config_module
from rainyasr.config import AppConfig
from rainyasr.gui.settings_dialog import ApiKeyValues, SettingsDialog


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
                "asr_format": "wav",
                "asr_language": "en",
            },
            "subtitle": {
                "font_family": "Inter, Arial, sans-serif",
                "font_size": 30,
                "window_width": 1120,
                "text_color": "#12ABEF",
                "bg_opacity": 65,
                "bilingual_mode": False,
            },
            "hotkey": {"toggle_hotkey": "ctrl+alt+r"},
            "language": {"target_lang": "en"},
        }
    )


@pytest.fixture
def dialog(qtbot) -> SettingsDialog:
    settings_dialog = SettingsDialog(
        _sample_config(),
        dashscope_api_key="dash-key",
        deepseek_api_key="deep-key",
    )
    qtbot.addWidget(settings_dialog)
    return settings_dialog


class TestSettingsDialog:
    def test_initializes_controls_from_config_and_api_keys(
        self,
        dialog: SettingsDialog,
    ) -> None:
        assert dialog._dashscope_key_edit.echoMode() == QLineEdit.EchoMode.Password
        assert dialog._deepseek_key_edit.echoMode() == QLineEdit.EchoMode.Password
        assert dialog.api_key_values() == ApiKeyValues("dash-key", "deep-key")

        assert dialog._sample_rate_edit.text() == "24000"
        assert dialog._sample_rate_edit.placeholderText() == "8000-48000 Hz"
        assert dialog._channels_group.checkedId() == 2
        assert dialog._frame_ms_edit.text() == "50"
        assert dialog._frame_ms_edit.placeholderText() == "20-500 ms"
        assert dialog._audio_queue_edit.text() == "120"
        assert dialog._audio_queue_edit.placeholderText() == "10-1000 frames"
        assert dialog._silence_threshold_edit.text() == "0.0007"
        assert dialog._silence_threshold_edit.placeholderText() == "0-1 RMS"
        assert dialog._asr_model_edit.text() == "custom-asr"
        assert dialog._combo_value(dialog._asr_language_combo) == "en"
        assert dialog._asr_format_value() == "wav"
        assert dialog._font_family_edit.text() == "Inter, Arial, sans-serif"
        assert dialog._font_size_edit.text() == "30"
        assert dialog._window_width_edit.text() == "1120"
        assert dialog._window_width_edit.placeholderText() == "400-1800 px"
        assert dialog._text_color == "#12ABEF"
        assert dialog._bg_opacity_edit.text() == "65"
        assert not dialog._bilingual_mode_check.isChecked()
        assert dialog._combo_value(dialog._target_lang_combo) == "en"

    def test_uses_codex_style_number_fields_without_spin_buttons(
        self,
        dialog: SettingsDialog,
    ) -> None:
        assert dialog.findChildren(QSpinBox) == []
        assert dialog._sample_rate_edit.property("numericField") is True
        assert dialog._font_size_edit.property("numericField") is True

    def test_tabs_group_settings_by_task_area(self, dialog: SettingsDialog) -> None:
        tabs = dialog.findChild(QTabWidget)
        assert tabs is not None

        assert [tabs.tabText(index) for index in range(tabs.count())] == [
            "API Keys",
            "Audio",
            "Recognition",
            "Appearance",
        ]

    def test_current_config_collects_nested_values(self, dialog: SettingsDialog) -> None:
        dialog._sample_rate_edit.setText("32000")
        dialog._channels_group.button(1).setChecked(True)
        dialog._frame_ms_edit.setText("80")
        dialog._audio_queue_edit.setText("240")
        dialog._silence_threshold_edit.setText("0.0004")

        dialog._asr_model_edit.setText("qwen3-asr-flash-realtime")
        dialog._asr_language_combo.setEditText("auto")
        dialog._asr_format_group.button(0).setChecked(True)

        dialog._font_family_edit.setText("PingFang SC, sans-serif")
        dialog._font_size_slider.setValue(40)
        dialog._window_width_edit.setText("1280")
        dialog._text_color = "#00AAFF"
        dialog._refresh_color_button()
        dialog._bg_opacity_edit.setText("45")
        dialog._bilingual_mode_check.setChecked(True)

        dialog._hotkey_edit.setKeySequence(QKeySequence("Ctrl+Alt+S"))
        dialog._set_combo_value(dialog._target_lang_combo, "ja")

        config = dialog.current_config()

        assert config.audio.sample_rate == 32000
        assert config.audio.channels == 1
        assert config.audio.frame_ms == 80
        assert config.audio.audio_queue_max_frames == 240
        assert config.audio.silence_rms_threshold == 0.0004
        assert config.asr.asr_model == "qwen3-asr-flash-realtime"
        assert config.asr.asr_language == "auto"
        assert config.asr.asr_format == "pcm"
        assert config.subtitle.font_family == "PingFang SC, sans-serif"
        assert config.subtitle.font_size == 40
        assert config.subtitle.window_width == 1280
        assert config.subtitle.text_color == "#00AAFF"
        assert config.subtitle.bg_opacity == 45
        assert config.subtitle.bilingual_mode is True
        assert config.hotkey.toggle_hotkey == "ctrl+alt+s"
        assert config.language.target_lang == "ja"

    @pytest.mark.parametrize(
        ("value", "message"),
        [
            ("", "Sample rate is required."),
            ("not_a_number", "Sample rate must be a number."),
            ("7999", "Sample rate must be between 8000 and 48000."),
        ],
    )
    def test_number_field_rejects_invalid_input(
        self,
        dialog: SettingsDialog,
        value: str,
        message: str,
    ) -> None:
        dialog._sample_rate_edit.setText(value)

        with pytest.raises(ValueError, match=message):
            dialog.current_config()

    @pytest.mark.parametrize(
        ("value", "message"),
        [
            ("", "Silence threshold is required."),
            ("not_a_number", "Silence threshold must be a number."),
            ("1.1", "Silence threshold must be between 0.0 and 1.0."),
        ],
    )
    def test_silence_threshold_rejects_invalid_input(
        self,
        dialog: SettingsDialog,
        value: str,
        message: str,
    ) -> None:
        dialog._silence_threshold_edit.setText(value)

        with pytest.raises(ValueError, match=message):
            dialog.current_config()

    def test_invalid_initial_asr_format_is_not_silently_rewritten(self, qtbot) -> None:
        config = _sample_config()
        config.asr.asr_format = "flac"
        dialog = SettingsDialog(
            config,
            dashscope_api_key="",
            deepseek_api_key="",
        )
        qtbot.addWidget(dialog)

        assert dialog._asr_format_group.checkedId() == -1
        with pytest.raises(ValueError, match="ASR audio format is required."):
            dialog.current_config()

    def test_asr_format_rejects_out_of_range_checked_id(
        self,
        dialog: SettingsDialog,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(dialog._asr_format_group, "checkedId", lambda: 999)

        with pytest.raises(ValueError, match="ASR audio format is required."):
            dialog.current_config()

    def test_accept_saves_config_and_emits_signals(
        self,
        qtbot,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = tmp_path / "config.toml"
        env_path = tmp_path / ".env"
        monkeypatch.setattr(config_module, "_config_toml_path", lambda: path)
        monkeypatch.setattr(config_module, "_env_file_path", lambda: env_path)
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

        dialog = SettingsDialog(
            _sample_config(),
            dashscope_api_key="dash-updated",
            deepseek_api_key="deep-updated",
        )
        qtbot.addWidget(dialog)
        dialog._sample_rate_edit.setText("44100")
        dialog._set_combo_value(dialog._target_lang_combo, "ko")

        saved_configs: list[AppConfig] = []
        submitted_keys: list[tuple[str, str]] = []
        dialog.settings_saved.connect(saved_configs.append)
        dialog.api_keys_submitted.connect(
            lambda dashscope, deepseek: submitted_keys.append((dashscope, deepseek))
        )

        dialog.accept()

        assert saved_configs[0].audio.sample_rate == 44100
        assert saved_configs[0].language.target_lang == "ko"
        assert submitted_keys == [("dash-updated", "deep-updated")]

        loaded = AppConfig.load()
        assert loaded.audio.sample_rate == 44100
        assert loaded.language.target_lang == "ko"

        saved_text = path.read_text(encoding="utf-8")
        assert "dash-updated" not in saved_text
        assert "deep-updated" not in saved_text

        saved_env = env_path.read_text(encoding="utf-8")
        assert "DASHSCOPE_API_KEY='dash-updated'" in saved_env
        assert "DEEPSEEK_API_KEY='deep-updated'" in saved_env

    def test_reject_does_not_save_config_or_emit_signals(
        self,
        qtbot,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = tmp_path / "config.toml"
        monkeypatch.setattr(config_module, "_config_toml_path", lambda: path)
        _sample_config().save()
        original_text = path.read_text(encoding="utf-8")

        dialog = SettingsDialog(
            _sample_config(),
            dashscope_api_key="dash-updated",
            deepseek_api_key="deep-updated",
        )
        qtbot.addWidget(dialog)
        dialog._sample_rate_edit.setText("44100")
        dialog._set_combo_value(dialog._target_lang_combo, "ko")

        saved_configs: list[AppConfig] = []
        submitted_keys: list[tuple[str, str]] = []
        dialog.settings_saved.connect(saved_configs.append)
        dialog.api_keys_submitted.connect(
            lambda dashscope, deepseek: submitted_keys.append((dashscope, deepseek))
        )

        dialog.reject()

        assert path.read_text(encoding="utf-8") == original_text
        assert saved_configs == []
        assert submitted_keys == []

    def test_validation_error_blocks_accept(
        self,
        qtbot,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = tmp_path / "config.toml"
        monkeypatch.setattr(config_module, "_config_toml_path", lambda: path)

        dialog = SettingsDialog(
            _sample_config(),
            dashscope_api_key="",
            deepseek_api_key="",
        )
        qtbot.addWidget(dialog)
        dialog._asr_model_edit.clear()

        errors: list[str] = []
        saved_configs: list[AppConfig] = []
        submitted_keys: list[tuple[str, str]] = []
        monkeypatch.setattr(dialog, "_show_validation_error", errors.append)
        dialog.settings_saved.connect(saved_configs.append)
        dialog.api_keys_submitted.connect(
            lambda dashscope, deepseek: submitted_keys.append((dashscope, deepseek))
        )

        dialog.accept()

        assert errors == ["ASR model is required."]
        assert not path.exists()
        assert saved_configs == []
        assert submitted_keys == []

    def test_choose_text_color_updates_selected_color(
        self,
        dialog: SettingsDialog,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            QColorDialog,
            "getColor",
            lambda *args: QColor("#aabbcc"),
        )

        dialog._choose_text_color()

        assert dialog._text_color == "#AABBCC"
        assert dialog._text_color_button.text() == "#AABBCC"
        assert "color: #000000;" in dialog._text_color_button.styleSheet()

    def test_color_button_uses_light_text_on_dark_background(
        self,
        dialog: SettingsDialog,
    ) -> None:
        dialog._text_color = "#003344"
        dialog._refresh_color_button()

        assert "color: #FFFFFF;" in dialog._text_color_button.styleSheet()

    def test_choose_font_updates_font_family(
        self,
        dialog: SettingsDialog,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            QFontDialog,
            "getFont",
            lambda *args: (QFont("Arial"), True),
        )

        dialog._choose_font()

        assert dialog._font_family_edit.text() == "Arial"
