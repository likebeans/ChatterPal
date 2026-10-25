# INT2 真实提供商验收

日期：2026-10-25。状态：**完整流程通过**。使用用户指定的 `https://maas.marketingforce.com/v1/chat/completions` / `Qwen3.8-27B`，经项目现有 OpenAI 兼容适配器调用，配合真实 Whisper base 和在线 Edge TTS Jenny，在 Chrome 中完成“描述周末”的三轮回答、结束反馈、两次关联重练、音频播放、页面刷新及 API 重启恢复。

## 实测结果

| 检查项 | 本轮结果 |
| --- | --- |
| 实际应用 | 正式 `APIServer`、真实提供商工厂、Vite、Chrome、SQLite；无教学 API 拦截，无提供商替身 |
| 隔离 | 独立临时数据库与证据目录；API/UI 端口 18210/13210 |
| 任务与草稿 | 开始 weekend、取提示、进出学习记录后文字草稿保留 |
| 三轮回答 | 两轮文字、一轮页面录音上传，保存三组用户/教练回合；所有教学操作 succeeded |
| 真实 ASR | 对话录音、重练录音各识别一次，共 2 次；耗时 1.876 秒、0.289 秒；使用本地已缓存 base 模型，无模型下载 |
| 真实 LLM | 3 次教练回复、1 次结束反馈、2 次新尝试评价，共 6 次，全部成功 |
| 真实 TTS | 3 次教练回复、1 次目标范读，共 4 次在线合成，全部成功 |
| 反馈 | status=available，活动/同伴/感受三个目标均有用户原话 evidence；保留一个语法重点 `I go hiking` → `I went hiking` |
| 重练 | 首次完整原句 + 文字重练 + 录音重练共 3 次尝试；parent_attempt_id 形成连续链，两次新尝试均有新表达证据，available/met |
| 音频有效性 | 原音/重练 WAV 为 PCM16、16 kHz、单声道；教练/范读 MP3 为 24 kHz、单声道；保存的四份音频均经 ffprobe 与 ffmpeg 完整解码 |
| 浏览器播放 | 教练回复和目标范读均在真实 `<audio>` 播放，readyState=4，currentTime 实际前进 |
| 页面刷新 | 从学习记录重新打开，完整会话快照一致 |
| API 重启 | 实际停止并重启 API 后，cookie 身份、会话、反馈及尝试一致；原音重新下载的 SHA-256 与重启前一致 |
| 页面 | pageerrors=[]；录音停止后媒体轨道释放；375px 无水平溢出；桌面和移动端截图已查看 |
| 结束检查 | SQLite quick_check=ok，验收 API/UI 服务已停止 |

浏览器录音输入由 macOS Samantha 生成固定合成语音，再通过 Chrome 文件麦克风设备进入页面真实录音按钮、PCM 编码和 multipart 上传路径。输入为：

> Last weekend, I went hiking with two friends. I felt relaxed because the trail was quiet.

Whisper 实际返回（原始转写保留开头空格）：

```text
 Last weekend, I went hiking with two friends, I felt relaxed because the trail was quiet.
```

文字首轮故意使用 `Last Saturday I go hiking with my friends.`；模型反馈准确引用其中的 `I go hiking` 并建议过去式。文字重练使用 `On Saturday, I hiked with two friends and felt relaxed in the fresh air.`，录音重练使用上述合成语音。两次评价都引用新尝试中的实际文字，不借用先前回合的证据。

## 实际等待时间

以下为单次本地运行中，从页面点击到操作成功被查询到的时间，包含对应提供商工作和轮询开销；不能据此推出稳定的性能分位数。

| 页面动作 | 秒 |
| --- | ---: |
| 开始任务 | 0.691 |
| 获取提示 | 0.565 |
| 第一轮文字 | 4.624 |
| 第二轮录音 | 19.972 |
| 第三轮文字 | 13.839 |
| 结束并生成反馈 | 26.634 |
| 第一次文字重练 | 13.801 |
| 第二次录音重练 | 5.672 |
| 获取目标范读 | 1.567 |

LLM 调用耗时为 3.412–26.313 秒；本次最长的是反馈。现有适配器请求超时为 30 秒，这次成功，仍需在更多样本中核对等待体验与超时比例。前端通过 202 operation 轮询等待，没有把同步模型等待放进教学 POST。

## 实测发现与修复

首跑的第二轮已经获得正确的 ASR 文字，但模型把有效教练 JSON 包装在完整的 Markdown `json` 代码块中，原解析器直接 json.loads 导致 `CoachingResponseError`。操作失败且录音草稿保留，未生成虚假成功回合。首跑截图及返回已保存。

将同一条真实返回仅移除代码块包装后，原有业务校验通过，确认问题在外层格式。新增 `src/chatterpal/learning/response_json.py`，供教练、反馈及自由重练评价共用：只兼容一层完整的 JSON/无语言代码块，仍拒绝前后解释、多个对象、重复字段和非有限数字；字段约束、用户原话引用和无声学评分要求继续生效。没有降低校验或增加付费自动重试。

先运行新增回归得到 9 项失败，再修复；最终教练/反馈相关 276 项、教学 HTTP 流程 21 项，共 **297 项定向回归通过**，Black 与 flake8 F 检查通过。保存的真实原始返回无需修改即可解析。之后重新执行完整真实浏览器流程并通过。此前离线全量 1753 项 Python、133 项前端通过属于修复前的独立基线，本次未重跑全量套件。

## 配置与复现

本地 `.env` 已配置用户选择的兼容接口，权限为 0600，受 Git 忽略。凭据未写入脚本、文档或验收结果。`OPENAI_BASE_URL` 在当前适配器中应填写完整 chat-completions URL。

以下命令在仓库根目录运行；会调用配置的真实外部服务。需要 Python 环境、frontend/node_modules 中的 Playwright、Chrome、ffmpeg/ffprobe，以及 macOS `say`。脚本默认从 `.env` 加载当前提供商配置，使用新建临时目录与独立数据库，并在结束后停止自己启动的服务。

```bash
INT2_PYTHON=/Users/dd_y/Code/learn/ChatterPal/.venv/bin/python \
CHROME_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
  node scripts/verify_int2_live.cjs
```

可用 `INT2_API_PORT`、`INT2_UI_PORT` 改变端口，`INT2_OUTPUT_DIR` 指定新的证据目录。`INT2_INPUT_WAV` 可指定包含同一测试句的替代输入，默认语音生成无需实体麦克风。`verify_int2_browser.cjs` 仍用于受控提供商离线验收，不能与本脚本的真实服务结果混用。

脚本仅包装真实方法以记录耗时、返回值或错误类型，不更改其输入输出；日志过滤本地凭据。页面操作、API、SQLite、Whisper、远端 LLM 和在线 TTS 均实际执行。

## 本地证据与范围

成功证据目录：`/var/folders/dt/0y71cp9s52j217lxh6nh3r4m0000gn/T/chatterpal-int2-live-lQwYCS/`。

- `results.json`：passed=true、各阶段耗时、音频格式/哈希、反馈、重练评价及恢复断言。
- `operations.json`、`session.json`：实际操作结果和保存的完整会话。
- `providers.json`、`provider-calls-initial.jsonl`：真实提供商类型、模型、逐次调用耗时/结果；2 ASR / 6 LLM / 4 TTS。
- `api-initial.log`、`api-restart.log`、`vite.log`：真实服务与重启日志。
- `original-turn.wav`、`retry-2.wav`、`coach-reply.mp3`、`target-example.mp3`：已验证音频；`original-after-restart.wav` 的哈希与原音一致。
- `live-weekend-desktop.png`、`live-weekend-feedback-mobile.png`、`live-weekend-practice-mobile.png`：已查看的实际截图。

首跑的代码块兼容问题证据：`/var/folders/dt/0y71cp9s52j217lxh6nh3r4m0000gn/T/chatterpal-int2-live-8DeLQX/`。更早的百炼配置曾返回 HTTP 400 / `Arrearage`，对应证据为 `chatterpal-int2-live-key-20261025-l_y3bq64/`；该账户状态不代表当前兼容接口状态。

这些证据位于开发机临时目录，清理后不会保留；仓库保留可重复运行的真实验收脚本。本次实际启动应用并交互，查看了截图；格式兼容问题通过保存的真实返回形成回归循环。

本次证明当前配置下完整流程能够工作。使用固定合成语音，未验证真人麦克风、口音覆盖、真实学习效果或声学发音质量；反馈只评价文字。未验证长期复习服务，未执行新 Docker 构建、远端 CI 或部署。实现位于 `integrate/product-round-2` 集成分支。
