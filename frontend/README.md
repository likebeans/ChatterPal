# ChatterPal 前端开发与学习验收

学习工作区使用正式学习接口，支持任务对话、原句反馈、关联重练、历史恢复、明确保存的复习与事实型进度。INT2「描述周末」已合入 main；当前优化工作树继续接入复习和不输出无依据分数的分析结果。共同边界见 [学习 API 契约](../docs/learning_api_contract.md)。

## 开发与检查

```bash
npm ci --no-audit --no-fund
npm test
npm run lint
npm run build
CHATTERPAL_API_TARGET=http://127.0.0.1:8010 \
  npm run dev -- --host 127.0.0.1 --port 13204 --strictPort
```

Vite 默认将 `/api` 代理到 `http://127.0.0.1:8010`，可用 `CHATTERPAL_API_TARGET` 指向独立后端。学习服务需允许页面对应的开发 Origin；也可由后端同源提供 `dist/`。

## 模块与接口

| 文件 | 职责 |
| --- | --- |
| `src/learning/types.ts` | INT2 会话、反馈、尝试、音频、操作和草稿类型 |
| `src/learning/client.ts` | HTTP 映射、共享身份握手、错误状态及 `record.data` 解析 |
| `src/learning/validation.ts` | 嵌套响应校验，接受正式接口的可空音频字段和附加 metadata |
| `src/learning/useLearningFlow.ts` | 流程状态、CAS revision、pending 恢复及迟到响应保护 |
| `src/learning/usePracticeDrafts.ts` | 按身份、会话、原句、重点和方式隔离的内存草稿 |
| `src/learning/PracticeInput.tsx` | 受控文字/录音输入，WAV 编码、试听及显式提交 |
| `src/learning/LearningWorkspace.tsx` | 任务、对话、原句与重练关系、历史、进度及设置 |

学习接口位于 `/api/learning`。先调用 `POST /bootstrap`，后续身份由 HttpOnly cookie 确认。历史详情从 `GET /records?kind=session&session_id=...` 的 `record.data` 读取，不把 record_id 当作 session_id。

写操作统一附带 UUID `request_id`。HTTP revision 从 1 开始，和内部领域 revision 分开；提交下一次操作前先取得当前 HTTP revision。`result_type=session` 是完整快照；提示返回 `{hint:string}`，尝试返回 attempt，范读返回音频 metadata。这些结果只要关联 session_id，就会重新读取会话。未知的 result_type 也保留结果并刷新会话。

`POST /sessions/{id}/finish` 保存已结束任务和有原句依据的反馈；每个重点关联的原话由服务端保存为首次尝试。自由表达和跟读分别关联同源句、同重点、同方式的 parent attempt。界面展示任务原话、重点片段、首次尝试和每次重练的接续关系；原音对应完整原话。反馈或评价不可用时展示原因，仍可以从用户原话进入重练，并可以重新获取不可用的反馈。

复习从练后重点或已保存的尝试明确保存，首次安排次日。`GET /reviews/due` 返回当前身份的到期项，无到期项时为 HTTP 200 和空列表。到期前隐藏新问题和问题编号；提示、看答案和延期写入服务端记录。提交使用复习自己的 revision，冲突时刷新该复习并保留草稿。只有到期后、无本轮提示、非原句复述、未使用过的问题且有本次文字达成证据时，才按 3 天／7 天延长排程。用完人工问题后仍可重复练习，界面说明不再增加独立迁移记录。

进度区展示已完成任务、保存尝试、同题重练、到期已评估回答及独立迁移的事实计数，并区分提前练习、有提示、原句复述、重复问题和不可评估的记录。不生成能力总分或掌握率。口语分析同样允许分数为空，展示维度来源、测量范围和不可用原因；文字匹配指标不代表声学准确率。

## 草稿与恢复

未发送的文字和已准备录音保存在当前工作区内存，按学习身份、会话、原句/重点及练习方式隔离。提示、新 revision、记录刷新、内部历史切页和顶层切页不会清除已准备的草稿。顶层切页和内部切页仍停止麦克风、取消未完成录音处理并停止播放。只有对应 turn/attempt 成功确认且草稿版本一致时才清除该份草稿；换身份或确认全量删除后清除全部旧草稿。整页刷新不会持久化未发送的录音正文。

localStorage 仅保存恢复操作所需的请求、操作、动作、会话、身份和草稿 receipt，不保存身份令牌、原文、转写或音频。浏览器存储受限时保留内存恢复能力。响应不确定时查询原操作或原 request_id，明确查无请求且当前页保留正文时才使用原编号重试，不自动新建付费操作。

401 暂停无效轮询，提供直接重新握手入口；新身份不会继承旧 pending 或旧正文。409 和异步 `revision_conflict` 提供刷新本次会话的恢复操作，保留未发送内容。404/410、其它终止错误停止无效查询并说明核对历史的下一步。删除使所有在途响应失效，迟到结果不会恢复已删除记录。

默认文字记录保留，原音仅供 1 小时临时回放；启用原音保留后保存 7 天。音频到期时显示说明，文字和重练记录继续可读。PCM16、单声道、16 kHz WAV 编码及可取消 Worker 复用既有实现。

## 自动验收

`npm test` 覆盖既有自由对话与分析、学习流程、草稿恢复、即时文字建议、并发迟到响应、复习排程和服务端 HTTP 形状边界。`npm run lint` 和 `npm run build` 检查所有前端代码与类型。2026-10-26 本工作树 155 项测试、build 和 lint 全部通过，范围见[剩余优化验收](../docs/remaining_optimizations_acceptance_2026-10-26.md)；INT2 的 133 项是历史结果。

已有学习浏览器脚本使用离线 INT2 契约 fixture，只在测试驱动中拦截 API，不属于真实教学服务或线上记录。生产页面不加载 fixture。

```bash
CHROME_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
  BROWSER_OUTPUT_DIR=/tmp/chatterpal-int2-contract-browser \
  npm run test:learning:browser -- http://127.0.0.1:13204
npm run test:browser -- http://127.0.0.1:13204
```

契约 fixture 脚本覆盖提示后草稿保留、pending 恢复、关联尝试及录音生命周期；其中历史复习未接入 fixture 只用于服务降级兼容，不表示正式服务仍未接入。脚本检查 375px 与横屏布局，启用 reduced motion，输出截图和 `results.json`。旧浏览器脚本继续覆盖自由对话、口语纠正和音频竞态。

真实 APIServer + SQLite + 受控 provider 的 socket 验收使用 `node ../scripts/verify_int2_browser.cjs`（需要在仓库安装 Python 环境，或通过 `INT2_PYTHON` 指定解释器）。驱动自行启动隔离服务，完成三轮对话、反馈、两次关联重练、刷新和实际 API 重启；默认端口为 18200/13200。

复习的实际页面验收为 `node ../scripts/verify_learning_review_browser.cjs`，默认端口 18220/13220。它使用受控提供商和注入的 UTC 时钟，覆盖到期前隐藏、3／7 天间隔、提示与看答案留痕、延期、冲突保稿、API 重启、录音上传回放、事实型进度、身份隔离，以及餐厅和酒店任务。两个驱动均不拦截教学 API。实际云端延迟、真人发音表现及跨日学习效果需要独立验证。

调用本地 `.env` 中真实提供商的驱动为 `node ../scripts/verify_int2_live.cjs`，默认端口 18210/13210，使用固定合成语音进入真实录音 UI；会调用外部服务。2026-10-26 当前工作树以 Qwen3.8-27B、Whisper base 与 Edge TTS 完成三轮、反馈、两次关联重练及刷新/重启恢复，12 次提供商调用全部成功，没有触发重试。结束反馈约 40 秒；此前观察到的服务故障和格式错误一并保留在[剩余优化验收](../docs/remaining_optimizations_acceptance_2026-10-26.md)。2026-10-25 的[历史验收](../docs/int2_live_provider_acceptance_2026-10-25.md)继续保留。
