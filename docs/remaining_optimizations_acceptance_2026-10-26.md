# 剩余优化验收记录

日期：2026-10-26。工作树：`ChatterPal-remaining-optimizations`；分支：`integrate/remaining-optimizations`；起点为已合入 main 的 INT2 提交 `f597ee783be74156aecf134639e9ef813ba0ea06`。本文记录提交前的工作树验收；提交与推送状态以 Git 历史及远端记录为准。

本文记录 F（TTS）、G（分析可信状态）、H（打包部署）、I（运行资源）与 E2（复习进度）的实现和验收。INT2 当时的结果保留在[周末验收记录](int2_weekend_acceptance.md)及[2026-10-25 真实提供商验收](int2_live_provider_acceptance_2026-10-25.md)，不将历史结果替代本次回归。

## 交付范围

| 范围 | 当前行为 | 验证依据 |
| --- | --- | --- |
| F：TTS | 冻结每个请求的有效合成参数；缓存区分提供商、模型、音色、语速、音量、音调、格式等；CosyVoice 控制传入 SDK；依据实际字节选择格式和 MIME，裸 PCM 包装为 WAV | 并发音色、缓存与重试回归；真实 Alibaba 类通过 SDK 边界控制验证模型与 speaker 分离；WAV / Ogg Opus 经实际 HTTP 下载；Edge 真实 MP3 在真实服务验收中解码与播放 |
| G：分析可信状态 | 不支持的总分、音素、流利度等分数为空；维度附来源、原因、证据、单位和校准状态；文字对齐处理词序、增删及重复；React / Gradio 解释不可用值 | 有效/静音/无效输入、缺依赖、词序变化、模型 JSON、配置注入和空值渲染回归；Chrome 分析页面验收 |
| H：打包部署 | 公开命令进入 wheel；可选运行时按 extras 安装并延迟导入；配置默认只读且拒绝覆盖；Docker 从已安装包启动 | 仓库外干净安装、实际 CLI/socket/Gradio、冻结依赖配置安装、linux/arm64 云端镜像与 nginx 代理验收 |
| I：运行资源 | 历史保留 system 和完整回合；会话有容量/闲置限制；活跃与排队会话受保护；生成音频有 TTL/数量/字节上限和下载租约；阶段指标使用有限标签与样本 | 600 次/路径受控基准、过载拒绝、下载租约、取消/停机与上传生命周期回归；独立复核四项 API 边界修复 |
| E2：复习进度 | 明确保存来源后次日到期；符合延迟独立新题证据时按 3／7 天排程；服务端保存提示/看答案；进度链接恢复具体回答及音频 | 真实 APIServer + SQLite + Chrome，注入 UTC 时钟；排程、冲突保稿、重启、一次 ASR、录音播放、历史恢复、身份隔离及餐厅/酒店流程 |

## 最终自动回归

| 检查 | 结果 |
| --- | --- |
| 全量 Python | **1960 passed，0 failed，0 errors，0 skipped**；92.40 秒；30 条依赖或兼容警告 |
| 全包覆盖率 | **8328 / 9543 = 87.27%**；保留默认 80% 门槛与既有排除配置 |
| 严格异常检查 | `RuntimeWarning` 和 `PytestUnhandledThreadExceptionWarning` 均提升为错误，完整运行通过 |
| 前端 | **155 passed，11 个测试文件全部通过**；TypeScript + Vite build、ESLint 零警告通过 |
| 静态与格式 | `git diff --check`、最终边界测试 Black、浏览器驱动语法检查通过；比较 main 后本轮新增 F401/F811/F821 为 0 |
| 既有静态问题 | 全 src 的 F401 检查仍有 **30 条 main 已存在的未用导入**；不将它描述为全仓 lint 已清零 |

后端报告：`/tmp/chatterpal-remaining-final-pytest.xml`、`/tmp/chatterpal-remaining-final-coverage.xml`、`/tmp/chatterpal-remaining-final-backend.log`。前端日志：`/tmp/chatterpal-remaining-final-frontend-{test,build,lint}.log`。源码、测试与关键配置的 200 文件哈希记录：`/tmp/chatterpal-remaining-final-source.json`。

首次最终全量是 1959 通过、1 失败：取消测试在事件循环退出后立刻断言上传文件消失，而文件所属工作线程还在执行清理。临时注入 100ms 清理延迟后，三个入口均可稳定复现；测试增加真实清理完成事件后同一探针 3 项通过，再运行全量得到上表结果。产品代码继续由工作线程在 `finally` 清理输入，取消等待者和关闭主事件循环不会提前删除正在使用的文件。

独立复核确认以下四项修复，并运行 8 项 HTTP/工作线程回归全部通过：Alibaba 广告音色只替换 speaker、不覆盖模型；health 使用独立 executor、提供商饱和时仍响应；上传由实际工作者持有直至完成；Opus 允许下载并返回 `audio/ogg`。

## 实际浏览器

复习最终证据：`/var/folders/dt/0y71cp9s52j217lxh6nh3r4m0000gn/T/chatterpal-review-browser-GP1qQA`。教学 API 没有拦截；ASR/LLM/TTS 使用受控提供商，时钟由测试注入，因此不代表云端响应质量或真实跨日学习效果。

该次运行确认：

- 到期前不返回未来问题与问题编号；独立作答依次排到 +3 天、+7 天。
- 提示、看答案和延期在服务端保存，API 重启后保留；客户端假报未使用提示不能改变证据。
- 外部提示造成真实 revision 409 后，页面刷新本项记录并保留未发送草稿。
- 复习录音通过实际页面采集、上传和播放；ASR 调用 1 次。刷新后从进度“查看对应尝试”恢复历史问题、原话、评价与录音，下载字节与原录音相同。
- 4 次复习回答中，2 次独立迁移、1 次有提示、1 次新题耗尽后的重复问题；重复问题不推进排程，未生成能力分或掌握率。
- 第二个浏览器身份读取另一身份的复习返回 404；375px 无横向溢出，`pageerrors=[]`。已查看手机进度页和刷新后历史回答截图。
- 餐厅和酒店任务各完成 3 个用户回合、有依据反馈及关联重练；这里只验证任务接线和持久化，受控教练文案不作为真实教学质量证据。

周末受控浏览器最终证据：`/var/folders/dt/0y71cp9s52j217lxh6nh3r4m0000gn/T/chatterpal-int2-browser-BQfCv0`。三轮用户回答、两个关联重练、提示/历史保留草稿、页面刷新、实际 API 停止后重启、375px 无横向溢出全部通过，`pageerrors=[]`。

## 真实提供商观察

使用此前选定的 OpenAI 兼容服务 / `Qwen3.8-27B`、Whisper `base` 与在线 Edge TTS。凭据仅从本地忽略文件读取；当前工作树没有复制 `.env`。验收使用合成语音通过实际浏览器录音输入；不代表实体麦克风或真人发音效果。

本轮已保留的非完整通过运行：

| 证据目录尾缀 | 实际结果 |
| --- | --- |
| `chatterpal-int2-live-jHiuyV` | 三轮成功，反馈 HTTP 读取在默认 30 秒超时；结束状态与原话安全保存 |
| `chatterpal-int2-live-RG8r7T` | 显式 60 秒后反馈返回；模型把一项重点的源回合 ID 写错，服务删除该项，保留有效重点与目标证据，状态为 `partial`；旧驱动仅允许 `available` 而退出 |
| `chatterpal-int2-live-GJpFy4` | 三轮成功，反馈调用失败；日志显示内容为空导致提供商错误，服务保存 unavailable 状态 |
| `chatterpal-int2-live-XByrRQ` | 第三轮模型返回普通文本，未符合必需 JSON；请求安全失败，已有会话与草稿保留 |

上述目录均位于 `/var/folders/dt/0y71cp9s52j217lxh6nh3r4m0000gn/T/`。不把这些运行计为完整通过，也不据此计算提供商可用率。

最终完整通过证据：`/var/folders/dt/0y71cp9s52j217lxh6nh3r4m0000gn/T/chatterpal-int2-live-vNQqar`，使用冻结后的本工作树源码、`OPENAI_TIMEOUT=60`。该次 **2 次 ASR、6 次 LLM、4 次 TTS 全部成功**，没有触发反馈重试或文字回合重新提交。三轮用户回答后反馈为 `available`、2 个重点均有当前原话引用、3 项任务目标均附原话证据；两次自由表达重练均 `available/met`，parent 链正确。页面刷新和实际 API 停止后重启，保存的会话与 HttpOnly 身份一致，原音 SHA256 一致；音频实际解码及播放通过，375px 无横向溢出，`pageerrors=[]`。最终记录有 2 个首次重点尝试与 2 个关联重练，共 4 个 attempts。

| 本次浏览器操作 | 耗时 |
| --- | ---: |
| 开始 / 提示 | 0.621 / 0.544 秒 |
| 文字第 1 轮 / 录音第 2 轮 / 文字第 3 轮 | 4.627 / 8.188 / 12.761 秒 |
| 结束并生成反馈 | 40.285 秒 |
| 文字重练 / 录音重练 | 5.644 / 7.143 秒 |
| 获取目标范读 | 1.045 秒 |

原音与重练 WAV 为 PCM16、单声道、16kHz；Edge 教练与范读为 MP3、24kHz，HTTP MIME 与实际编码一致。上述耗时是一次完整浏览器运行，不是生产 P95。最后一次成功不消除前面观察到的超时、空内容、格式及引用错误；该兼容服务仍需要明确的失败/降级状态和后续可靠性观察。

验收驱动按既有产品契约允许 `partial`，仍要求保留 1–2 个可核对原话的重点、所有目标原话证据和两次有效重练。不可用反馈最多通过页面明确重试一次；失败的文字回合最多重新提交一次，确认原保存快照/草稿未变且使用新 request_id。失败操作、提供商错误与重试均进入结果文件，不改变生产重试策略或放宽 JSON/原话校验。`OPENAI_TIMEOUT` 新增为显式配置，默认仍为 30 秒，合法范围 `(0,300]`；本次重跑设为 60 秒。

普通教练及结束反馈提示已移除为复习预留的 `transfer_questions`，公开 records/session/operation 也移除此内部字段；原始 owner 导出保留完整内容。该边界已覆盖三项任务的回归。

## 打包与容器

最终 wheel：246,625 字节，SHA256 `a8cc98525026674b8e9b22aff45bd6fc9422b8644bcb7e7d1e7d51b1462bef33`。包内 82 文件的源码、wheel、仓库外安装与容器安装逐字节一致，包指纹为 `0c9b5aa47f9c60666d9a7f8d06226ec2a1af403d6e66e774d0adf3f66baaf484`。

| 镜像 / 入口 | 最终结果 |
| --- | --- |
| 云端后端 linux/arm64 | 294,661,003 字节；缓存重建 14.166 秒；`sha256:6a18ddc4bf51e596c325c4123359693b25cfa72917d7064709290cfcef18ee87` |
| 前端 linux/arm64 | 25,887,066 字节；构建 10.376 秒；`sha256:755a2833e9fad8b1ab4a67929fa8539e2de1b99c6bc236dc5a7d0c85496d35e7`；最终核对 44 项输入未变后复用 |
| 容器启动与交互 | 2.191 秒到健康；前后端内置 healthcheck、直接与 nginx 代理聊天 200、同会话保留、TTS 失败保留文字全部通过 |
| 仓库外 wheel | 0.952 秒到健康；两轮 API + 一轮终端聊天、配置、TTS 失败保留文字与正常关闭通过 |

容器使用独立内部网络与受控兼容提供商；没有挂载 `/app/src` 或 `/app/scripts` 回退到源码，云端镜像排除本地模型与设备依赖。临时容器/网络已清理。干净安装 extras 与 macOS Gradio 的范围见[部署实测](deployment.md#打包与安装实测2026-10-25)。证据位于 `/tmp/chatterpal-h-validation.4mWFTC/`。

未验证 amd64、默认 full 容器、Docker Gradio profile、Windows 批处理实际运行或 Linux x86_64 CPU 专用安装。缓存构建/启动时间不代表无缓存交付耗时、真实模型推理速度或内存占用。

## 可复现资源基准

ASR/LLM/TTS 均是固定延迟的离线实现，仍经过实际服务编排、SQLite 和 runtime。每条路径 600 请求，并发 4、学习工作线程 2、排队上限 8、提供商等待参数 5ms：

| 路径 | P50 | P95 | 成功吞吐 | 错误 |
| --- | ---: | ---: | ---: | ---: |
| 旧聊天音频路径 | 22.05ms | 24.71ms | 181.72/s | 0/600 |
| 学习 runtime 路径 | 42.70ms | 65.33ms | 88.66/s | 0/600 |

RSS 当前值从 45.63MiB 到 50.25MiB，过程峰值 50.62MiB。会话不超过 8、生成音频不超过 16、TTS 缓存不超过 8、每个阶段样本不超过 256；40 轮历史探针最终保留 19 条消息，符合 system + 完整回合且不超过配置 20。临时文件采样峰值 31,828 字节；受控到期并清缓存后临时文件/字节、会话、上传副本均为 0。持久 SQLite 331,776 字节单独保留。

过载探针 120 请求、并发 12、工作线程 2、排队 4：学习 6 次准入/完成、114 次 busy；旧聊天 8 次准入/完成、112 次会话容量拒绝。拒绝不调用提供商，结束后 pending/running/queued 均为 0。拒绝吞吐不能当作有效服务容量。

证据：`/tmp/chatterpal-runtime-benchmark-600.json` 与 `/tmp/chatterpal-runtime-benchmark-overload.json`。这些是当前实现的受控基准，没有同环境的旧版吞吐对照，不宣称提升百分比或真实提供商生产 P95，也不替代长周期线上资源观察。

## 复现

从本工作树根目录运行。使用项目自己的 `.venv`，或显式设置 `PYTHONPATH="$PWD/src"` 指向本树；不要让旧 editable 安装或其它工作树的相对覆盖率路径影响结果。

```bash
uv sync --frozen --python 3.12
uv run python -m pytest -q --cov-report=xml --junitxml=pytest-results.xml \
  -W error::RuntimeWarning -W error::pytest.PytestUnhandledThreadExceptionWarning
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run lint

INT2_PYTHON="$PWD/.venv/bin/python" node scripts/verify_int2_browser.cjs
INT2_PYTHON="$PWD/.venv/bin/python" node scripts/verify_learning_review_browser.cjs

# 会调用已配置的真实外部服务；可另设 CHROME_EXECUTABLE_PATH。
INT2_PYTHON="$PWD/.venv/bin/python" INT2_ENV_FILE=/path/to/private.env \
  OPENAI_TIMEOUT=60 node scripts/verify_int2_live.cjs

uv run python scripts/benchmark_runtime.py --iterations 600 --concurrency 4 \
  --workers 2 --pending 8 --delay-ms 5
uv run python scripts/benchmark_runtime.py --iterations 120 --concurrency 12 \
  --workers 2 --pending 4 --delay-ms 5
```

当前仍以单实例 SQLite 为部署边界，每项任务仅有两道人工迁移问题。声学分数校准、真人学习效果、更多迁移内容、真实负载容量与未测平台属于后续独立工作；本轮实现没有对此作完成承诺。
