# ChatterPal 部署指南

## 配置契约

**第一次安装请从仓库中的 [`.env.example`](../.env.example) 复制配置，以 [`Settings`](../src/chatterpal/config/settings.py) 和[组件工厂](../src/chatterpal/config/loader.py)的实际读取行为为准。** 本文解释同一套配置，不再维护另一份完整 `.env`、Dockerfile 或 Compose 示例。

应用按“代码默认值 → `.env` → 进程环境变量”的顺序加载，后者优先。`get_settings()` 首先读取当前工作目录的 `.env`，不存在时才尝试源码所在仓库根目录的 `.env`；相对路径始终相对于进程工作目录。建议在仓库根目录执行本指南命令。加载不会将文件值写入 `os.environ`，因此 `reload_settings()` 能重新读取文件修改。

旧文件或共享 `.env` 中多余的键会被忽略，这只保证兼容读取；增加一个未被代码读取的键不会启用对应功能。下文列出高级聊天配置的实际入口和限制。

## 环境与安装

### 系统要求

- Python **3.12+**，推荐 `.python-version` 指定的 **3.12**，与 `pyproject.toml` 一致。
- Git、[uv](https://docs.astral.sh/uv/getting-started/installation/)；本地 React 开发还需要 Node.js 20+ 和 npm。
- 建议至少 4 GB 内存，较大的 Whisper 模型需要更多内存和磁盘空间。
- 首次 Whisper 识别可能下载模型；DashScope、OpenAI、Edge TTS、Aliyun NLS 需要访问对应在线服务。

### 原生依赖与安装失败排查

Python 包安装前先准备音频工具。PyAudio 需要 PortAudio；没有可用 wheel 时还需要 C 编译器和 Python 头文件。

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

如需使用 pip，可用 Python 3.12 创建虚拟环境，然后用该环境的解释器执行 `python -m pip install -e .`；uv 路径使用已提交的锁文件，更便于复现。

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

设置 `LLM_PROVIDER=openai` 与 `OPENAI_API_KEY`；可选 `OPENAI_MODEL` 默认 `gpt-3.5-turbo`。当前 OpenAI 适配器直接向 URL 发送 HTTP POST，因此 `OPENAI_BASE_URL` 若设置，必须是**完整聊天接口地址**（例如 `https://api.openai.com/v1/chat/completions`），不是仅到 `/v1` 的 SDK 根地址。不设置时工厂使用该完整默认地址。

### ASR

`ASR_PROVIDER=whisper` 是默认值，不需要云端 ASR 凭据。`WHISPER_MODEL` 支持 `tiny`、`base`、`small`、`medium`、`large`、`large-v2`、`large-v3`。降低模型大小可减少内存使用。

当前 Whisper 适配器使用 SpeechRecognition 的 Whisper 入口，模型缓存遵循 Whisper 的 `XDG_CACHE_HOME/whisper`（未设置时使用用户缓存目录）。`MODEL_CACHE_DIR` 会创建应用模型目录，但不会重定向 Whisper 的下载缓存。`WHISPER_DEVICE` 目前只传入适配器配置，识别实现没有读取它；不要依赖该变量强制选择 CPU/GPU。GPU 的 PyTorch 安装需按运行平台单独配置。

如需 Aliyun NLS，提供商名称为 **`ASR_PROVIDER=alibaba`**，安装可选 SDK：

```bash
uv pip install --python .venv alibabacloud-nls-python-sdk
```

然后配置 `ALIYUN_ASR_APP_KEY`，并选择一种认证方式：

| 认证方式 | 必需变量 |
| --- | --- |
| 已签发 token | `ALIYUN_ASR_TOKEN` |
| AccessKey 换取 token | `ALIYUN_ASR_ACCESS_KEY_ID` 与 `ALIYUN_ASR_ACCESS_KEY_SECRET` |
| 旧 AccessKey 名称 | `ALIBABA_API_KEY` 与 `ALIBABA_API_SECRET`，仅作为 AccessKey 的兼容回退 |

两种方式都需要 NLS 项目的应用 Key。旧文档中的 `DEFAULT_ASR_ENGINE=aliyun` 不被读取。默认镜像未安装可选 NLS SDK；若切换云端 ASR，需要在自己的镜像中添加该依赖。

### TTS

`TTS_PROVIDER=edge` 为默认值，使用 `EDGE_TTS_VOICE`、`EDGE_TTS_RATE` 和 `EDGE_TTS_VOLUME`。速率和音量是字符串，例如 `+0%`、`-10%`。该服务需要网络连接。项目要求 `edge-tts>=7.3.1`；旧锁定版本 7.2.0 的合成接口可能返回 HTTP 403，更新仓库后请执行 `uv sync --locked` 同步依赖。

设置 `TTS_PROVIDER=alibaba` 可使用 CosyVoice，凭据与 DashScope LLM 相同：优先 `DASHSCOPE_API_KEY`，兼容 `ALIBABA_API_KEY`，无需 ASR Secret。`ALIBABA_TTS_VOICE` 默认 `longxiaochun`，适配器使用 `cosyvoice-v1` 模型。

`ALIBABA_TTS_VOLUME` 是 0–100 的整数；`ALIBABA_TTS_SPEECH_RATE` 和 `ALIBABA_TTS_PITCH_RATE` 是 0.5–2.0 的浮点倍率，默认都是 `1.0`。当前 Alibaba TTS 适配器保存这些值，但 SDK 调用只传递 model / voice，**这些音量、语速和音调配置尚不会改变服务端合成结果**。Edge 的对应参数会实际传递给服务。

## 启动与验证

### API 与 React

在仓库根目录启动 API：

```bash
uv run python scripts/run_api.py
```

另开终端，从仓库根目录启动前端：

```bash
cd frontend
npm ci
npm run dev
```

| 服务 | 本地地址 | 说明 |
| --- | --- | --- |
| React 开发界面 | http://localhost:3010 | Vite 将 `/api` 代理到本机 8010 |
| API | http://localhost:8010 | `scripts/run_api.py` 默认绑定 `127.0.0.1:8010`，通过 `API_HOST` / `API_PORT` 调整 |
| API 文档 | http://localhost:8010/docs | FastAPI Swagger UI |
| API 健康检查 | http://localhost:8010/health | 组件初始化状态，不能证明远端 API Key 有效 |

使用以下跨平台命令检查健康接口：

```bash
uv run python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8010/health').read().decode())"
```

### INT2 学习流程与独立开发端口

API 默认挂载 `/api/learning`，前端的“今日练习”可完成“描述周末”的多轮对话、结束反馈和关联重练。学习身份来自浏览器 HttpOnly cookie，记录保存到 `LEARNING_DB_PATH`，默认 `data/learning/learning.sqlite3`。API 重启后使用相同数据库和 cookie 找回已保存记录。未发送草稿只在当前页面内存中保存，刷新前请先提交。

多个 worktree 可分别设置 `API_PORT`、`LEARNING_DB_PATH` 和 Vite 的 `CHATTERPAL_API_TARGET`。例如 macOS / Linux：

```bash
API_PORT=18200 LEARNING_DB_PATH=data/learning-int2.sqlite3 uv run python scripts/run_api.py
CHATTERPAL_API_TARGET=http://127.0.0.1:18200 npm --prefix frontend run dev -- --host 127.0.0.1 --port 13200
```

使用 Vite 的同源 `/api` 代理；如浏览器直接跨源请求 API，将前端 Origin 加入 `LEARNING_ALLOWED_ORIGINS`。`LEARNING_MAX_WORKERS`、`LEARNING_MAX_PENDING` 和操作超时限制学习工作队列，pending 上限至少等于 worker 数量。部署范围是单个 API 实例与本地 SQLite。

默认原音仅在数据库中临时保存 1 小时供回放，启用保留后默认 7 天；导出仅含文字与资产元数据。次日复习尚未接入，页面明确显示不可用。详细字段见 [学习 API 契约](learning_api_contract.md)，离线真实浏览器、容器 smoke 和完整回归命令见 [INT2 验收记录](int2_weekend_acceptance.md)。

### 可选 Gradio

```bash
uv run python -m chatterpal.web.app
```

默认地址 http://localhost:7860。`GRADIO_SERVER_NAME` 默认 `127.0.0.1`，`GRADIO_PORT` 默认 `7860`，`GRADIO_SHARE` 默认 `false`。局域网或容器访问可将主机设置为 `0.0.0.0`。

`chatterpal.web.app` 提供应用工厂和启动入口，没有可供 Gunicorn / Waitress 使用的模块级 WSGI `app`；直接使用上述入口。API 使用 FastAPI / Uvicorn，由 `scripts/run_api.py` 启动。

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

默认锁文件在 Linux x86_64 上可能包含体积较大的 PyTorch CUDA 依赖；首次构建需要相应网络和磁盘空间。当前构建未提供独立 CPU 依赖配置，镜像可在无 GPU 环境使用 CPU，但依赖下载体积仍可能较大。

本仓库不提供 `Dockerfile.prod`、`docker-compose.prod.yml`、Redis 缓存集成或平台专用云部署清单。部署到云平台时，以现有容器入口为基础配置服务端口、环境变量和持久化存储；不要依赖不存在的根目录 `app.py` 自动发现应用。前后端通常需要分别部署，并配置实际可达的 `/api/` 代理。

## 高级配置与兼容性

### Settings 的聊天设置

`MAX_HISTORY_LENGTH`（默认 20）、`SESSION_TIMEOUT`（默认 3600 秒）和可选 `SYSTEM_PROMPT` 传给 Gradio 的 `ChatService`。当前 API 的 `ChatService` 使用自身默认值。旧模板中的 `CHAT_MAX_HISTORY_LENGTH`、`CHAT_SESSION_TIMEOUT`、`CUSTOM_SYSTEM_PROMPT` 不会映射到这些字段。

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

- `AUDIO_SAMPLE_RATE`、`AUDIO_MAX_DURATION`、`AUDIO_TEMP_DIR` 可由 Settings / 音频配置辅助函数读取，但 API 当前将上传和生成文件写入系统临时目录下的 `chatterpal/`。Compose 用 `TMPDIR` 将系统临时目录放在音频卷内。不要依赖 `AUDIO_MAX_DURATION` 作为 API 上传限额。
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
