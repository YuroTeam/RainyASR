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

**注意**：Task 1-6 及配套测试/CI 已完成，后续从 Task 7 开始。主链路已调整为 DashScope 实时 WebSocket ASR；Task 5 的 PCM/WAV 编码工具保留为实时音频发送、调试录音与文件转写 fallback 的基础能力。

---

## 关键修正与改进（相对原始 plan.md）

### 1. 依赖修正
原始 plan.md 列出的依赖与 `pyproject.toml` 不符，需统一：

| 库 | plan.md 状态 | 实际状态 | 说明 |
|---|---|---|---|
| `sounddevice` | 计划使用 | **已安装** | macOS/Linux 音频捕获 |
| `soundfile` | 未提及 | **已安装** | WAV 读写 |
| `qasync` | 计划使用 | **已安装** | Qt + asyncio 桥接 |
| `pydantic` | 计划使用 | **已安装** | 配置模型验证 |
| `python-dotenv` | 计划使用 | **已安装** | `.env` 环境变量加载；已移除错误的 `dotenv` wrapper 包 |
| `pynput` | 计划使用 | **已安装** | 全局快捷键 |
| `dashscope` | 计划使用 | **已安装** | Qwen ASR 实时 WebSocket SDK |
| `logfire` | 计划使用 | **已安装** | 结构化日志 |
| `pyaudio` | 未提及 | **已安装（Windows only）** | Windows WASAPI 底层音频 |
| `pyaudiowpatch` | 未提及 | **已安装（Windows only）** | Windows loopback 设备检测 |
| `soundcard` | 未提及 | **已安装** | 备选音频捕获方案 |
| `openai` | 未提及 | **已安装** | DeepSeek 翻译（OpenAI 兼容接口） |

> **为什么用 sounddevice 而非 soundfile 录制**：`sounddevice` 基于 PortAudio，提供实时流式回调；`soundfile` 仅用于文件读写。两者互补。
> **为什么用 logfire**：Pydantic 团队出品的结构化日志与可观测性工具，自动捕获异常堆栈、支持性能追踪，且与 Pydantic 生态深度集成。
> **配置加载约定**：`EnvConfig` 首次访问环境变量时才调用 `load_dotenv()`，避免 `import rainyasr.config` 产生文件系统读取和环境变量修改副作用。

### 2. 实时 ASR 策略：WebSocket 音频流
原始方案是固定 5 秒文件切片，容易切断句子且端到端延迟较高。主链路改为 DashScope 实时语音识别：
- **ASR 模型优先级**：默认 `qwen3-asr-flash-realtime`；如需更低成本或更稳定中文转写，可实测 `fun-asr-realtime`
- **音频格式**：优先 `pcm`，16 kHz，单声道，16-bit little-endian PCM
- **推送粒度**：从 `sounddevice` 回调接收 float32 frame，必要时显式应用可配置增益/peak limiter，再转换为 PCM16 bytes 后持续发送到 WebSocket
- **结果处理**：partial transcript 只更新原文临时字幕；final transcript 才进入翻译队列
- **保留 fallback**：Task 5 的 WAV 编码和 Task 4 的 ring buffer 可用于调试录音、文件转写 fallback、断线重连后的短时补偿

### 3. 线程安全模型
`sounddevice` 回调在独立线程中运行，与 Qt/qasync 主循环交互需加锁：
```
sounddevice callback thread
    │
    ▼
asyncio.Queue / thread-safe handoff
    │
    ▼
PCM16 frame bytes
    │
    ▼
RealtimeASRProvider.send_audio()
    │
    ▼
DashScope WebSocket ──> partial/final events ──> Translation queue ──> Qt Signal ──> GUI update
```
- `AudioRingBuffer` 保留，用 `threading.Lock` 保护读写，主要用于调试、录音回放和 fallback
- 实时主链路优先使用 `asyncio.Queue` 或 `loop.call_soon_threadsafe()` 将音频 frame 从回调线程投递到 asyncio/qasync 事件循环；回调线程不得做阻塞 stdout/file I/O
- GUI 更新必须通过 `QMetaObject.invokeMethod` 或自定义 Qt Signal

### 4. 背压与并发控制
- **ASR 连接**：主流程保持 1 条实时 WebSocket 会话，避免并发文件请求
- **音频队列上限**：限制待发送 frame 数；网络阻塞时丢弃最旧 frame 或触发重连，保证实时性优先
- **翻译并发**：Translation 请求最多 1-2 个并发；只对 final transcript 翻译，partial 不翻译
- **API 超时**：ASR 连接/心跳/首包分别设置超时；Translation 20 秒
- **重试**：ASR WebSocket 断线后指数退避重连；Translation 失败重试 2 次

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

| File | Action | 状态 | Why |
|---|---|---|---|
| `pyproject.toml` | UPDATE | ✅ | 补全所有依赖，含 Windows 平台限定包 |
| `src/rainyasr/__init__.py` | CREATE | ✅ | 包标识 |
| `src/rainyasr/config.py` | CREATE | ✅ | Pydantic 配置模型，含 API Key、音频参数、样式 |
| `.env.example` | CREATE | ✅ | 环境变量模板 |
| `config/config.toml` | CREATE | ✅ | 用户偏好配置（TOML），位于项目根目录 `config/` 子文件夹下 |
| `src/rainyasr/audio/capture.py` | CREATE | ✅ | 跨平台系统音频检测（macOS/Linux: sounddevice，Windows: pyaudiowpatch） |
| `src/rainyasr/audio/ring_buffer.py` | CREATE | ✅ | 线程安全环形音频缓冲区 |
| `src/rainyasr/audio/wav.py` | CREATE | ✅ | PCM16/WAV 编码工具；默认纯 PCM 转换，显式 gain/limiter 用于低电平 loopback |
| `src/rainyasr/audio/__init__.py` | CREATE | ✅ | 音频模块导出 |
| `src/rainyasr/providers/base.py` | CREATE | ✅ | RealtimeASRProvider + TranslationProvider 抽象基类 |
| `src/rainyasr/providers/asr.py` | CREATE | ✅ | Qwen/DashScope 实时 WebSocket ASR 封装 |
| `src/rainyasr/providers/translate.py` | CREATE | ✅ | DeepSeek OpenAI 兼容实现 |
| `src/rainyasr/providers/__init__.py` | CREATE | ✅ | Provider 导出 |
| `src/rainyasr/gui/subtitle_window.py` | CREATE | ✅ | 无边框置顶悬浮字幕窗口 |
| `src/rainyasr/gui/settings_dialog.py` | CREATE | ⏳ | 设置面板对话框 |
| `src/rainyasr/gui/__init__.py` | CREATE | ✅ | GUI 模块导出（空） |
| `src/rainyasr/worker.py` | CREATE | ⏳ | 后台录音+实时 ASR 连接+翻译调度工作协程 |
| `src/rainyasr/app.py` | CREATE | ⏳ | QApplication + qasync 事件循环初始化 |
| `src/rainyasr/main.py` | CREATE | ⏳ | 程序入口（CLI + GUI 启动） |
| `src/rainyasr/__main__.py` | CREATE | ⏳ | 支持 `python -m rainyasr` 入口 |
| `src/main.py` | DELETE | ✅ | 空文件已删除 |
| `tests/test_capture.py` | CREATE | ✅ | 跨平台音频设备检测测试（按平台 skip） |
| `.github/workflows/test.yml` | CREATE | ✅ | GitHub Actions CI（ruff + pytest，三平台） |
| `.claude/plans/rainyasr.plan.md` | CREATE | ✅ | 本实施计划 |
| `scripts/test_capture.py` | CREATE | ✅ | Task 6 手动验证脚本；非阻塞统计输出，保存增益后的调试 WAV |
| `scripts/test_asr.py` | CREATE | ✅ | Task 8 手动验证脚本；正弦波/谐波测试音发送至 DashScope 实时 ASR |
| `scripts/` | CREATE | ✅ | 手动验证脚本目录（test_capture.py、test_asr.py，后续 Task 9/12 继续补充） |

---

## Tasks

### Task 1: 依赖与项目结构初始化 ✅
- **Action**: 修正 `pyproject.toml` 依赖（含 `logfire`）；创建 `src/rainyasr/` 目录结构；配置 ruff lint/format；删除空 `src/main.py`
- **Mirror**: `uv` 包管理约定，`src/` 布局
- **Validate**:
  ```bash
  uv sync && uv run python -c "import rainyasr"
  uv run ruff check src/ && uv run ruff format --check src/
  ```

### Task 2: 配置模型（config.py + config.toml） ✅
- **Action**: 用 Pydantic + python-dotenv + TOML 定义双层配置：
  - **`EnvConfig`**（敏感信息，从 `.env` 读取）：`DASHSCOPE_API_KEY`, `DEEPSEEK_API_KEY`, `DASHSCOPE_BASE_URL`, `DEEPSEEK_BASE_URL`, `ASR_MODEL`, `TRANSLATE_MODEL`, `LOGFIRE_TOKEN`
  - `.env` 通过 `load_env_file()` 延迟加载，避免导入 `rainyasr.config` 时产生副作用
  - **`AppConfig`**（用户偏好，持久化到 `config/config.toml`，即项目根目录下的 `config/` 文件夹）：
    - 音频：`sample_rate=16000`, `channels=1`, `frame_ms=100`, `audio_queue_max_frames=100`
    - ASR：`asr_model="qwen3-asr-flash-realtime"`, `asr_format="pcm"`, `asr_language="auto"`
    - 字幕样式：`font_family`, `font_size`, `text_color`, `bg_opacity`, `bilingual_mode`
    - 快捷键：`toggle_hotkey = "ctrl+shift+r"`
    - 目标语言：`target_lang`
  - 使用 `tomllib`（读取）+ `tomli-w`（写入）处理 TOML
  - 同时创建 `.env.example` 模板供用户参考
- **Mirror**: Pydantic v2 BaseModel + python-dotenv + TOML 模式
- **Validate**:
  ```bash
  uv run python -c "
  from rainyasr.config import AppConfig, EnvConfig
  c = AppConfig.load()
  assert c.audio.sample_rate == 16000
  c.audio.sample_rate = 48000
  c.save()
  c2 = AppConfig.load()
  assert c2.audio.sample_rate == 48000
  print('EnvConfig DASHSCOPE_API_KEY:', bool(EnvConfig.dashscope_api_key()))
  print('OK')
  "
  ```

### Task 3: 跨平台音频设备检测（capture.py — 检测部分） ✅
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

### Task 4: 环形音频缓冲区（ring_buffer.py） ✅
- **Action**: 实现线程安全的 `AudioRingBuffer`：
  - 内部用 `numpy.ndarray`，`dtype=float32`
  - 容量：`sample_rate * max_duration`（默认 15 秒）
  - 方法：`write(samples)`, `read_last_n_seconds(n)` 返回 numpy 数组
  - 用 `threading.Lock` 保护
- **Mirror**: 生产者-消费者锁模式
- **Validate**:
  ```bash
  uv run python -m pytest tests/test_ring_buffer.py -v
  # 测试：单线程读写一致性、多线程并发安全、边界条件（含超大写入截断、写指针回绕）
  ```

### Task 5: PCM/WAV 音频编码工具（wav.py）✅
- **Action**: 实现音频编码工具：
  - `float32_to_pcm16(audio_array: np.ndarray, *, gain: float = 1.0, headroom: float = 0.95) -> bytes`
    - 输入 float32 数组（范围 [-1, 1]）
    - 默认仅 clip 到 [-1, 1]，转换为 16-bit little-endian PCM bytes
    - 当调用方显式传 `gain > 1.0` 时，先做线性增益；若超过 `headroom`，使用整体 peak normalize 避免硬裁剪失真
    - 空数组返回 `b""`
    - 实时 WebSocket ASR 主链路使用该函数发送音频 frame
  - `peak_normalize(audio_array: np.ndarray, headroom: float = 0.95) -> np.ndarray`
    - 用于低电平 loopback 的调试放大和后续可配置增益
  - `encode_wav(audio_array: np.ndarray, sample_rate: int) -> bytes`
    - 输出标准 WAV 格式 bytes（16-bit PCM）
    - 使用 `soundfile` 写入 `io.BytesIO`
    - 用于录音调试、保存样本、文件转写 fallback
- **Mirror**: `numpy` PCM 转换 + `soundfile.write` with `BytesIO`
- **Validate**:
  ```bash
  uv run python -c "
  import numpy as np
  from rainyasr.audio.wav import encode_wav, float32_to_pcm16
  data = np.random.uniform(-1, 1, 16000).astype(np.float32)
  pcm = float32_to_pcm16(data)
  assert len(pcm) == len(data) * 2
  wav_bytes = encode_wav(data, 16000)
  assert wav_bytes[:4] == b'RIFF'
  print(f'WAV size: {len(wav_bytes)} bytes')
  "
  ```

### Task 6: 命令行验证实时音频采集 ✅
- **Action**: 编写临时脚本 `scripts/test_capture.py`：
  - 检测设备 → 启动录制 10 秒
  - 同时验证两条输出：
    1. 实时路径：持续产生 PCM16 frame，主线程定时打印 frame 计数、raw/gain RMS 与峰值；音频回调只做轻量统计入队，不做阻塞打印
    2. 调试路径：保存 `audio_test.wav`，写出前显式应用 `DEFAULT_GAIN` + `peak_normalize`
  - 用播放器验证 WAV 内容是否为系统音频（而非麦克风）
- **Mirror**: 独立验证脚本
- **Validate**: 手动播放 `audio_test.wav` 确认内容是系统音频；终端持续打印非零音量 frame；生成音频已加入 `.gitignore`

### Task 7: Provider 抽象基类（base.py）✅
- **Action**: 定义：
  ```python
  @dataclass(frozen=True)
  class TranscriptEvent:
      text: str
      is_final: bool
      segment_id: str | None = None

  class RealtimeASRProvider(ABC):
      @abstractmethod
      async def start(self) -> None: ...

      @abstractmethod
      async def send_audio(self, pcm_bytes: bytes) -> None: ...

      @abstractmethod
      def events(self) -> AsyncIterator[TranscriptEvent]: ...

      @abstractmethod
      async def stop(self) -> None: ...

  class TranslationProvider(ABC):
      @abstractmethod
      async def translate(self, text: str, target_lang: str = "zh", history: list[str] | None = None) -> str: ...
  ```
- **Mirror**: Python ABC 抽象基类
- **Validate**:
  ```bash
  uv run python -c "from rainyasr.providers.base import RealtimeASRProvider; import inspect; assert inspect.isabstract(RealtimeASRProvider)"
  ```

### Task 8: 实时 ASR Provider 实现（asr.py）✅
- **Action**: `QwenRealtimeASRProvider`：
  - 基于 DashScope Qwen-ASR-Realtime Python SDK 或 WebSocket API
  - 默认模型：`qwen3-asr-flash-realtime`
  - 输入格式：`pcm`，16 kHz，单声道，16-bit little-endian
  - `start()`：建立实时 ASR 会话，注册 partial/final 回调或启动 WebSocket 读循环
  - `send_audio(pcm_bytes)`：发送 PCM16 frame；调用方负责控制 frame 大小与节流
  - `events()`：普通方法，返回 `AsyncIterator[TranscriptEvent]`，调用方用 `async for event in asr.events()`
  - `TranscriptEvent.segment_id`：如果后端提供句段 ID 则填充，用于替换 partial 和去重 final；没有则为 `None`
  - `stop()`：发送结束帧并关闭 WebSocket
  - 异常处理：连接失败、认证失败、服务端关闭、超时；支持指数退避重连
  - **注意**：文件转写 `qwen3-asr-flash-filetrans` 仅作为 fallback，不作为主链路
- **Mirror**: DashScope realtime ASR WebSocket / callback event pattern
- **Validate**:
  ```bash
  DASHSCOPE_API_KEY=xxx uv run python scripts/test_asr.py
  # 从系统音频实时采集 PCM frame，发送到 DashScope，终端持续打印 partial/final transcript
  ```

### Task 9: 翻译 Provider 实现（translate.py）✅
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
- **Action**: `SubtitleWindow(QWidget)`：
  - 窗口标志：`Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotHaveShadow | Qt.Tool`
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
  - 持有 `RealtimeASRProvider`, `TranslationProvider`, `SubtitleWindow`，可选持有 `AudioRingBuffer` 用于调试/fallback
  - `start()`：
    1. 检测音频设备，启动 `sounddevice.InputStream`
    2. 在音频回调中将 float32 frame 转为 PCM16 bytes
    3. 使用 `loop.call_soon_threadsafe()` 投递到有上限的 `asyncio.Queue`
    4. 启动 `audio_sender_task` 持续调用 `asr.send_audio()`
    5. 启动 `asr_event_task` 消费 partial/final transcript
    6. 启动 `translation_task` 只翻译 final transcript
  - partial transcript：按 `segment_id` 替换原文临时字幕，通过 Qt Signal 更新，不进入翻译请求
  - final transcript：按 `segment_id` 优先去重；无 ID 时退化为文本去重，然后进入翻译队列，携带最近 2 句 history 调用 `translate.translate()`
  - 背压：音频队列满时丢弃最旧 frame 并记录日志；连续阻塞则重连 ASR
  - `stop()`：停止录音流，关闭 ASR 会话，取消 sender/event/translation 协程
  - **日志**：使用 `logfire` 记录 ASR 连接状态、音频队列水位、partial/final 事件、翻译请求/响应、异常堆栈
- **Mirror**: asyncio + Qt Signal 跨线程通信
- **Validate**:
  ```bash
  uv run python scripts/test_worker.py
  # 验证：持续录音 -> 实时 ASR partial/final -> final 翻译 -> 终端打印双语字幕，不卡 UI
  ```

### Task 13: 设置面板（settings_dialog.py）
- **Action**: `SettingsDialog(QDialog)`：
  - API Key 输入（DashScope + DeepSeek，QLineEdit + password 掩码）
  - 目标语言选择（QComboBox：zh, en, ja, ko...）
  - 实时音频参数（采样率、frame_ms、音频队列上限，带合理范围限制）
  - ASR 参数（模型、语言、音频格式；默认 `qwen3-asr-flash-realtime` + `pcm`）
  - 字幕样式（字体选择、大小滑动条、颜色选择器、透明度滑动条）
  - 快捷键绑定（QKeySequenceEdit）
  - 保存时调用 `AppConfig.save()`；**待补充**：`save()` 需设置文件权限 `0o600`（当前实现未包含）
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
  1. 初始化 `logfire`（`logfire.configure()`，若环境变量 `LOGFIRE_TOKEN` 存在则自动上传，否则仅本地输出）
  2. 加载 `AppConfig`
  3. 检测音频设备（失败则弹窗提示安装指引）
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
    │       │       │       ├──> Task 5 (PCM/WAV 编码)
    │       │       │       │       │
    │       │       │       │       ├──> Task 6 (实时采集验证) ──┐
    │       │       │       │       │                            │
    │       ├──> Task 7 (Provider 基类)                          │
    │       │       │                                            │
    │       │       ├──> Task 8 (实时 ASR) <─────────────────────┤
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
uv run ruff check src/ tests/ scripts/ && uv run ruff format --check src/ tests/ scripts/

# 类型检查（建议添加 mypy）
# uv add --dev mypy
# uv run mypy src/rainyasr

# 单元测试
uv run pytest -q

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
| DashScope 实时 WebSocket 断线/首包超时 | 中 | 高 | sender/event task 分离，心跳/首包/空闲超时独立处理；指数退避重连并提示 UI 状态 |
| 全屏游戏遮挡字幕窗口 | 中 | 中 | WindowStaysOnTopHint 在大多数场景有效；文档说明部分游戏（反作弊全屏）可能无法置顶 |
| partial transcript 抖动或重复 | 中 | 中 | UI 区分 partial/final；只翻译 final；对 final 文本做简单去重和 history 限长 |
| Worker 协程异常未捕获导致静默崩溃 | 中 | 高 | `process_window()` 内层 try/except 捕获所有异常，通过 `logfire` 记录异常堆栈并继续循环 |
| 翻译历史上下文过长导致 Token 消耗增加 | 低 | 低 | 限制 history 为最近 2 句，平均增加 <100 tokens/请求 |
| PyInstaller 打包后动态库缺失 | 中 | 高 | 每个平台 CI/CD 构建测试；`--collect-all sounddevice` 确保 PortAudio 库被打包 |

---

## Acceptance Criteria

- [x] Task 1-8 完成，配套测试/CI 就绪
- [x] Task 9 完成
- [ ] Task 10-16 完成
- [x] `uv run ruff check src/` 零错误
- [x] `uv run ruff check src tests scripts` 零错误
- [x] macOS 音频设备检测与 Task 6 采集验证通过（BlackHole/Multi-Output）
- [ ] Windows/Linux 音频设备检测待真机验证
- [ ] 完整流程验证：播放视频 -> 系统音频捕获 -> 实时 ASR partial/final -> 翻译 -> 悬浮字幕显示
- [ ] API 断开时程序不崩溃，UI 提示错误
- [x] 配置持久化（config.py + TOML）已实现
- [ ] 全局快捷键可正常切换字幕显示/隐藏
- [ ] PyInstaller 打包后的可执行文件可在目标平台独立运行
