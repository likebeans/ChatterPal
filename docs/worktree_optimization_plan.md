# ChatterPal 并行优化计划

> 历史分工：本计划记录 2026-10-23 起的工程任务，保留用于追溯，不按其中“后续批次”重复创建任务或工作树。后续学习流程与大部分 F–I 工程项已经交付；当前功能见 [README](../README.md)，最新收尾范围与验证结果见 [2026-10-26 收尾验收](closeout_acceptance_2026-10-26.md)。现有工作树的未提交内容需先辨认归属与是否已集成，再决定如何处理。

日期：2026-10-23。初始调查基于 `a90b85c`；聊天基线现已提交为 `2f4fc30`。第一轮 A–E 已按顺序进入独立集成分支 `integrate/optimization-round-1`，集成修复为 `575164f`。首次验收的 36 项失败已全部修复，重新联合验收后端 1273 项通过、覆盖率 81.59%，前端、真实 API、Gradio 会话和新源码 ARM64 Docker 检查通过。本轮修复、回归测试与验收文档统一保存为后续优化基线；完整结果与边界见 [`integration_recovery.md`](integration_recovery.md)，首次失败清单保留在 [`integration_round_1.md`](integration_round_1.md)。本文保留原任务范围与分工，后续任务应从保存本轮修复后的基线开始。

## 推荐安排

第一轮开 **5 个 worktree**，分别执行 A–E。先修正功能和资源问题，恢复测试能力；第二轮再处理 TTS、部署依赖和评分可信度。最后才拆分大类和扩大并发。

聊天链路的 88 项针对性回归已复验通过，并保存为本地提交 `2f4fc30`。A–E 实际均从 `a90b85c` 创建；集成人先保存聊天基线，再按 E → C → A → B → D 合并，保留各任务独立提交。

| 阶段 | 可同时进行的任务 | 进入条件或实际状态 |
| --- | --- | --- |
| 准备 | 保存聊天基线 | 已完成：`2f4fc30`，含聊天优化、回归和计划文档 |
| 第一轮 | A 分析后端、B React、C ASR、D Gradio、E 测试 | 五组独立提交均已完成，起点为 `a90b85c` |
| 集成 | 聊天基线 → E → C → A → B → D | 已无冲突合入独立集成分支；联合验收另行记录 |
| 第二轮 | 先 F TTS，再 H 打包部署 | 第一轮集成验收通过；两项可能共享导入与调用点，各自从更新后的集成提交创建新 worktree |
| 第三轮 | G 评分可信度 | A、B、C、E、F 已合入；评分字段和页面需要一起调整 |
| 第四轮 | I 运行资源与结构优化 | 正确性回归稳定，先测资源和延迟，再拆分与优化 |

P0 表示影响功能正确性、结果可信度或基本验收；P1 表示明显影响体验、性能或交付；P2 表示后续可维护性和规模改进。任务可以同时包含不同优先级的子项。

## 第一轮任务与文件归属

以下路径以仓库根目录为起点。每组新增自己的测试；共享的 `docs/optimization_plan.md`、本计划及文档索引由集成人统一更新。

| 任务 / 分支 | 优先级 | 主要负责文件 | 暂不修改 |
| --- | --- | --- | --- |
| A `fix/analysis-pipeline` | P0 | `services/correction.py`、`services/ai_correction.py`、`core/assessment/phoneme.py`、`core/assessment/prosody.py`、`web/api_server.py` | React、ASR、TTS、Settings、评分公式 |
| B `fix/frontend-audio-state` | P0 / P1 | `frontend/src/`；必要的前端测试工具及锁文件 | Python、API 字段名称、整站视觉重设计 |
| C `perf/asr-runtime` | P1 | `core/asr/base.py`、`core/asr/whisper.py`、`config/settings.py`、`config/loader.py`、`.env.example` | API 路由、聊天服务、Aliyun 回调隔离、依赖版本 |
| D `fix/gradio-session-state` | P1 | `web/app.py`；必要的 `web/components/` 接线 | `services/chat.py`、API、Settings、React |
| E `test/restore-collection` | P0 | 原有破损测试、`tests/conftest.py`、pytest/coverage 配置、CI | 生产业务代码、其他组新增测试、依赖版本 |

完整 Python 路径前缀为 `src/chatterpal/`。若任务发现必须跨越归属范围，应先记录具体阻塞和所需接口，由集成人安排；避免两组同时改一个文件的不同位置。

### A：修通基础分析和 AI 分析

**需要修复的问题**

- `CorrectionReport` 丢失已经得到的 `recognized_text`，API 和 AI 服务却读取该字段。
- `AIAnalysisResult` 没有 API 读取的 `overall_score`、`recognized_text`；其 `difficulty_assessment` 与 API 使用的 `difficulty_level` 也不一致。
- AI 提示词里的 JSON 大括号与 `.format()` 冲突；基础分析失败后，异常路径还可能引用未赋值的 `base_report`。
- 音素与韵律分析器按临时目录前缀删除文件，可能删除调用方的输入，影响后续分析。
- 两个分析上传路由仍使用秒级文件名，清理没有覆盖全部异常路径；同步分析还会阻塞 API 事件循环。

**实施范围**

1. 明确服务报告到 HTTP 响应的映射，贯通真实转写、纠错、建议、难度与下一场景。
2. 修复提示词格式化和异常回退；AI 未配置时明确使用基础分析，不能伪装成完成了 AI 分析。本轮不新增模型提供商或依赖。
3. 只删除当前调用创建并拥有的临时文件；路径、字节和元组输入分别处理所有权。
4. 分析上传使用唯一文件名与 `finally` 清理；把同步提供商和分析调用移出事件循环。
5. 保留现有 HTTP 字段；评分校准交给 G。第一轮页面应说明当前分数尚未校准。

**验收标准**

- 基础与 AI 两条路由在成功、AI 未配置、基础分析失败、AI 返回无效数据时都有明确结果或可解释错误。
- 真实转写完整经过 ASR → 报告 → HTTP；不以参考文本冒充识别文本。
- 调用方输入文件在成功和失败后均保留；内部临时文件和上传副本均清理。
- 两个同时提交的分析请求不共用上传文件；慢分析期间 `/health` 仍可响应。
- 新增 `tests/test_pronunciation_contract.py`、`tests/test_ai_correction_contract.py`、`tests/test_analysis_file_ownership.py`。使用真实服务与路由，仅替换外部边界。

**可复制任务说明**

> 执行 docs/worktree_optimization_plan.md 的 A 任务。先用真实服务和 HTTP 路由复现报告字段、异常回退和文件所有权问题，再局部修复。只修改 A 归属文件与 A 新增测试，保持本文约定的 HTTP 字段，保留聊天链路的 88 项回归。不改评分公式、不新增提供商。完成后报告修改、测试命令与输出、遗留问题和实际 diff 范围；提交和外发按主会话授权执行。

### B：录音、播放和 React 状态

**需要修复的问题**

- 聊天用户录音的 `blob:` URL 被拼成 Base64 音频地址，导致回放失败。
- `AudioRecorder.tsx` 把录音资源放在 `window.currentRecording`，缺少组件卸载和部分初始化失败时的清理。
- 发音页把 MediaRecorder 数据包装成 `audio/wav`，实际字节没有转换为 WAV。
- 对象 URL 没有及时释放；清空聊天、重录或切换场景后，旧请求仍可能写回。
- `App.tsx` 条件卸载标签页，切页会丢失聊天会话和分析状态。
- 基础分析页面读取 `score/original_text/corrected_text`，与当前 API 字段不一致。

**实施范围**

1. 统一录音输出为可解码的 PCM16、单声道、16 kHz WAV；确需重采样时做实际重采样，文件头与真实采样率一致。
2. 复用录音逻辑，用组件引用保存资源；停止、卸载、切页、权限请求晚返回或初始化失败时释放麦克风、节点和 AudioContext。
3. 区分 URL 与 Base64 音源；替换、清空和卸载时回收对象 URL。
4. 使用请求取消和请求序号保护状态；即使取消未能阻止服务器完成，也忽略旧响应。
5. 切页保留已完成的聊天和分析状态，同时停止录音；按既有 API 字段显示结果。
6. 补齐录音按钮名称、键盘操作、加载和失败恢复；保留现有页面布局。

**验收标准**

- 录音可以回放并上传；输出文件可由音频解码器实际读取，不能只检查 MIME。
- 录音中切页后麦克风释放；返回页面保留已有聊天；重复录音不累积活动流。
- 清空聊天后晚响应不恢复旧消息和 session；场景 A 的晚响应不会覆盖场景 B。
- 基础和 AI 分析按约定字段显示，错误和不可用状态可理解；当前固定评分明确标示尚未校准。
- `npm run build` 通过；补充前端生命周期/竞态测试与浏览器实际录音流程验证。必要测试工具由本组维护，后端测试组不修改前端配置。

**可复制任务说明**

> 执行 docs/worktree_optimization_plan.md 的 B 任务。负责整个 frontend 的录音、播放、页面状态和过期请求处理，避免拆给多个代理同时修改页面。按本文冻结的 API 字段开发；先复现 blob 回放、伪 WAV、卸载泄漏和晚响应写回，再修复。只改前端及其测试工具，不改 Python 或重新设计整站。交付构建结果、可重复的浏览器操作与竞态测试。

### C：Whisper 复用和 ASR 输入正确性

**需要修复与可以优化的地方**

- 当前 SpeechRecognition Whisper 适配器每次调用 `whisper.load_model()`，热请求仍付模型加载成本。
- 文件识别先消耗半秒做环境噪声调整，随后才读取正文，录音开头可能丢失。
- 默认中文策略与英语练习目标不一致，长英文单词还会被应用启发式规则扣减置信度。
- ASR 基类的 Gradio 元组处理路径混合了识别结果和音频输入，需要用真实调用验证并修正。
- 线程化后必须限制同一模型的并发推理，避免内存和显存随请求增长。

**实施范围**

1. 在适配器内受控延迟加载并复用模型，按模型与设备配置管理；避免修改第三方库的全局行为或无限缓存模型。
2. 有效传递已有设备配置，定义并发推理限制和初始化失败后的重试行为。
3. 保留完整录音样本；统一路径、字节、Gradio 元组到正确的音频输入。
4. 明确 `en/zh/auto` 语言策略与显式配置；正常长英文词不能仅因长度被判错。
5. 保留 ASR 公共签名、重试和错误类型，以及已完成的 Aliyun 请求隔离。

**验收标准**

- 重复与并发请求只加载预期数量的模型；加载失败可恢复，模型缓存有上限。
- 带明确开头信号的测试音频没有被截掉；三种输入形式均得到一致文本。
- 正常长英文词不触发长度惩罚；语言和设备配置真实传入推理边界。
- 新增 `tests/test_whisper_runtime.py`、`tests/test_asr_inputs.py`、`tests/test_asr_configuration.py`。
- 离线测试不下载模型。若本机已有模型，再记录冷启动、热请求、峰值内存和实际模型信息；没有模型则明确该性能部分未实测，不承诺提升百分比。

**可复制任务说明**

> 执行 docs/worktree_optimization_plan.md 的 C 任务。限定修改 ASR 基类、Whisper 适配器、ASR 配置接线及自己的新测试。先确认实际第三方调用是否每次加载模型，建立加载次数、完整输入和英文识别策略的回归，再实现受控复用。保留 Aliyun 并发隔离和公共接口，不改 API/聊天服务/依赖锁。分别说明离线机制验证与真实模型实测结果。

### D：Gradio 浏览器会话

**需要修复的问题**

实际页面固定传入 `default_web_session`，没有保存服务新建会话的 ID。已离线复现连续两次发送创建两个会话，第二次历史只剩第二轮；改 `web/components/chat_tab.py` 而不检查实际入口，无法修复该问题。

**实施范围**

- 从实际 `web/app.py` 接线入手，使用每个浏览器独立的 `gr.State` 保存会话 ID。
- 可在首次发送前调用已有 `create_session()`，再复用该 ID，保留 `process_chat()` 签名。
- 清空操作同步重置界面和当前浏览器的服务端会话；失败不导致其他浏览器历史变化。
- 核实发送与输入提交回调、音频输出和现有组件之间的实际关系。

**验收标准**

- 同一浏览器连续三轮保持同一个会话，LLM 上下文含前轮回复。
- 两个浏览器会话相互隔离；一个清空或失败不影响另一个。
- 真实启动 Gradio 并驱动实际事件回调；只调用 `ChatService` 不能代替入口验收。
- 新增 `tests/test_gradio_sessions.py`；不修改 `services/chat.py`，不把演示评分页写成已完成能力。

**可复制任务说明**

> 执行 docs/worktree_optimization_plan.md 的 D 任务。修复 web/app.py 实际聊天页面的浏览器会话持久化和隔离。使用现有 create_session/process_chat 接口与 gr.State，不改聊天服务。验证连续三轮、双浏览器隔离、清空和失败恢复；必须覆盖实际 Gradio 事件接线。其他页面演示回调仅记录，不扩展评分算法。

### E：恢复测试收集与离线验收

**需要修复的问题**

本轮复核了 36 个 `test_*.py`，其中以下 17 个有语法错误：

```text
test_core_functionality_only.py    test_end_to_end_chat_flows.py
test_error_handling.py             test_error_scenarios_integration.py
test_final_verification.py         test_functionality_verification.py
test_integrated_error_handling.py  test_llm.py
test_performance.py                test_performance_and_ux.py
test_real_functionality.py         test_runner.py
test_services.py                   test_topic_error_handling.py
test_topic_integration.py          test_tts_enhanced.py
test_tts_error_handling.py
```

此外，部分共享 fixture 使用无法解码的假音频、旧接口或不一致的评分尺度；全仓 80% 覆盖率门槛没有完整运行结果支撑。原有 `test_tts.py` 还存在 4 项已确认的基线失败，需要逐项判断测试预期是否过时。

**实施范围**

1. 保留每个测试的意图，修复语法与编码损坏；先让全量收集通过。
2. 将真实云调用、设备和模型测试改为明确 opt-in；默认离线测试替换外部边界，音频 fixture 使用有效 WAV。
3. 仅在接口和行为证据充分时更新过时断言；生产缺陷要交给模块负责人修复，不能以跳过测试消除。
4. 本组可维护 `tests/conftest.py` 和上述旧测试，必要时修正其他旧测试预期。不改其他组新增测试，也不降低上一批 88 项回归的断言。
5. 第一轮只修改 `pyproject.toml` 的 pytest/coverage 配置，CI 放在 `.github/workflows/`；依赖版本、extras 和 `uv.lock` 留给 H。

**验收标准**

- `uv run python -m pytest -o addopts='' --collect-only -q` 返回成功；外部服务不参与默认收集。
- 输出离线全量执行结果，明确区分修复的测试错误、真实产品缺陷与 opt-in 测试。
- 加入可重复的 CI 收集与离线回归；覆盖率用实际报告说明，不能靠排除主模块或删除断言过关。
- 第一轮集成后全量离线回归通过才算整体完成；若 E 提前完成收集，仍由对应模块修复已记录的生产缺陷，再由集成人验证。

**可复制任务说明**

> 执行 docs/worktree_optimization_plan.md 的 E 任务。先恢复 17 个旧测试的语法与收集，再修正有证据的旧 fixture/接口断言，建立默认离线、真实外部依赖 opt-in 的测试入口。只修改旧测试、共享 fixture、pytest/coverage 配置和 CI，不修改生产代码或其他组新增测试。不通过 blanket skip、删断言或降低覆盖率掩盖缺陷；提供全量结果与应交回业务组的可复现失败清单。

## 第一轮共同接口约定

A、B 可以并行，依据是当前 HTTP 模型已有以下字段；第一轮不各自发明新字段名。

```text
POST /api/pronunciation/analyze
  overall_score: number
  recognized_text: string
  corrections: [{type, original, corrected, explanation}]
  suggestions: string[]

POST /api/pronunciation/ai-analyze
  overall_score: number
  recognized_text: string
  grammar_corrections: [{type, original, corrected, explanation}]
  pronunciation_feedback: [{type, original, corrected, explanation}]
  scenario_suggestions: [{scenario_type, difficulty_level, reason}]
  personalized_tips: string[]
  difficulty_level: string
  next_scenario: string | null
  confidence_score: number
  detailed_analysis: string
```

服务内部可以有不同模型，由 A 在 HTTP 边界转换与验证。`next_scenario` 有值时需可用于当前场景选择器，例如 `daily_conversation-intermediate`。`recognized_text` 是实际转写；识别失败时使用明确的失败或不可用状态。第一轮保留数值字段以便修通现有接口；页面说明评分尚未校准，G 再统一升级缺失维度、来源与可评估状态。

聊天接口继续使用现有字段和同源 `/api`，保持旧 `chat_with_audio()` 三元组与新详细结果接口兼容。ASR 改进通过现有工厂进入两套 Web 入口。

## 第二轮及后续可优化项

### F：TTS 参数、缓存与音频格式（P1）

**分支**：`fix/tts-request-options`。在第一轮后开始，先合入，再由 H 拆分可选依赖与导入。

- 修复并发请求通过共享实例修改音色造成串音色的风险；音色和语速等参数随请求传递。
- 缓存键纳入实际提供商、模型、音色、语速、音量、音调和格式；测试同文本不同配置不共用错误音频。
- 将 CosyVoice 已声明的参数传入 SDK，先核对已安装版本支持的参数。
- 统一真实音频编码、扩展名与下载 Content-Type；不能把 MP3 字节标成 WAV。
- 清理 TTS 基类重复的 `test_connection/clear_cache/get_cache_stats`，先锁定当前有效行为，再删除被覆盖实现。

文件归属：`core/tts/`、聊天服务中 TTS 调用、API 聊天与下载路由及自己的新测试。验收：双音色并发隔离、缓存区分参数、真实格式可解码、失败仍返回文本、既有 Edge 事件循环回归通过。公共合成/播放接口保持兼容。

> 执行 F：先建立音色并发、缓存配置隔离和编码一致性的失败回归，再修复请求级参数与 SDK 接线。只修改 TTS 和必要调用点，不同时重写整个聊天服务。报告实际编码、缓存行为与保留接口。

### G：评分可信度与分析结果来源（P0，依赖前序接口稳定）

**分支**：`fix/assessment-validity`。在 A、B、C、E、F 后开始，因为会同时影响报告模型、API 和结果页面。

- 移除固定演示评分作为真实评估结果的行为。
- 为每个维度标明来源、可用状态及计算依据；无音频、依赖缺失或证据不足时显示不可评估，不能填零或默认分冒充测量。
- 缺失维度不再直接当零分参与加权；LLM JSON 的类型、范围、必填项与失败回退需要验证。
- 文本差异使用能处理顺序、遗漏、插入和重复词的对齐方式；文本相似度不冒充声学发音准确率。
- 接入真实已提取特征时明确其解释边界；专业音素准确率需要额外对齐模型与标注样本，作为独立研究决策。
- AI 提供商与统一配置的整合另作兼容性检查，不混入未经验证的模型迁移。

文件归属：`core/assessment/`、纠错与评估服务、分析 API 模型、前端结果渲染及新回归。验收：静音、无效输入、缺依赖、缺维度、异常 JSON 和词序变化有明确结果；有标注样本时报告与人工标签的误差/相关性，没有样本时只完成可信状态与解释，不宣称评分已校准。

> 执行 G：先定义维度来源、不可用状态和分数语义，再连同报告、API、UI 一起实现。消除固定评分和缺失维度按零计分，用有效输入与失败样本检验。将工程正确性与评分校准分别报告。

### H：CLI 打包与部署依赖（P1 / P2）

**分支**：`build/optional-runtimes`。在 E、F 后开始，基于已合入的 TTS 行为调整依赖与延迟导入。

- `chatterpal-run`、`chatterpal-setup` 指向未进入 wheel 的 `scripts` 包，需迁入可发布包或修正正式入口。
- 区分 API 云端、本地 Whisper、Gradio 和设备录音所需依赖；使用延迟导入，使不启用的运行时不必安装。
- 统一开发依赖定义和锁文件；明确 CPU 安装方式，避免云端镜像无必要包含本地推理/CUDA 依赖。
- 核对 API、React、Gradio 的实际部署入口和文档，记录镜像大小、构建时间及启动时间；amd64 与 arm64 分别实测，未测的平台明确标出。

文件归属：`pyproject.toml`、`uv.lock`、`scripts/`、包内 CLI、需要延迟导入的模块、Docker/Compose、部署文档。保留 F 已验证的 TTS 行为。验收：干净目录安装构建出的 wheel 后，公开命令至少可显示帮助并运行对应入口；云端配置在没有本地推理/设备依赖时能启动；Docker 健康检查与 `/api` 代理通过。若无法实测某平台，不用另一平台结果代替。

> 执行 H：从已合并 E 的基线恢复发布入口，再拆可选依赖和延迟导入。用干净 wheel 安装和真实 Docker 启动验证；保证现有配置兼容。只有本组改依赖锁，记录实际镜像与启动数据。

### I：运行资源、观测与职责拆分（P1 / P2）

**分支**：`refactor/runtime-lifecycle`。在行为回归稳定后进行，建议继续拆成多个小提交。

- 生成音频、过期会话和缓存建立可配置的上限/TTL；活跃请求使用的资源不能被清理。
- 为 ASR、LLM、TTS 与排队时间分别记录耗时，关联请求但不记录密钥；测量并发下 P50/P95、错误率、RSS、临时目录增长。
- 按实测决定线程/推理并发上限、超时和过载响应；单独验证取消请求后工作线程与清理的行为。
- 逐步把 `services/chat.py` 的会话、提供商编排、Gradio 适配分离，保留现有公共接口和行为回归。
- 跨进程会话存储、任务队列、流式语音和 GPU 推理服务只在实际部署需求明确后立项。

验收：可控长时运行下文件、会话和缓存有界；清理不影响正在下载/推理的请求；提供可重复压测命令和拆分前后行为对比。不以行数减少作为唯一验收。

> 执行 I：先测资源增长和阶段延迟，先修生命周期，再逐步拆分职责。不要一次迁移数据库、引入队列或改所有公共接口。每一步保留可回滚边界与原有行为回归。

## worktree 与后续批次起点

第一轮工作区已存在，不能重复执行创建或清理命令。集成工作区为 `/Users/dd_y/Code/learn/ChatterPal-integration`，分支为 `integrate/optimization-round-1`。主工作区保留聊天基线提交；A、E 工作区中未提交的聊天副本仍属于各自工作区，集成只采用其任务提交。

后续批次应从验收完成后的集成提交创建新 worktree。先检查 `git branch --list`、`git worktree list` 和各工作区状态；分支与目录已存在时复用或选择新名称，不清理其他会话的未提交内容。

运行时使用独立的 `node_modules`、临时音频和会话目录。Python 验收若复用现有虚拟环境，必须明确从集成工作区导入源码并核对依赖锁。多套服务同时运行时分配不同端口，并相应设置代理和 CORS；当前 API 启动脚本固定 8010、Vite 代理固定指向 8010，单改前端端口并不能切换后端。

## 集成与最终验收

1. 集成人在独立分支依次合入 E、C、A、B、D；分支完成时间不同可调整，但 A+B 必须联合验证。各任务只由自己的分支提交一次，避免重复 cherry-pick 与 merge。
2. 原有聊天 88 项回归继续通过；运行各组新增测试与全量离线测试。全部新增计数以最终运行结果为准。
3. 真实 HTTP 验证文本聊天、语音聊天、基础与 AI 分析、场景查询、音频下载、慢提供商期间健康检查和异常清理。
4. 浏览器验证录音→回放→上传→展示，切页/清空/重录/切场景后旧请求不会污染状态；Gradio 验证多轮与双浏览器隔离。
5. 测试默认不依赖真实云密钥或模型下载；若某项真实模型/浏览器/平台未实测，交付中明确说明。
6. 每组输出：修改范围、复现命令、测试命令与结果、兼容性影响、待解决问题。公共计划由集成人更新；提交、推送、PR 和合并仍按用户授权执行。

第一轮已存在的针对性聊天回归命令见 `docs/optimization_plan.md`。新任务先增加能捕获真实问题的回归，再实施；不要同时进行全库格式化、框架升级或无关重命名。
