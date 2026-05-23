# RainyASR

[中文 README](README.md)

RainyASR is a cross-platform real-time subtitle translation tool built with Python and Qt. It captures system playback audio, transcribes speech with DashScope Qwen realtime ASR, translates it through Qwen-MT or another OpenAI-compatible API, and displays bilingual subtitles in a borderless always-on-top overlay.

## Features

- Cross-platform system audio capture: Windows WASAPI loopback, macOS BlackHole, Linux PulseAudio/PipeWire monitor sources
- DashScope Qwen realtime speech recognition
- Qwen-MT/OpenAI-compatible translation interface, defaulting to `qwen-mt-flash`
- Borderless, always-on-top, draggable subtitle overlay
- Hover controls on the overlay: a settings gear and a close button
- Bilingual or monolingual subtitle display
- Configurable font family, font size, window width, text color, background opacity, and subtitle mode
- Live reload for subtitle appearance settings; audio, ASR, translation target, API key, and hotkey changes restart affected runtime components automatically
- Global hotkey to show or hide subtitles, default `ctrl+shift+r`
- System tray menu for show/hide, Settings, and Quit
- Silence gating and local audio preroll to reduce requests during silence while preserving speech starts

## Requirements

- Python `>=3.13`
- [uv](https://docs.astral.sh/uv/)
- PySide6
- A PortAudio-compatible audio environment
- DashScope API key. If you use a non-Qwen translation backend, you also need the corresponding translation API key.

## Quick Start

### 1. Install uv

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows PowerShell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Verify the installation:

```bash
uv --version
```

### 2. Sync Dependencies

```bash
git clone <repository-url>
cd RainyASR
uv sync
```

### 3. Configure API Keys

Create a `.env` file in the project root:

```bash
DASHSCOPE_API_KEY=your_dashscope_api_key

# Optional
TRANSLATE_MODEL=qwen-mt-flash
TRANSLATE_BASE_URL=
TRANSLATE_API_KEY=

# Set these only when using DeepSeek as the fallback translation backend
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
LOGFIRE_TOKEN=
```

You can also start the app and open Settings from the tray menu or the overlay gear button. Saving Settings writes API keys to the project-root `.env`, so future launches can load them automatically.

### 4. Prepare System Audio Capture

- macOS: install BlackHole and create a Multi-Output Device.
- Windows: usually no extra driver is needed; RainyASR uses WASAPI loopback.
- Linux: use a PulseAudio/PipeWire monitor source and make sure PortAudio can access it.

See "Platform Notes" below for details.

### 5. Run

```bash
# Recommended: use the CLI entry registered in pyproject.toml
uv run rainyasr

# Equivalent: use the Python module entry
uv run python -m rainyasr
```

Avoid running `src/rainyasr/main.py` directly. The app expects the package entrypoint and project-root environment so imports, `.env` loading, and dependencies are consistent.

On startup RainyASR:

1. Configures Logfire. It runs locally by default; telemetry is uploaded only when `LOGFIRE_TOKEN` is set.
2. Loads `config/config.toml`.
3. Reads API keys from environment variables or `.env`; if keys are missing, Settings opens automatically.
4. Detects a loopback audio device.
5. Starts the subtitle overlay, worker, global hotkey, and system tray menu.

Runtime controls:

- Press the default hotkey `ctrl+shift+r` to show or hide subtitles.
- Hover over the subtitle overlay to reveal the top-right controls: the gear opens Settings, and the close button quits the app.
- Use the system tray menu to open Settings, show/hide subtitles, or quit.
- Subtitle appearance changes in Settings apply immediately. Audio, ASR, translation target, API key, and hotkey changes restart affected components automatically.
- Quitting from the tray, closing the subtitle overlay, pressing terminal `Ctrl+C`, or receiving `SIGTERM` all trigger graceful shutdown.
- On shutdown, RainyASR stops the worker, unregisters the hotkey, and saves `config/config.toml`.

Common startup messages:

- Settings opens automatically: in the default Qwen-MT mode this usually means `DASHSCOPE_API_KEY` is missing. If you use a non-Qwen translation backend, set `TRANSLATE_API_KEY` or `DEEPSEEK_API_KEY` as well.
- `API keys required`: the active ASR/translation backend still does not have the required API key in Settings or `.env`.
- `Audio loopback device not found`: configure system audio capture for your platform. On Linux, confirm that a monitor source exists. On macOS, confirm that BlackHole is installed and part of the output chain.
- macOS Accessibility permission prompt: grant permission to the current terminal or packaged app in System Settings > Privacy & Security > Accessibility.

## Configuration

User preferences are stored in `config/config.toml`:

```toml
[audio]
sample_rate = 16000
channels = 1
frame_ms = 100
audio_queue_max_frames = 100
silence_rms_threshold = 0.0003

[asr]
asr_model = "qwen3-asr-flash-realtime"
asr_format = "pcm"
asr_language = "auto"

[subtitle]
font_family = "PingFang SC, Microsoft YaHei, sans-serif"
font_size = 24
window_width = 1000
text_color = "#FFFFFF"
bg_opacity = 80
bilingual_mode = true

[hotkey]
toggle_hotkey = "ctrl+shift+r"

[language]
target_lang = "zh"
```

Sensitive values are read from environment variables or `.env` and are not written to `config/config.toml`. Saving API keys in Settings updates the project-root `.env`:

| Environment variable | Description |
|---|---|
| `DASHSCOPE_API_KEY` | DashScope API key for realtime ASR; also used by default for Qwen-MT translation |
| `DASHSCOPE_COMPATIBLE_BASE_URL` | Optional DashScope OpenAI-compatible API base URL |
| `TRANSLATE_MODEL` | Translation model override, default `qwen-mt-flash` |
| `TRANSLATE_BASE_URL` | Optional generic OpenAI-compatible translation API base URL override |
| `TRANSLATE_API_KEY` | Optional generic translation API key override |
| `DEEPSEEK_API_KEY` | DeepSeek API key, required only when using a DeepSeek translation backend |
| `DEEPSEEK_BASE_URL` | Optional DeepSeek/OpenAI-compatible API base URL |
| `LOGFIRE_TOKEN` | Optional token for uploading Logfire telemetry |

`audio.silence_rms_threshold` controls the local silence gate. If Logfire repeatedly shows `Dropping local silence before ASR session starts` and the logged `rms` is below this threshold, RainyASR considers the system audio too quiet and does not open the ASR session. macOS BlackHole input can have very low RMS values, so lower this in Settings > Audio, for example to `0.0003` or below. You can use `scripts/test_capture.py` to inspect the actual `raw_rms`.

## Development Workflow

Install pre-commit hooks:

```bash
uv run pre-commit install
```

Lint and format:

```bash
uv run ruff check src/ tests/ scripts/
uv run ruff format src/ tests/ scripts/
```

Run tests:

```bash
uv run pytest -q
```

Common focused tests:

```bash
uv run pytest tests/test_main.py -q
uv run pytest tests/test_worker.py -q
uv run pytest tests/test_hotkey.py -q
uv run pytest tests/test_subtitle_window.py -q
```

## Manual Validation Scripts

| Script | Purpose | Command |
|---|---|---|
| `scripts/test_capture.py` | Detect loopback devices and record 10 seconds of system audio | `uv run python scripts/test_capture.py` |
| `scripts/test_asr.py` | Send test audio to DashScope realtime ASR | `DASHSCOPE_API_KEY=xxx uv run python scripts/test_asr.py` |
| `scripts/test_translate.py` | Interactive translation provider test | `uv run python scripts/test_translate.py` |
| `scripts/test_worker.py` | Worker end-to-end path test | `uv run python scripts/test_worker.py` |
| `scripts/demo_subtitle.py` | Visual preview for the subtitle overlay | `uv run python scripts/demo_subtitle.py` |
| `scripts/verify_subtitle_window.py` | Subtitle window lifecycle and behavior checks | `uv run python scripts/verify_subtitle_window.py` |

Subtitle window smoke check:

```bash
# macOS / Linux
uv run python scripts/verify_subtitle_window.py --smoke

# Windows PowerShell
uv run python .\scripts\verify_subtitle_window.py --smoke
```

Manual visual check:

```bash
# macOS / Linux
uv run python scripts/verify_subtitle_window.py

# Windows PowerShell
uv run python .\scripts\verify_subtitle_window.py
```

Checklist:

- The window has no system frame and can be dragged.
- The subtitle window stays above normal application windows.
- Hovering over the window shows the settings gear and close button. The gear opens Settings, and the close button quits the current app.
- Changing subtitle font size, color, width, background opacity, or bilingual mode in Settings updates the overlay immediately, and hover controls remain available.
- Closing one demo subtitle window closes only that window.
- On Linux, verify always-on-top behavior in the target desktop environment, such as GNOME/X11, GNOME/Wayland, or KDE/Wayland.

## Platform Notes

### macOS

System audio capture requires a virtual audio driver such as [BlackHole](https://github.com/ExistentialAudio/BlackHole):

```bash
brew install blackhole-2ch
```

After installing BlackHole through Homebrew, you usually need to restart the system for the driver to become available.

To hear audio while RainyASR captures system playback, create a Multi-Output Device:

1. Open Audio MIDI Setup.
2. Click `+` in the lower-left corner and choose "Create Multi-Output Device".
3. Select `BlackHole 2ch` and your current speakers/headphones.
4. Enable drift correction for BlackHole.
5. Set the new Multi-Output Device as the system sound output.

Global hotkeys require Accessibility permission on macOS. If startup reports missing permission, grant access to the current terminal or packaged app in System Settings > Privacy & Security > Accessibility.

### Windows

RainyASR uses Windows built-in WASAPI loopback capture, so extra drivers are usually not required. If the system default output device is playing audio, the app will try to locate the corresponding loopback device automatically.

If detection fails:

- Confirm that audio is playing through the default output device.
- Confirm that the output device is enabled.
- Restart the app or switch the default output device once.

### Linux

Linux requires PortAudio and a PulseAudio/PipeWire monitor source.

Install PortAudio:

```bash
# Debian / Ubuntu
sudo apt-get install portaudio19-dev

# Fedora
sudo dnf install portaudio-devel

# Arch Linux
sudo pacman -S portaudio
```

Confirm that a monitor source exists:

```bash
pactl get-default-sink
pactl list short sources | grep monitor
```

RainyASR first tries the default sink's `.monitor` source and points `PULSE_SOURCE` to it. If PortAudio/sounddevice does not expose a usable `pulse` input device, RainyASR continues searching for input devices whose names contain `monitor`. It does not fall back to the microphone because that would capture the wrong audio source.

To inspect or switch recording sources manually, install `pavucontrol`:

```bash
# Debian / Ubuntu
sudo apt-get install pavucontrol

# Fedora
sudo dnf install pavucontrol
```

After starting RainyASR or `scripts/test_capture.py`, open the Recording tab in `pavucontrol` and switch the recording stream to the `Monitor of ...` source.

Wayland desktop environments differ in their always-on-top and global hotkey policies. If the global hotkey is unavailable, use the tray menu to show or hide subtitles, and record the desktop environment, display protocol, and window manager when reporting the issue.

## Project Structure

```text
.
├── config/
│   └── config.toml
├── scripts/
│   ├── test_capture.py
│   ├── test_asr.py
│   ├── test_translate.py
│   ├── test_worker.py
│   ├── demo_subtitle.py
│   └── verify_subtitle_window.py
├── src/rainyasr/
│   ├── audio/
│   ├── gui/
│   ├── providers/
│   ├── app.py
│   ├── config.py
│   ├── hotkey.py
│   ├── main.py
│   └── worker.py
├── tests/
├── plan.md
├── pyproject.toml
├── uv.lock
├── README.en.md
└── README.md
```

## License

MIT
