# 第一轮集成与联合验收

日期：2026-10-23。分支：`integrate/optimization-round-1`。工作区：`/Users/dd_y/Code/learn/ChatterPal-integration`。

> 本文封存首次验收结果。后续对这 36 项失败的修复、重新测量的覆盖率及新一轮联合验收结果，见 [`integration_recovery.md`](integration_recovery.md)；下文的失败清单与日志保留为历史证据。

聊天基线与 A–E 已按约定顺序集成，联合验收已执行完毕，集成修复保存为 `575164f`。**整体门槛未通过**：后端仍有 36 项失败，覆盖率 73.76% 低于 80%。前端、受控真实 API 联调、Gradio 会话事件及 ARM64 Docker 部署检查通过；这不消除全仓阻塞。

| 验收项 | 最终结果 |
| --- | --- |
| 后端全量收集 | 1073 项，退出 0 |
| 后端全量执行 | 1037 通过、36 失败、0 初始化错误、0 跳过；退出 1 |
| 全包覆盖率 | 73.76%，80% 门槛失败 |
| 前端组件测试 / build / lint | 59 项通过；构建与 lint 退出 0 |
| 真实 React → API 联调 | 初轮 14 项通过并发现目录缺陷；目录修复复验 6 项通过，最终状态/样式复验 5 项通过 |
| Gradio 实际 HTTP 事件 | 11 通过、1 项语音入口能力检查跳过 |
| Docker ARM64 | 3 服务健康、11 项部署检查通过，最终生产前端 4 组契约浏览器检查通过 |

下一步先按下方失败清单修通旧组件、历史、错误分类和偏好，再完成 F/G 对应的 TTS 与评分缺陷并恢复覆盖率；重新通过全仓门槛后再考虑发布合并。

## 集成范围

所有任务实际从 `a90b85c` 开始。先将主工作区的聊天优化保存为 `2f4fc30`，再从本地 `main` 创建独立集成分支，依次执行保留任务提交的 merge。

| 顺序 | 分支 | 任务提交 | 集成提交 |
| --- | --- | --- | --- |
| 1 | `perf/chat-request-pipeline` | `2f4fc30` | `1a2c344` |
| 2 / E | `test/restore-collection` | `cc5dcb9` | `fca96a8` |
| 3 / C | `perf/asr-runtime` | `7442a64` | `bed1cc6` |
| 4 / A | `fix/analysis-pipeline` | `2d809db` | `d1cd936` |
| 5 / B | `fix/frontend-audio-state` | `aa64c37` | `239a1e8` |
| 6 / D | `fix/gradio-session-state` | `e60172a` | `dbf21af` |

六次合并均无文本冲突，六个任务提交均为集成分支的祖先。A、E 工作区的未提交聊天副本未参与重复暂存。`main` 保持 `a90b85c`；本轮没有推送、创建 PR、合入 main 或删除原工作区。

## 后端全量验收

最终独立运行 `full-isolated-final`：**1073 项收集成功，1037 通过、36 失败、0 初始化错误、0 跳过，39 条警告**；测试耗时 149.28 秒，进程退出码 1。全包覆盖率 **5079 / 6886 = 73.76%**，未达到默认 **80%** 门槛。联合验收整体未通过，当前分支不能作为已通过 CI 的发布版本。

最终运行从集成工作区导入全部 45 个已加载项目模块，97 个源码、测试及配置文件在运行前后哈希一致。默认外部服务、真实模型、设备 opt-in 均关闭。JUnit 1073 项唯一测试及终端结果一致；`pytest-cov` 在覆盖率不达标时另将 `session.testsfailed` 加 1，因此退出元数据的 37 表示 36 个用例失败加覆盖率失败，不能写成 37 个失败用例。

第一次完整执行曾为 1001 通过、50 失败、10 初始化错误，覆盖率 73.05%。其中本地监听、`ps` 及工作区写入限制来自验收进程额外施加的限制，最终运行移除了这些限制，保留仓库自带离线保护。中间两次执行曾共用输出前缀，结果保留为历史证据；最终使用独立日志、JUnit、coverage 与 pytest 临时目录，日志只有一组开始/退出标记且无 NUL 字节。一次负载下的 Gradio 排队超时在最终独立全量中通过，未放宽超时或断言。失败测试未被删除、跳过或改成宽松断言。

### 保留的 36 项功能阻塞

| 类别 | 数量 | 实际失败与下一步 |
| --- | ---: | --- |
| 评分与韵律 | 4 | F0 返回默认 150 Hz 而非测试音频的 180 Hz；供应商错误被当成功；综合评分没有音素分析；建议未使用语速。按 G 明确可评估状态并接入实际特征。 |
| 旧 Gradio 组件 | 20 | `Markdown(scale=...)`、`Microphone(info=...)` 与 Gradio 5 不兼容；聊天返回格式、评分/纠错服务签名没有对齐。实际 `app.py` 会话入口单独验收通过，不能代替这些组件测试。 |
| 历史记录 | 2 | 分页输出与 messages 格式不兼容；清空后把内部 system prompt 计入可见历史。 |
| ASR 分类与错误提示 | 6 | 原始权限/格式异常未映射为对应用户提示；供应商失败被归为无语音；空文本提示成音频格式错误。应保留原因并分别处理输入与供应商错误。 |
| TTS | 2 | 配置磁盘缓存时调用不存在的 `_init_cache_dir`；自动播放配置未生效。纳入 F。 |
| 用户偏好 | 2 | 默认配置浅复制导致嵌套值被污染，重置与导入前重置未恢复默认。需隔离默认配置对象。 |

完整失败用例 ID 见本文末尾；每项保留原断言。解决上述失败后还需重新测量覆盖率，功能通过不自动代表覆盖率达到 80%。

集成时已修复的交互问题：

- C 直接调用 `whisper.load_model` 后，E 的模型 guard 和旧 fixture 仍拦截旧入口。新增直接 loader 的 opt-in 检查；真实解码与服务测试改为仅替换 `load_model`/`transcribe` 边界。
- 回环代理可能将外部请求转发出去，单独限制 socket 目的地址不足。增加 urllib 请求目标和重定向打开路径的检查，并验证正常显式 opt-in。
- 旧模型工厂断言补齐新增的语言和缓存配置；前端同源检查识别 TypeScript 泛型调用；清空环境变量的配置测试在隔离目录中验证默认路径。
- 旧版增强 ASR 曾接受 NumPy 波形；新文件解码路径破坏了这一行为。恢复原质量验证兼容性，保留原错误处理测试。
- 场景列表曾列出 32 个组合，但只有 3 个实际提供内容。现只列可用场景，无效枚举返回 422，未提供内容返回 404；没有虚构新场景。
- 前端应用与当前选择相同的推荐场景时，原先会清空上下文与已完成分析，又不会触发依赖未变的加载 effect。增加同值提前返回；不同场景继续取消旧请求并切换内容，新增组件回归先红后绿。

新增直接模型与代理保护回归先观察到 4 个缺失阻断失败，再修复；场景契约的 5 项回归也先复现失败。默认覆盖率门槛维持 80%，不能以针对性测试代替全仓结果。

## 前端与真实 API 浏览器联调

`npm ci --no-audit --no-fund` 安装通过；初始集成版本 Vitest 58 项全部通过，`npm run build`、`npm run lint` 通过。已有契约浏览器检查 4 组通过；这些检查替换 HTTP 响应，用于录音资源、重采样、晚响应与布局验证。

额外启动真实 Uvicorn（8010）和 Vite（13012），Chrome 使用测试麦克风，通过真实 `/api` socket 请求驱动 APIServer、ChatService、CorrectionService、AICorrectionService 和 WhisperASR，只替换外部提供商边界。真实联调初轮 14 组通过，另 1 组场景目录检查暴露 29 个不可用组合；场景修复后独立的 socket 与 UI 检查 6 组通过，包括可用目录、404/422、实际选择器、推荐跳转、晚响应隔离、移动布局及上传清理。

- 文本与语音聊天复用/清空会话；10 次上传对应 10 次模型推理边界调用，模型加载 1 次。
- 浏览器录音实际输出单声道、16 kHz、16 bit PCM WAV，并经浏览器音频解码；覆盖一次识别、转写一致、用户录音回放、音频下载。
- 基础分析显示真实转写和纠错；AI 未配置回退及受控 AI 成功解析均经过真实服务和 HTTP。场景切换、重录、清空和慢响应不会写回旧结果。
- 受控 LLM/ASR/AI 阻塞期间 `/health` 仍响应，样本耗时约 4.71/6.77/7.49 ms；这是本机受控样本，不是生产延迟承诺。
- 最终无输入/分析上传残留、无保留的分析任务，仅保留回复音频；无未捕获页面异常。保留 ScriptProcessor 弃用提示及已有声学依赖警告，不宣称零警告。
- 修复纠错标签缺失背景导致的低对比度，真实基础与 AI 截图均复查，白字与 `#5745a6` 背景对比度约 7.48:1。

最终组件回归 **59 / 59 通过（5 个文件）**，`npm run build`、`npm run lint` 均退出 0。相同推荐场景的新增用例先复现清空缺陷，再验证修复；最终真实 Vite → Uvicorn 浏览器检查 **5 / 5 通过**：同场景保留上下文与报告、不同场景请求实际目标并清空旧报告、基础与 AI 标签对比度、375 px 无横向溢出及上传/录音清理。该轮 console error 与未捕获页面异常均为 0；仍有已有 ScriptProcessor 弃用警告。

## Gradio 实际事件

真实 `create_app(settings)` / `ChatterPalApp.create_interface()` 于 17860 启动，`gradio_client` 经注册的 `/chat_response` 和 `/clear_chat` HTTP 队列执行：**11 通过、0 失败、1 项能力检查跳过**。

验证三轮复用同一 ID 且携带前轮回复、两个客户端 session hash 隔离、只删除被清空会话、清空后新 ID、初次/既有会话失败恢复、慢发送期间排队清空、两个排队发送、多轮上下文窗口、可解码回复音频与 TTS 失败保留文本。测试服务器已关闭。

跳过项为 Gradio 语音输入的单次 ASR：实际聊天页只有文本输入，回调固定传入 `audio=None`、`use_text_input=True`，没有可驱动的语音输入事件。该限制没有用服务函数测试冒充通过；语音聊天的真实联调在 React/API 路径完成。

## Docker

最终集成源码重建成功，独立项目 `chatterpal-integration-20261023` 的 **backend、frontend、gradio 三服务均通过 Compose 健康检查**。只绑定回环地址，端口分别为 18010、13010、17861；未读取主机 `.env`，运行配置使用明确的非密钥占位符和本机不可用 LLM 地址。

部署驱动的 **11 项检查全部通过**：

- 后端及 Nginx 健康检查、FastAPI OpenAPI/Swagger 可访问。
- Nginx `/api` 代理访问全部 3 个可用场景成功，缺少内容返回 404、无效枚举返回 422。
- 生产 JavaScript 与 WAV Worker 由 Nginx 正确返回 JavaScript 类型；4 个静态产物 SHA256 与最终本地前端构建一致。
- 未运行的本地 LLM 提供商返回可解释 500，随后健康检查仍正常；这验证错误路径，不表示真实云端聊天成功。
- 实际 Gradio 入口及配置包含浏览器 state 和 `chat_response` 事件。
- Python 服务以 UID 999 运行，`ffmpeg`、`flac` 存在，应用、缓存、数据、临时音频、日志及偏好目录可写。
- `/app/src/chatterpal` 与已安装 wheel 的全部 **48 个 Python 文件**逐一匹配集成工作区哈希。

最终生产前端另由 Chrome 驱动 **4 组契约浏览器检查全部通过**：真实录音与 16 kHz/单声道/PCM16 WAV 解码、回放上传与状态竞态、权限晚返回和初始化失败恢复、375 px/横屏/减少动画及切页清理、60 秒编码时 UI 可响应且 Worker 可终止。截图已检查，未捕获页面异常为 0。这套脚本替换 `/api` 响应；真实服务行为由上一节 Vite → Uvicorn 联调覆盖，不把两种证据混为一次真实供应商测试。

本次仅实测 **linux/arm64**。`docker image inspect` 记录后端镜像大小 594,807,735 字节（约 567 MiB）、前端 25,868,661 字节（约 24.7 MiB）；此字段为本机 Docker 引擎报告值，不是镜像传输大小，也不是内存占用。没有 AMD64 实测或真实 Whisper 冷/热推理性能结论。

验收后保存容器日志，停止并移除本轮独立项目的临时服务与网络。保留本轮镜像、5 个独立命名卷与本机证据，方便后续复查；没有执行卷删除或全局清理。原工作区和其他 Docker 项目未改动。

## 验证环境与边界

- 本机 macOS，Python 3.12；后端验收复用既有 `.venv`，显式从集成工作区导入源码。
- Docker 平台实测范围为本机 Linux ARM64；没有 AMD64 实测结果。
- 正式验收使用受控的外部 ASR/LLM/TTS 与 Whisper 模型边界，未使用真实云密钥。首轮全量测试发现旧 fixture 与新 Whisper 入口不一致，意外下载了 145,262,807 字节的 `base.pt`，随后中止；原日志和文件保留在验收目录。修复直接模型加载保护与 urllib 目标检查后重新验收，首轮中断结果不计为通过。端到端测试验证应用调用与数据传递，不代表真人识别质量或真实模型性能。
- 浏览器录音采用 Chromium 测试麦克风；不代表实体麦克风或所有移动浏览器已验收。
- 发音评分仍未校准；Gradio 的评分和纠错演示页、TTS 参数及格式、CLI 可发布入口、可选依赖、资源 TTL 和性能观测属于 F–I 后续批次。

## 证据位置

本机日志、驱动脚本、截图和 JSON 结果：`/private/tmp/chatterpal-integration-20261023/`。该目录是本机验收证据，不属于 Git 源码交付。

- 后端：`python-whole-suite/logs/full-isolated-final.log`、`reports/pytest-results-full-isolated-final.xml`、`reports/coverage-full-isolated-final.xml`、`reports/full-isolated-final-summary.json`。`reports/full-isolated-final-evidence.json` 封存 15 项证据的 SHA256；配置、导入来源和源码指纹在同目录。
- 前端：`frontend-acceptance/npm-{test,build,lint}-final.log`、`frontend-acceptance/browser-b-final/results.json` 与截图；初轮真实链路和场景修复证据分别在 `browser-real/`、`browser-scenarios-fixed/`。
- Gradio：`gradio/results.json`，包括实际事件、会话、能力缺口和关闭状态。
- Docker：`docker-build.log` 记录首次构建，最终镜像构建日志为 `docker-build-{backend,frontend}-final.log`；`docker-up-final.log` 记录三服务健康检查，`docker-results.json` / `docker-verification-final.log` 记录 11 项部署检查，`docker-services-final.log` 保留应用日志，`docker-browser-final/` 保留最终生产前端截图与 4 组浏览器结果。

## 复跑命令

仓库根目录运行以下标准命令，与 CI 相同。测试默认不允许外部服务、真实模型与设备；不要追加 `--run-external`、`--run-models` 或 `--run-devices` 作为离线验收的替代。每次全量运行使用独立输出目录且串行执行。

```bash
uv sync --frozen --python 3.12
uv run --frozen python -m pytest -o addopts='' --collect-only -q
uv run --frozen python -m pytest -q --cov-report=xml \
  --junitxml=pytest-results.xml -W error::RuntimeWarning

npm --prefix frontend ci --no-audit --no-fund
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run lint
```

本机复用了原工作区 `.venv`，通过独立驱动将 `sys.path`、工作目录及 pytest 子进程 `PYTHONPATH` 指向集成源码，并清除测试子进程的代理变量；没有更改主机代理。实际最终命令：

```bash
/Users/dd_y/Code/learn/ChatterPal/.venv/bin/python -I \
  /private/tmp/chatterpal-integration-20261023/python-whole-suite/scripts/acceptance.py \
  collect-final-serial
/Users/dd_y/Code/learn/ChatterPal/.venv/bin/python -I \
  /private/tmp/chatterpal-integration-20261023/python-whole-suite/scripts/acceptance.py \
  full-isolated-final
```

离线 `uv lock --check` 通过，共 159 个包；依赖清单和锁文件未因集成修复改变。

Docker 实测使用原 Compose 文件加本机独立端口/镜像/占位配置覆盖，等价命令如下。覆盖文件和探针保存在证据目录；默认部署可按仓库已有 Docker 文档运行。

```bash
CHATTERPAL_ENV_FILE=/private/tmp/chatterpal-integration-20261023/docker.runtime.env \
  docker compose -p chatterpal-integration-20261023 \
  --project-directory /Users/dd_y/Code/learn/ChatterPal-integration \
  -f /Users/dd_y/Code/learn/ChatterPal-integration/docker-compose.yml \
  -f /private/tmp/chatterpal-integration-20261023/compose.acceptance.yml \
  --profile gradio build backend frontend
# 同一 Compose 参数下运行：
# up -d --no-build --wait --wait-timeout 180 backend frontend gradio
# 验收后：down（保留独立命名卷和镜像）

CHATTERPAL_ENV_FILE=/private/tmp/chatterpal-integration-20261023/docker.runtime.env \
  /Users/dd_y/Code/learn/ChatterPal/.venv/bin/python -I \
  /private/tmp/chatterpal-integration-20261023/verify_docker.py
CHROME_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
BROWSER_OUTPUT_DIR=/private/tmp/chatterpal-integration-20261023/docker-browser-final \
  npm --prefix frontend run test:browser -- http://127.0.0.1:13010
```

## 最终失败用例清单

以下全部为实际执行失败，完整堆栈保存在后端 JSON 与 JUnit 中。此清单用于后续修复和复验，不加入默认跳过列表。

```text
tests/test_assessment.py::TestProsodyAnalyzer::test_analyze_prosody_success
tests/test_assessment.py::TestProsodyAnalyzer::test_analyze_prosody_failure
tests/test_assessment.py::TestPronunciationCorrector::test_assess_comprehensive
tests/test_assessment.py::TestPronunciationCorrector::test_generate_suggestions
tests/test_chat_tab_interactions.py::TestChatTabInputModeInteractions::test_initial_input_mode_from_preferences
tests/test_error_scenarios_integration.py::TestAudioDeviceFailureScenarios::test_audio_permission_denied
tests/test_error_scenarios_integration.py::TestUserFriendlyErrorMessages::test_error_message_localization[permission]
tests/test_error_scenarios_integration.py::TestUserFriendlyErrorMessages::test_error_message_localization[format]
tests/test_functionality_verification.py::TestWebComponentsBasic::test_chat_tab_creation
tests/test_functionality_verification.py::TestWebComponentsBasic::test_score_tab_creation
tests/test_functionality_verification.py::TestWebComponentsBasic::test_correct_tab_creation
tests/test_history_management.py::TestHistoryManagement::test_history_pagination_formatting
tests/test_history_management.py::TestHistoryManagementIntegration::test_cleared_history_does_not_count_system_prompt
tests/test_integrated_error_handling.py::TestIntegratedErrorHandling::test_user_friendly_error_messages
tests/test_services.py::TestChatService::test_error_handling_asr_failure
tests/test_services.py::TestChatService::test_process_chat_error_handling
tests/test_tts_enhanced.py::TestTTSEnhanced::test_disk_cache_operations
tests/test_tts_enhanced.py::TestTTSIntegration::test_auto_play_functionality
tests/test_user_preferences.py::TestUserPreferences::test_reset_to_defaults
tests/test_user_preferences.py::TestUserPreferences::test_export_import_preferences
tests/test_web_components.py::TestChatTab::test_create_interface
tests/test_web_components.py::TestChatTab::test_format_conversation_display
tests/test_web_components.py::TestScoreTab::test_create_interface
tests/test_web_components.py::TestScoreTab::test_evaluate_pronunciation_with_target
tests/test_web_components.py::TestScoreTab::test_format_score_display
tests/test_web_components.py::TestScoreTab::test_generate_feedback_text
tests/test_web_components.py::TestScoreTab::test_error_handling_service_failure
tests/test_web_components.py::TestCorrectTab::test_create_interface
tests/test_web_components.py::TestCorrectTab::test_analyze_pronunciation
tests/test_web_components.py::TestCorrectTab::test_format_detailed_analysis
tests/test_web_components.py::TestCorrectTab::test_format_phoneme_analysis
tests/test_web_components.py::TestCorrectTab::test_generate_improvement_suggestions
tests/test_web_components.py::TestCorrectTab::test_get_practice_exercises
tests/test_web_components.py::TestCorrectTab::test_error_handling_service_failure
tests/test_web_components.py::TestWebComponentIntegration::test_cross_tab_data_flow
tests/test_web_components.py::TestWebComponentIntegration::test_consistent_audio_handling
```
