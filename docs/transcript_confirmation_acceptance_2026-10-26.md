# CI 修复与录音转写核对验收

> 交付状态更新（2026-10-26）：下文是实现当时的本地验收记录。录音核对及 CI 修复现已进入 `main@73ab2d8`；该 SHA 的 [GitHub Actions 40264524073](https://github.com/likebeans/ChatterPal/actions/runs/40264524073) 后端 1978 项、前端 186 项及 build/lint 均通过。下文“尚未提交/推送”与“未产生新远端绿色运行”只描述记录时点。后续 Gradio 收尾和即时纠正校验见 [收尾验收](closeout_acceptance_2026-10-26.md)；真实提供商历史记录仍按原范围解释。

日期：2026-10-26。工作树：`ChatterPal-transcript-confirmation`，分支：`feat/transcript-confirmation`，起点：main `d98b13b19f3ef09b1393ce8f4358e4a9c256d466`。本文记录当前未提交工作树的本地验收；尚未提交、推送、合并或部署，也未重新运行远端 GitHub Actions。

## 交付行为

任务回答、关联重练和复习录音统一使用：录音 → “识别并核对录音” → 核对或修改文字、试听或重录 → “确认文字并提交”。预览只调用 ASR，不调用 LLM/TTS，不增加源记录 revision；最终提交复用识别结果和同一份原音资产。

机器原文与确认文字分别保存为 `machine_transcript` 和 `confirmed_text`，并记录 `transcription_id,confirmed_at`。既有 `text` / `transcript` 和文字反馈使用确认文字；不将用户修改后的文字当作声学测量结果。历史可查看机器识别原文。

预览是既有 runtime 中 action=`transcribe` 的持久操作，ready 草稿是 schema v2 的内部子记录。最终确认在同一事务中校验并消费草稿、转移原音关联、提交正式记录和成功操作；不复制 BLOB，不重复识别。原有文字和直接音频 API 保持兼容。

ready 草稿及私有原音在 1 小时有效期内支持整页刷新和 API 重启恢复。浏览器只持久化请求、操作、转写及草稿归属编号；未上传录音、文字正文和核对修改不存入 localStorage，未确认修改不保证跨整页刷新保留。来源 revision 变化不会自动重新绑定。放弃或重录先撤销服务端草稿；撤销结果不确定时保留内容和恢复编号。

## CI 故障与修复

原失败：`tests/test_packaged_cli.py::test_base_wheel_config_commands_need_no_runtime_extras`。main 的 [失败运行](https://github.com/likebeans/ChatterPal/actions/runs/40254529555)为 1959 passed、1 failed；前端通过，后端覆盖率仍达到门槛。

根因：`uv sync --frozen` 缓存了已安装轮子，但未保证新离线 resolver 所需的全部索引元数据及候选版本文件。仅依赖 runner 或开发机热缓存，使新虚拟环境中的 `uv pip install --offline` 不可复现。隔离缓存可稳定复现；只补元数据还可能选择锁文件之外、未缓存的候选版本。

修复：联网准备阶段从冻结锁导出基础依赖并下载 binary wheels 与构建依赖到显式 wheelhouse；打包构建和安装测试使用 `--offline --no-index --find-links`、新缓存、`--no-config --no-python-downloads`。基础 wheel 安装仍实际解析依赖，没有增加 `--no-deps`；原有 CLI 外部目录运行、extras 隔离、配置只读和拒绝覆盖断言保留。未设置 wheelhouse 时明确输出准备命令。完整本地命令见根目录 README。

| 检查 | 结果 |
| --- | --- |
| 本机新准备缓存、空 wheelhouse、新测试缓存 | 16 项打包测试通过 |
| 独立复核 | 16 项通过；移除 PyYAML wheel 后安装按预期失败 |
| extras 隔离 | 基础环境无 numpy/whisper/torch/pyaudio/gradio/parselmouth/openai/fastapi；仓库外 CLI 配置与 demo 成功 |
| Linux ARM64 / Python 3.12.15 | 原失败用例 1 passed，2.25 秒；全新 wheelhouse 和离线安装缓存 |
| GitHub Actions | 当前工作树尚未推送，未产生新的远端绿色运行 |

Linux 使用既有 `chatterpal-h-cloud:20261025-4mwftc` 镜像只读挂载当前源码，没有重新构建镜像。补充探针使用系统 Python 和独立 pytest，禁用应用级 conftest，以实际执行原打包测试及其 installed_wheel/base-venv 断言。此前两次补充容器探针分别被默认 venv 无 pip、应用 conftest 缺 numpy 挡住；修正探针入口后得到上表结果，不将失败探针计为通过。

CI 本机证据：`/tmp/chatterpal-ci-final.8fs1SO`；独立证据：`/tmp/chatterpal-ci-independent-review.06aklkx7`、`/tmp/chatterpal-ci-review-negative.iy4vcm3y`；Linux 最终日志：`/tmp/chatterpal-transcript-linux-ci-final.log`。

## 最终自动回归

| 检查 | 结果 |
| --- | --- |
| 完整 Python 回归 | **1978 passed，0 failed，0 errors，0 skipped**；103.16 秒；30 条依赖或兼容警告 |
| 全包覆盖率 | **8549 / 9786 = 87.36%**；既有 80% 门槛和排除项不变 |
| 严格异常检查 | RuntimeWarning、PytestUnhandledThreadExceptionWarning 提升为错误，完整运行通过 |
| 转写后端专项 | 18 passed，覆盖确认、恢复、归属、冲突、失败、并发、期限、取消和通用写保护 |
| 修复后的关联 runtime/store 检查 | 109 passed |
| 前端完整回归 | **186 passed，12 个测试文件**；其中转写流程 25 项，HTTP client 31 项 |
| 前端构建与 lint | TypeScript + Vite build、ESLint 零警告通过 |
| 触及文件静态检查 | Python Black、flake8 F 类、浏览器驱动语法、`git diff --check` 通过 |

全量后仅将测试复用 fixture 的导入改为显式别名并绑定，修正新增 F401/F811；18 项专项、Black 和触及文件 flake8 再次通过，产品源码未变。未声称全仓既有未用导入已清零。

后端证据：`/tmp/chatterpal-transcript-final-pytest.log`、`/tmp/chatterpal-transcript-final-pytest.xml`、`/tmp/chatterpal-transcript-confirmation-final.coverage`。前端证据：`/tmp/chatterpal-transcript-frontend-{tests,build,lint}.log`。

## 独立复核后的修复

独立复核发现三项可复现边界，均先补测试复现，再修复：

1. 通用 `/records/{transcription_id}` 删除可绕过专用已消费保护，删除成功确认的操作回执，却留下正式回答。现在通用草稿删除返回 403，确认幂等结果仍可恢复；源记录或身份整体删除保持级联清理。
2. 排队识别被撤销后仍会进入 ASR。现在工作者执行提供商之前重新校验持久 claim；已撤销且尚未开始的识别不调用 ASR。正在运行的同步提供商不承诺可强制中断，其迟到结果仍无发布权。
3. 通用批量创建和操作完成入口漏掉 transcription 创建限制。现在通用单条、批量和完成入口统一拒绝创建内部草稿，只有明确的 transcribe 完成流程可创建；回归确认无伪造记录留下。

专项还覆盖：LLM 失败后草稿仍 ready 并可明确重试；同一草稿并发确认只执行一次；删除期间阻止迟到发布；提供商处理期间到期完整回滚；清除原音时中断 pending ASR；复习识别接收在 due_at 前、确认在之后仍记为提前练习；相同请求恢复及不同有效内容冲突；原音和机器原文随重启恢复。

## 实际浏览器与手机截图

专项驱动：`scripts/verify_transcript_confirmation_browser.cjs`。实际 Chrome、Vite 同源代理、APIServer socket 与 SQLite；没有拦截教学 API。ASR/LLM/TTS 是受控提供商，浏览器用模拟麦克风进入真实录音组件。

最终证据目录：

`/var/folders/dt/0y71cp9s52j217lxh6nh3r4m0000gn/T/chatterpal-transcript-browser-Cd2pyp`

该次完整通过：

- 录音与放弃未识别录音不会提交回答或调用 ASR。
- 三入口各预览一次，ASR 共 **3 次**；每次预览只增加 ASR，LLM/TTS 和源快照不变。最终确认均不增加 ASR；完整运行 LLM 6 次、TTS 3 次。
- 核对框显示机器原文，编辑后提交 JSON 只有转写 ID、确认文字和原有上下文字段；保存的正式文字与机器原文分别保留。
- 原音 audio_asset_id 复用，任务确认前后下载字节完全一致；服务端原音在 API 重启和整页刷新后实际播放。
- 重放最终请求恢复同 operation，新请求再次消费返回 409。
- 内部历史切页保留核对修改；ready 草稿刷新和 API 重启后恢复，无再次识别。
- 复习预览不改变排程和问题；符合条件的确认按既有证据推进排程；历史刷新恢复确认文字和机器原文。
- 第二身份读取草稿和私有原音均 404；`pageerrors=[]`。
- 375px 手机与 812px 横屏无横向溢出。实际 DOM 只有一个页头，scrollWidth=375；手机核对 composer 宽 307px，右边界 341px。已查看最终整页、视口和核对区截图，确认按钮可见且内容无截断。

首轮手机长截图出现重复页头，不能作为布局已验证的依据。DOM 始终只有一个页头；截屏前清除输入焦点、回到页顶并等待两帧后，完整长截图正常。专项驱动同时保存独立视口和核对区截图，避免只依赖整页合成结果。

截图：`transcript-check-desktop.png`、`transcript-check-mobile.png`、`transcript-check-mobile-viewport.png`、`transcript-check-mobile-composer.png`、`transcript-history-desktop.png`；布局测量：`mobile-layout.json`；请求和操作：`results.json`、`operations.json`。

更新后的 `scripts/verify_learning_review_browser.cjs` 也已通过，包含 3／7 天排程、提示与看答案、到期隐藏、冲突保稿、API 重启、录音核对、原音回放、历史、身份隔离和餐厅/酒店任务，ASR 1 次。证据目录：`/var/folders/dt/0y71cp9s52j217lxh6nh3r4m0000gn/T/chatterpal-review-browser-cfrmS1`。

`frontend/browser-tests/verify-learning.cjs` 的契约 fixture 浏览器回归通过，包含文字草稿、pending 刷新恢复、关联重练、新录音核对、0.75× 播放、375px/横屏和麦克风释放，两个场景 errors 均为空。证据：`/tmp/chatterpal-transcript-contract-browser`。此 fixture 与上面的实际服务验收分别记录。

## 范围与现有限制

本次未重新调用真实云端提供商。历史 Qwen3.8-27B / Whisper base / Edge TTS 结果见[剩余优化验收](remaining_optimizations_acceptance_2026-10-26.md)，不能替代新增核对流程的真实服务质量或延迟观察。`scripts/verify_int2_live.cjs` 已适配明确核对按钮，但本次只做语法检查。

识别中的请求尚无 transcription ID；通过 request/operation 恢复，不自动重跑未知 ASR。ready 草稿默认 1 小时；保留开启只在最终确认时升级原音期限。丢弃已物理删除的草稿再次 DELETE 返回 404，没有新增撤销 tombstone。过期音频仍使用既有按生命周期回收，没有新增后台定时器。单实例 SQLite、人工任务/复习题目数量及评分范围保持原有边界。
