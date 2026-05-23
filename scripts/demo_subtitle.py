"""Demo script to visually inspect the SubtitleWindow."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from rainyasr.config import SubtitleConfig
from rainyasr.gui.subtitle_window import SubtitleWindow, configure_macos_overlay_app


def quit_after_all_windows_close(app: QApplication, windows: Sequence[SubtitleWindow]) -> None:
    """Quit the demo only after every subtitle window has been closed."""
    open_windows = set(windows)

    def mark_closed(window: SubtitleWindow) -> None:
        open_windows.discard(window)
        if not open_windows:
            app.quit()

    for window in windows:
        window.closed.connect(lambda window=window: mark_closed(window))


def main() -> None:
    app = QApplication(sys.argv)
    configure_macos_overlay_app()

    # Demo 1: Default bilingual mode
    window = SubtitleWindow()
    window.update_subtitle(
        "Hello world, this is a real-time subtitle demo.",
        "你好世界，这是一个实时字幕演示。",
        is_partial=False,
    )
    window.show()

    # Demo 2: Monolingual mode (translation only)
    config = SubtitleConfig(
        font_size=28,
        text_color="#E2E8F0",
        bg_opacity=75,
        bilingual_mode=False,
    )
    window2 = SubtitleWindow(config)
    window2.update_subtitle(
        "Original text is hidden in monolingual mode.",
        "单语模式下只显示译文",
        is_partial=True,
    )
    window2.move(window.x() + window.width() + 40, window.y())
    window2.show()

    # Demo 3: Larger font, different color
    config3 = SubtitleConfig(
        font_size=32,
        text_color="#FDE68A",
        bg_opacity=60,
        bilingual_mode=True,
    )
    window3 = SubtitleWindow(config3)
    window3.update_subtitle(
        "The quick brown fox jumps over the lazy dog.",
        "敏捷的棕色狐狸跳过了懒狗。",
        is_partial=False,
    )
    window3.move(window.x(), window.y() + window.height() + 40)
    window3.show()

    quit_after_all_windows_close(app, (window, window2, window3))

    # Simulate partial -> final transition on window1
    def simulate_partial():
        window.update_subtitle(
            "This text is still being recognized...",
            "这段文字仍在识别中……",
            is_partial=True,
        )

    def simulate_final():
        window.update_subtitle(
            "Recognition complete. Final result shown.",
            "识别完成。显示最终结果。",
            is_partial=False,
        )

    QTimer.singleShot(3000, simulate_partial)
    QTimer.singleShot(6000, simulate_final)

    print("SubtitleWindow demo running. Close all windows to exit.")
    print("Features to check:")
    print("  - Window stays on top of other apps")
    print("  - Drag window by clicking and dragging")
    print("  - Hover over any window to reveal the close button")
    print("  - Window 2: monolingual mode, only translation shown")
    print("  - Window 3: larger font, amber text")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
