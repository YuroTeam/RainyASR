# Plan: RainyASR 跨平台实时字幕翻译工具

**Source PRD**: `plan.md`
**Complexity**: Large
**Estimated Duration**: 40-60 小时（单人全栈）

## Summary

RainyASR 是一款跨平台桌面字幕翻译工具，通过录制系统音频流，利用 Qwen ASR (DashScope) 进行语音识别、DeepSeek 进行翻译，最终以无边框悬浮窗展示双语字幕。本计划将原始 PRD 中的 8 个 Phase 细化为可独立验证的 16 个 Task，每个 Task 包含明确的输入、输出、验证命令和回退策略。

## Pattern Grounding

| Category | Source | Pattern |
|---|---|---|
| Naming | `pyproject.toml` | 包名 `rainyasr`，模块使用小写下划线 |
| Dependencies | `pyproject.toml` | 使用 `uv` 管理，Python >=3.13 |
| Project layout | `src/main.py` | `src/rainyasr/` 源码结构，平铺模块 |
| Code style | `pyproject.toml` | `ruff` 已配置，需启用 lint + format |

**注意**：当前代码库几乎为空，以下模式将在实现中建立。

---

## 关键修正与改进（相对原始 plan.md）

### 1. 依赖修正
原始 plan.md 列出的依赖与 `pyproject.toml` 不符，需统一：

| 库 | plan.md 状态 | 实际状态 | 操作 |
|---|---|---|---|
| `sounddevice` | 计划使用 | **未安装** | `uv add sounddevice` |
| `soundfile` | 未提及 | **已安装** | 保留，用于 WAV 读写 |
| `qasync` | 计划使用 | **未安装** | `uv add qasync` |
| `pydantic` | 计划使用 | **未安装** | `uv add pydantic` |
| `pynput` | 计划使用 | **未安装** | `uv add pynput` |
| `dashscope` | 计划使用 | **未安装** | `uv add dashscope` |

> **为什么用 sounddevice 而非 soundfile 录制**：`sounddevice` 基于 PortAudio，提供实时流式回调；`soundfile` 仅用于文件读写。两者互补。

### 2. 音频切片策略改进：滑动窗口（Sliding Window）
原始方案是固定 5 秒切片，容易切断句子。改为：
- **窗口大小**：6 秒音频
- **步进间隔**：3 秒（每 3 秒提取一次最近 6 秒的音频）
- **重叠率**：50%
- **效果**：句子完整性显著提升，端到端延迟仍为 3-4 秒

### 3. 线程安全模型
`sounddevice` 回调在独立线程中运行，与 Qt/qasync 主循环交互需加锁：
```
sounddevice callback thread
    │
    ▼
threading.Lock
    │
    ▼
RingBuffer (write)
    ▲
    │
asyncio thread (qasync + Qt)
    │
    ▼
RingBuffer (read) ──> WAV ──> API ──> Qt Signal ──> GUI update
```
- 环形缓冲区用 `threading.Lock` 保护读写
- GUI 更新必须通过 `QMetaObject.invokeMethod` 或自定义 Qt Signal

### 4. 背压与并发控制
- **最大并发 ASR 请求**：2 个（防止网络波动时请求堆积）
- **丢弃策略**：如果当前有 2 个未完成请求，新切片直接丢弃（保证实时性）
- **API 超时**：ASR 15 秒，Translation 20 秒
- **重试**：ASR 失败重试 1 次，Translation 失败重试 2 次，指数退避

### 5. 翻译上下文
`TranslationProvider.translate()` 接收 `history: list[str]` 参数，携带最近 2 句已翻译原文，提示词中注入上下文：
```
历史上下文（仅作参考，不重复翻译）：
- [history[0]]
- [history[1]]

请翻译：[current_text]
```

---

## Files to Change

| File | Action | Why |
|---|---|---|
| `pyproject.toml` | UPDATE | 补全缺失依赖：sounddevice, qasync, pydantic, pynput, dashscope |
| `src/rainyasr/__init__.py` | CREATE | 包标识 |
| `src/rainyasr/config.py` | CREATE | Pydantic 配置模型，含 API Key、音频参数、样式 |
| `src/rainyasr/audio/capture.py` | CREATE | 跨平台系统音频检测与录制封装 |
| `src/rainyasr/audio/ring_buffer.py` | CREATE | 线程安全环形音频缓冲区 |
| `src/rainyasr/audio/wav.py` | CREATE | 内存 WAV 封装（BytesIO） |
| `src/rainyasr/audio/__init__.py` | CREATE | 音频模块导出 |
| `src/rainyasr/providers/base.py` | CREATE | ASRProvider + TranslationProvider 抽象基类 |
| `src/rainyasr/providers/asr.py` | CREATE | Qwen ASR (DashScope) 异步任务封装 |
| `src/rainyasr/providers/translate.py` | CREATE | DeepSeek OpenAI 兼容实现 |
| `src/rainyasr/providers/__init__.py` | CREATE | Provider 导出 |
| `src/rainyasr/gui/subtitle_window.py` | CREATE | 无边框置顶悬浮字幕窗口 |
| `src/rainyasr/gui/settings_dialog.py` | CREATE | 设置面板对话框 |
| `src/rainyasr/gui/__init__.py` | CREATE | GUI 模块导出 |
| `src/rainyasr/worker.py` | CREATE | 后台录音+API 调度工作协程 |
| `src/rainyasr/app.py` | CREATE | QApplication + qasync 事件循环初始化 |
| `src/rainyasr/main.py` | CREATE | 程序入口（CLI + GUI 启动） |
| `src/main.py` | DELETE | 空文件，由 `src/rainyasr/main.py` 替代 |
| `.claude/plans/rainyasr.plan.md` | CREATE | 本实施计划 |

---

## Tasks

### Task 1: 依赖与项目结构初始化
- **Action**: 修正 `pyproject.toml` 依赖；创建 `src/rainyasr/` 目录结构；配置 ruff lint/format；删除空 `src/main.py`
- **Mirror**: `uv` 包管理约定，`src/` 布局
- **Validate**:
  ```bash
  uv sync && uv run python -c "import rainyasr"
  uv run ruff check src/ && uv run ruff format --check src/
  ```

### Task 2: 配置模型（config.py）
- **Action**: 用 Pydantic 定义 `AppConfig`，包含：
  - API 配置：`dashscope_api_key`, `deepseek_api_key`, `dashscope_base_url`, `deepseek_base_url`, `asr_model`, `translate_model`
  - 音频配置：`sample_rate=16000`, `channels=1`, `window_size_sec=6.0`, `step_sec=3.0`, `max_concurrent_requests=2`
  - 字幕样式：`font_family`, `font_size`, `text_color`, `bg_opacity`, ` bilingual_mode: bool`
  - 快捷键：`toggle_hotkey: str = "ctrl+shift+r"`
  - 持久化路径：`~/.config/RainyASR/config.json`，保存时设置文件权限 `0o600`
- **Mirror**: Pydantic v2 BaseModel 模式
- **Validate**:
  ```bash
  uv run python -c "
  from rainyasr.config import AppConfig
  c = AppConfig()
  c.save()
  c2 = AppConfig.load()
  assert c2.dashscope_api_key == c.dashscope_api_key
  import os, stat
  path = os.path.expanduser('~/.config/RainyASR/config.json')
  assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
  "
  ```

### Task 3: 跨平台音频设备检测（capture.py — 检测部分）
- **Action**: 实现 `AudioDeviceDetector`：
  - Windows：枚举 WASAPI hostapi，查找 `Loopback` 设备
  - macOS：枚举设备名包含 `BlackHole` 的输入设备
  - Linux：枚举设备名包含 `Monitor` 的输入设备，fallback 到 default
  - 返回 `(device_id, device_name, sample_rate)` 或抛出 `NoLoopbackDeviceError`
- **Mirror**: `sounddevice.query_devices()` API
- **Validate**:
  ```bash
  uv run python -c "
  from rainyasr.audio.capture import AudioDeviceDetector
  d = AudioDeviceDetector()
  info = d.find_loopback_device()
  print(f'Device: {info.name}, SR: {info.sample_rate}')
  "
  ```

### Task 4: 环形音频缓冲区（ring_buffer.py）
- **Action**: 实现线程安全的 `AudioRingBuffer`：
  - 内部用 `numpy.ndarray`，`dtype=float32`
  - 容量：`sample_rate * max_duration`（默认 15 秒）
  - 方法：`write(samples)`, `read_last_n_seconds(n)` 返回 numpy 数组
  - 用 `threading.Lock` 保护
- **Mirror**: 生产者-消费者锁模式
- **Validate**:
  ```bash
  uv run python -m pytest tests/test_ring_buffer.py -v
  # 测试：单线程读写一致性、多线程并发安全、边界条件
  ```

### Task 5: 内存 WAV 封装（wav.py）
- **Action**: 实现 `encode_wav(audio_array: np.ndarray, sample_rate: int) -> bytes`：
  - 输入 float32 数组（范围 [-1, 1]）
  - 输出标准 WAV 格式 bytes（16-bit PCM）
  - 使用 `soundfile` 写入 `io.BytesIO`
- **Mirror**: `soundfile.write` with `BytesIO`
- **Validate**:
  ```bash
  uv run python -c "
  import numpy as np
  from rainyasr.audio.wav import encode_wav
  data = np.random.uniform(-1, 1, 16000).astype(np.float32)
  wav_bytes = encode_wav(data, 16000)
  assert wav_bytes[:4] == b'RIFF'
  print(f'WAV size: {len(wav_bytes)} bytes')
  "
  ```

### Task 6: 命令行验证音频录制
- **Action**: 编写临时脚本 `scripts/test_capture.py`：
  - 检测设备 → 启动录制 10 秒 → 保存 `audio_test.wav`
  - 用播放器验证内容是否为系统音频（而非麦克风）
- **Mirror**: 独立验证脚本
- **Validate**: 手动播放 `audio_test.wav` 确认内容是系统音频

### Task 7: Provider 抽象基类（base.py）
- **Action**: 定义：
  ```python
  class ASRProvider(ABC):
      @abstractmethod
      async def transcribe(self, audio_bytes: bytes) -> str: ...

  class TranslationProvider(ABC):
      @abstractmethod
      async def translate(self, text: str, target_lang: str = "zh", history: list[str] | None = None) -> str: ...
  ```
- **Mirror**: Python ABC 抽象基类
- **Validate**:
  ```bash
  uv run python -c "from rainyasr.providers.base import ASRProvider; import inspect; assert inspect.isabstract(ASRProvider)"
  ```

### Task 8: ASR Provider 实现（asr.py）
- **Action**: `QwenASRProvider`：
  - 基于 `dashscope` SDK 的 `Transcription` 模块
  - `transcribe(audio_bytes: bytes)` 流程：
    1. 将 WAV bytes 写入临时文件（`tempfile.NamedTemporaryFile`，后缀 `.wav`）
    2. 调用 `dashscope.Transcription.call(model="qwen3-asr-flash-filetrans", file=file_path, language="auto")`
    3. SDK 内部完成异步任务提交 → 轮询 → 返回结果
    4. 清理临时文件
  - 返回 `result.output.results[0].transcription`
  - 异常处理：`dashscope.errors.RequestFailure`, `dashscope.errors.InvalidRequest`，重试 1 次
  - **注意**：DashScope 语音识别非 OpenAI 兼容接口，不支持直接传 bytes，需借助临时文件
- **Mirror**: `dashscope` SDK `Transcription` 同步调用模式
- **Validate**:
  ```bash
  DASHSCOPE_API_KEY=xxx uv run python scripts/test_asr.py
  # 读取 audio_test.wav，调用 DashScope API，终端打印识别文字
  ```

### Task 9: 翻译 Provider 实现（translate.py）
- **Action**: `DeepSeekTranslationProvider`：
  - base_url: `https://api.deepseek.com`
  - model: `deepseek-chat`
  - 系统提示词注入历史上下文（最近 2 句）
  - 异常处理：重试 2 次，指数退避
- **Mirror**: OpenAI compatible chat completions
- **Validate**:
  ```bash
  uv run python scripts/test_translate.py
  # 输入英文句子，终端打印中文翻译
  ```

### Task 10: 无边框悬浮字幕窗口（subtitle_window.py）
- **Action**: `SubtitleWindow(QFramelessWindow)`：
  - 窗口标志：`FramelessWindowHint | WindowStaysOnTopHint | WindowDoesNotHaveShadow | Tool`
  - 属性：`WA_TranslucentBackground`, `WA_TransparentForMouseEvents`（可选）
  - 显示内容：两行 QLabel（原文 + 译文，或仅译文）
  - 鼠标拖拽：重写 `mousePressEvent` / `mouseMoveEvent`
  - 样式：通过 QSS 动态设置字体、颜色、透明度
  - 提供 `update_subtitle(original: str, translated: str)` 公共方法
- **Mirror**: PySide6 QWidget API
- **Validate**:
  ```bash
  uv run python -c "
  from PySide6.QtWidgets import QApplication
  from rainyasr.gui.subtitle_window import SubtitleWindow
  app = QApplication([])
  w = SubtitleWindow()
  w.update_subtitle('Hello world', '你好世界')
  w.show()
  app.exec()
  "
  # 验证：窗口置顶、无边框、可拖动、文字显示正常
  ```

### Task 11: qasync 事件循环桥接（app.py）
- **Action**: `App` 类：
  - 创建 `QApplication(sys.argv)`
  - 用 `qasync.QEventLoop` 替代默认 asyncio loop
  - 集成 `SubtitleWindow`
  - 提供 `run()` 方法启动事件循环
- **Mirror**: `qasync` 官方示例模式
- **Validate**:
  ```bash
  uv run python -c "from rainyasr.app import App; print('App import OK')"
  ```

### Task 12: 后台 Worker 协程（worker.py）
- **Action**: `SubtitleWorker`：
  - 持有 `AudioRingBuffer`, `ASRProvider`, `TranslationProvider`, `SubtitleWindow`
  - `start()`：启动 `sounddevice.InputStream` 回调写入 ring buffer；启动 `asyncio.Task` 每 3 秒调度一次 `process_window()`
  - `process_window()`：
    1. 检查并发数（`< max_concurrent_requests`）
    2. 从 ring buffer 读取最近 6 秒音频
    3. encode WAV
    4. 并发数 +1，调用 `asr.transcribe()`
    5. 如果识别结果非空，调用 `translate.translate(history=recent_2)`
    6. 通过 Qt Signal 发射 `(original, translated)` 给 GUI
    7. 并发数 -1
  - `stop()`：停止录音流，取消协程任务
- **Mirror**: asyncio + Qt Signal 跨线程通信
- **Validate**:
  ```bash
  uv run python scripts/test_worker.py
  # 验证：持续录音 -> API 调用 -> 终端打印双语字幕，不卡 UI
  ```

### Task 13: 设置面板（settings_dialog.py）
- **Action**: `SettingsDialog(QDialog)`：
  - API Key 输入（DashScope + DeepSeek，QLineEdit + password 掩码）
  - 目标语言选择（QComboBox：zh, en, ja, ko...）
  - 切片参数（窗口大小、步进间隔，带合理范围限制）
  - 字幕样式（字体选择、大小滑动条、颜色选择器、透明度滑动条）
  - 快捷键绑定（QKeySequenceEdit）
  - 保存时调用 `AppConfig.save()`，权限 0o600
- **Mirror**: PySide6 表单布局
- **Validate**: 手动测试 UI 交互

### Task 14: 全局快捷键（pynput）
- **Action**: `GlobalHotkeyManager`：
  - 使用 `pynput.keyboard.GlobalHotKeys` 注册 `toggle_hotkey`
  - 回调通过 `QMetaObject.invokeMethod` 切换字幕窗口显示/隐藏
  - macOS 提示用户授予辅助功能权限
  - 应用退出时注销热键
- **Mirror**: `pynput` global hotkey pattern
- **Validate**: 手动测试快捷键响应

### Task 15: 主入口集成（main.py）
- **Action**: 程序入口逻辑：
  1. 加载 `AppConfig`
  2. 检测音频设备（失败则弹窗提示安装指引）
  3. 初始化 `App` -> `SubtitleWindow` -> `SubtitleWorker`
  4. 启动全局快捷键
  5. 托盘图标（可选，但推荐）：`QSystemTrayIcon` + 右键菜单（显示/隐藏、设置、退出）
  6. 优雅退出：停止 worker、保存配置
- **Mirror**: PySide6 应用生命周期
- **Validate**:
  ```bash
  uv run python -m rainyasr
  # 完整流程验证：启动 -> 检测 -> 录音 -> API -> 字幕显示
  ```

### Task 16: PyInstaller 打包配置
- **Action**: `rainyasr.spec` 或 `pyproject.toml` [tool.pyinstaller]：
  - 包含 `sounddevice` 依赖的 PortAudio 动态库
  - Windows：`--windowed`，隐藏控制台
  - macOS：`--windowed`，`.app`  bundle
  - Linux：单文件可执行
  - 图标、版本信息
- **Mirror**: PyInstaller spec 文件
- **Validate**: 在三平台分别构建并运行测试

---

## 开发顺序与依赖图

```
Task 1 (依赖/结构)
    │
    ├──> Task 2 (配置)
    │       │
    │       ├──> Task 3 (音频检测)
    │       │       │
    │       │       ├──> Task 4 (环形缓冲)
    │       │       │       │
    │       │       │       ├──> Task 5 (WAV 封装)
    │       │       │       │       │
    │       │       │       │       ├──> Task 6 (音频录制验证) ──┐
    │       │       │       │       │                            │
    │       ├──> Task 7 (Provider 基类)                          │
    │       │       │                                            │
    │       │       ├──> Task 8 (ASR) <──────────────────────────┤
    │       │       │                                            │
    │       │       ├──> Task 9 (翻译) <─────────────────────────┤
    │       │       │                                            │
    │       ├──> Task 10 (字幕窗口)                               │
    │       │       │                                            │
    │       ├──> Task 11 (qasync)                                 │
    │       │       │                                            │
    │       └───> Task 12 (Worker) <─────────────────────────────┘
    │               │
    │               ├──> Task 13 (设置面板)
    │               │
    │               ├──> Task 14 (快捷键)
    │               │
    │               └──> Task 15 (主入口)
    │                       │
    │                       └──> Task 16 (打包)
```

**建议开发节奏**：
- 第 1-2 天：Task 1-6（音频链路打通）
- 第 3-4 天：Task 7-9（API 链路打通）
- 第 5-7 天：Task 10-15（GUI 集成与主程序）
- 第 8-10 天：Task 16 + 跨平台测试 + 文档

---

## Validation

```bash
# 代码风格
uv run ruff check src/ && uv run ruff format --check src/

# 类型检查（建议添加 mypy）
# uv add --dev mypy
# uv run mypy src/rainyasr

# 单元测试（建议添加 pytest）
# uv add --dev pytest pytest-asyncio
# uv run pytest tests/ -v

# 端到端验证（需要配置 API Key）
export DASHSCOPE_API_KEY="..."
export DEEPSEEK_API_KEY="..."
uv run python -m rainyasr
```

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| macOS BlackHole 未安装/配置错误 | 高 | 高 | 启动时检测，未检测到弹窗引导用户；提供详细文档链接 |
| Linux sounddevice 无法找到 PortAudio | 中 | 高 | 文档说明需 `pulseaudio-alsa` 或 `portaudio19-dev`；提供 fallback 到 `arecord` 的备用方案 |
| API Key 无效/额度耗尽 | 中 | 中 | 启动时验证 API 连通性（轻量 health check）；错误提示区分"网络"与"认证"问题 |
| 全屏游戏遮挡字幕窗口 | 中 | 中 | WindowStaysOnTopHint 在大多数场景有效；文档说明部分游戏（反作弊全屏）可能无法置顶 |
| 音频切片切断句子导致 ASR 质量差 | 中 | 中 | 滑动窗口 50% 重叠缓解；V2 引入 VAD 彻底解决这个问题 |
| Worker 协程异常未捕获导致静默崩溃 | 中 | 高 | `process_window()` 内层 try/except 捕获所有异常，记录日志并继续循环 |
| 翻译历史上下文过长导致 Token 消耗增加 | 低 | 低 | 限制 history 为最近 2 句，平均增加 <100 tokens/请求 |
| PyInstaller 打包后动态库缺失 | 中 | 高 | 每个平台 CI/CD 构建测试；`--collect-all sounddevice` 确保 PortAudio 库被打包 |

---

## Acceptance Criteria

- [ ] Task 1-16 全部完成
- [ ] `uv run ruff check src/` 零错误
- [ ] macOS/Windows/Linux 至少一个平台完整流程验证通过：播放视频 -> 系统音频捕获 -> ASR -> 翻译 -> 悬浮字幕显示
- [ ] API 断开时程序不崩溃，UI 提示错误
- [ ] 配置持久化，重启后恢复用户设置
- [ ] 全局快捷键可正常切换字幕显示/隐藏
- [ ] PyInstaller 打包后的可执行文件可在目标平台独立运行
