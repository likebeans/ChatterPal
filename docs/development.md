# 开发与验证 ChatterPal

本页面向修改源码和测试的贡献者，说明如何启动应用、选择验证范围和复现持续集成（CI）检查。产品入口见 [README](../README.md)，贡献流程见 [CONTRIBUTING.md](../CONTRIBUTING.md)，提供商与容器配置见[部署指南](deployment.md)。

## 准备开发环境

开发基线使用 [`.python-version`](../.python-version) 指定的 Python 3.12，前端使用 Node.js 20+ 和 npm。安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)；当前 CI 使用 uv 0.10.7，并从 `uv.lock` 安装冻结依赖。

默认开发依赖组包含完整运行时和测试工具，安装 PyAudio 等扩展需要原生依赖。macOS 与 Ubuntu / Debian 可按下列命令准备；其他系统及编译失败处理见[原生依赖说明](deployment.md#原生依赖与安装失败排查)，不据此推断所有平台已经验证。

macOS 使用 Homebrew 安装音频工具；缺少编译工具时先安装 Xcode Command Line Tools：

```bash
xcode-select --install
brew install ffmpeg flac portaudio libsndfile
```

Ubuntu / Debian 安装音频库与构建工具，并确保 Python 头文件匹配实际的 3.12 解释器：

```bash
sudo apt-get update
sudo apt-get install build-essential python3-dev portaudio19-dev ffmpeg flac libsndfile1
```

克隆仓库并安装冻结的开发依赖。以下命令从仓库根目录执行，后文另有说明的前端命令除外：

```bash
git clone https://github.com/likebeans/ChatterPal.git
cd ChatterPal
uv python install 3.12
uv sync --frozen --python 3.12
npm --prefix frontend ci --no-audit --no-fund
```

默认 `uv sync` 会安装 `dev` 依赖组，其中包含 `full` 运行时，无需额外添加 `--dev`。只做前端开发时可仅安装 Node.js 依赖；选择较小的 Python 运行时 extras 时参考[安装配置](deployment.md#克隆并安装)。本页的后端测试依赖完整开发环境。

## 启动 React 与 API

React 是主要界面，Vite 默认在 3010 端口提供页面，并将 `/api` 代理到本机 8010。正常学习流程需要选定提供商的配置；仅执行默认测试或浏览器 fixture 检查无需填写真实凭据。

需要启动真实应用时，在根目录复制模板，编辑提供商配置，再检查字段类型：

```bash
cp .env.example .env
uv run --frozen python scripts/verify_deployment_config.py
uv run --frozen chatterpal-run --help
```

配置检查器默认读取 `.env.example`，如需检查自己的文件及必需凭据，使用 `uv run --frozen python scripts/verify_deployment_config.py --env-file .env --require-credentials`。它只检查本地配置，不验证远端密钥有效性；进程环境变量优先于 `.env`，字段定义见 [`Settings`](../src/chatterpal/config/settings.py)。

在根目录启动 API；`--mode api` 必须显式指定，因为 CLI 的默认模式是 Gradio：

```bash
uv run --frozen chatterpal-run --mode api --host 127.0.0.1 --port 8010
```

另开终端，从根目录进入前端目录启动 Vite：

```bash
cd frontend
npm run dev -- --host 127.0.0.1 --port 3010 --strictPort
```

打开 [React 页面](http://127.0.0.1:3010)、[API 文档](http://127.0.0.1:8010/docs)或[健康接口](http://127.0.0.1:8010/health)。`/health` 反映本地组件初始化状态；默认本地 Whisper 首次识别可能下载模型，Edge TTS 和云端提供商需要网络，部分提供商调用会计费。

多 worktree 开发时，为 API 设置独立端口和 `LEARNING_DB_PATH`，并通过 `CHATTERPAL_API_TARGET` 将 Vite 代理指向该 API。直接跨源请求还需配置 `LEARNING_ALLOWED_ORIGINS`；具体配置见[独立开发端口说明](deployment.md#int2-学习流程与独立开发端口)。

Gradio 作为可选界面保留。完整开发环境已包含依赖；在根目录运行 `uv run --frozen chatterpal-run --mode web --host 127.0.0.1 --port 7860`，然后打开 [Gradio 页面](http://127.0.0.1:7860)。

## 定位要修改的模块

按运行路径查找实现与契约，避免把历史验收记录当作当前命令定义：

| 路径 | 职责 |
| --- | --- |
| `frontend/src/` | React 界面、录音编码、API 客户端和学习流程 |
| `src/chatterpal/web/` | FastAPI 服务、路由、音频资源与可选 Gradio 界面 |
| `src/chatterpal/learning/` | 任务、转录确认、重练、复习和 SQLite 存储 |
| `src/chatterpal/services/` | 聊天、纠错、评估和会话服务 |
| `src/chatterpal/core/` | ASR、LLM、TTS 适配器和音频分析 |
| `src/chatterpal/config/`、`src/chatterpal/cli/` | 配置校验、组件构造和公开命令 |
| `tests/`、`frontend/src/test/`、`frontend/browser-tests/` | 后端、前端单元测试和浏览器 fixture 驱动 |
| `scripts/`、`.github/workflows/tests.yml` | 跨层验收脚本与 CI 命令 |

修改学习数据结构和接口时，对照[学习 API 契约](learning_api_contract.md)与[存储契约](learning_store_contract.md)。Python 格式与类型工具配置在 `pyproject.toml`，前端脚本在 `frontend/package.json`；沿用相邻代码风格并保持改动集中。

## 选择验证范围

根据行为变化选择相关测试，PR 中记录执行命令和结果。完整后端回归、浏览器 fixture、真实 HTTP 与真实服务验收具有不同边界，应分别说明执行范围。

### 运行后端局部测试

[`tests/conftest.py`](../tests/conftest.py) 默认隔离主机凭据和数据路径，阻止外部网络、真实模型加载与实体录音设备访问，允许回环地址通信。默认用例使用 provider double、合成音频和临时存储；安装依赖及准备 wheelhouse 的下载发生在测试之前。

在根目录运行与改动相关的用例。例如配置变化可运行以下测试，`--no-cov` 仅为局部诊断关闭全包覆盖率检查：

```bash
uv run --frozen python -m pytest tests/test_configuration_contract.py -q --no-cov
```

同样可选择 `tests/learning/` 下的具体文件或 `-k` 条件。单文件结果不能替代完整回归的覆盖率结论；若包含 `tests/test_packaged_cli.py`，先执行下一节的 wheelhouse 准备。

### 准备完整后端测试

完整测试包含在仓库外构建、安装 wheel 并检查公开 CLI 的用例。它们要求设置 `CHATTERPAL_TEST_WHEELHOUSE`，并在测试前下载基础依赖和构建后端的 wheel；缺少该目录时会失败，即使其他依赖已经缓存。

在 macOS / Linux 的同一终端中创建临时根目录；以下下载命令与 [CI 的准备步骤](../.github/workflows/tests.yml)一致，CI 额外通过 `GITHUB_ENV` 把变量传给后续步骤：

```bash
export RUNNER_TEMP="$(mktemp -d)"
export CHATTERPAL_TEST_WHEELHOUSE="$RUNNER_TEMP/packaged-cli-wheelhouse"
uv export --frozen --no-dev --no-emit-project --quiet --output-file "$RUNNER_TEMP/packaged-cli-requirements.txt"
uv run --no-project --python 3.12 --with pip python -m pip download --only-binary=:all: --no-cache-dir --dest "$CHATTERPAL_TEST_WHEELHOUSE" --requirement "$RUNNER_TEMP/packaged-cli-requirements.txt"
uv run --no-project --python 3.12 --with pip python -m pip download --only-binary=:all: --no-cache-dir --dest "$CHATTERPAL_TEST_WHEELHOUSE" hatchling
```

安装完成并保留上述环境变量后，在根目录执行与 CI 相同的收集和回归命令：

```bash
uv run --frozen python -m pytest -o addopts='' --collect-only -q
uv run --frozen python -m pytest -q --cov-report=xml --junitxml=pytest-results.xml -W error::RuntimeWarning -W error::pytest.PytestUnhandledThreadExceptionWarning
```

[`pyproject.toml`](../pyproject.toml) 默认启用整个 `src/chatterpal` 包的覆盖率统计，并要求至少 80%。报告写入终端、`htmlcov/` 和 `coverage.xml`，JUnit 结果写入 `pytest-results.xml`；完整验收保留这一门槛。CI 的后端 job ID 为 `offline`，其检查范围不包含真实服务、模型推理或实体麦克风验收。

### 运行前端检查

在 `frontend/` 中执行单元测试、包含 TypeScript 检查的生产构建和 ESLint 检查：

```bash
npm test
npm run build
npm run lint
```

这些命令对应 CI 的 `frontend` job 中的基础检查。交互、录音、请求恢复或布局变化时，继续执行适用的浏览器验证；CI 实际包含哪些浏览器步骤以 `tests.yml` 为准。

### 使用浏览器 fixture 验证交互

`frontend/browser-tests/` 通过 Playwright 启动 Chromium，以假麦克风和拦截后的 API 响应验证界面、录音编码及资源释放。它不需要 Python API 或真实提供商，也不验证后端存储和远端服务。

先在 `frontend/` 安装 Chromium；Linux 缺少浏览器系统依赖时使用 `npx playwright install --with-deps chromium`：

```bash
npx playwright install chromium
npm run dev -- --host 127.0.0.1 --port 3010 --strictPort
```

保持 Vite 运行，在另一个终端的 `frontend/` 目录显式传入相同页面地址：

```bash
npm run test:browser -- http://127.0.0.1:3010
npm run test:learning:browser -- http://127.0.0.1:3010
```

两个驱动不会自行启动 Vite，省略 URL 时的默认端口分别为 3012 和 13204。`CHROME_EXECUTABLE_PATH` 可指定已有 Chromium / Chrome 可执行文件；`BROWSER_OUTPUT_DIR` 可指定截图和 `results.json` 的输出目录，建议使用仓库外的临时目录。

### 验证真实 HTTP、SQLite 与转录确认

仓库根目录的浏览器驱动可以自行启动 API 和 Vite，使用真实 HTTP、cookie 与临时 SQLite，但在提供商边界注入受控实现。需要完整 Python 开发环境、已安装的前端依赖和 Chromium；它们不会证明真实 ASR / LLM / TTS 服务有效。

按改动选择对应脚本，从根目录执行。例如转录确认与复习变化可运行以下命令：

```bash
node scripts/verify_transcript_confirmation_browser.cjs
node scripts/verify_learning_review_browser.cjs
```

两者使用 `.venv/bin/python` 作为默认解释器。转录确认脚本使用 `TRANSCRIPT_PYTHON` 指定解释器，API / UI 端口默认为 18240 / 13240，可通过 `TRANSCRIPT_API_PORT` / `TRANSCRIPT_UI_PORT` 调整。复习脚本使用 `INT2_PYTHON` 指定解释器，端口默认为 18220 / 13220，可通过 `REVIEW_API_PORT` / `REVIEW_UI_PORT` 调整。脚本输出验收目录，分享日志、截图或数据库前先检查私有数据。

### 单独运行真实服务、模型或设备验收

pytest 的 `external_api`、`model`、`device` marker 分别需要 `--run-external`、`--run-models`、`--run-devices` 显式开启。仅添加开关不会把已有 mock 用例变成真实服务测试；新增真实测试时，同时标注资源 marker 并准备对应凭据、模型或设备。

[`verify_int2_live.cjs`](../scripts/verify_int2_live.cjs) 与 [`verify_transcript_confirmation_live.cjs`](../scripts/verify_transcript_confirmation_live.cjs) 使用真实提供商，不属于默认离线回归。后者要求 `TRANSCRIPT_ENV_FILE` 指向私有配置，使用 macOS `say`、FFmpeg / ffprobe 和合成语音驱动假麦克风，还可能触发本地模型下载及付费调用。只有需要验证对应外部服务时才按脚本前提运行，并记录平台、模型、输入和实际调用范围。

用 Chromium 的手机尺寸视口测试布局，只能说明该视口的行为。合成输入、真实服务调用和实体设备验收应分别记录；不要把其中一种结果写成真人发音、实体手机或其他操作系统已经验证。

## 排查环境与运行问题

先区分依赖安装、本地配置、应用行为与真实服务错误，再选择检查：

| 现象 | 排查方向 |
| --- | --- |
| 安装时报 `portaudio.h` 或编译错误 | 检查原生音频库、编译器与 Python 3.12 头文件，参考部署指南 |
| `tests/test_packaged_cli.py` 报缺少 wheelhouse | 在同一终端完成 wheel 下载并保留 `CHATTERPAL_TEST_WHEELHOUSE` |
| 浏览器脚本连不上页面 | 确认 Vite 已启动、URL 与端口一致，或跨层驱动使用的端口未被占用 |
| 页面正常但 `/api` 请求失败 | 检查 API 地址、`CHATTERPAL_API_TARGET` 与开发服务器代理；直接跨源时核对允许的 Origin |
| 配置修改未生效 | 检查进程环境变量覆盖并重启服务；读取 `.env` 的优先级见部署指南 |
| `/health` 成功但聊天或音频失败 | 查看服务端进程输出，并分别检查提供商配置、网络与模型资源 |

需要进一步反馈时，按[问题报告说明](../CONTRIBUTING.md#报告问题或提出功能建议)提供最小复现和脱敏结果。凭据、真实对话、原音、数据库、cookie 与浏览器存储状态都可能包含私有信息，安全问题按 [SECURITY.md](../SECURITY.md) 处理。
