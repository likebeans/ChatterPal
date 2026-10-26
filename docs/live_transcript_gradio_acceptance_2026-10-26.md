# 真实录音核对与 Gradio 入口验收

> 本文保留早前本地工作树的真实提供商与 Gradio 验收记录。所列临时目录在本次收尾时已不可读取，不能视作本次独立复验。本次新增的即时纠正校验、当前源码指纹、完整回归与受控提供商页面结果统一记录在 [收尾验收](closeout_acceptance_2026-10-26.md)，不将早前真实提供商调用计为本次运行。

日期：2026-10-26。工作分支 `feat/live-transcript-gradio`，基线 `main@73ab2d8e10f8cd8c198a86940675e75ff300ff81`。本轮按顺序先完成真实提供商录音核对，再接通独立 Gradio 的评分与纠错页面。本记录对应本地未提交工作树，不代表新代码已推送、合入主线或部署。

## 真实提供商录音核对

驱动：`scripts/verify_transcript_confirmation_live.cjs`。实际链路为 Chrome 录音 UI → React/Vite → APIServer → SQLite → 配置中的真实 ASR/LLM/TTS。未拦截教学 API，未替换提供商输出，也未直接写入业务结果。验收时 79 个业务 Python 文件的 SHA256 均与基线相同。

| 提供商 | 配置 | 最终完整运行的调用结果 |
| --- | --- | --- |
| ASR | Whisper `base` | 4 次成功、0 次失败 |
| LLM | OpenAI 兼容服务 / `Qwen3.8-27B` | 6 次成功、0 次失败 |
| TTS | 在线 Edge / `en-US-JennyNeural` | 3 次成功、0 次失败 |

计数是适配器方法调用次数，不是网络底层重试计数。4 次 ASR 分别为任务回合、已识别后明确放弃的草稿、关联重练、到期复习。最终完整运行没有触发失败恢复或额外提交重试；结束反馈为 `available`。

真实配置只在子进程从已有私有文件读取，没有复制 `.env`，没有在结果中输出密钥或完整配置。显式使用 `OPENAI_TIMEOUT=60`。音频输入由 macOS `say` 合成，再通过 Chrome 的假麦克风进入应用录音控件；这不是实体手机、物理麦克风或真人发音验收。

已完成的检查：

- 任务回合、关联重练、到期复习均先识别，再编辑并明确确认；机器原文与确认文字分别保存，正式反馈依据确认文字。
- 每次识别只调用一次 ASR，不调用 LLM/TTS，不改正式记录、revision 或复习排程；确认提交不再识别，不重传原音，仅发送确认字段、request ID 和原上下文。
- 三个入口均复用相同的 `audio_asset_id`、SQLite BLOB 行与 SHA256；下载后使用 ffprobe/ffmpeg 解码，并在浏览器实际播放。
- 就绪草稿在浏览器刷新和 API 进程停止/重启后恢复；恢复不重跑 ASR。内部导航保留编辑；完整刷新恢复机器原文，之后可重新编辑确认。
- 未识别放弃和已就绪放弃都不形成正式回答；已就绪放弃后草稿与私有原音均不可读取。
- 同 request ID 恢复同 operation，换 request ID 重复消费返回 409，未额外调用提供商；其他身份读取草稿、原音均返回 404。
- 次日复习使用统一注入时钟从 `2026-10-26T08:00:00Z` 推至 `2026-10-27T08:00:00Z`；测试身份通过真实偏好 UI 开启原音保留 7 天。复习成功后排程至 `2026-10-30T08:00:00Z`，原任务持久历史未被改写。教练生成音频按其 1 小时期限过期，未将正常过期判成历史损坏。
- 完成后重新打开任务、重练和复习历史，确认文字、机器原文、关联关系和原音仍可查看；375px 页面与截图无横向溢出，浏览器 `pageerrors=[]`。

| 最终完整运行中的操作 | 浏览器测得耗时 |
| --- | ---: |
| 任务录音识别 | 2.089 秒 |
| 任务录音确认与教练回复 | 7.029 秒 |
| 关联重练识别 / 确认评价 | 0.434 / 6.978 秒 |
| 到期复习识别 / 确认评价 | 0.434 / 8.230 秒 |
| 结束并生成反馈 | 36.855 秒 |

这些是单次合成语音验收的操作耗时，不是生产 P95、真人发音质量或长期提供商可用率。

完整通过证据目录：`/var/folders/dt/0y71cp9s52j217lxh6nh3r4m0000gn/T/chatterpal-transcript-live-akT9gL`。包含 `results.json`、`operations.json`、`provider-calls.jsonl`、收据、SQLite、下载音频、API 日志和六张桌面/手机截图。临时进程已关闭，18260/13260 端口已释放。浏览器恢复状态文件只保存在该本机临时目录，权限为 0600，不入库或分享。

在最终完整运行之前，有两次驱动失败，证据分别位于 `chatterpal-transcript-live-SnJXel` 和 `/tmp/chatterpal-transcript-live.SwVqcH`：两次均各完成 1 次真实 Whisper ASR，LLM/TTS 调用均为 0，随后在读取 Chrome 不提供的 multipart `postDataBuffer()` 时发生 TypeError。它们不计为完整通过，也不是提供商失败。驱动修复为只读观察原 FormData 元数据，随后原样发送，不修改请求或提供商返回。

## Gradio 主入口修复

此前 `ChatterPalApp.create_interface()` 在主页面注册的评分、纠错回调只返回“演示输出”，即使提供商配置可用也不会调用真实服务。已有 `ScoreTab`、`CorrectTab` 虽单独通过测试，却没有被主页面使用。

本轮移除这两段占位回调，实际主页面直接复用已有组件及 `EvaluationService` / `CorrectionService`，保留“对话练习 / 发音评分 / 发音纠错”三个中文标签页。两个分析入口均接受录音和上传音频；评分入口要求参考文本，纠错入口允许目标文本为空。没有新增评分算法、能力等级或音素准确率，文本对齐、分析来源与不可评估原因沿用服务契约。

`tests/test_gradio_analysis_entry.py` 从实际 `create_app()` 构建并启动 HTTP 服务，验证主页面事件注册、音频上传与 numpy 输入、一次识别、可选目标、五项报告字段、输入缺失不调用提供商、识别失败后恢复，以及 LLM 失败时保留识别和文字对齐结果。修复前注册检查失败。

独立复核发现上传双声道音频后，韵律转换将交错声道直接写成单声道 WAV。新增 HTTP 回归通过实际纠错入口上传 1 秒、16 kHz 的双声道录音，修复前读到 32000 个单声道采样（2 秒），测试失败。修复后按时间帧混合声道，原音仍为 1 秒，波形等于左右声道均值，真实 Parselmouth 基频约为 180 Hz。没有修改能力评分方法。

实际浏览器驱动为 `scripts/verify_gradio_analysis_browser.cjs`；默认使用受控提供商，传入 `GRADIO_ENV_FILE` 时使用真实配置。它通过主页面选择标签页、上传音频并点击按钮，检查报告和手机布局，不通过内部函数调用代替 UI 操作。

- 真实提供商运行通过，证据目录 `chatterpal-gradio-analysis-SGbXHV`：真实 Whisper ASR 2 次成功，真实 LLM 1 次成功，分别对应评分、纠错上传与文字建议；评分 ASR 1.569 秒、LLM 20.038 秒，纠错 ASR 0.351 秒。两个入口均返回真实转写和明确的不可评估边界；纠错空参考文本可提交。此页不调用 TTS。
- 双声道修复后的最终受控运行通过，证据目录 `chatterpal-gradio-analysis-s8SQW0`：两个上传入口加一次 Chrome 假麦克风录音，共 3 次 ASR、1 次 LLM；输入缺失不调用提供商。录音停止后等待上传返回 200 及播放器就绪，再用键盘 Enter 提交纠错，报告返回成功。
- 375×812 与 812×375 无横向溢出，`pageerrors=[]`。DOM 中只有一个 ChatterPal 页头；另一个一级标题为标签页标题“发音练习分析”。早期全页截图出现重复页头的拼接异常，最终保存正常上下屏和按页面高度一次渲染的长图，并已人工查看；没有为截图修改产品布局。
- 两次运行均记录到 Gradio `/gradio_api/queue/data` 的 `net::ERR_ABORTED` 流关闭事件；报告和操作均完成，不能将其写成“没有失败网络请求”。

以上 Gradio 证据目录均位于 `/var/folders/dt/0y71cp9s52j217lxh6nh3r4m0000gn/T/`。早期受控浏览器运行曾因标签/隐藏上传控件定位失败；扩展录音检查时又发现驱动未等待上传完成、误数标签页标题及错误依赖播放器 URL 等问题。这些运行均保留 `passed=false`，不计为通过。最终驱动以实际上传响应、播放按钮及服务调用数判定成功。

## 回归结果

- 音频分析、实际 Gradio 主入口、聊天会话与启动相关回归：120 项通过。
- 最终完整后端：1986 项通过，54 条警告，覆盖率 87.38%，80% 门槛通过；启用 `RuntimeWarning` 与未处理线程异常报错。警告主要来自 Gradio 的事件循环、旧聊天格式、默认音色与 WebSocket 依赖。
- 全量测试中的聊天清空检查曾等待客户端 `IN_QUEUE` 通知超时，单独复跑通过。现改为观察同一会话、同一并发队列中的真实清空事件，并保留慢回复未结束前不能完成清空、结束后会话被删、新会话不含旧上下文等断言。未修改聊天产品逻辑。
- 首次全量命令从旧工作目录启动，1985 项测试通过但统计了错误源码，覆盖率 1.36% 因而失败；该运行不计为覆盖率通过。后续正确目录运行出现上述队列测试失败；修正测试等待条件及双声道处理后，最终全量通过。
- 前端本轮没有代码变化，未重复前端套件；基线 `main@73ab2d8` 的远程 CI 已通过 186 项前端测试与 build/lint。
- 两个新增浏览器脚本通过 `node --check`，相关 Python 文件通过 Flake8 F 检查，新测试通过 Black，补丁通过 `git diff --check`。

最终全量日志为 `/tmp/chatterpal-live-gradio-verified-pytest.log`，覆盖率 XML 为 `/tmp/chatterpal-live-gradio-verified-coverage.xml`。这些是本地结果，本轮尚未提交、推送或触发新远程 CI。

## 复现

在当前工作树根目录使用本地已安装环境；如果复用其他工作树的 Python，必须令 `PYTHONPATH` 指向当前树的 `src`。浏览器脚本需要 `frontend/node_modules`、Chrome、ffmpeg 和 macOS `say`，凭据文件须留在原私有位置。

```bash
TRANSCRIPT_PYTHON=/path/to/python \
TRANSCRIPT_ENV_FILE=/path/to/private.env \
CHROME_EXECUTABLE_PATH=/path/to/chrome \
OPENAI_TIMEOUT=60 node scripts/verify_transcript_confirmation_live.cjs

GRADIO_PYTHON=/path/to/python \
CHROME_EXECUTABLE_PATH=/path/to/chrome \
node scripts/verify_gradio_analysis_browser.cjs

# 真实 Gradio 提供商验收，显式传入原私有配置：
GRADIO_PYTHON=/path/to/python \
GRADIO_ENV_FILE=/path/to/private.env \
CHROME_EXECUTABLE_PATH=/path/to/chrome \
OPENAI_TIMEOUT=60 node scripts/verify_gradio_analysis_browser.cjs
```

临时证据只供本机核查，不是持久归档。评分标注校准、真人学习效果、多平台覆盖和生产容量仍属独立验证范围。
