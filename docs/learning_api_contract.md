# INT2 学习 API 契约

状态：2026-10-26，INT2、E2 复习和事实型进度已合入 main `d98b13b`；历史验收见[剩余优化验收记录](remaining_optimizations_acceptance_2026-10-26.md)。当前工作树新增录音转写核对，[本次验收](transcript_confirmation_acceptance_2026-10-26.md)记录本地全量回归、Linux 打包检查及实际浏览器结果；下面记录实现契约。原 INT2 复用 A2 `c520010`、B2 `98db184` 和 D2 前端一致性快照，交付“描述周末”的开练、对话、反馈、重练与刷新恢复。复用现有提供商，不新增评分模型。原验收命令、证据与范围见 [INT2 验收记录](int2_weekend_acceptance.md)。

## 身份与响应

所有学习接口位于 `/api/learning`。先 `POST /bootstrap`，后续 owner 来自 HttpOnly cookie，不接受客户端 owner。复用 A2 的 Origin、归属、导出删除和 no-store 响应。资源不存在或不属于当前 owner 返回 404。

HTTP 会话 `revision` 为 SQLite record revision，从 1 开始；B2 内部领域版本保存在 `domain.revision`，不作为客户端 CAS。会话视图保存 `session_id,task_id,task,status,revision,turns,feedback,attempts,correction_mode,created_at,updated_at`，兼容现有 D2。`turns` 为扁平 `{turn_id,role,text,audio?}`，开场白为 assistant turn。可选 `coaching` 按 `source_turn_id` 关联用户回合，保存 `status=available/unavailable/deferred`、reason 和最多一条引用原话的文字建议；前端在该用户回合旁显示，历史无此字段时仍可恢复。持久快照额外保存 `domain`，用于恢复 B2 不可变状态，不从前端接受该字段。

所有教学写请求携带 8–128 字符 request_id。耗时请求返回 HTTP 202 和 A2 operation：`operation_id,request_id,status,session_id,result_type,result_id,result,error`，状态为 pending/succeeded/failed/interrupted。轮询与响应丢失恢复复用 GET `/operations/{id}`、`/requests/{request_id}`。结果 `result_type=session` 时 result 为完整会话；hint/result、attempt/result、audio/result 使用对应类型，客户端重新读取会话。相同 owner/action/request_id 与相同有效内容只执行一次；不同内容 409。失败不自动重放付费调用。

开始、回合、提示、结束、反馈、尝试、范读均由有界 runtime 预留操作并执行；provider 不在 SQL 事务中调用。一个会话同一时间至多一个教学写操作；有 pending 回合时不能结束。完成时原子校验 owner、record revision、操作租约和源记录存在，并提交快照/子记录/音频与成功结果。删除后完成不能恢复记录。任务超时或停机中断不会允许旧工作者发布结果。

## 路由

- GET `/tasks`、`/tasks/{task_id}`：B2 版本化人工任务。先支持 weekend；相同实现可服务另两项内容。
- POST `/sessions`：`{request_id,task_id,content_version,correction_mode}`。生成稳定业务 session_id 和版本 1 会话，开场白进入 turns。
- POST `/sessions/{id}/turns`：JSON `{request_id,expected_revision,text}` 或 multipart request_id、expected_revision、audio。也支持下文的 JSON 转写确认输入，三种输入互斥，上传字节受上限限制。直接音频经过 ASR 一次，转写确认复用识别结果；教练和存储使用最终确认文字。TTS 失败保留文字。
- POST `/sessions/{id}/hints`：`{request_id,expected_revision,kind}`，kind 为 sentence_starter/simpler_question；依次选择任务步骤，返回 hint，并保存 B2 hint 事件。
- POST `/sessions/{id}/finish`：`{request_id,expected_revision}`。拒绝无用户回合和 pending；结束并请求有原句依据的结构化反馈。反馈不可用仍保存已结束任务与 unavailable 说明；不假称完成目标。
- POST `/sessions/{id}/feedback`：`{request_id,expected_revision}`。仅对 completed 会话生成/重试反馈。已有有效反馈返回现有快照，不覆盖源版本。
- POST `/attempts`：JSON 或 multipart，字段 `request_id,session_id,source_revision,source_turn_id,focus_id?,parent_attempt_id?,mode,target_text?,text|audio`。mode 为 read_aloud/free_response。read_aloud 必须有目标句，且匹配该源句或已验证建议；free_response 不强制目标句。source_revision 必须等于当前 HTTP revision；源句和 parent 必须属于当前 owner/session/重点。支持下文转写确认输入，复用原音和识别结果；直接音频执行一次 ASR。保留各次尝试，原子保存 attempt 子记录与会话。只有已验证的文字依据能产生评价；不输出固定分数。
- GET `/attempts/{id}`：当前 owner 的独立尝试。
- POST `/attempts/{id}/example-audio`：`{request_id}`；目标由服务端关联的源句/建议获取，复用 TTS，结果为 LearningAudio。范读单独关联到源会话记录，不增加会话 revision；同 request_id 重放相同音频，换 attempt 必须换 request_id。提供商失败返回明确失败，保留文字练习。
- GET `/progress`：会话、尝试、重练、复习和独立延迟迁移的事实计数，附具体尝试依据；不返回能力分或掌握率。提前练习、提示后作答、原句复述、重复问题及不可评估分别统计。
- POST `/reviews`：`{request_id,session_id,source_revision,source_turn_id,focus_id?,attempt_id?}`；明确保存一个来源重点或尝试，首次排程为服务端 UTC 次日。来源记录、版本、turn/focus/attempt 必须互相匹配。保存目标指纹及来源引用，后续来源不匹配时返回 409。
- GET `/reviews/due`：`{items,next_cursor:null}`，返回当前 owner 的到期复习；无到期项时 HTTP 200、items 为空。
- GET `/reviews/{review_item_id}`：公开复习视图，包含已保存的 `attempts[]`（历史问题、转写、评价、音频和排程证据），供刷新后从进度链接恢复。未到期的新问题不提前返回 prompt/question_id；不返回目标答案、内部任务快照或目标指纹。用完人工新问题后允许重复练习，`independent_variant_available=false`，说明不再增加独立迁移记录。
- POST `/reviews/{id}/reveal`：`{request_id,expected_revision}`；原子记录本轮已看答案后返回参考表达。POST `/reviews/{id}/hints` 额外支持 `kind=sentence_starter|simpler_question`，服务端持久记录提示。客户端不能通过 `used_hint=false` 等字段声明独立完成。
- POST `/reviews/{id}/attempts`：JSON 或 multipart `{request_id,expected_revision,text|audio}`，也支持下文转写确认输入；新音频识别一次，确认输入复用识别结果。评价必须引用本次最终文字，复习尝试与排程更新原子提交。只有到期、无本轮提示/看答案、非原句复述、未用过的人工问题且评价 available/met 时才按约 3 天、7 天排程。已有有效评价会消耗该人工问题；技术失败保留排程和问题。提示、提前作答、重复问题或无依据评价不推进排程。
- POST `/reviews/{id}/defer`：`{request_id,expected_revision,days}`，days 为 1–30，默认 1；这是用户明确延期，单独留痕，不清除已经使用的提示。

历史、profile、audio、导出删除复用 A2。正式服务器关闭通用 records 写接口，教学写只能通过业务边界；读取仍用于历史恢复。普通 records/session/operation 响应会移除为复习保留的 `transfer_questions`；review/review_attempt 使用公开投影，不泄漏目标指纹、源文和内部快照。完整 owner 导出保留原始记录以便迁移；源学习历史本来可读，因此复习状态仅说明“服务端记录的提示/看答案”，不声称能识别用户在其他页面查看资料。独立 A2 挂载默认保持兼容。

## 录音转写核对

任务回合、重练和复习使用相同的两阶段录音流程。`POST /transcriptions` 接收 multipart `request_id`、JSON 字符串 `context` 和单个 `audio` 文件，返回 HTTP 202 operation，action 为 `transcribe`，成功时 `result_type=transcription`。这一步只识别一次，不调用教练、评价或 TTS，也不改变会话或复习 revision。

`context` 必须与最终提交完全对应：

| kind | 字段 |
| --- | --- |
| turn | `session_id,expected_revision` |
| attempt | `session_id,source_revision,source_turn_id,focus_id,parent_attempt_id,mode,target_text`；可空的关联或目标字段显式传 `null` |
| review | `review_item_id,expected_revision` |

成功结果及 `GET /transcriptions/{transcription_id}` 直接返回 `{transcription_id,status,context,machine_transcript,audio,received_at,expires_at}`。status 为 ready/consumed/expired；audio 为既有私有音频 metadata。识别前和发布时均检查来源、owner 和版本；重练还验证原句、重点、parent 和练习方式。未完成的识别只有 request/operation receipt，没有可恢复的 transcription ID。未知结果查询原请求，不自动重新识别。

用户核对或修改后，将 JSON `transcription_id` 和 `confirmed_text` 提交到原有 `/sessions/{id}/turns`、`/attempts` 或 `/reviews/{id}/attempts`，同时携带原有 request_id 和上下文字段。例如任务回合：

```json
{"request_id":"confirm-example-001","expected_revision":1,"transcription_id":"draft-uuid","confirmed_text":"I went hiking with friends last Saturday."}
```

最终提交只允许 `text`、multipart `audio`、`transcription_id + confirmed_text` 三种输入之一；确认字段必须成对，不接受客户端覆盖机器转写。原有文字和直接音频接口保持兼容，当前录音 UI 使用核对流程。教练与评价依据确认文字；保存的回合或 attempt 分别记录 `machine_transcript,confirmed_text,transcription_id,confirmed_at`，既有 `text` / `transcript` 为确认文字。未修改时仍保存两份来源信息。反馈中的首次尝试继承源回合的完整文字和录音来源，不把重点片段当作整段录音内容。

最终确认复用同一 audio_asset_id 和音频字节，不再调用 ASR。准入和最终事务均校验草稿仍 ready、来源和上下文一致、音频存在且未过期；成功后原子消费草稿、转移音频关联、保存正式记录并完成操作。相同最终 request_id 可恢复已有结果；新请求重复消费返回 409。LLM 或发布失败不消费草稿，仍可在有效期内明确发起新的最终请求。来源版本变化返回 409，不会自动绑定新版本或重新识别。

`DELETE /transcriptions/requests/{request_id}` 可撤销识别或丢弃已就绪草稿；`DELETE /transcriptions/{transcription_id}` 丢弃已就绪草稿，成功均返回 204。删除会撤销关联在途确认，迟到结果不能发布；已消费返回 409，已物理删除后再次 DELETE 返回 404；通用 `/records/{id}` 删除转写草稿返回 403。准备中的录音和草稿临时保存 1 小时，过期确认返回 410；ready 草稿及原音可在 API 重启后恢复。确认时保留关闭，则沿用临时音频期限；保留开启则按配置延长原音期限。关闭保留同时清除现有音频并中断 pending 识别，防止迟到识别重新保存原音。

复习以识别请求初次接收的 `received_at` 判断提前练习；在识别或核对期间跨过 due_at 不会转为独立延迟迁移。

## 反馈与提供商端口

`LearningProvider.recognize(audio: bytes) -> str`、`reply(messages: list[dict[str,str]]) -> str`、`synthesize(text: str) -> bytes`，均同步，由 runtime 工作线程执行。reply 复用已配置 LLM，支持 B2 教练 JSON 与反馈 JSON；不依赖旧 Gemini 专用路径。教练、反馈和重练评价共用严格 JSON 解析，兼容一层完整的 Markdown JSON/无语言代码块包装；包装之外的解释、多个对象、重复字段、非有限数字仍拒绝，字段和原话依据校验不变。普通教练与结束反馈提示不包含为复习预留的 `transfer_questions`。TTS 按真实字节识别容器/MIME。

`LearningFeedback`：feedback_id、source_revision、status=available/partial/unavailable、reason、strength、最多两个 focuses、criteria。focus 必须带 source_turn_id、原句中的 exact substring original、不同的 suggested、explanation、category 和 source=text。criteria 中 result=met/not_met/unverified；met 必须有当前 user turn 的原话 evidence，无法验证引用则降级。声学/音素准确率不在本轮评估范围。

结束时为每项有效重点保存首次 free_response attempt，transcript 为该源用户回合的完整原文，以保持与首次音频一致；focus.original 仍是该原文的精确片段。后续重练通过 parent_attempt_id 形成链，parent 必须来自同 session/source_turn_id/focus_id/mode；第一次跟读的 parent 为 null。评价字段为 status、result、reason、source；无效模型输出或服务不可用返回 unavailable/unverified，已有成功回合及原文不丢失。

## 音频与配置

默认不长期保存原音；临时回放资产以 SQLite BLOB 私有保存 1 小时，API 重启后在到期前仍可回放，到期时访问立即失效并显示 expired。过期字节在学习服务首次初始化、上传、bootstrap、关闭时清理；本轮没有后台定时清理器。retain_audio=true 时原音按配置保存，默认 7 天。关闭保留会清除现有音频字节。范读/教练音频同样按 owner 保护，使用 1 小时 TTL。音频与记录/操作在同一事务提交，不在失败后留下孤立资产；导出不含音频字节。通用 A2 音频行为保持兼容。

新增配置：API_HOST/API_PORT（默认回环地址/8010）、LEARNING_DB_PATH、LEARNING_COOKIE_SECURE、LEARNING_MAX_AUDIO_BYTES、LEARNING_AUDIO_RETENTION_DAYS、LEARNING_MAX_WORKERS、LEARNING_MAX_PENDING、LEARNING_OPERATION_TIMEOUT、LEARNING_SHUTDOWN_TIMEOUT、LEARNING_ALLOWED_ORIGINS。Vite 使用 CHATTERPAL_API_TARGET 指定代理后端。测试为学习数据库指定独立临时目录；单实例 SQLite 是本轮部署范围。

## 验收

真实 APIServer + SQLite + 受控 provider 先验证：三轮 weekend → finish/有依据反馈 → 两次关联尝试 → 刷新/服务重启恢复；一次 ASR；重复 request_id；版本冲突；慢提供商期间 health；pending-finish 竞争；owner 隔离；删除与在途操作；TTS/LLM 失败与异常 JSON。浏览器驱动通过真实 socket，不拦截教学 API。后续以 Qwen3.8-27B、Whisper base 与在线 Edge TTS 完成[真实提供商验收](int2_live_provider_acceptance_2026-10-25.md)：页面录音上传、三轮、反馈、两次关联重练、播放及刷新/API 重启恢复。实测使用合成输入，真人发音学习效果另行验证。
