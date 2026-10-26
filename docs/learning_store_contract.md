# A2 学习数据存储契约

状态：2026-10-26，A2 存储已接入 INT2、复习和进度，main 基线为 `d98b13b`；当前工作树增加录音转写核对。本文保留独立存储接口和 A2 历史验收依据；正式教学流程、DTO、runtime 和音频语义以 [INT2 学习 API 契约](learning_api_contract.md) 为准。会话、反馈、关联重练、复习排程和操作恢复均使用本仓储。

存储实现位于 `learning/storage/sqlite.py`，身份与记录 router 位于 `web/routes/learning_records.py`，教学 router 位于 `web/routes/learning_tasks.py`。正式 APIServer 使用 Settings 配置并关闭通用 records 创建/更新，防止绕过教学校验；下面的独立挂载示例保留原 A2 兼容行为。原 A2 范围与验收结果记录在本文末尾。

## HTTP 边界

前缀 `/api/learning`。浏览器先调用 `POST /bootstrap`，再读写私有数据，使用 `credentials: include`。身份只从 HttpOnly、SameSite=Lax cookie `chatterpal_learner` 解析；不接受提交的 learner_id 作为授权。缺失、伪造或已撤销 cookie 的私有请求返回 401；bootstrap 可以建立新的匿名身份。清除浏览器 cookie 后无法恢复旧身份，导出文件不是登录凭证。

| 方法与路径 | 输入 | 返回 |
| --- | --- | --- |
| POST /bootstrap | 无 | `{learner_id, profile, pending_operations}` |
| GET /profile | 无 | profile |
| PATCH /profile | 偏好字段 JSON | 更新后的 profile |
| GET /records | 可选 kind、session_id、limit、cursor | `{items,next_cursor}`，items 为完整 record |
| GET /records/{record_id} | record_id | 完整 record |
| POST /records | `{request_id,kind,session_id?,data}` | record；`X-Operation-ID` 响应头 |
| PUT /records/{record_id} | `{request_id,expected_revision,data}` | record；版本冲突 409 |
| DELETE /records/{record_id} | record_id | `{deleted:true}`，连同子记录、音频和关联操作结果删除 |
| GET /sessions | 可选 limit、cursor | `{items,next_cursor}`，items 为 session record 的 data 快照 |
| GET /sessions/{session_id} | 业务 session_id | session record 的 data 快照 |
| POST /records/{record_id}/audio | multipart audio + request_id | audio 元数据；需先开启 retain_audio |
| GET /audio/{audio_asset_id} | 资产 ID | 受身份保护的音频字节；无权访问、不存在或已过期均 404 |
| GET /operations/{operation_id} | 操作 ID | operation |
| GET /requests/{request_id} | 可选 action | operation；跨 action 重用键时未指定 action 返回 409 |
| GET /export | 无 | JSON 下载附件，只含自己的文本记录、关联 ID、操作及音频元数据 |
| DELETE /data | 无；容忍前端附带 request_id | `{deleted:true}`，删除该身份全部数据并清除 cookie |

所有私有响应均为 `Cache-Control: no-store`。写请求验证 Origin / Fetch Metadata。HTTP 开发环境 cookie 不带 Secure；HTTPS 或注入 `learning_cookie_secure=True` 强制 Secure。cookie 的浏览器期限为一年；数据库中的身份一直有效到显式删除，不依赖默认 SECRET_KEY。

Profile：

```json
{"goal":"daily","daily_minutes":8,"correction_mode":"after_session","retain_audio":false,"onboarding_completed":false}
```

`goal` 为 daily/travel/work，daily_minutes 为整数 1–120，correction_mode 为 immediate/after_session。关闭音频保留同时清理已有音频，文本记录保留。字段赋值天然可重复；profile 与删除接口没有额外操作结果记录。资源创建/修改和耗时业务操作使用持久请求去重。

Record：

```json
{"record_id":"UUID","kind":"session","session_id":"business-session-id","data":{},"revision":1,"created_at":"UTC ISO8601","updated_at":"UTC ISO8601","audio_assets":[]}
```

`record_id` 是独立 UUID，和业务 session_id 分离。kind 支持 session/chat/analysis/attempt/review，以及供服务内部使用的 review_attempt/event/transcription。data 是业务完整 JSON 快照，必须为对象，最大 1 MiB；A2 不推断评分、掌握度或反馈可信度。业务快照里的 record_id/audio_asset_id 不被自动解释为数据库外键。列表按创建顺序倒序分页，更新不改变分页顺序。cursor 固定查询过滤条件，不含身份凭证，每次查询仍按当前 owner 过滤。

Audio：`{audio_asset_id,record_id,media_type,size_bytes,sha256,created_at,expires_at,state,url}`。available 提供相对 URL；expired 无 URL。默认单个资产最大 16 MiB。只识别 WAV、MP3、Ogg、FLAC、WebM 容器签名，不信任上传文件名和 Content-Type；这不是音频可解码性或语音质量评估。独立上传接口需明确开启 retain_audio，默认保留 7 天。INT2 教学回合通过原子完成接口保存 1 小时临时回放音频；启用保留后，原音按配置保存，默认 7 天。临时和保留音频均可在 API 重启后、到期前恢复。

短音频作为 SQLite BLOB 保存，记录、资产和权限删除使用同一个事务，避免孤儿资产。路径由 LEARNING_DB_PATH 配置，容器使用已有 `/app/data` 卷。元数据查询不读取音频 BLOB；仅授权下载才读取字节。bootstrap、保存音频、学习服务首次初始化和关闭、显式 `purge_expired_audio()` 会回收过期字节；访问权限在 expires_at 到达时立刻失效，即使回收尚未执行。目前没有定时后台清理器。关闭保留会清除临时和保留音频。关闭保留、过期、删除后，旧操作重放也不会返回仍可用的下载链接。

Operation：

```json
{"operation_id":"UUID","request_id":"caller-key","status":"succeeded","session_id":null,"result_type":"record","result_id":"UUID","result":{},"error":null}
```

请求键长 8–128，允许字母、数字、点、下划线、冒号、短横线。唯一约束为 `(learner_id, action, request_id)`；同键、同操作、同 payload 返回已有结果，不同 payload 返回 409 `request_conflict`。D2 每次操作使用 UUID，因此 `/requests/{request_id}` 无需指定 action。复用同键到多个 action 时必须额外提供 action。创建与更新的数据库事务失败会完整回滚，不留下成功操作；请求可重试。

资源删除会清理其操作结果和子记录，防止读取删除前缓存结果。全部数据删除会撤销身份，任何在途 claim 完成都重新检查身份；删除前已取出的网络字节无法撤回。安全删除不替代部署备份的生命周期管理。

## 仓储内部接口

使用标准库 sqlite3，无新增依赖。每次调用创建/关闭连接，开启 WAL、foreign_keys、busy_timeout、secure_delete；写入使用 BEGIN IMMEDIATE，不在数据库事务内调用提供商。schema user_version 当前为 2，支持从 0 建库与从 1 迁移；拒绝更高版本，保留原 journal_mode。数据库文件权限为 0600。UTC 时间来自可注入 clock。

```python
from chatterpal.learning.storage import LearningStore

repo = LearningStore("data/learning/learning.sqlite3")
bootstrap, cookie_token = repo.bootstrap()
owner = bootstrap["learner_id"]
record, operation_id = repo.create_record(
    owner, "request-001", kind="session", session_id="business-id", data={"turns": []}
)
restored = repo.get_record(owner, record["record_id"])
```

多条关联写入通过 `commit_records(owner, request_id, action, payload, creates=[...], updates=[...])`：creates 每项带唯一 key、kind、data，可指定 session_id 和 parent_record_id；updates 每项带 record_id、expected_revision、data。返回 `({created:{key:record},updated:[record]}, operation_id)`。尝试记录、完整回合及复习排程因此可以一起落库或一起回滚。parent_record_id 需同一 owner，删除父记录同时删除其子记录。业务服务必须显式指定此关系，不能仅把关联 ID 放在 opaque data 中期待自动删除。

耗时提供商操作用 `begin_operation` 预留，获 claim 后再调用提供商：

```python
claim = repo.begin_operation(
    owner, "external-001", "turn", {"text":"Hello"},
    record_id=record["record_id"], expected_revision=record["revision"],
)
if claim.acquired:
    # 此处由 runtime 调用 provider，位于数据库事务外。
    repo.complete_operation(
        owner, claim.operation["operation_id"], claim.lease_token,
        updates=[{
            "record_id": record["record_id"], "expected_revision": 1,
            "data": {"turns": [{"role":"user","text":"Hello"},
                                {"role":"assistant","text":"Hi"}]},
        }],
    )
```

重复 claim 返回原 operation 且 acquired=False；仅持有 lease_token 的工作者可完成。complete_operation 可原子创建 record，或提交 creates/updates；会复查 owner、绑定源记录和 expected_revision。业务服务要将源记录绑定到 claim，防止记录删除或版本变更后发布迟到结果。失败用 fail_operation，error 为 `{code,message}`，不自动重试。外部失败结果持久化，同键仍返回失败，显式重试需新键。

默认租约 300 秒，过期状态为 interrupted，不自动再次调用提供商。INT2 runtime 在停机或独占实例恢复时调用 `interrupt_operations([owned_operation_ids])` 立即中断自己遗留的 pending；不能在任意新连接创建时全局中断其他活跃工作者。A2 不承诺第三方模型跨进程崩溃的 exactly-once，也不实现 R0 有界后台队列、付费调用重试或任务结束状态机。

## 转写草稿与原音复用

录音核对继续使用 schema v2，内部子记录增加 kind=`transcription`，parent 指向源 session 或 review，不新增表或迁移。草稿记录包含服务端生成的 ID、初始接收时间、期限、机器转写、来源记录/版本、完整业务 context 和原音 SHA256。通用创建、批量写入与操作完成入口拒绝直接创建 transcription；通用更新和删除也拒绝绕过专用草稿流程。正式 HTTP 教学写仍只能经过业务服务。

预览使用 action=`transcribe` 的持久操作，只有 ASR 成功并通过来源、租约、期限校验才原子写入 ready 子记录、私有原音和操作结果。source revision 保持不变。ready 草稿保存 1 小时，API 重启可恢复；pending 识别使用既有 runtime 中断语义，不自动重新调用 ASR。原始操作重读会根据草稿当前状态投影 ready/consumed/expired，避免返回已经消费的旧 ready 结果。

最终操作通过 `begin_operation(..., transcription_consume={transcription_id,context})` 校验 owner、ready 状态、来源/版本、期限和原音有效性，并关联到该子记录。`complete_operation` 在同一事务再次校验，将现有 audio_assets.record_id 转移到正式来源，保留 audio_asset_id、SHA256 和 BLOB，标记 consumed，同时保存正式回合或 attempt 及操作成功结果。不开启保留时沿用临时期限，开启时延长到配置的保留期限。任何失败均回滚消费、音频转移和正式写入，草稿仍可在有效期内用于明确的新最终请求。

同一个最终 request_id 先恢复已有操作；新键不能再次消费同一草稿。不同上下文或变更的源 revision 不能重新绑定。丢弃草稿删除关联操作、子记录及音频；pending 识别按请求 ID 撤销，使迟到工作者失去发布权。已消费草稿不能再丢弃；物理删除后重复撤销返回 404。关闭音频保留在清除字节的同一事务中中断 pending transcribe，后续确认也必须重新通过原音存在校验。源记录或身份删除使用原有级联关系清理草稿和操作。

正式回合与尝试分别保存 `machine_transcript,confirmed_text,transcription_id,confirmed_at`；教练和评价使用确认文字。导出保持既有文本和元数据约定，不包含音频 BLOB。

## 独立挂载与 INT2 交接

```python
from types import SimpleNamespace
from fastapi import FastAPI
from chatterpal.learning.storage import LearningStore
from chatterpal.web.routes.learning_records import mount_learning_api

app = FastAPI()
mount_learning_api(
    app, SimpleNamespace(learning_cookie_secure=False),
    store=LearningStore("/your/isolated/learning.sqlite3"),
    allowed_origins={"http://localhost:13204", "http://127.0.0.1:13204"},
)
```

不用 APIServer 即可启动此 app；不会加载 ASR 模型或初始化云服务。传入实例由调用方控制路径与音频上限，也可注入 settings-like 对象并延迟建库。没有传 allowed_origins 时沿用原 3000/3010 开发源；同源请求始终允许。Origin 参数不替代 CORS，Vite 使用相对 `/api` 代理无需浏览器跨源访问。业务 router 可复用 `app.state.learning_repository` 和 `learning_identity` 依赖。`learning.api` 仅保留初版候选挂载导入的兼容转发。

INT2 已接入 DTO/Protocol、有界 runtime、正式 API、Settings 和测试数据库隔离，组合 B2 会话、引用原话的反馈与关联重练。`complete_operation` 支持在同一事务中写入记录、返回业务结果和音频，并在响应构建后、提交前再次校验租约。音频占位符为精确单键 `{"audio_key":"user"}`；audio-only 结果可关联现存源记录而不增加 revision。Linux ARM64 缓存运行镜像挂载当前源码的离线联合验证已通过，未进行新镜像构建。以上 Linux 检查为 INT2 历史结果；复习排程已在 main `d98b13b` 接入。旧聊天与发音接口不自动迁移其全局内存历史。

## A2 验证

局部测试：`tests/learning/`，真实临时 SQLite，98 项通过。覆盖匿名 cookie、重启恢复、双身份隔离、线程/独立进程并发去重、请求冲突、乐观版本、关联原子提交、迁移、数据保真、音频期限与导出删除、慢操作删除竞态。

真实 socket 验证：独立 FastAPI + A2 router + SQLite，21 项通过；停止再启动服务后身份、偏好、历史、复习时间、音频及操作恢复正常，第二身份不可读取，JSON 导出与删除正常。没有调用外部模型或实体音频设备。临时服务已关闭。证据目录 `/private/tmp/chatterpal-a2-20261024-Z5sy3h/http-02/`。

D2 的独立真实浏览器验证结果为 12 项通过、errors=[]，证据 `/tmp/chatterpal-d2.O2ZJBy/a2-results.json`，截图 `a2-history.png` / `a2-after-delete.png`。使用真实 FastAPI + SQLite + 同源前端构建产物，验证 cookie、偏好刷新、records 快照解析、请求恢复、重复保存、双身份隔离、JSON 下载、重启与删除。受控会话由 POST /records 创建，因此不证明任务提供商或 B2/C2/E2 教学流程。

全仓首轮 1369 项通过、2 项失败，覆盖率 82.79%。两项均来自验收驱动：spawn 子进程重入未加 main 保护的驱动，以及 pytester 子进程导入共享 venv 的旧 editable worktree。修正临时驱动后，原两项定向复测通过；未修改生产行为或旧断言。第二次完整离线回归 **1371 项全部通过**，41 条既有依赖/Gradio 警告，耗时 222.93 秒；全包覆盖 **6409/7741 = 82.79%**，原有 80% 门槛保留。运行期间 114 个源码/测试/配置文件哈希稳定，50 个模块导入记录来自当前树（包含无 `__file__` 的 routes 命名空间）。之后仅移除一条测试文件未使用的 `import json`，重新验证全部 98 项 A2 测试；生产源码未变。Black 检查、flake8 的 F 类检查及 diff 空白检查通过。最终汇总 `/private/tmp/chatterpal-a2-20261024-Z5sy3h/acceptance-summary.json`，完整日志 `full-02.log`。
