# RainyASR

基于 Python & Qt 的跨平台实时字幕翻译工具。通过录制系统音频流，利用 ASR 进行语音识别、LLM 进行翻译，最终以无边框悬浮窗展示双语字幕。

## 功能特性

- 跨平台系统音频捕获（Windows / macOS / Linux）
- 实时语音识别（DashScope / Qwen ASR）
- 智能翻译（DeepSeek / OpenAI 兼容接口）
- 无边框置顶悬浮字幕窗口
- 全局快捷键显示/隐藏字幕
- 可自定义字幕样式（字体、颜色、透明度）
- 滑动窗口音频切片，平衡延迟与句子完整性

## 开发环境

- **Python**: >= 3.13
- **包管理器**: [uv](https://docs.astral.sh/uv/)
- **GUI 框架**: PySide6
- **音频处理**: sounddevice / soundfile
- **代码风格**: ruff
- **提交检查**: pre-commit

## 前置准备

### 1. 安装 uv

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

安装完成后，确保 `uv` 在 PATH 中：

```bash
uv --version
```

### 2. 克隆项目并同步依赖

```bash
git clone <repository-url>
cd RainyASR

# 创建虚拟环境并安装所有依赖
uv sync
```

`uv sync` 会根据 `pyproject.toml` 和 `uv.lock` 自动创建虚拟环境并安装精确版本的依赖。

### 3. 安装 pre-commit hooks

```bash
uv run pre-commit install
```

这会在 `.git/hooks/` 中安装 pre-commit 钩子，确保每次提交前自动运行代码检查。

## 开发工作流

### 运行代码检查

```bash
# 检查代码风格问题
uv run ruff check src/

# 自动修复可修复的问题
uv run ruff check --fix src/

# 格式化代码
uv run ruff format src/
```

### 手动运行 pre-commit（可选）

```bash
# 对所有文件运行检查
uv run pre-commit run --all-files

# 对暂存文件运行检查
uv run pre-commit run
```

### 运行项目

```bash
# 开发模式运行
uv run python -m rainyasr

# 或直接运行入口文件
uv run python src/rainyasr/main.py
```

## 配置说明

首次启动时会自动创建配置文件，路径：

- **macOS**: `~/.config/RainyASR/config.json`
- **Windows**: `%APPDATA%/RainyASR/config.json`
- **Linux**: `~/.config/RainyASR/config.json`

需要配置的项：

| 配置项 | 说明 |
|--------|------|
| `dashscope_api_key` | DashScope API Key（用于语音识别） |
| `deepseek_api_key` | DeepSeek API Key（用于翻译） |
| `asr_model` | ASR 模型，默认 `qwen3-asr-flash-filetrans` |
| `translate_model` | 翻译模型，默认 `deepseek-chat` |

## 项目结构

```
.
├── src/rainyasr/          # 主源码
│   ├── audio/             # 音频捕获与处理
│   ├── providers/         # ASR / 翻译 Provider
│   ├── gui/               # Qt 界面
│   ├── config.py          # 配置管理
│   ├── worker.py          # 后台录音 + API 调度
│   ├── app.py             # 应用入口
│   └── main.py            # 程序入口
├── .claude/plans/         # 设计文档与计划
├── pyproject.toml         # 项目配置与依赖
├── uv.lock               # 依赖锁定文件
└── README.md
```

## 平台注意事项

### macOS

系统音频捕获需要安装虚拟音频驱动，例如 [BlackHole](https://github.com/ExistentialAudio/BlackHole)。

```bash
brew install blackhole-2ch
```

> **注意**：通过 Homebrew 安装 BlackHole 后，**必须重启系统**才能使驱动生效。

#### 配置混合输出（推荐）

为了同时从 Mac 扬声器听到声音并捕获音频流到 RainyASR，需要创建一个**多输出设备（Multi-Output Device）**：

1. 打开**音频 MIDI 设置**（在"启动台"搜索 "Audio MIDI Setup" 或 "音频 MIDI 设置"）
2. 点击左下角 **+** 按钮，选择**创建多输出设备**
3. 在右侧勾选以下两个设备：
   - **BlackHole 2ch**（用于音频捕获）
   - **MacBook Pro 扬声器**（或你实际使用的输出设备，用于监听）
4. 勾选 **BlackHole 2ch 的漂移矫正**
5. 右键点击刚创建的**多输出设备**，选择**将此设备用于声音输出**

这样配置后，系统声音会同时输出到 BlackHole（供 RainyASR 捕获）和你的扬声器（供你收听），无需在系统设置中来回切换默认输出设备。

### Windows

使用 WASAPI loopback 捕获系统音频，**无需额外安装驱动**。

WASAPI（Windows Audio Session API）loopback 是 Windows 内置功能，RainyASR 会自动检测系统中的 Loopback 输入设备并捕获。只要系统音频正常输出，即可直接使用，无需配置虚拟音频设备。

如果检测不到 Loopback 设备，请检查系统是否使用 WASAPI 作为默认音频后端（绝大多数 Windows 系统默认即是）。

### Linux

#### 1. 安装 PortAudio 开发库

`sounddevice` 依赖 PortAudio，编译时需要系统头文件：

```bash
# Debian / Ubuntu
sudo apt-get install portaudio19-dev

# Fedora
sudo dnf install portaudio-devel

# Arch Linux
sudo pacman -S portaudio
```

#### 2. 音频捕获方式

RainyASR 在 Linux 上按以下优先级检测音频源：

1. **PulseAudio / PipeWire Monitor**（推荐）：设备名通常包含 "Monitor"，可直接捕获系统音频且不影响正常播放
2. **默认输入设备**（Fallback）：未找到 Monitor 时，使用系统默认输入设备

大多数现代发行版（Ubuntu 22.04+、Fedora、Arch 等）默认使用 PipeWire 或 PulseAudio，播放音频时会自动创建 Monitor 设备，无需手动配置。

#### 3. 同时输出到扬声器和录制（可选）

如果你的系统没有自动创建 Monitor 设备，可以通过 `pavucontrol` 手动将应用音频同时路由到 Monitor：

```bash
# 安装 PulseAudio 音量控制
sudo apt-get install pavucontrol   # Debian / Ubuntu
sudo dnf install pavucontrol       # Fedora
```

打开 **pavucontrol** → **播放** 标签页 → 选择目标应用 → 将其输出设备改为 **"Monitor of XXX"**。

> **注意**：Linux 的 Monitor 设备本质就是系统音频的镜像，通常无需像 macOS 那样手动创建"混合输出"。如果程序启动后提示找不到 Monitor，确保有音频正在播放（如播放视频或音乐），此时 PipeWire/PulseAudio 才会创建对应的 Monitor 源。

## License

MIT
