"""Cross-platform smoke/manual verification for SubtitleWindow."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from rainyasr.config import SubtitleConfig
from rainyasr.gui.subtitle_window import SubtitleWindow, configure_macos_overlay_app


def create_windows() -> tuple[SubtitleWindow, SubtitleWindow, SubtitleWindow]:
    """Create the three windows used by manual and smoke verification."""
    window = SubtitleWindow()
    window.update_subtitle(
        "Window 1: close me first; the other windows should stay open.",
        "窗口 1：先关闭我，其他窗口应该继续显示。",
    )
    window.show()

    window2 = SubtitleWindow(
        SubtitleConfig(
            font_size=28,
            text_color="#E2E8F0",
            bg_opacity=75,
            bilingual_mode=False,
        )
    )
    window2.update_subtitle(
        "Hidden in monolingual mode.",
        "窗口 2：单语字幕窗口。",
    )
    window2.move(window.x() + window.width() + 40, window.y())
    window2.show()

    window3 = SubtitleWindow(
        SubtitleConfig(
            font_size=32,
            text_color="#FDE68A",
            bg_opacity=60,
            bilingual_mode=True,
        )
    )
    window3.update_subtitle(
        "Window 3: close all windows to end the process.",
        "窗口 3：全部窗口关闭后进程才退出。",
    )
    window3.move(window.x(), window.y() + window.height() + 40)
    window3.show()

    return window, window2, window3


def quit_after_all_windows_close(app: QApplication, windows: Sequence[SubtitleWindow]) -> set:
    """Quit only after every tracked subtitle window has closed."""
    open_windows = set(windows)

    def mark_closed(window: SubtitleWindow) -> None:
        open_windows.discard(window)
        print(f"closed: remaining={len(open_windows)}")
        if not open_windows:
            app.quit()

    for window in windows:
        window.closed.connect(lambda window=window: mark_closed(window))

    return open_windows


def print_window_flag_report(window: SubtitleWindow) -> None:
    """Print platform-neutral window flags that every desktop should expose."""
    flags = window.windowFlags()
    checks = {
        "FramelessWindowHint": bool(flags & Qt.WindowType.FramelessWindowHint),
        "WindowStaysOnTopHint": bool(flags & Qt.WindowType.WindowStaysOnTopHint),
        "NoDropShadowWindowHint": bool(flags & Qt.WindowType.NoDropShadowWindowHint),
        "WindowDoesNotAcceptFocus": bool(flags & Qt.WindowType.WindowDoesNotAcceptFocus),
        "Tool": bool(flags & Qt.WindowType.Tool),
        "WA_TranslucentBackground": window.testAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground
        ),
        "WA_ShowWithoutActivating": window.testAttribute(
            Qt.WidgetAttribute.WA_ShowWithoutActivating
        ),
    }

    print("window flag report:")
    for name, ok in checks.items():
        print(f"  [{'ok' if ok else 'missing'}] {name}")


def run_smoke(app: QApplication, windows: Sequence[SubtitleWindow], open_windows: set) -> int:
    """Automatically verify close lifecycle without requiring manual clicks."""
    errors: list[str] = []

    def close_first_window() -> None:
        windows[0].close()

    def assert_first_close_only_closed_one() -> None:
        if len(open_windows) != 2:
            errors.append(
                f"expected 2 remaining windows after first close, got {len(open_windows)}"
            )
        if not windows[1].isVisible() or not windows[2].isVisible():
            errors.append("closing the first window hid another subtitle window")

        windows[1].close()
        windows[2].close()

    def fail_on_timeout() -> None:
        errors.append("smoke test timed out before all windows closed")
        app.quit()

    QTimer.singleShot(50, close_first_window)
    QTimer.singleShot(150, assert_first_close_only_closed_one)
    QTimer.singleShot(3000, fail_on_timeout)

    app.exec()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print("subtitle window smoke verification passed")
    return 0


def run_manual(app: QApplication, windows: Sequence[SubtitleWindow]) -> int:
    """Run manual visual verification."""
    print("SubtitleWindow cross-platform manual verification")
    print(f"Python platform: {sys.platform}")
    print(f"Qt platform: {QApplication.platformName()}")
    print_window_flag_report(windows[0])
    print("")
    print("Manual checks:")
    print("  1. Hover a subtitle window and click the close button.")
    print("  2. Verify only that window closes; the other windows remain visible.")
    print("  3. Drag windows and verify they move smoothly.")
    print("  4. Switch to another app and verify subtitles stay above normal windows.")
    print("  5. Close every subtitle window; the process should then exit.")

    return app.exec()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="run an automatic lifecycle smoke check and exit",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app = QApplication([sys.argv[0]])
    app.setQuitOnLastWindowClosed(False)
    configure_macos_overlay_app()

    windows = create_windows()
    open_windows = quit_after_all_windows_close(app, windows)

    if args.smoke:
        return run_smoke(app, windows, open_windows)

    return run_manual(app, windows)


if __name__ == "__main__":
    raise SystemExit(main())
