# Plan: RainyASR Rust 优化版本

**Source PRD**: `plan.md`, `.claude/plans/rainyasr.plan.md`
**Complexity**: Large
**Estimated Duration**: 60-90 小时（单人全栈）
**Trigger Condition**: Python 版本出现以下任一瓶颈时启动

---

## 触发条件（何时启动 Rust 重构）

| 指标 | Python 版本阈值 | Rust 目标 |
|---|---|---|
| 打包体积 | > 80MB (PyInstaller) | < 15MB 单二进制 |
| 冷启动时间 | > 3 秒 | < 500ms |
| 内存占用（空闲） | > 200MB | < 50MB |
| 长期运行稳定性 | 偶发崩溃/内存泄漏 | 编译期保证安全 |
| 反编译保护需求 | 高（Python 字节码易逆向） | 高（Native 二进制）|

> **建议策略**：先用 Python 完成 V1 验证市场需求，若用户反馈积极且上述指标不达标，再启动 Rust 重构。

---

## Summary

将 RainyASR 从 Python/PySide6 迁移至 Rust，核心目标是**更小的体积、更快的启动、更低的内存占用**。采用 `egui` + `eframe` 构建轻量级悬浮字幕窗口，`tokio` 处理异步 API 调用，`cpal` 录制系统音频，ASR 基于 DashScope Qwen 异步任务接口手动轮询。Rust 版本在功能上与 Python 版本完全对等，但在分发、性能和安全性上有质的飞跃。

---

## Rust 技术栈选型

| 层级 | Crate | 用途 | 备选方案 |
|---|---|---|---|
| GUI 框架 | `egui` + `eframe` | 即时模式 UI，极轻量 (~5MB) | `iced`（更精美但更大）、`slint`（声明式，需 C++ 编译器）|
| 窗口管理 | `winit` (eframe 内置) | 跨平台窗口创建与事件循环 | `tao`（Tauri fork，系统级行为更强）|
| 异步运行时 | `tokio` | 异步任务、定时器、HTTP 请求 | `async-std`（不推荐，tokio 生态更完善）|
| 音频 I/O | `cpal` | 跨平台实时音频流录制 | `rodio`（偏重播放，不适用录制场景）|
| WAV 编码 | `hound` | 纯 Rust WAV 文件编码 | 无（直接手写 RIFF 头也可，但 hound 更可靠）|
| HTTP 客户端 | `reqwest` | 异步 HTTP，multipart 上传音频，轮询任务结果 | `hyper`（太底层，reqwest 更合适）|
| JSON/配置 | `serde` + `serde_json`/`toml` | 序列化与配置解析 | 无 |
| 全局快捷键 | `global-hotkey` | 跨平台热键注册 | `rdev`（偏重输入监听）|
| 托盘图标 | `tray-icon` | 系统托盘 + 右键菜单 | `tao` 的 tray 模块 |
| 配置目录 | `dirs` | 获取 `~/.config/` 等标准路径 | 无 |
| 错误处理 | `thiserror` + `anyhow` | 结构化错误与传播 | 无 |
| 日志 | `tracing` + `tracing-subscriber` | 结构化日志 | `env_logger`（简单场景）|
| 并发控制 | `tokio::sync::Semaphore` | 背压与并发限制 | `async-channel` |
| 打包 | `cargo-bundle` / `cargo-wix` / `cargo-deb` | 各平台安装包 | 直接用 `cargo build --release` |

**GUI 框架选择理由**：
- `egui` 是即时模式 UI，API 简单，非常适合"两行文字 + 设置面板"这种极简界面
- `eframe` 封装了 `winit` + `egui` + 渲染后端，提供 `NativeOptions` 一行代码设置无边框/置顶/透明
- 若未来需要更复杂的 UI（多语言选择器、颜色拾取器等），可平滑迁移到 `iced`（架构相似，都是响应式）

---

## 与 Python 版本的关键架构差异

### 1. 异步模型：无需桥接
Python 需要 `qasync` 桥接 Qt 事件循环与 asyncio。Rust 中 `tokio` 就是原生运行时，与 `winit` 事件循环通过 `EventLoopProxy` 通信即可，架构更简单：

```
winit event loop (main thread)
    │
    ├── GUI 渲染 (egui)
    │
    └── EventLoopProxy::send_event()
            │
            ▼
    tokio runtime (同线程或独立线程)
        │
        ├── Timer (每 3 秒触发)
        │
        ├── HTTP tasks (ASR/Translation)
        │
        └── cpal audio callback thread
                │
                └── crossbeam channel → tokio task
```

### 2. 线程安全：编译期保证
Python 需要手动设计 `threading.Lock` + `Qt Signal`。Rust 中：
- `cpal` 回调线程通过 `crossbeam::channel` 向 tokio 任务发送音频数据
- 环形缓冲区用 `parking_lot::RwLock`（比 std::sync::Mutex 更快）
- 编译器强制检查 Send/Sync，不会出现数据竞争

### 3. 背压控制：Semaphore
Python 需要手动维护并发计数器。Rust 中：
```rust
let semaphore = Arc::new(Semaphore::new(2)); // 最大 2 个并发 ASR 请求

async fn process_window() {
    let _permit = semaphore.try_acquire()?; // 满了直接返回 Err，丢弃当前切片
    // ... API 调用
}
```

### 4. 配置管理
Python 用 Pydantic。Rust 等效方案：
```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
struct AppConfig {
    #[serde(default = "default_dashscope_url")]
    dashscope_base_url: String,
    dashscope_api_key: String,
    #[serde(default)]
    audio: AudioConfig,
    #[serde(default)]
    style: SubtitleStyle,
}
```
- 校验：用 `validator` crate 的 `#[validate]` 注解
- 持久化：serde_json/toml 到 `dirs::config_dir()/RainyASR/config.toml`
- 权限：`std::fs::set_permissions(path, Permissions::from_mode(0o600))`

---

## Files to Change

```
rainyasr/
├── Cargo.toml
├── Cargo.lock
├── src/
│   ├── main.rs              # 程序入口
│   ├── lib.rs               # 模块聚合
│   ├── config.rs            # AppConfig + 持久化
│   ├── audio/
│   │   ├── mod.rs
│   │   ├── device.rs        # 跨平台设备检测
│   │   ├── ring_buffer.rs   # 线程安全环形缓冲区
│   │   └── wav.rs           # hound WAV 编码
│   ├── providers/
│   │   ├── mod.rs
│   │   ├── asr.rs           # DashScope Qwen ASR（手动轮询）
│   │   └── translate.rs     # DeepSeek 翻译
│   ├── gui/
│   │   ├── mod.rs
│   │   ├── app.rs           # eframe::App 实现
│   │   ├── subtitle_window.rs  # 字幕渲染逻辑
│   │   └── settings_panel.rs   # 设置面板
│   ├── worker.rs            # tokio 调度核心
│   └── hotkey.rs            # 全局快捷键
├── assets/
│   └── icon.png             # 应用图标
└── scripts/
    ├── test_capture.rs      # 音频录制验证
    └── test_api.rs          # API 连通性验证
```

---

## Tasks

### Task 1: Cargo 项目初始化与依赖配置
- **Action**: `cargo init --name rainyasr`; 配置 `Cargo.toml` 全量依赖；创建目录结构
- **Validate**:
  ```bash
  cargo check
  cargo clippy -- -D warnings
  cargo fmt --check
  ```

### Task 2: 配置模型与持久化
- **Action**: `src/config.rs`：
  - `AppConfig` struct，含 API 密钥、音频参数、字幕样式、快捷键
  - 使用 `serde` + `toml` 序列化
  - 保存路径：`dirs::config_dir()/RainyASR/config.toml`
  - 文件权限 `0o600`
  - 用 `validator` crate 校验字段（如 URL 格式、热键语法）
- **Validate**:
  ```bash
  cargo test config::tests
  ```

### Task 3: 跨平台音频设备检测
- **Action**: `src/audio/device.rs`：
  - `AudioDeviceDetector` struct
  - Windows：遍历 `cpal` devices，匹配 WASAPI Loopback
  - macOS：匹配设备名含 `BlackHole`
  - Linux：匹配设备名含 `Monitor`，fallback default
  - 返回 `DeviceInfo { id, name, sample_rate }` 或 `AudioError::NoLoopbackDevice`
- **Validate**:
  ```bash
  cargo run --example detect_device
  ```

### Task 4: 线程安全环形缓冲区
- **Action**: `src/audio/ring_buffer.rs`：
  - `AudioRingBuffer`：内部 `VecDeque<f32>` + `parking_lot::RwLock`
  - 容量：`sample_rate * 15`（15 秒）
  - 方法：`write(samples: &[f32])`, `read_last_n_seconds(n: f32) -> Vec<f32>`
  - 单测覆盖：并发读写、边界条件、溢出覆盖
- **Validate**:
  ```bash
  cargo test audio::ring_buffer::tests -- --nocapture
  ```

### Task 5: 内存 WAV 编码
- **Action**: `src/audio/wav.rs`：
  - `encode_wav(samples: &[f32], sample_rate: u32) -> Result<Vec<u8>>`
  - 使用 `hound::WavWriter` 写入 `Cursor<Vec<u8>>`
  - 输出标准 16-bit PCM WAV bytes
- **Validate**:
  ```bash
  cargo test audio::wav::tests
  # 验证输出 bytes 以 RIFF 头开头
  ```

### Task 6: 命令行验证音频录制
- **Action**: `scripts/test_capture.rs`（binary target）：
  - 检测设备 → 启动 cpal Stream → 录制 10 秒 → 保存 `audio_test.wav`
- **Validate**: 手动播放验证内容为系统音频

### Task 7: Provider Trait 设计
- **Action**: `src/providers/mod.rs`：
  ```rust
  #[async_trait::async_trait]
  pub trait AsrProvider: Send + Sync {
      async fn transcribe(&self, audio_bytes: Vec<u8>) -> Result<String>;
  }

  #[async_trait::async_trait]
  pub trait TranslationProvider: Send + Sync {
      async fn translate(&self, text: &str, target_lang: &str, history: Option<&[&str]>) -> Result<String>;
  }
  ```
- **Validate**:
  ```bash
  cargo check
  # 确认 trait 编译通过
  ```

### Task 8: ASR Provider (DashScope Qwen)
- **Action**: `src/providers/asr.rs`：
  - `QwenAsrProvider { client: reqwest::Client, api_key, base_url }`
  - **核心挑战**：DashScope 语音识别是**异步任务模式**，非 OpenAI 兼容接口，Rust 无官方 SDK
  - `transcribe(audio_bytes: Vec<u8>)` 流程：
    1. 将 WAV bytes 写入临时文件（`tempfile` crate）
    2. **提交任务**：`POST {base_url}/api/v1/services/audio/asr/transcription`
       - Header: `Authorization: Bearer {api_key}`, `X-DashScope-Async: enable`
       - Body: JSON `{ "model": "qwen3-asr-flash-filetrans", "input": { "file_url": "..." } }`
       - **问题**：`file_url` 需要可访问的 URL，不能传本地路径
       - **解决**：先将音频上传到 DashScope 的文件服务（或使用 SDK 隐式上传逻辑），或调研是否支持 multipart 直接上传
    3. **轮询结果**：`GET {base_url}/api/v1/tasks/{task_id}`
       - 间隔 500ms，超时 30 秒
       - 直到 `task_status` 为 `SUCCEEDED` 或 `FAILED`
    4. 返回 `results[0].transcription`
    5. 清理临时文件
  - 错误类型：`AsrError::SubmitFailed` / `AsrError::PollingTimeout` / `AsrError::TaskFailed`
  - **风险**：异步任务模式的延迟（提交+轮询）可能比同步接口多 1-3 秒，需实测评估
- **Mirror**: 手动 HTTP 轮询模式（参考 DashScope Python SDK 源码逻辑）
- **Validate**:
  ```bash
  DASHSCOPE_API_KEY=xxx cargo run --example test_asr
  # 读取 audio_test.wav，提交 DashScope 任务，轮询结果，终端打印识别文字
  ```

### Task 9: 翻译 Provider (DeepSeek)
- **Action**: `src/providers/translate.rs`：
  - `DeepSeekTranslator`：reqwest 调用 `/chat/completions`
  - 系统提示词注入 history（最近 2 句）
  - 超时 20 秒，重试 2 次，指数退避
- **Mirror**: OpenAI compatible chat completions
- **Validate**:
  ```bash
  DEEPSEEK_API_KEY=xxx cargo run --example test_translate
  ```

### Task 10: GUI 窗口框架（无边框/置顶/透明）
- **Action**: `src/gui/mod.rs` + `src/gui/app.rs`：
  - 使用 `eframe::NativeOptions` 配置窗口：
    ```rust
    viewport: egui::ViewportBuilder::default()
        .with_decorations(false)
        .with_always_on_top()
        .with_transparent(true)
        .with_inner_size([600.0, 80.0])
    ```
  - 实现 `eframe::App` trait
  - 鼠标拖拽：在 `update()` 中处理 `ui.interact()` 的 drag 响应
- **Validate**:
  ```bash
  cargo run --example subtitle_demo
  # 验证：无边框、置顶、透明、可拖动
  ```

### Task 11: 字幕渲染与样式
- **Action**: `src/gui/subtitle_window.rs`：
  - `SubtitleState { original: String, translated: String }`
  - 渲染：两行 `egui::Label`，支持
    - 字体大小调节
    - 文字颜色（RGB）
    - 背景透明度（Alpha）
    - 双语/仅译文模式切换
  - 样式通过 `egui::Style` 和 `egui::RichText` 动态设置
- **Validate**: 手动测试样式切换

### Task 12: 异步桥接与 Worker 核心
- **Action**: `src/worker.rs`：
  - `SubtitleWorker` 持有：
    - `Arc<AudioRingBuffer>`
    - `Arc<dyn AsrProvider>`
    - `Arc<dyn TranslationProvider>`
    - `tokio::sync::mpsc::Sender<SubtitleState>`（向 GUI 发送结果）
    - `Arc<Semaphore>`（背压）
  - `start()`：
    1. 启动 `cpal::Stream`，回调中 `buffer.write()`
    2. 启动 `tokio::task`：`loop { interval.tick().await; self.process_window().await }`
  - `process_window()`：
    1. `semaphore.try_acquire()?` → 满了直接 return
    2. `buffer.read_last_n_seconds(6.0)`
    3. `encode_wav()`
    4. `asr.transcribe().await`（内部包含提交+轮询）
    5. `translate.translate(history=...).await`
    6. `gui_tx.send(SubtitleState { ... }).await`
  - 全链路 `?` 传播错误，外层 `match` 记录日志不崩溃
- **Validate**:
  ```bash
  cargo test worker::tests
  DASHSCOPE_API_KEY=xxx DEEPSEEK_API_KEY=xxx cargo run --example test_worker
  ```

### Task 13: 全局快捷键
- **Action**: `src/hotkey.rs`：
  - `GlobalHotkeyManager` 封装 `global-hotkey` crate
  - 注册 `Ctrl+Shift+R`（可配置）
  - 回调通过 `EventLoopProxy` 发送自定义事件到 winit 循环
  - macOS 启动时检测辅助功能权限，未授权则弹窗引导
- **Validate**: 手动测试热键切换显示/隐藏

### Task 14: 设置面板
- **Action**: `src/gui/settings_panel.rs`：
  - 点击托盘"设置"或热键触发，弹出独立窗口
  - egui 表单：
    - TextEdit（API Key，密码掩码）
    - ComboBox（目标语言）
    - Slider（窗口大小、步进间隔、字体大小、透明度）
    - ColorPicker（文字颜色）
    - 保存按钮 → `config.save()`
- **Validate**: 手动测试配置持久化

### Task 15: 托盘图标与主入口
- **Action**: `src/main.rs`：
  1. 初始化 `tracing-subscriber` 日志
  2. 加载 `AppConfig`
  3. 检测音频设备（失败则弹窗 + 打开文档链接）
  4. 初始化 `tray-icon`（菜单：显示/隐藏、设置、退出）
  5. 启动 `GlobalHotkeyManager`
  6. 启动 `SubtitleWorker`（tokio runtime）
  7. 运行 `eframe::run_native()`
  8. 优雅退出：`worker.stop()` → 保存配置 → 注销热键
- **Validate**:
  ```bash
  cargo run --release
  # 完整流程验证
  ```

### Task 16: 跨平台打包
- **Action**:
  - **macOS**: `cargo-bundle` → `RainyASR.app`，包含图标、Info.plist
  - **Windows**: `cargo-wix` → `.msi` 安装包，或直接用 `cargo build --release` 单 `.exe`
  - **Linux**: `cargo-deb` → `.deb` 包，或单二进制 + `.desktop` 文件
  - 每个包包含 `cpal` 所需系统音频库依赖说明文档
- **Validate**: 在目标平台裸机环境测试安装和运行

---

## 开发顺序与依赖图

```
Task 1 (Cargo 初始化)
    │
    ├──> Task 2 (配置)
    │       │
    │       ├──> Task 3 (音频检测)
    │       │       │
    │       │       ├──> Task 4 (环形缓冲)
    │       │       │       │
    │       │       │       ├──> Task 5 (WAV) ──> Task 6 (录制验证)
    │       │       │
    │       ├──> Task 7 (Trait)
    │       │       │
    │       │       ├──> Task 8 (ASR)
    │       │       │
    │       │       └──> Task 9 (翻译)
    │       │
    │       ├──> Task 10 (GUI 窗口)
    │       │       │
    │       │       ├──> Task 11 (字幕渲染)
    │       │       │
    │       │       └──> Task 14 (设置面板)
    │       │
    │       └──> Task 12 (Worker)
    │               │
    │               ├──> Task 13 (热键)
    │               │
    │               └──> Task 15 (主入口)
    │                       │
    │                       └──> Task 16 (打包)
```

**建议开发节奏**：
- 第 1-3 天：Task 1-6（音频链路）
- 第 4-6 天：Task 7-9（API 链路）
- 第 7-11 天：Task 10-15（GUI 集成与主程序）
- 第 12-15 天：Task 16 + 跨平台测试

---

## Validation

```bash
# 编译检查
cargo check
cargo clippy -- -D warnings
cargo fmt --check

# 单元测试
cargo test

# 端到端验证（需配置 API Key）
export DASHSCOPE_API_KEY="..."
export DEEPSEEK_API_KEY="..."
cargo run --release

# 体积检查
ls -lh target/release/rainyasr
# 目标：Linux/macOS < 10MB，Windows < 15MB
```

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `egui` 窗口行为跨平台不一致 | 中 | 高 | 先用 `eframe` 测试三平台窗口标志；若有问题 fallback 到 `tao` |
| `cpal` + Linux PipeWire 兼容性 | 中 | 高 | 同 Python 方案；文档说明需 `pulseaudio-alsa`；预留 ALSA direct  fallback |
| DashScope ASR 异步任务延迟过高 | **高** | **高** | **Python V1 阶段必须实测**：若轮询延迟 > 5 秒，需考虑换同步 ASR 方案或改用 WebSocket 实时识别 |
| DashScope `file_url` 需外部存储 | **中** | **中** | 调研是否支持 multipart 直接上传；若不支持，需集成临时文件上传逻辑 |
| tokio + winit 事件循环集成复杂度 | 中 | 中 | 参考 `egui` 官方示例 `custom_window_frame`；使用 `EventLoopProxy` 单向通信 |
| macOS 代码签名与公证 | 中 | 高 | 文档说明个人开发者可用 `xattr -cr` 绕过；正式分发需 Apple Developer 账号 |
| Rust 编译时间拖慢迭代 | 高 | 低 | 使用 `cargo check` 代替 `cargo build`；sccache 加速；Release 模式仅在最终打包时使用 |
| `global-hotkey` 在某些游戏中失效 | 中 | 中 | 同 Python 方案，文档说明限制；提供托盘图标作为备选操作方式 |

---

## Python → Rust 迁移对照表

| Python 模块 | Rust 模块 | 迁移复杂度 |
|---|---|---|
| `config.py` (Pydantic) | `config.rs` (serde + validator) | 低 |
| `audio/capture.py` | `audio/device.rs` | 低 |
| `audio/ring_buffer.py` | `audio/ring_buffer.rs` | 中（需理解 Rust 所有权）|
| `audio/wav.py` | `audio/wav.rs` | 低 |
| `providers/base.py` | `providers/mod.rs` (trait) | 低 |
| `providers/asr.py` (dashscope SDK) | `providers/asr.rs` (手动 HTTP 轮询) | **高**（无 Rust SDK，需自建异步任务轮询）|
| `providers/translate.py` | `providers/translate.rs` | 低 |
| `gui/subtitle_window.py` | `gui/subtitle_window.rs` | **高**（Qt → egui API 差异大）|
| `gui/settings_dialog.py` | `gui/settings_panel.rs` | **高**（Qt 表单 → egui 表单）|
| `app.py` (qasync) | `main.rs` + `gui/app.rs` | 中（事件循环集成）|
| `worker.py` | `worker.rs` | 中（asyncio → tokio）|
| `main.py` | `main.rs` | 低 |

> **迁移策略**：不建议直接重写。建议边参考 Python 业务逻辑，边按本计划 Task 顺序独立实现 Rust 版本。音频/API 层逻辑可直接翻译，GUI 层需要重新设计（Qt Widget → egui immediate mode）。

---

## Acceptance Criteria

- [ ] Task 1-16 全部完成
- [ ] `cargo clippy` 零 warning
- [ ] `cargo test` 全部通过
- [ ] 至少一个平台完整流程验证通过
- [ ] 打包后体积：macOS/Linux < 10MB，Windows < 15MB
- [ ] 冷启动时间 < 1 秒
- [ ] 内存占用（空闲）< 50MB
- [ ] API 断开时程序不崩溃，UI 显示错误状态
- [ ] 配置持久化，重启后恢复
- [ ] 全局快捷键正常工作
