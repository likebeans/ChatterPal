# ChatterPal

ChatterPal 是一个英语口语练习项目，提供 React + FastAPI 学习流程，也保留独立的 Gradio 界面。它复用 Whisper / 阿里云 ASR、OpenAI 兼容 / 阿里百炼 LLM 和 Edge / CosyVoice TTS，帮助用户对话、查看原话反馈并重练。

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## 当前功能

- **任务学习**：人工编写的“描述周末”“点餐”“酒店入住”任务，保存任务版本、文字或录音回合、提示、结束反馈和关联重练。反馈最多选两个重点，引用本次原话。
- **录音核对**：任务回答、重练和复习可先识别录音，核对或修改文字后再确认提交，也可试听、放弃或重录。机器转写与确认文字分别保留，同一份草稿只识别一次。
- **记录恢复**：匿名 HttpOnly cookie 身份，SQLite 保存会话、反馈、尝试、操作状态及私有音频；支持刷新、API 重启、JSON 导出和删除个人数据。
- **复习与进度**：明确保存重点表达后次日到期；只有延迟作答、未使用本轮提示且有新表达依据时，才按约 3 天、7 天排程。提示和看答案由服务端记录。当前每项任务只有两道人工迁移问题，用完后继续练习会标为重复问题，不增加独立迁移记录。
- **自由聊天**：文字与录音输入、音色选择和音频播放。进程内会话有数量、闲置时间及历史长度限制；需要持久学习记录时使用任务入口。
- **表达分析**：比较参考文本与机器转写，提供文字建议；可选声学工具提供可用的原始测量。独立 Gradio 主页面的评分、纠错入口已接入现有服务，支持录音与上传音频，纠错参考文本可留空。即时纠正和结束反馈共用文字建议校验，不合格建议明确不可用并保留有效教练回复。每个维度说明来源、证据、状态和适用范围。

当前没有经过人工标注数据校准的发音总分、音素准确率或能力等级。ASR 置信度不代表英语能力，文本匹配率也不代表声学发音准确率；缺失的维度返回 `null` 和原因。任务目标达成、同题重练与延迟新题证据分别记录，不合成“掌握率”。

2026-10-25，“描述周末”已通过 Qwen3.8-27B、Whisper base 和在线 Edge TTS 的[真实提供商完整验收](docs/int2_live_provider_acceptance_2026-10-25.md)，包含浏览器录音上传、三轮对话、结束反馈、两次关联重练、播放以及刷新/API 重启恢复。2026-10-26 的[录音核对三入口与 Gradio 真实提供商记录](docs/live_transcript_gradio_acceptance_2026-10-26.md)保留了修改确认、原音复用、草稿恢复及服务接线结果；其临时原始证据目前不可读取，本次收尾另以受控提供商复验实际页面，见[最新验收](docs/closeout_acceptance_2026-10-26.md)。这些运行使用合成语音输入，不能证明真人发音效果或生产负载下的延迟。

## 快速开始

开发环境使用 Python 3.12、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和 Git；React 前端需要 Node.js 20+。完整开发环境包含本地 Whisper、Gradio 和可选音频工具：

```bash
git clone https://github.com/likebeans/ChatterPal.git
cd ChatterPal
uv python install 3.12
uv sync --locked --python 3.12
```

完整环境的原生依赖：macOS 可用 `brew install ffmpeg portaudio`；Ubuntu/Debian 可用 `sudo apt-get install build-essential portaudio19-dev ffmpeg libsndfile1`。Windows 的 FFmpeg 需加入 PATH；PyAudio 安装问题见[部署指南](docs/deployment.md)。云端精简安装不要求本地 Whisper 或麦克风采集依赖。

复制 `.env.example` 为 `.env` 并编辑。默认组合是本地 Whisper、Edge TTS 和阿里百炼 LLM，需要 `DASHSCOPE_API_KEY`；不需要阿里云 ASR Secret。也可通过 `LLM_PROVIDER=openai`、`OPENAI_API_KEY`、`OPENAI_MODEL`、`OPENAI_BASE_URL` 选择兼容服务；`OPENAI_BASE_URL` 是完整的 chat-completions URL。进程环境变量优先于 dotenv，也可以完全不使用 `.env` 文件。`OPENAI_TIMEOUT` 控制兼容服务的 HTTP 超时，默认 30 秒、范围 0–300 秒（不含 0）；响应较慢的服务可显式设为 60，学习操作另受总超时约束。

```bash
# 后端：默认 127.0.0.1:8010
uv run chatterpal-run --mode api
```

另开终端启动 React：

```bash
cd frontend
npm ci
npm run dev
```

访问 http://localhost:3010；API 文档在 http://localhost:8010/docs，健康检查在 http://localhost:8010/health。默认只绑定本地回环地址。

独立 Gradio 和终端聊天：

```bash
uv run chatterpal-run --mode web
uv run chatterpal-run --mode chat
```

Gradio 默认地址为 http://localhost:7860。首次使用本地 Whisper 会下载所选模型。

## 可选依赖和安装命令

发布 wheel 内包含可独立使用的命令入口；帮助命令不加载模型或 UI。`chatterpal-setup` 默认检查环境，使用 `chatterpal-setup env` 显式创建配置，并拒绝覆盖现有文件。

```bash
chatterpal-run --help
chatterpal-setup --help
```

运行时依赖按用途分组：

| extra | 用途 |
| --- | --- |
| `api` | FastAPI / Uvicorn 与学习接口 |
| `cloud` | 云端 ASR、LLM、TTS 及其音频准备依赖 |
| `local-whisper` | 本地 Whisper / Torch |
| `assessment` | 可选声学分析工具 |
| `gradio` | 独立 Gradio 界面 |
| `microphone` | Python 本地麦克风采集 |
| `full` | 完整运行时 |
| `dev` | 完整运行时、测试和开发工具 |

例如安装构建出的 wheel：

```bash
pip install 'dist/chatterpal-0.1.0-py3-none-any.whl[api,cloud]'
chatterpal-run --mode api
```

精简安装需要选择云端 ASR 并配置相应凭据；默认 `ASR_PROVIDER=whisper` 需要另装 `local-whisper`。开发者的默认 `uv sync` 安装完整测试环境；精简安装、Docker 构建参数与平台验收范围见[部署指南](docs/deployment.md)。

## 存储和运行边界

任务学习默认使用单实例 SQLite；数据库路径由 `LEARNING_DB_PATH` 指定。音频是按 owner 保护的私有 BLOB，临时回放默认 1 小时，用户明确开启原音保留时默认 7 天。关闭保留会清除现有音频字节。导出包含文字和元数据，不包含音频字节。

自由聊天的生成音频使用单独的临时目录，按 TTL、文件数和总字节数回收；正在下载的文件有读取租约。响应依据真实容器返回扩展名与 MIME，明确的裸 PCM 会包装为浏览器可播放的 WAV。TTS 失败保留已生成的文字。

耗时学习写请求由有界 runtime 执行，携带 `request_id` 和版本校验。重复请求可恢复已有操作；删除、超时或租约撤销后，迟到的提供商结果不能重新发布。旧聊天和分析请求同样限制处理名额，调用方断开后，后台工作实际结束才释放文件和名额。

`/health` 的运行快照只输出有界计数和阶段耗时，不包含练习内容、音频或凭据。控制提供商基准由 `scripts/benchmark_runtime.py` 运行；它用于复现工程行为，不能当作真实提供商的生产 P95。

## Docker Compose

配置 `.env` 后，在仓库根目录运行：

```bash
docker compose up --build -d
docker compose ps
```

默认启动后端 API（8010）与静态 React 前端（3010）。生产环境需自行完成 HTTPS、访问控制、持久卷和容量配置。云端精简构建、可选 Gradio 与具体已验证平台见[部署指南](docs/deployment.md#docker-compose-部署)。

在同一局域网内进行两天手机试用，可使用[macOS 试用环境](docs/trial_environment.md)：固定应用版本、Caddy HTTPS、独立 SQLite、用户级进程管理与在线备份。实际启动及浏览器验收见[2026-10-26 试用环境验收](docs/trial_environment_acceptance_2026-10-26.md)。

## 开发与验证

打包测试使用明确准备的 wheelhouse，测试阶段禁用网络和索引，并为安装创建新缓存。首次或依赖锁变化后先执行以下准备命令（此阶段联网）：

```bash
export CHATTERPAL_TEST_WHEELHOUSE="$(mktemp -d)"
uv export --frozen --no-dev --no-emit-project --quiet --output-file "$CHATTERPAL_TEST_WHEELHOUSE/base.txt"
uv run --no-project --python 3.12 --with pip python -m pip download --only-binary=:all: --no-cache-dir --dest "$CHATTERPAL_TEST_WHEELHOUSE" --requirement "$CHATTERPAL_TEST_WHEELHOUSE/base.txt"
uv run --no-project --python 3.12 --with pip python -m pip download --only-binary=:all: --no-cache-dir --dest "$CHATTERPAL_TEST_WHEELHOUSE" hatchling
uv run python -m pytest
cd frontend
npm test
npm run build
npm run lint
```

后端默认覆盖率门槛为 80%；本次收尾工作树全量 2057 项通过、零失败或跳过，覆盖率 87.41%，包含即时建议校验及持久化重放、Gradio 主入口 HTTP 上传、双声道时长/基频和聊天会话回归。前端 186 项、build/lint 及 Gradio、React 实际浏览器验收本轮均已复跑通过，详见[收尾与即时纠正校验验收](docs/closeout_acceptance_2026-10-26.md)。这是基于 `main@73ab2d8` 的收尾改动的本地验收结果；主线历史 CI 见[录音核对验收](docs/transcript_confirmation_acceptance_2026-10-26.md)，`main@d98b13b` 的历史结果保留在[剩余优化验收](docs/remaining_optimizations_acceptance_2026-10-26.md)。

受控提供商的浏览器驱动包括 `scripts/verify_int2_browser.cjs`、`scripts/verify_learning_review_browser.cjs` 和 `scripts/verify_transcript_confirmation_browser.cjs`。真实提供商录音核对使用 `scripts/verify_transcript_confirmation_live.cjs`，通过 `TRANSCRIPT_ENV_FILE` 指向原私有配置。`scripts/verify_gradio_analysis_browser.cjs` 默认受控，设置 `GRADIO_ENV_FILE` 后使用真实提供商。真实验收需要可用凭据、Whisper 模型、Chrome 和音频工具；配置只在本地子进程读取。真实服务验收与受控基准分别记录。

## 文档

- [部署与配置](docs/deployment.md)
- [学习 API 契约](docs/learning_api_contract.md)
- [Gradio 收尾与即时纠正校验验收（2026-10-26）](docs/closeout_acceptance_2026-10-26.md)
- [真实录音核对与 Gradio 入口验收（2026-10-26）](docs/live_transcript_gradio_acceptance_2026-10-26.md)
- [CI 修复与录音核对验收（2026-10-26）](docs/transcript_confirmation_acceptance_2026-10-26.md)
- [剩余优化与复习验收（2026-10-26）](docs/remaining_optimizations_acceptance_2026-10-26.md)
- [SQLite 学习存储契约](docs/learning_store_contract.md)
- [INT2 离线与浏览器验收](docs/int2_weekend_acceptance.md)
- [INT2 真实提供商验收](docs/int2_live_provider_acceptance_2026-10-25.md)
- [开发指南](docs/development.md)

项目仍在持续开发。可运行流程和工程回归不代表经过校准的评分、学习效果验证或生产容量认证。

## 许可证与致谢

使用 [MIT License](LICENSE)。感谢 [Liyulingyue](https://github.com/Liyulingyue/) 的思路、[likebeans](https://github.com/likebeans/) 的项目开发，以及 Whisper、阿里云、Microsoft Edge TTS、Gradio 和 React 生态的支持。
