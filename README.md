# RainyASR

RainyASR 是一个基于 Python 和 Qt 的跨平台实时字幕翻译工具。它捕获系统播放音频，使用 DashScope Qwen 实时 ASR 识别语音，再通过 Qwen-MT/OpenAI 兼容接口翻译，并在无边框置顶悬浮窗中显示双语字幕。

## 功能特性

- 跨平台系统音频捕获：Windows WASAPI loopback、macOS BlackHole、Linux PulseAudio/PipeWire monitor
- DashScope Qwen 实时语音识别
- Qwen-MT/OpenAI 兼容翻译接口，默认 `qwen-mt-flash`
- 无边框、置顶、可拖拽字幕悬浮窗
- 双语/单语字幕显示，支持字体、字号、颜色、背景透明度配置
- 全局快捷键显示/隐藏字幕，默认 `ctrl+shift+r`
- 系统托盘菜单：显示/隐藏字幕、设置、退出
- 静音门控和本地预滚音频，减少无声段请求并保留开头语音

## 环境要求

- Python `>=3.13`
- [uv](https://docs.astral.sh/uv/)
- PySide6
- PortAudio 兼容音频环境
- DashScope API Key。使用非 Qwen 翻译后端时，还需要对应翻译 API Key

## 快速开始

### 1. 安装 uv

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows PowerShell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

确认安装成功：

```bash
uv --version
```

### 2. 同步依赖

```bash
git clone <repository-url>
cd RainyASR
uv sync
```

### 3. 配置 API Key

在项目根目录创建 `.env`：

```bash
DASHSCOPE_API_KEY=your_dashscope_api_key

# 可选
TRANSLATE_MODEL=qwen-mt-flash
TRANSLATE_BASE_URL=
TRANSLATE_API_KEY=

# 使用 DeepSeek 作为备用翻译后端时再设置
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
LOGFIRE_TOKEN=
```

也可以启动后通过托盘菜单打开 Settings 填写 API Key。点击 Save 后会写入项目根目录 `.env`，后续启动会自动读取。

### 4. 准备系统音频捕获

- macOS：安装 BlackHole，并创建多输出设备。
- Windows：通常无需额外驱动，使用系统 WASAPI loopback。
- Linux：需要 PulseAudio/PipeWire monitor source，并确保 PortAudio 可用。

具体步骤见下方“平台注意事项”。

### 5. 启动方式

```bash
# 推荐：使用 pyproject.toml 中注册的命令行入口
uv run rainyasr

# 等价：使用 Python 模块入口
uv run python -m rainyasr
```

不建议直接运行 `src/rainyasr/main.py`。主程序依赖包入口和项目根目录环境，使用上面两个入口可以确保 import 路径、`.env` 加载和依赖环境一致。

启动后会依次执行：

1. 配置 Logfire。本地默认可运行；只有设置了 `LOGFIRE_TOKEN` 才上传 telemetry。
2. 加载 `config/config.toml`。
3. 从环境变量或 `.env` 读取 API Key；如果缺少 API Key，会自动打开 Settings。
4. 检测系统 loopback 音频设备。
5. 启动字幕窗口、worker、全局快捷键和系统托盘菜单。

运行时操作：

- 使用默认快捷键 `ctrl+shift+r` 显示/隐藏字幕。
- 通过系统托盘菜单打开 Settings、显示/隐藏字幕或退出。
- 在 Settings 中修改字幕样式会立即应用；修改音频、ASR、翻译目标、API Key 或快捷键后，运行组件会自动重启。
- 从托盘退出、关闭字幕窗口、终端 `Ctrl+C` 或 `SIGTERM` 都会触发优雅退出。
- 退出时会停止 worker、注销热键并保存 `config/config.toml`。

常见启动提示：

- Settings 自动打开：默认 Qwen-MT 模式下通常说明缺少 `DASHSCOPE_API_KEY`。使用非 Qwen 翻译后端时，还需要 `TRANSLATE_API_KEY` 或 `DEEPSEEK_API_KEY`。
- `API keys required`：说明 Settings 或 `.env` 中仍未填写当前 ASR/翻译后端需要的 API Key。
- `Audio loopback device not found`：按下方平台说明配置系统音频捕获。Linux 上请确认存在 monitor source；macOS 上请确认 BlackHole 已安装并作为输出链路的一部分。
- macOS Accessibility 权限提示：到 System Settings > Privacy & Security > Accessibility 授权当前终端或打包后的应用。

## 配置

用户偏好保存在项目内 `config/config.toml`：

```toml
[audio]
sample_rate = 16000
channels = 1
frame_ms = 100
audio_queue_max_frames = 100

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

敏感配置从环境变量或 `.env` 读取，不会写入 `config/config.toml`。在 Settings 中保存 API Key 时，会同步更新项目根目录 `.env`：

| 环境变量 | 说明 |
|---|---|
| `DASHSCOPE_API_KEY` | DashScope API Key，用于实时 ASR；默认也用于 Qwen-MT 翻译 |
| `DASHSCOPE_COMPATIBLE_BASE_URL` | DashScope OpenAI 兼容 API Base URL，可选 |
| `TRANSLATE_MODEL` | 翻译模型覆盖值，默认 `qwen-mt-flash` |
| `TRANSLATE_BASE_URL` | 通用 OpenAI 兼容翻译 API Base URL 覆盖值，可选 |
| `TRANSLATE_API_KEY` | 通用翻译 API Key 覆盖值，可选 |
| `DEEPSEEK_API_KEY` | DeepSeek API Key，仅使用 DeepSeek 翻译后端时需要 |
| `DEEPSEEK_BASE_URL` | DeepSeek/OpenAI 兼容 API Base URL，可选 |
| `LOGFIRE_TOKEN` | 存在时上传 Logfire telemetry，可选 |

## 开发工作流

安装 pre-commit hooks：

```bash
uv run pre-commit install
```

代码检查和格式化：

```bash
uv run ruff check src/ tests/ scripts/
uv run ruff format src/ tests/ scripts/
```

运行测试：

```bash
uv run pytest -q
```

常用局部测试：

```bash
uv run pytest tests/test_main.py -q
uv run pytest tests/test_worker.py -q
uv run pytest tests/test_hotkey.py -q
uv run pytest tests/test_subtitle_window.py -q
```

## 手动验证脚本

| 脚本 | 功能 | 运行方式 |
|---|---|---|
| `scripts/test_capture.py` | 检测 loopback 设备并录制 10 秒系统音频 | `uv run python scripts/test_capture.py` |
| `scripts/test_asr.py` | 发送测试音频到 DashScope 实时 ASR | `DASHSCOPE_API_KEY=xxx uv run python scripts/test_asr.py` |
| `scripts/test_translate.py` | 翻译 Provider 交互测试 | `uv run python scripts/test_translate.py` |
| `scripts/test_worker.py` | Worker 端到端链路测试 | `uv run python scripts/test_worker.py` |
| `scripts/demo_subtitle.py` | 悬浮字幕窗口视觉预览 | `uv run python scripts/demo_subtitle.py` |
| `scripts/verify_subtitle_window.py` | 字幕窗口生命周期与窗口行为验证 | `uv run python scripts/verify_subtitle_window.py` |

字幕窗口 smoke 验证：

```bash
# macOS / Linux
uv run python scripts/verify_subtitle_window.py --smoke

# Windows PowerShell
uv run python .\scripts\verify_subtitle_window.py --smoke
```

人工视觉验证：

```bash
# macOS / Linux
uv run python scripts/verify_subtitle_window.py

# Windows PowerShell
uv run python .\scripts\verify_subtitle_window.py
```

检查项：

- 窗口无系统边框，能拖拽移动。
- 切换到普通应用窗口后，字幕仍保持在普通窗口之上。
- 鼠标移入窗口后显示关闭按钮。
- 关闭任意一个字幕窗口时，只关闭当前窗口。
- Linux 需要在目标桌面环境实际验证置顶行为，例如 GNOME/X11、GNOME/Wayland、KDE/Wayland。

## 平台注意事项

### macOS

系统音频捕获需要安装虚拟音频驱动，例如 [BlackHole](https://github.com/ExistentialAudio/BlackHole)：

```bash
brew install blackhole-2ch
```

通过 Homebrew 安装 BlackHole 后，通常需要重启系统让驱动生效。

为了同时听到声音并让 RainyASR 捕获系统音频，建议创建多输出设备：

1. 打开“音频 MIDI 设置”。
2. 点击左下角 `+`，选择“创建多输出设备”。
3. 勾选 `BlackHole 2ch` 和当前扬声器/耳机。
4. 为 BlackHole 勾选“漂移矫正”。
5. 将新建的多输出设备设为系统声音输出。

全局快捷键在 macOS 上需要 Accessibility 权限。若启动时提示权限不足，请在 System Settings > Privacy & Security > Accessibility 中授权当前终端或打包后的应用。

### Windows

RainyASR 使用 Windows 内置 WASAPI loopback 捕获系统音频，通常不需要额外驱动。只要系统默认输出设备正常播放声音，程序会尝试自动找到对应 loopback 设备。

如果检测失败：

- 确认系统正在通过默认输出设备播放音频。
- 确认输出设备未被禁用。
- 尝试重启应用或切换一次默认输出设备。

### Linux

Linux 需要 PortAudio 和 PulseAudio/PipeWire monitor source。

安装 PortAudio：

```bash
# Debian / Ubuntu
sudo apt-get install portaudio19-dev

# Fedora
sudo dnf install portaudio-devel

# Arch Linux
sudo pacman -S portaudio
```

确认存在 monitor source：

```bash
pactl get-default-sink
pactl list short sources | grep monitor
```

RainyASR 会优先使用默认 sink 的 `.monitor` source，并通过 `PULSE_SOURCE` 指向它。如果 PortAudio/sounddevice 没有暴露可用的 `pulse` 输入设备，则会继续查找名称中包含 `monitor` 的输入设备。程序不会回退到麦克风，因为这会捕获错误音频源。

如需手动检查或切换录音源，可以安装 `pavucontrol`：

```bash
# Debian / Ubuntu
sudo apt-get install pavucontrol

# Fedora
sudo dnf install pavucontrol
```

启动 RainyASR 或 `scripts/test_capture.py` 后，打开 `pavucontrol` 的 Recording 标签页，将对应录音流切到 `Monitor of ...` source。

Wayland 桌面环境对置顶窗口和全局快捷键的策略差异较大。若全局快捷键不可用，优先使用托盘菜单显示/隐藏字幕，并记录桌面环境、显示协议和窗口管理器。

## 项目结构

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
└── README.md
```

## License

MIT
