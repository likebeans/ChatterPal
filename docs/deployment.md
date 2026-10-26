# ChatterPal 部署指南

## 配置契约

配置字段以 [`Settings`](../src/chatterpal/config/settings.py) 和[组件工厂](../src/chatterpal/config/loader.py)为准。源码安装可复制 [`.env.example`](../.env.example)；wheel 安装使用 `chatterpal-setup env` 显式创建用户配置，也可以只设置进程环境变量，无需 `.env`。

公开 CLI 按“代码默认值 → 选定的 dotenv 文件 → 进程环境变量”的顺序加载，后者优先。`--env-file` 优先于 `CHATTERPAL_ENV_FILE`；未指定时先找当前目录的 `.env`，再找 `$XDG_CONFIG_HOME/chatterpal/.env`（默认 `~/.config/chatterpal/.env`）。显式指定的文件不存在时报告错误。CLI 加载文件值到进程环境且不覆盖已有变量，修改文件后重启命令即可。

直接使用 Python 的 `get_settings()` 则先读取当前目录 `.env`，不存在时尝试源码根目录 `.env`；这种读取不会修改 `os.environ`，`reload_settings()` 可重新读取文件修改。两条路径中的相对数据路径都相对于进程工作目录。

旧文件或共享 `.env` 中多余的键会被忽略，这只保证兼容读取；增加一个未被代码读取的键不会启用对应功能。下文列出高级聊天配置的实际入口和限制。

## 环境与安装

### 系统要求

- Python **3.12+**，推荐 `.python-version` 指定的 **3.12**，与 `pyproject.toml` 一致。
- Git、[uv](https://docs.astral.sh/uv/getting-started/installation/)；本地 React 开发还需要 Node.js 20+ 和 npm。
- 本地完整运行时建议至少 4 GB 内存，较大的 Whisper 模型需要更多内存和磁盘空间；云端 API 按实际并发与上传大小配置资源。
- 首次 Whisper 识别可能下载模型；DashScope、OpenAI、Edge TTS、Aliyun NLS 需要访问对应在线服务。

### 原生依赖与安装失败排查

只有启用本地音频、Whisper、评分或设备录音的运行时才需要相应原生工具。`microphone` / `full` 的 PyAudio 需要 PortAudio；没有可用 wheel 时还需要 C 编译器和 Python 头文件。`cloud` 中官方 NLS SDK 的 OSS 依赖含 `crcmod` 扩展，部分平台安装时也需要编译器，但无需 PortAudio 或本地模型。下列系统安装命令适用于完整本地开发环境。

macOS（Homebrew）：

```bash
xcode-select --install
brew install ffmpeg flac portaudio libsndfile
```

Ubuntu / Debian：

```bash
sudo apt-get update
sudo apt-get install build-essential python3-dev portaudio19-dev ffmpeg flac libsndfile1
```

若系统 `python3-dev` 不是 3.12，请安装与实际构建 PyAudio 的解释器匹配的开发头文件。Windows 推荐 64 位 Python 3.12，通常可直接使用 PyAudio wheel；先安装 FFmpeg 并加入 `PATH`。如果 pip/uv 转为源码构建，需要 Visual C++ Build Tools 和 PortAudio 开发库。也可以使用下文 Docker 部署，由镜像安装 Linux 原生依赖。

`portaudio.h` 缺失属于系统依赖问题；`ffmpeg` / `flac` 找不到属于可执行文件或 `PATH` 问题。修改 API Key 无法解决这些安装错误。

### 克隆并安装

以下命令适用于 macOS、Linux 和 Windows PowerShell，无需激活虚拟环境：

```bash
git clone https://github.com/likebeans/ChatterPal.git
cd ChatterPal
uv python install 3.12
uv sync --locked --python 3.12
```

`uv sync --locked` 默认安装统一的 `dev` 依赖组，含完整运行时与测试工具，保留已有开发和 CI 行为。生产环境用 `--no-dev` 显式选择需要的 extras：

```bash
# 纯配置与 CLI；不会安装音频、云提供商或 UI 运行时。
uv sync --locked --no-dev

# 云端 API；需选择 ASR_PROVIDER=alibaba 并配置云提供商凭据。
uv sync --locked --no-dev --extra api --extra cloud
```

| Extra | 用途 |
| --- | --- |
| `api` | FastAPI、Uvicorn、multipart 上传；单独安装不提供语音或模型引擎 |
| `audio` | NumPy、SoundFile、SciPy 音频读写与转换 |
| `aliyun-asr` | 音频工具与官方 Aliyun NLS SDK |
| `cloud` | Aliyun NLS、Edge TTS、DashScope / OpenAI 客户端及音频工具 |
| `local-whisper` | 本地 Whisper、PyTorch、SpeechRecognition；不含 PyAudio |
| `assessment` | Parselmouth、Librosa 和音频分析依赖 |
| `gradio` | 完整 Gradio UI 及云端、本地识别和分析依赖；不含 PyAudio |
| `microphone` | 本地识别与 PyAudio 设备录音 |
| `full` | 所有运行时，含设备录音 |
| `dev` | `full` 与统一开发工具；也是默认 uv 开发依赖组的来源 |

wheel 安装与源码无关，可在任意目录使用公开命令：

```bash
python -m pip install '/path/to/chatterpal-0.1.0-py3-none-any.whl[api,cloud]'
chatterpal-run --help
chatterpal-setup check
chatterpal-setup env
# 编辑命令打印出的配置路径，再校验凭据是否齐全；此检查不调用提供商。
chatterpal-setup verify
chatterpal-run --mode api
```

本地源码用 pip 安装时，完整开发环境为 `python -m pip install -e '.[dev]'`，仅 Gradio 为 `python -m pip install -e '.[gradio]'`；裸 `pip install -e .` 只安装基础配置与 CLI。公开的 `chatterpal` 与 `chatterpal-run` 共享入口，支持 `web`（默认）、`api`、`chat`、`test`、`demo` 模式。`test` 模式需要源码测试目录，离开仓库时传 `--tests-path /path/to/checkout/tests`。所有公开命令的 `--help` 与只读 `chatterpal-setup check` 无需运行时 extras。

`chatterpal-setup` 默认只检查已安装版本，不安装依赖、不创建文件或目录。`env` 独占创建配置文件（POSIX 权限 0600，新建父目录 0700），已有文件或符号链接会保持原样并返回失败；`dirs` 才显式创建用户配置、数据和缓存目录。

### Linux / Windows 的本地 CPU 安装

云端 `api,cloud` 安装不引入 Torch 或 CUDA。需要本地 Whisper 且希望避免 Linux x86_64 默认 Torch 的 CUDA 下载时，使用独立虚拟环境，先从 PyTorch CPU 索引安装锁中对应版本，再安装本地运行时：

```bash
python -m pip install --index-url https://download.pytorch.org/whl/cpu 'torch==2.8.0'
python -m pip install '/path/to/chatterpal-0.1.0-py3-none-any.whl[api,cloud,local-whisper]'
```

不要随后运行默认 `uv sync`，它会按通用锁恢复完整开发依赖。`WHISPER_DEVICE=cpu` 控制推理设备，不改变 pip/uv 选择的 Torch 安装包。macOS 使用 PyPI 的原生 Torch 包；CPU 索引命令不适用于 macOS。这里提供安装路径，尚未实测 Linux x86_64 / Windows 的 CPU 专用安装。

macOS / Linux 复制模板：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

使用编辑器打开 `.env`，为选定的提供商填写凭据。默认组合为本地 Whisper ASR、Edge TTS 和阿里百炼 LLM，仅需填写 `DASHSCOPE_API_KEY`。

## 提供商配置

### Alibaba DashScope LLM

默认 `LLM_PROVIDER=alibaba`。`DASHSCOPE_API_KEY` 是百炼 API Key，**不需要 `ALIBABA_API_SECRET`**。兼容旧名 `ALIBABA_API_KEY`；两者都设置时优先使用 `DASHSCOPE_API_KEY`。Aliyun NLS 的 AccessKey 与百炼 API Key 用途不同，建议分别配置。

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `ALIBABA_MODEL` | `qwen-plus` | 模型名称，需在账户中可用 |
| `ALIBABA_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | OpenAI SDK 兼容接口根地址 |
| `ALIBABA_MAX_TOKENS` | `2000` | 正整数，最大输出 token 数 |
| `ALIBABA_TEMPERATURE` | `0.7` | 浮点数，采样温度 |
| `ALIBABA_TOP_P` | `0.8` | 浮点数，核采样参数 |
| `ALIBABA_ENABLE_SEARCH` | `false` | 布尔值，向支持的模型传递搜索选项 |

这些参数通过共享工厂进入 API 和 Gradio 的 LLM 实例。模型名称、区域和采样值还需满足服务端约束。

### OpenAI LLM

设置 `LLM_PROVIDER=openai` 与 `OPENAI_API_KEY`；可选 `OPENAI_MODEL` 默认 `gpt-3.5-turbo`，`OPENAI_TIMEOUT` 为大于 0、最多 300 秒的 HTTP 超时，默认 30 秒。当前 OpenAI 适配器直接向 URL 发送 HTTP POST，因此 `OPENAI_BASE_URL` 若设置，必须是**完整聊天接口地址**（例如 `https://api.openai.com/v1/chat/completions`），不是仅到 `/v1` 的 SDK 根地址。不设置时工厂使用该完整默认地址。

### ASR

`ASR_PROVIDER=whisper` 是默认值，需安装 `local-whisper`（`gradio` / `full` 已包含），不需要云端 ASR 凭据。`WHISPER_MODEL` 支持 `tiny`、`base`、`small`、`medium`、`large`、`large-v2`、`large-v3`。降低模型大小可减少内存使用。

Whisper 在首次识别时加载模型，并按模型 / 设备缓存；下载目录遵循 `XDG_CACHE_HOME/whisper`（未设置时使用用户缓存目录）。`MODEL_CACHE_DIR` 创建应用模型目录，不重定向 Whisper 下载缓存。`WHISPER_DEVICE=cpu` 明确选择 CPU；`auto` 在可用时选择 CUDA。GPU 的 PyTorch 安装需按运行平台单独配置。

如需 Aliyun NLS，提供商名称为 **`ASR_PROVIDER=alibaba`**，安装可选 SDK：

```bash
uv sync --locked --no-dev --extra api --extra cloud
```

然后配置 `ALIYUN_ASR_APP_KEY`，并选择一种认证方式：

| 认证方式 | 必需变量 |
| --- | --- |
| 已签发 token | `ALIYUN_ASR_TOKEN` |
| AccessKey 换取 token | `ALIYUN_ASR_ACCESS_KEY_ID` 与 `ALIYUN_ASR_ACCESS_KEY_SECRET` |
| 旧 AccessKey 名称 | `ALIBABA_API_KEY` 与 `ALIBABA_API_SECRET`，仅作为 AccessKey 的兼容回退 |

两种方式都需要 NLS 项目的应用 Key。旧文档中的 `DEFAULT_ASR_ENGINE=aliyun` 不被读取。`aliyun-asr`、`cloud` 和 `full` 都包含官方 `alibabacloud-nls-python-sdk`，它提供 `nls` 模块，也声明 Matplotlib / OSS 依赖。不要安装名称相近的其他 NLS 包。

### TTS

`TTS_PROVIDER=edge` 为默认值，使用 `EDGE_TTS_VOICE`、`EDGE_TTS_RATE` 和 `EDGE_TTS_VOLUME`。速率和音量是字符串，例如 `+0%`、`-10%`。该服务需要网络连接。项目要求 `edge-tts>=7.3.1`；旧锁定版本 7.2.0 的合成接口可能返回 HTTP 403，更新仓库后请执行 `uv sync --locked` 同步依赖。

设置 `TTS_PROVIDER=alibaba` 可使用 CosyVoice，凭据与 DashScope LLM 相同：优先 `DASHSCOPE_API_KEY`，兼容 `ALIBABA_API_KEY`，无需 ASR Secret。`ALIBABA_TTS_VOICE` 默认 `longxiaochun`，适配器使用 `cosyvoice-v1` 模型。

`ALIBABA_TTS_VOLUME` 是 0–100 的整数；`ALIBABA_TTS_SPEECH_RATE` 和 `ALIBABA_TTS_PITCH_RATE` 是 0.5–2.0 的浮点倍率，默认都是 `1.0`。Alibaba TTS 现将音量、语速、音调和所选音频格式传给 SDK；Edge 对应参数也会实际传递。每个请求固定自己的参数，缓存键包含这些有效参数；请求中的音色覆盖不修改共享默认值。生成文件按实际音频容器返回扩展名和 MIME；显式 PCM16 单声道输出包装为 WAV。

## 启动与验证

### API 与 React

在仓库根目录启动 API（wheel 安装省略 `uv run --no-sync`）：

```bash
uv run --no-sync chatterpal-run --mode api
```

`--no-sync` 保留上一步显式选择的 extras；直接 `uv run` 可能恢复默认完整开发组。原 `python scripts/run_api.py` 仍作为源码入口兼容。CLI 支持 `--host`、`--port` 和 `--env-file`。

另开终端，从仓库根目录启动前端：

```bash
cd frontend
npm ci
npm run dev
```

| 服务 | 本地地址 | 说明 |
| --- | --- | --- |
| React 开发界面 | http://localhost:3010 | Vite 将 `/api` 代理到本机 8010 |
| API | http://localhost:8010 | 默认绑定 `127.0.0.1:8010`，通过 `API_HOST` / `API_PORT` 或 CLI `--host` / `--port` 调整 |
| API 文档 | http://localhost:8010/docs | FastAPI Swagger UI |
| API 健康检查 | http://localhost:8010/health | 组件初始化状态，不能证明远端 API Key 有效 |

使用以下跨平台命令检查健康接口：

```bash
uv run --no-sync python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8010/health').read().decode())"
```

### INT2 学习流程与独立开发端口

API 默认挂载 `/api/learning`，前端的“今日练习”可完成“描述周末”的多轮对话、结束反馈和关联重练。学习身份来自浏览器 HttpOnly cookie，记录保存到 `LEARNING_DB_PATH`，默认 `data/learning/learning.sqlite3`。API 重启后使用相同数据库和 cookie 找回已保存记录。未发送草稿只在当前页面内存中保存，刷新前请先提交。

多个 worktree 可分别设置 `API_PORT`、`LEARNING_DB_PATH` 和 Vite 的 `CHATTERPAL_API_TARGET`。例如 macOS / Linux：

```bash
API_PORT=18200 LEARNING_DB_PATH=data/learning-int2.sqlite3 uv run python scripts/run_api.py
CHATTERPAL_API_TARGET=http://127.0.0.1:18200 npm --prefix frontend run dev -- --host 127.0.0.1 --port 13200
```

使用 Vite 的同源 `/api` 代理；如浏览器直接跨源请求 API，将前端 Origin 加入 `LEARNING_ALLOWED_ORIGINS`。`LEARNING_MAX_WORKERS`、`LEARNING_MAX_PENDING` 和操作超时限制学习工作队列，pending 上限至少等于 worker 数量。部署范围是单个 API 实例与本地 SQLite。

默认原音仅在数据库中临时保存 1 小时供回放，启用保留后默认 7 天；导出仅含文字与资产元数据。完成跟读或重练后可显式保存表达，次日到期复习；提示 / 揭示与验证状态分别记录。详细字段见 [学习 API 契约](learning_api_contract.md)，实际浏览器与完整回归结果以相应验收记录为准。

### 可选 Gradio

```bash
uv sync --locked --no-dev --extra gradio
uv run --no-sync chatterpal-run --mode web
```

默认地址 http://localhost:7860。`GRADIO_SERVER_NAME` 默认 `127.0.0.1`，`GRADIO_PORT` 默认 `7860`，`GRADIO_SHARE` 默认 `false`。局域网或容器访问可将主机设置为 `0.0.0.0`。

`chatterpal.web.app` 提供应用工厂和启动入口，没有可供 Gunicorn / Waitress 使用的模块级 WSGI `app`；直接使用上述入口。API 使用 FastAPI / Uvicorn，公开入口为 `chatterpal-run --mode api`；`scripts/run_api.py` 只在源码中保留兼容。Docker 后端与可选 Gradio 都使用安装到 wheel 中的 CLI，不依赖容器内另拷源码或 `scripts` 包。

### 本地配置检查

检查模板或实际 `.env` 的字段类型，无需 API 请求或模型下载：

```bash
uv run python scripts/verify_deployment_config.py
uv run python scripts/verify_deployment_config.py --env-file .env --require-credentials
```

第一个命令检查模板，允许尚未填写的凭据；第二个额外检查选定提供商所需凭据是否存在。检查器遵循相同的环境变量覆盖规则，并隐藏输入值。它不会验证密钥是否在服务端有效。

## Docker Compose 部署

使用仓库中的 [`docker-compose.yml`](../docker-compose.yml)、后端 [`Dockerfile`](../Dockerfile)、前端 [`Dockerfile`](../frontend/Dockerfile) 和 [`nginx.conf`](../frontend/nginx.conf)。准备好根目录 `.env` 后执行：

```bash
docker compose config --quiet
docker compose up --build -d
docker compose ps
docker compose logs --tail=100 backend frontend
```

默认服务为 `backend`（8010）和 `frontend`（宿主机 3010 → nginx 80）。前端将 `/api/` 请求转发到 Compose 网络中的后端；构建后的页面无需写死 `localhost:8010`。API 文档和健康接口仍位于后端 8010。前端容器自身 `/health` 只检查 nginx。

可选 Gradio profile：

```bash
docker compose --profile gradio up --build -d
```

Gradio 使用独立进程和 7860 端口。Compose 会为容器设置对外监听地址。

Compose 通过 `env_file` 读取 `.env`。如需其他文件，可在 macOS / Linux 设置 `CHATTERPAL_ENV_FILE`，或在 PowerShell 设置 `$env:CHATTERPAL_ENV_FILE` 后运行相同命令。Compose 的 `environment` 明确覆盖存储路径，因此宿主机 `.env` 的路径不会逃出容器挂载目录。模型缓存、应用数据、临时音频、日志和偏好使用命名卷；`docker compose down` 保留这些卷。

Docker 默认 `CHATTERPAL_EXTRAS=full`，保留完整本地 / Gradio / 设备运行时。云端 API 可在凭据文件中选择 `ASR_PROVIDER=alibaba`、配置 NLS 认证与 LLM 凭据，再只安装云端 extras：

```bash
CHATTERPAL_EXTRAS='api cloud' docker compose up --build -d backend frontend
# 或独立构建后端镜像：
docker build --build-arg 'CHATTERPAL_EXTRAS=api cloud' -t chatterpal-cloud:local .
```

Windows PowerShell 先设置 `$env:CHATTERPAL_EXTRAS='api cloud'`，再运行同一个 Compose 命令。构建参数决定安装的 Python 包，环境变量决定使用的提供商；两者需要一致。这个云端镜像包含官方 NLS SDK，无 Whisper、Torch、CUDA、Gradio、Parselmouth 或 PyAudio。编译器只留在 builder 阶段。可选 Gradio 服务需要带 `gradio` 或 `full` 的镜像。

`full` / `local-whisper` 的通用锁在 Linux x86_64 仍可能下载较大的 PyTorch CUDA 依赖；`WHISPER_DEVICE=cpu` 不能减少依赖下载。上文 CPU 专用 pip 路径与 Docker 的通用锁是两种安装方式，当前 Dockerfile 未提供 CPU 专用锁。后端健康检查和前端 `/api/` 代理需要在实际容器启动后验证；未测试的平台不能沿用另一架构的结果。

本仓库不提供 `Dockerfile.prod`、`docker-compose.prod.yml`、Redis 缓存集成或平台专用云部署清单。部署到云平台时，以现有容器入口为基础配置服务端口、环境变量和持久化存储；不要依赖不存在的根目录 `app.py` 自动发现应用。前后端通常需要分别部署，并配置实际可达的 `/api/` 代理。

## 打包与安装实测（2026-10-25）

macOS arm64 / Python 3.12.12 的独立虚拟环境完成以下检查，安装目录与工作目录均在源码仓库外：

| 安装配置 | 已安装发行包数量（含 ChatterPal） | 验证 |
| --- | --- | --- |
| 基础 wheel | 9 | 三个公开命令帮助、只读版本检查、纯环境变量配置校验、demo、私有配置创建与不覆盖、环境变量优先级 |
| `api,cloud` 冻结依赖 + wheel | 72 | 真实 CLI socket `/health`、两轮聊天、终端聊天、会话保留、TTS 失败保留文字、lifespan 关闭；无本地模型 / 设备 / Gradio / 声学依赖 |
| `aliyun-asr` | 37 | 官方 `nls` 导入与已有 token 构造，无网络识别 |
| `local-whisper` | 36 | CPU 配置构造，无模型加载 / 下载，无 PyAudio |
| `gradio` | 125 | CLI 启动真实页面和 `/config`，42 个组件 / 4 个事件依赖，无 PyAudio |
| `full` | 126 | 冻结安装与依赖兼容检查 |
| 默认 `dev` | 146 | 冻结安装与依赖兼容检查；110 项打包、配置、启动、部署与 API 资源回归通过 |

云端聊天使用本地可控 OpenAI 兼容服务；TTS 使用无效音色在本地失败，不发送外部语音请求。启动与依赖导入耗时受缓存影响，此处不作生产性能结论。未测试真实付费提供商、Whisper 推理、实体录音设备、Windows 批处理运行或 Linux x86_64 CPU 专用安装。完整开发回归仍按仓库既有覆盖率门槛执行。`chatterpal-run --mode test --test-type config` 会执行真实配置测试并保留仓库覆盖率门槛；只做局部诊断时可显式设置 `PYTEST_ADDOPTS='-o addopts='`，不能以局部覆盖率结果替代全仓验收。

### 稳定源码的 wheel / 容器复验（2026-10-26）

最终源码冻结包含 API 工作线程 / 上传生命周期、Alibaba 音色、Opus、OpenAI 超时配置和复习提示隔离修复，以及两个未使用类型导入的清理。冻结后重新构建 wheel 和云端后端，确认构建期间源码未变化；包含历史复习 UI 的既有前端镜像经 44 项构建输入哈希和镜像 ID 复核后复用，输入无变化。实际平台为 **linux/arm64**：

| 镜像 | Docker 引擎报告大小 | 使用缓存的构建耗时 | 完整镜像 ID |
| --- | --- | --- | --- |
| `chatterpal-h-cloud:20261025-4mwftc` | 294,661,003 字节 | 14.166 秒（最终冻结重建） | `sha256:6a18ddc4bf51e596c325c4123359693b25cfa72917d7064709290cfcef18ee87` |
| `chatterpal-h-frontend:20261025-4mwftc` | 25,887,066 字节 | 10.376 秒（原构建，本次核验复用） | `sha256:755a2833e9fad8b1ab4a67929fa8539e2de1b99c6bc236dc5a7d0c85496d35e7` |

大小是镜像内容大小，不是传输大小或内存占用。首次成功云端构建耗时 832.251 秒，包含网络重试；此前两次分别因下载截断导致 uv wheel 哈希不符、Debian 镜像站 502 失败，没有绕过哈希验证。上表记录使用缓存的构建，不能作为无缓存构建性能。

最终冻结 wheel 为 246,625 字节，SHA256 `a8cc98525026674b8e9b22aff45bd6fc9422b8644bcb7e7d1e7d51b1462bef33`。将 wheel 内 82 个包文件的相对路径与各文件 SHA256 组成按键排序的紧凑 JSON，再计算 SHA256，得到包指纹 `0c9b5aa47f9c60666d9a7f8d06226ec2a1af403d6e66e774d0adf3f66baaf484`。源码、构建 wheel、仓库外安装与容器内 `/opt/venv/lib/python3.12/site-packages/chatterpal` 的这 82 个文件逐字节一致；容器没有 `/app/src` 或 `/app/scripts` 回退路径。

最终冻结 wheel 的真实 CLI `/health`、两轮 API 聊天、一轮终端聊天及关闭验证通过。最终镜像在独立 `--internal` Docker 网络中启动，以同网络的可控 OpenAI 兼容端点提供回复；后端与前端内置健康检查均为 healthy，直接后端与 nginx `/api/chat` 代理均返回 200、保留同一会话，TTS 在本地失败时仍返回文字。容器启动至健康为 2.191 秒，wheel socket 为 0.952 秒；这些只反映该受控环境中的缓存与启动开销。临时容器和网络已清理，未启用外部服务请求。

本次没有构建或验证 amd64、CPU 专用 Linux 镜像、默认 `full` 镜像或 Docker Gradio profile。此前 macOS Gradio 实际页面检查不替代这些平台 / 部署方式的验证。

## 高级配置与兼容性

### Settings 的聊天设置

`MAX_HISTORY_LENGTH`（默认 20）与 `SESSION_TIMEOUT`（默认 3600 秒）传给公开 CLI 和 API 的 `ChatService`；这两个入口还传入 `MAX_SESSIONS`（默认 1000）作为会话容量上限。Gradio 也读取历史长度、超时与可选 `SYSTEM_PROMPT`。旧模板中的 `CHAT_MAX_HISTORY_LENGTH`、`CHAT_SESSION_TIMEOUT`、`CUSTOM_SYSTEM_PROMPT` 不会映射到这些字段。

### 增强聊天配置管理器

[`ChatConfigManager`](../src/chatterpal/services/chat_config.py) 管理 JSON 文件，默认路径为 `CACHE_DIR/chat_config/chat_config.json`，并在同目录的 `backups/` 保存最近的备份。它接受 `audio`、`topic_generation`、`session`、`ui` 四个节；字段和校验范围以对应 dataclass 为准。

这套配置不是自动加载的环境变量映射。当前 API / Gradio 启动入口也没有自动应用此管理器。只有显式使用 `get_chat_config_manager()` / `get_chat_config()` 的集成代码才会读取这些设置。可在扩展代码中使用以下方式更新和持久化有效配置：

```python
from chatterpal.services.chat_config import ChatConfigManager

manager = ChatConfigManager()
manager.update_config({
    "audio": {"auto_play": False, "playback_speed": 1.25},
    "topic_generation": {"default_difficulty": "beginner", "preferred_categories": ["daily"]},
    "session": {"max_history_length": 30},
    "ui": {"theme": "dark"},
})
```

该更新只改变管理器持有的数据，使用方仍需将配置传给实际服务。`import_config()` / `export_config()` 使用 JSON。[`config/chat_config.yaml.example`](../config/chat_config.yaml.example) 是参考草案；将其复制成 `.yaml` 不会被当前启动入口或 JSON 管理器自动加载。草案中的 `cache`、`performance`、`security`、`system_prompts` 等节没有对应的自动运行时应用路径。

因此旧文档中的 `TOPIC_*`、`UI_*`、`CHAT_AUTO_SAVE`、`TTS_CACHE_SIZE`、`CHAT_CACHE_TTL`、`ENABLE_AUDIO_CACHE`、`MAX_CONCURRENT_REQUESTS`、`REQUEST_TIMEOUT`、`RETRY_ATTEMPTS` 等环境变量不再列入应用模板。它们不会自动控制服务、缓存或并发。需要这些能力时，应先实现并连接对应消费者。

### 其他已知配置边界

- `AUDIO_TEMP_DIR` 决定 API 上传与生成音频目录，Compose 固定为 `/app/temp_audio` 并挂载音频卷。生成文件还受 `GENERATED_AUDIO_TTL`、`GENERATED_AUDIO_MAX_FILES`、`GENERATED_AUDIO_MAX_BYTES` 约束。不要依赖 `AUDIO_MAX_DURATION` 作为 API 上传限额。
- `LOG_LEVEL`、`LOG_FILE` 是配置字段，但当前两个启动入口都自行初始化 INFO 日志，尚未按这两个值创建文件日志处理器。使用进程输出或 `docker compose logs` 观察运行状态。
- `SECRET_KEY`、`ALLOWED_HOSTS` 仍保留在 Settings 供已有调用方使用，当前 Web 入口没有据此启用身份认证或主机白名单，因此模板不将它们列为安全开关。

## 常见问题

- **`extra_forbidden` 或类型错误**：使用当前 `.env.example` 重新核对配置，并运行上述本地检查器。浮点 TTS 倍率应写成 `1.0` 或 `1.25`，不要沿用旧文档的 -500～500 刻度。
- **配置修改后未生效**：进程环境变量会覆盖文件。检查终端、容器 `environment` 和部署平台变量，然后重启进程或在调用代码中执行 `reload_settings()`。
- **缺少密钥**：默认只需百炼 Key；切到 OpenAI 或 Aliyun ASR 时按所选提供商配置，NLS Secret 不能替代百炼 Key。
- **下载模型失败或内存不足**：先检查网络与缓存目录写权限，改用 `WHISPER_MODEL=tiny` / `base` 以降低资源需求。
- **端口冲突**：停止占用 8010 / 3010 的进程，或调整实际 API / Vite / Compose 启动配置。`GRADIO_PORT` 仅影响 Gradio。
- **健康检查成功但聊天失败**：`/health` 仅证明本地组件已初始化。检查服务端权限、模型名和网络错误；配置检查器不会发送真实请求。

问题反馈请附上 Python / uv / 操作系统版本、失败命令和已脱敏的错误信息，提交至 [GitHub Issues](https://github.com/likebeans/ChatterPal/issues)。
