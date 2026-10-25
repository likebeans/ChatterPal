# ChatterPal 前端开发与 INT2 验收

INT2 在 `ChatterPal-product-integration` 工作树接入真实学习接口，优先完成“描述周末”的开练、对话、提示、结束反馈、原句重练与历史恢复。原有自由对话、口语纠正和音频生命周期继续保留。共同边界见 `../docs/learning_api_contract.md`。

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

`GET /reviews/due` 当前返回 503 `review_unavailable`，界面明确显示尚未接入，不提供保存次日复习的承诺。进度只显示服务端保存的事实计数和对应记录，未接入的复习/迁移计数为 0，不生成能力总分。

## 草稿与恢复

未发送的文字和已准备录音保存在当前工作区内存，按学习身份、会话、原句/重点及练习方式隔离。提示、新 revision、记录刷新、内部历史切页和顶层切页不会清除已准备的草稿。顶层切页和内部切页仍停止麦克风、取消未完成录音处理并停止播放。只有对应 turn/attempt 成功确认且草稿版本一致时才清除该份草稿；换身份或确认全量删除后清除全部旧草稿。整页刷新不会持久化未发送的录音正文。

localStorage 仅保存恢复操作所需的请求、操作、动作、会话、身份和草稿 receipt，不保存身份令牌、原文、转写或音频。浏览器存储受限时保留内存恢复能力。响应不确定时查询原操作或原 request_id，明确查无请求且当前页保留正文时才使用原编号重试，不自动新建付费操作。

401 暂停无效轮询，提供直接重新握手入口；新身份不会继承旧 pending 或旧正文。409 和异步 `revision_conflict` 提供刷新本次会话的恢复操作，保留未发送内容。404/410、其它终止错误停止无效查询并说明核对历史的下一步。删除使所有在途响应失效，迟到结果不会恢复已删除记录。

默认文字记录保留，原音仅供 1 小时临时回放；启用原音保留后保存 7 天。音频到期时显示说明，文字和重练记录继续可读。PCM16、单声道、16 kHz WAV 编码及可取消 Worker 复用既有实现。

## 自动验收

最终 `npm test` 共 133 项通过，包含原有 59 项回归，以及学习流程、草稿/恢复、即时文字建议、11 项并发对抗回归和真实 HTTP 形状边界测试。`npm run lint` 和 `npm run build` 检查所有前端代码与类型。验收结果及完整复现命令见 [INT2 验收记录](../docs/int2_weekend_acceptance.md)。

已有学习浏览器脚本使用离线 INT2 契约 fixture，只在测试驱动中拦截 API，不属于真实教学服务或线上记录。生产页面不加载 fixture。

```bash
CHROME_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
  BROWSER_OUTPUT_DIR=/tmp/chatterpal-int2-contract-browser \
  npm run test:learning:browser -- http://127.0.0.1:13204
npm run test:browser -- http://127.0.0.1:13204
```

脚本覆盖提示后草稿保留、pending 刷新恢复不重复写入、结束自动保存首次尝试、两次关联重练、复习未接入说明、真实测试麦克风 WAV 上传和 0.75× 播放、录音跨内部页面恢复、内部和顶层切页释放资源。检查 375px 与横屏无水平溢出，启用 reduced motion，输出截图和 `results.json`。旧浏览器脚本继续覆盖自由对话、口语纠正和音频竞态。

真实 APIServer + SQLite + 受控 provider 的 socket 验收使用 `node ../scripts/verify_int2_browser.cjs`（需要在仓库安装 Python 环境，或通过 INT2_PYTHON 指定解释器）。驱动自行启动隔离服务，完成三轮对话、反馈、两次关联重练、刷新和实际 API 重启；默认端口为 18200/13200。真实云端质量、真人发音学习效果与长期复习服务不属于这些离线测试的结论。

调用本地 `.env` 中真实提供商的驱动为 `node ../scripts/verify_int2_live.cjs`，默认端口 18210/13210，使用固定合成语音进入真实录音 UI；会调用外部服务。2026-10-25 已以 Qwen3.8-27B、Whisper base 与 Edge TTS 通过完整验收，结果、耗时、依赖及运行命令见[真实提供商验收记录](../docs/int2_live_provider_acceptance_2026-10-25.md)。
