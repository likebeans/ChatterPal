# 集成失败修复与重新联合验收

日期：2026-10-23。分支：`integrate/optimization-round-1`。工作区：`/Users/dd_y/Code/learn/ChatterPal-integration`。验收以 `266fb05` 为基线；本轮修复、回归测试与本文一同保存到集成分支，作为后续优化基线。

**原来的 36 项失败全部修复，本轮联合验收通过。** 后端完整执行 1273 项，0 失败、0 初始化错误、0 跳过；全包行覆盖率 **5803 / 7112 = 81.59%**，默认 80% 门槛保持不变。逐项对照首次验收的 JUnit，36 个原失败用例均保留且通过。首次验收的 1037 通过、36 失败、73.76% 覆盖率及中间问题仍封存在 [`integration_round_1.md`](integration_round_1.md)。

| 验收项 | 本轮最终结果 |
| --- | --- |
| 后端收集 / 完整离线执行 | 独立收集 1273 项成功；执行 1273 通过，0 失败、0 错误、0 跳过；退出 0；188.17 秒 |
| 全包覆盖率 | 81.59%，80% 门槛通过 |
| 前端组件测试 / 构建 / lint | 59 项通过，构建与 lint 退出 0 |
| 最终 React → 真实 API | 15 组通过；额外 5 组状态、场景及样式检查通过 |
| 前端契约浏览器检查 | 4 组通过，覆盖真实录音、WAV 解码、竞态与资源释放 |
| Gradio 实际入口 HTTP 队列 | 11 项通过；1 项语音聊天入口能力检查跳过 |
| 旧 Gradio 组件 | 72 项针对性检查通过；包含聊天、评分、纠错的真实 HTTP 队列；同时进入最终全量 |
| Docker | 当前源码重建；Linux ARM64 三服务健康，11 项部署检查和 4 组生产浏览器检查通过 |
| 依赖锁与补丁格式 | `uv lock --check --offline` 通过，159 个包；`git diff --check` 通过 |

## 修复内容

### 36 项原失败

| 类别 | 原数量 | 最终修复 |
| --- | ---: | --- |
| 评分与韵律 | 4 | 返回真实整段声学测量，180 Hz 录音的 F0 来自 Parselmouth；配置的 myprosody 全部失败时不报告成功；纠错结果保存整段声学摘要，低语速建议明确对应语速。保留现有演示评分并标明尚未校准。 |
| 旧 Gradio 组件 | 20 | 修正 Gradio 5 构造参数；页面绑定 messages 适配器；评分和纠错调用真实服务签名，并将结果格式化成组件要求的字符串。 |
| 历史记录 | 2 | 分页返回 messages；可见计数排除内部 system prompt，清空后显示零条。 |
| ASR 分类与错误提示 | 6 | 保留提供商异常原因，区分无语音、服务错误、超时、权限及格式错误；空文本使用独立文本错误提示；每次错误定义深复制，避免原因和建议在请求间污染。 |
| TTS | 2 | 实现可选磁盘缓存初始化、校验与持久化；新合成、内存命中和磁盘命中均支持可选自动播放，播放失败保留成功音频。 |
| 用户偏好 | 2 | 默认值、配置合并和导出快照使用深复制；嵌套设置修改后重置和导入前重置可恢复默认值。 |

磁盘 TTS 缓存使用经过类型、时间、base64 和 SHA256 校验的 JSON，旧 pickle 文件作为损坏条目处理，不执行反序列化；同目录临时文件经 flush、fsync 和原子替换落盘。TTL、容量淘汰和清空同时处理内存与磁盘；缓存写入或播放失败不会重新调用已成功的合成提供商。

声学统计修复了将傅里叶系数实部误作频谱权重的问题，改用非负复数幅值；真实双频录音验证重心与扩散，严格 RuntimeWarning 检查通过。静音、无有声帧或依赖缺失明确标记不可用，兼容默认值不冒充实测。详细/专家纠错与基础摘要复用同一次声学分析，失败结果也不重复测量。

### 覆盖率补测发现的相关缺陷

新增文本、日志、评估服务和提供商响应边界测试后，还复现并最小修复了：

- 带额外字段的日志绕过级别过滤；记录异常时将布尔值交给 formatter 导致该条日志和堆栈丢失；彩色控制台 formatter 修改共享记录，令文件输出混入 ANSI 字符。统一交给标准日志入口创建记录，并恢复彩色格式化后的级别名称。
- HTTP 200 的 OpenAI SSE 流包含 `error` 时被当成成功的空回复；现在抛出包含提供商原因的 `LLMError`，已输出的前缀不会被重放。
- CosyVoice 返回字符串或字典时被当成音频成功返回；现在拒绝非二进制响应，并将 SDK 的 bytearray 复制为不可变 bytes。

这不包含完整的 TTS 参数、音色缓存隔离或音频格式设计重构，也没有引入新模型、依赖或评分训练。

## 回归测试与契约调整

本轮新增 200 项回归用例，全部进入最终 1273 项完整执行：

| 新测试文件 | 项数 | 主要行为 |
| --- | ---: | --- |
| `test_assessment_recovery.py` | 3 | 真实声学摘要、复数频谱数学、静音不可用状态 |
| `test_correction_analysis_reuse.py` | 4 | 三种分析级别只测量一次，失败结果不被重试 |
| `test_error_preference_recovery.py` | 14 | ASR 原因、错误实例与嵌套偏好隔离 |
| `test_evaluation_fallback_contracts.py` | 12 | 评估服务降级、自由发言、比较方向、识别失败与模块状态 |
| `test_legacy_gradio_callbacks.py` | 4 | 实际组件 postprocess 与 HTTP 队列，失败保留历史及真实报告展示 |
| `test_logging_contracts.py` | 6 | 字段、Unicode、级别过滤、堆栈、文件轮转及装饰器保留结果 |
| `test_provider_response_contracts.py` | 118 | 真实适配器的响应解析、流中断、HTTP/SDK 错误、二进制音频和重试次数 |
| `test_text_processing_contracts.py` | 14 | 文本规范化、分词、可读性、相似度、时间戳和反馈差异 |
| `test_tts_cache_recovery.py` | 25 | 跨实例缓存、TTL、淘汰、损坏恢复、原子落盘与播放失败保留音频 |

外部提供商只在 requests/SDK/模型边界替换；音频解码、适配器、服务编排和相关声学提取保持实际实现。真实云服务、模型权重和设备 opt-in 均关闭。

旧测试有两类契约需要明确校正，未删除行为断言或加入默认 skip：

1. 两处 Gradio 事件绑定断言和一处 messages postprocess 调用改为新的 `_handle_chat_messages` 适配器；原 `_handle_chat` 的 pair 格式相等断言仍保留，实际 messages 组件仍执行 postprocess。
2. `test_asr_inputs.py` 的文件所有权参数化用例将 `None`、`ASRError`、`ValueError` 分别期望为 `ASR_NO_SPEECH`、`ASR_SERVICE_ERROR`、`AUDIO_FORMAT_ERROR`。第一遍全量因旧分类预期出现 4 项失败；校正后全部通过。原调用次数和调用方文件逐字节不变的断言保留。

原全包覆盖范围、排除规则、80% 门槛、离线保护与 CI 配置均未修改。最终只有父验收进程执行全量，使用独立日志、JUnit、coverage、模型缓存和临时目录；106 个源码、测试和配置文件前后哈希一致，45 个已加载项目模块全部来自集成工作区。JUnit、收集列表、退出码和终端总数相互核对，无重复用例或日志覆盖。

本轮第一遍完整执行为 1119 通过、4 项旧 ASR 预期失败、覆盖率 75.54%；新增有意义的边界测试并修复其发现的问题后，最终 `full-recovery-02` 得到 1273 通过、81.59%。最终全量保留 41 条警告，主要是 Gradio tuples/事件循环、websockets、librosa/audioread 的弃用或兼容提示，以及已有启动音色不在选项中的提示；没有 RuntimeWarning 被忽略。

## 浏览器、Gradio 与容器验收

最终 Chrome 经过真实 Vite → Uvicorn socket 验证 15 组功能和 5 组场景/样式回归：文本及语音聊天、单次 ASR、16 kHz 单声道 PCM16 WAV、录音回放、基础与 AI 分析/降级、场景目录 404/422、同值场景保留报告、慢响应失效、上传清理及提供商阻塞时健康检查响应。另 4 组前端契约检查覆盖权限晚返回、部分初始化失败、切页清理和 60 秒编码的 Worker 可取消。源码在最终浏览器执行期间未变；状态/样式检查无 console error，所有浏览器检查无未捕获页面异常。桌面与 375 px 截图已实际检查，无横向页面溢出。

实际 `app.py` 的 Gradio HTTP 队列重新验证 11 项，包括多轮上下文、两个浏览器隔离、失败重试、排队清空、可下载回复音频及 TTS 失败保留文本。语音聊天入口能力检查仍为 1 项 skip：当前实际聊天页只有文本输入，没有可驱动的语音聊天事件。修复的旧 `ChatTab`、`ScoreTab`、`CorrectTab` 单独通过真实 HTTP 测试；本轮没有把共享的旧组件实例挂入实际入口，实际页面中的评分/纠错演示回调仍需后续接线。

Docker 前两次构建在 `auth.docker.io/token` 遇到 `EOF`，随后固定到先前已知的 Python/Nginx 摘要并成功命中本地缓存，再使用仓库原 Dockerfile 与 Compose 正常重建。没有更改主机代理、认证或仓库构建文件。一次同时构建 backend/gradio 时，两个 exporter 竞争同一镜像标签；最终仅 `build backend frontend`，共享后端镜像供两个 Python 服务使用。

最终镜像在独立项目 `chatterpal-recovery-r02w9h` 启动三服务并通过健康检查，11 项部署探针与 4 组生产浏览器检查通过：

- Nginx /api 同源代理、全部 3 个可用场景、OpenAPI/Swagger、JavaScript 和 WAV Worker 正常。
- `/app/src/chatterpal` 与已安装 wheel 的 48 个 Python 文件都匹配最终工作区 SHA256；4 个静态产物匹配本轮本地构建。
- Python 以 UID 999 运行，FFmpeg/FLAC 存在，运行目录可写；受控不可用 LLM 返回错误后健康检查仍正常。
- 实测平台为 `linux/arm64`；后端镜像 ID 为 `sha256:dfb0098aca81b1a48d9a774aa50b68c8d83e8b0443ae471c097db0188380139a`，引擎报告大小 594,803,617 字节。

最终停止并移除本轮三容器和网络，保留镜像、5 个独立卷及证据。API、Vite、Gradio 验收进程已关闭，8010、13012、17860、18010、13010、17861 端口均可重新绑定；未清理其他 Docker 项目或原 worktree。

## 边界与后续

发音总分和分项仍为兼容保留的演示/启发式数值，页面和报告明确“尚未校准”；整段基频、共振峰等测量不等于音素对齐或真人发音准确率。本轮没有真实云端质量、Whisper 冷/热推理性能、实体麦克风、全部移动浏览器或 Linux AMD64 实测结论。

旧 Gradio 组件的修复不代表实际入口已接线其全部功能。TTS 提供商参数与格式统一、缓存键配置隔离、评分模型校准、CLI/可选依赖和跨进程资源策略继续按 F–I 计划处理。首次集成时曾意外下载 Whisper `base.pt`，其事实与旧日志仍保留在首次验收报告；本轮默认离线保护保持启用。

验收证据在提交前封存，因此其中的 `HEAD` 为 `266fb0598514e6bfdb159701f39c8de8cfe82156`，`uncommitted: true` 描述当时状态。后续优化应从包含本文的修复提交创建 worktree；提交前已核对最终验收的 106 个源码、测试和配置文件哈希一致。本次交付目标为集成分支，本地 `main` 保持 `a90b85c`。

## 复跑与证据

仓库标准后端命令（与 CI 相同）：

```bash
uv run --frozen python -m pytest -o addopts='' --collect-only -q
uv run --frozen python -m pytest -q --cov-report=xml \
  --junitxml=pytest-results.xml -W error::RuntimeWarning
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run lint
```

本轮复用现有 Python 3.12 `.venv`，驱动显式从集成工作区导入源码、清除子进程代理并隔离输出。最终完整命令：

```bash
/Users/dd_y/Code/learn/ChatterPal/.venv/bin/python -I \
  /private/tmp/chatterpal-failures-20261023-R02w9H/pytest_runner.py full-recovery-02
```

证据根目录：`/private/tmp/chatterpal-failures-20261023-R02w9H/`。这是本机验收证据，不纳入 Git 源码：

- `acceptance-summary.json` 汇总交叉核验结果，`evidence-sha256.json` 封存 24 份关键证据的 SHA256。
- `full-recovery-02.log` 与 `python/full-recovery-02/`：JUnit、coverage XML/HTML、配置、导入来源、收集列表、源码指纹和退出码；`full-recovery-01.log` 保留中间失败。
- `frontend-{test,build,lint}.log`、`frontend-build/`：本轮前端测试与构建产物。
- `browser-final/`：最终真实 API、状态/样式、契约浏览器驱动、请求日志、音频、截图和关闭状态；第一遍本轮结果另存于 `frontend-acceptance/`。
- `gradio-final/`：实际入口队列结果、HTTP 事件、提供商上下文和关闭状态；旧组件的针对性证据位于 `gradio/`。
- `docker-{build,up,verification,browser,services,down}-final.log`、`docker-results.json`、`docker-acceptance-summary.json`、`docker-browser-final/`：新源码容器构建、三服务与生产浏览器结果；前期 EOF 和标签竞争日志也保留。
- `seal_acceptance.py`：交叉检查原 36 项失败、最终 1273 项用例、完整覆盖率、源码稳定性、导入路径、浏览器与 Docker 结果，并写入证据哈希清单。
