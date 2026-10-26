# INT2「描述周末」验收记录

日期：2026-10-25。实现与离线联合验收完成；集成分支为 `integrate/product-round-2`，已合入 main。本文件保留 INT2 当时的验收范围；后续复习、评分可信状态和运行时优化见[2026-10-26 验收](remaining_optimizations_acceptance_2026-10-26.md)，下面的“尚未接入”不代表后续工作树状态。

## 交付结果

主流程已接入正式 FastAPI、现有 ASR/LLM/TTS 适配器和 SQLite：选择「描述周末」→ 多轮文字或录音回答 → 显式结束 → 引用本次原话的反馈 → 首次尝试和两次关联重练 → 查看对比 → 浏览器刷新与 API 重启后恢复已保存记录。

- 反馈最多两个重点，原句、目标和证据必须能在本次记录中核对。自由表达接受不同说法；跟读只核对转写文字，不声称测量声学发音或能力分。
- 即时纠正可在对应用户回合显示；结束后集中反馈保留 deferred 状态。提供商异常或不合法输出显示不可用原因，已成功保存的对话不丢失。
- 每次上传只识别一次，同一原始转写用于教练、评价和保存。TTS 失败保留文字。
- 请求去重、HTTP revision、同会话写操作互斥和租约共同保护提交；删除、取消、超时及停机后，迟到的工作结果不能发布。
- 原话和每次重练分别保存，通过同源句、同重点、同方式的 parent 形成链；自由表达和跟读分别关联。
- 未发送的文字和准备录音保存在当前页面内存，内部切页、提示和记录更新后保留；仅对应提交确认后清除。刷新不恢复未发送正文。
- 默认音频暂存 SQLite 1 小时，启用保留后的原音默认 7 天；到期前可跨 API 重启回放。关闭保留会删除已有音频字节，文字记录继续保留。
- 次日复习尚未接入：API 返回 `review_unavailable`，页面明确说明，可从学习记录继续重练。

## 来源与工作区

| 项目 | 来源 |
| --- | --- |
| INT2 工作树 | `/Users/dd_y/Code/learn/ChatterPal-product-integration` |
| 基线 main | `6d67fbc44f5e9c3dd24aa24fe1ff4cf2d7efca3d` |
| A2 存储 | `c520010856709de793e19349b8536659e09b6ac3` |
| B2 任务与教练 | `98db184be7c250d1294f8ca18cec50f1a0812593` |
| D2 前端 | 从 `ChatterPal-learning-ui` 获取 22 文件一致性快照，读取前后 SHA256 核对；原清单 `/tmp/chatterpal-int2-d2-snapshot.json` |

A2/B2 以 diff 导入，D2 以文件快照导入，再完成本轮接线和修复。INT2 最终实现提交为 `f597ee783be74156aecf134639e9ef813ba0ea06`，随后已快进合入并推送 main；该提交在功能分支和 main 的 CI 均通过。以下全量离线结果记录的是提交前验收，真实提供商兼容修复及其专项结果见后文。原文件 SHA256 清单位于本地 `/tmp/chatterpal-int2-final/worktree-files.json`，它是当时的快照，不代表后续优化工作树。

## 最终验证

| 验证 | 结果与证据 |
| --- | --- |
| 全量 Python | **1753 passed，0 failed，0 skipped**；41 条依赖或旧 Gradio 警告，76.18 秒 |
| 全包覆盖率 | **7580 / 8933 = 84.85%**；保留原默认 80% 门槛，没有新增排除 |
| 测试隔离 | 138 个 Python 源码、测试与配置文件运行中指纹不变；所有已导入 chatterpal 模块来自 INT2 工作树；RuntimeWarning 和线程异常提升为错误 |
| 全量前端 | **133 / 133 passed，0 failed，0 skipped**；保留旧 59 项回归；TypeScript + Vite build、ESLint 零警告 |
| 实际 Chrome 与 socket | 真实 Vite + APIServer + SQLite；教学 API 没有拦截；三轮、反馈、两次重练、提示和历史保稿、刷新、后端停止再启动全部通过，pageerrors=[] |
| 手机布局 | 375px 无横向溢出，单个页头，实际滚动到反馈和输入区；已查看桌面、手机截图 |
| Linux ARM64 | 缓存运行镜像挂载当前源码，断网 smoke 通过：文字与 WAV、一次 ASR、反馈、关联重练、重建 API 后记录与音频恢复 |
| 部署与静态检查 | Compose 模板配置、学习模块 Black、flake8 F、`git diff --check` 通过 |

全量 Python 已覆盖 210 项反馈测试、98 项原 A2 存储测试、50 项 INT2 原子存储测试、13 项 runtime、14 项 API 边界和 7 项周末纵向测试。这里按最终完整 JUnit 计数，不使用中途审计报告的临时数量。

前端新增 11 项对抗回归，覆盖旧 POST、操作查询、会话读取的迟到响应，401 后恢复入口、失败删除保稿、删除期间读取及到期音频引用。实际 socket 主流程验收后，最后补充的删除期间读取保护由最终 133 项全量中的第 11 项对抗回归覆盖。

后端首次完整运行有两项验收驱动失败：临时 runner 未加 main 入口保护导致 spawn 重入 pytest；pytester 子进程未继承 INT2 PYTHONPATH、导入了旧 editable 工作树。修正临时 runner 的入口保护与环境后，全量通过，未修改旧断言、放宽离线边界或降低覆盖率。首次日志保留在 `/tmp/chatterpal-int2-backend-first-run.log`。

## 复现命令

在 INT2 仓库根目录执行。常规安装使用本项目自己的 `.venv`；本次本机验收使用共享解释器 `/Users/dd_y/Code/learn/ChatterPal/.venv/bin/python` 并显式指定 INT2 导入路径。

```bash
uv sync --locked --python 3.12
PYTHONPATH="$PWD/src" GRADIO_ANALYTICS_ENABLED=False HF_HUB_DISABLE_TELEMETRY=1 \
  uv run python -m pytest -q --cov-report=xml --junitxml=pytest-results.xml \
  -W error::RuntimeWarning -W error::pytest.PytestUnhandledThreadExceptionWarning
npm --prefix frontend ci --no-audit --no-fund
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run lint
```

离线真实浏览器验收自行启动隔离 API/Vite，默认端口 18200/13200，输出临时数据库、请求记录、日志和截图，结束后关闭服务。提供商在驱动内替换为受控边界，无须配置真实凭据。

```bash
INT2_PYTHON="$PWD/.venv/bin/python" \
CHROME_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
  node scripts/verify_int2_browser.cjs
```

Linux 或其它 Chrome 安装位置需替换 executable；也可预先安装 Playwright 的浏览器并省略该变量。可以通过 `INT2_API_PORT`、`INT2_UI_PORT`、`INT2_OUTPUT_DIR` 指定端口和证据目录。

容器 smoke 使用本机已有镜像，不启动在线服务、不下载模型：

```bash
docker run --rm --network none --entrypoint /opt/venv/bin/python \
  -v "$PWD/src:/app/src:ro" -v "$PWD/tests:/app/tests:ro" \
  -v "$PWD/scripts/verify_int2_container.py:/app/verify_int2_container.py:ro" \
  -e PYTHONPATH=/app/src -e GRADIO_ANALYTICS_ENABLED=False \
  -e HF_HUB_DISABLE_TELEMETRY=1 chatterpal-backend:local \
  /app/verify_int2_container.py /app
CHATTERPAL_ENV_FILE=.env.example docker compose --env-file .env.example config --quiet
```

正常使用现有提供商启动应用：

```bash
API_PORT=18200 LEARNING_DB_PATH=data/learning-int2.sqlite3 \
  uv run python scripts/run_api.py
# 另一个终端，仍在仓库根目录：
CHATTERPAL_API_TARGET=http://127.0.0.1:18200 \
  npm --prefix frontend run dev -- --host 127.0.0.1 --port 13200 --strictPort
```

访问 `http://127.0.0.1:13200`。正常提供商需要按 [部署指南](deployment.md) 配置；真实浏览器的离线验收命令不依赖这些配置。

## 本地证据与范围

- Python：`/tmp/chatterpal-int2-final-backend.log`、`/tmp/chatterpal-int2-final/{pytest-results.xml,coverage.xml,backend-summary.json}`。
- 最终前端：`/tmp/int2-frontend-review-full-final.json`、`/tmp/int2-frontend-review-full-final-{test,build,lint}.log`。
- 最新实际浏览器：`/var/folders/dt/0y71cp9s52j217lxh6nh3r4m0000gn/T/chatterpal-int2-browser-wrX97q/`，内含 `results.json`、API/Vite 日志及 `weekend-*.png`。
- 容器：`/tmp/chatterpal-int2-container.log`；结果为 `passed=true,sqlite_restart=true,asr_calls=1,attempts=2,audio_recovered=true`。
- 本次使用已加载的 run skill 启动实际 app，并在 Chrome 中交互、检查截图；未以测试套件或 fixture 页面替代 socket 验收。

这些是开发机临时证据，清理临时目录后不会保留。复现脚本已在仓库中；如需将这条启动、驱动和截图路径保存为项目技能，可使用 `/run-skill-generator`。没有执行新 Docker 镜像构建、推送、远端 CI 或部署；容器结果仅代表缓存 Linux ARM64 运行环境挂载源码的验证。本报告所述离线验收没有调用真实云服务、下载模型或使用实体麦克风。

后续于 2026-10-25 使用 Qwen3.8-27B、Whisper base 与在线 Edge TTS 完成了[真实提供商完整验收](int2_live_provider_acceptance_2026-10-25.md)，并修复完整 JSON 代码块包装的解析兼容问题，另有 297 项定向回归通过。该实测使用固定合成语音，真人麦克风覆盖与学习效果仍待验证。

当前部署范围为单 API 实例与本地 SQLite；临时音频到期即拒绝访问，字节在初始化、上传、bootstrap、关闭及显式清理时回收，没有定时清理器。匿名身份依赖浏览器 cookie，清除 cookie 后没有账号找回能力。复习排程尚未实现。
