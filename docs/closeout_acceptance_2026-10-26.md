# Gradio 收尾与即时纠正校验验收

日期：2026-10-26。工作树 `ChatterPal-live-transcript-gradio`，分支 `feat/live-transcript-gradio`，基线 `main@73ab2d8e10f8cd8c198a86940675e75ff300ff81`。本文随基线之上的收尾实现一并提交。验收运行时改动尚未提交，已测代码以本文源码指纹和验收阶段补丁清单识别；提交版本以包含本文的 Git 提交为准。尚未推送、合并或部署。

## 交付行为

Gradio 实际主页面已移除评分与纠错的演示回调，直接复用 `ScoreTab`、`CorrectTab` 和现有服务。两个入口支持录音、上传；评分需要参考文本，纠错参考文本可空。报告显示实际识别文本、文本对齐和可用声学测量，未测量的能力维度明确不可评估。保留原有聊天与三个中文标签页。

双声道上传转换为单声道时按同一时间帧取左右声道均值，避免把交错声道当连续采样导致时长翻倍。实际 Gradio HTTP 回归包含 1 秒、16 kHz 双声道音频，校验转换波形、时长和约 180 Hz 的真实基频。

即时纠正与结束反馈复用 `learning/text_advice.py` 的同一套文字建议规则，同时检查解释和建议表达。无声学依据的发音、音素、流利度或能力分数断言不作为可用即时建议保存；该建议降为 `unavailable`，保留有效教练回复与用户原话。结束后反馈偏好仍为 `deferred`。

正常语法、词汇、数量及已有主题词重排保持可用。独立复核发现整句词频相等的原有例外会误拦截普通语法修改，因此补充了有限上下文规则：同一原句中的 `10% off`、`accent workshop` 等未变短语可随时态或冠词修改保留；完整、独立的通用说明 `Use an before a vowel sound.` 可用。

复核进一步检查了例外边界：折扣必须匹配完整数值，包括符号、小数点和分组逗号，整句重排也比较完整数值，防止 `5%` 被改为 `.5%` 或 `10%` 被改为 `-10%`；允许短语按原句出现次数和建议中位置校验，不删除原文后再匹配，因此 `Your accuracy is 10% off the expected level.` 不能借原有折扣绕过检查。嵌入其他句子的冠词短语、分号附加声学评价和新增发音断言均被拒绝。这仍是保守的文字模式校验，不是完整语义判断器。

新增 HTTP 回归经过实际 APIServer、operation runtime 和 SQLite，确认不合格建议不会出现在成功回执、历史、重复请求或 API 重启恢复结果中；教练回复仍可合成，未触发额外 ASR。

## 本轮已经执行的验证

| 检查 | 本轮结果 |
| --- | --- |
| 后端全量（最终兼容修复后） | 2057 项，0 失败、0 错误、0 跳过，99.51 秒 |
| 全包行覆盖率 | 8549 / 9780 = 87.41%；原有 80% 门槛通过，共享文字校验模块 100% |
| 即时建议 HTTP 持久化/重放/重启回归 | 2 项通过，已包含在全量中 |
| 前端完整回归 | 186 项，12 个测试文件通过 |
| 前端构建 | TypeScript + Vite 通过，产物保存到独立验收目录 |
| 前端 lint | 通过，零警告 |
| Gradio 实际浏览器 | 上传评分、空参考纠错、假麦克风录音、键盘 Enter、375px/横屏通过 |
| React 实际浏览器 | 任务、关联重练、复习核对，原音复用、刷新/API 重启、重复请求和身份隔离通过 |

最初新增的声学断言回归在修复前为 16 项失败、16 项通过，修复后为 32 项通过。独立复核随后提出普通语法建议误拦截，追加即时纠正和结束反馈用例后，修复前 6 项失败、322 项通过；有限上下文修复后，coaching、feedback、review 和 HTTP 持久化相关回归共 348 项通过。这些聚焦测试包含在最终全量中，不与全量数量相加。

全量后端使用正确工作树 cwd 和 `PYTHONPATH`，保留 `--cov=src/chatterpal`、原覆盖率范围与 80% 门槛，启用 `RuntimeWarning` 和未处理线程异常报错。打包测试使用预备 wheelhouse、新缓存和离线安装。最终 JUnit、覆盖率及日志为证据目录中的 `backend-accepted-results.xml`、`coverage-accepted.xml`、`backend-accepted.log`，HTML 报告为 `htmlcov-accepted/`。共有 54 条非阻塞警告，主要来自 WebSocket、Gradio 聊天格式和事件循环弃用，测试替身音色列表，以及音频解码回退；没有把警告写成零警告。

新增及修改的 Python 模块已通过 flake8 F 类名称/导入检查，learning 与相关回归文件通过 Black 格式检查；两个新增浏览器脚本通过 `node --check`，补丁通过 `git diff --check`。Gradio 独立聚焦复核 195 项通过，原规则抽取的 coaching/feedback/review 聚焦回归 335 项通过，均是最终全量的子集。

兼容修复后独立复核已通过：24 组输入分别经过即时纠正与结束反馈两个调用入口，共 48 个结果；14 类异常均移除建议并保留有效教练回复，10 类正常建议均保留原修改和说明。复核范围内无剩余阻塞问题。最终聚焦回归为 `context-final-span-green.xml` 中 366 项通过，不把此前中间版全量 2028、2039 项或聚焦用例累计为最终总数。

### Gradio 实际页面

驱动：`scripts/verify_gradio_analysis_browser.cjs`。实际 Chrome → Gradio HTTP → 现有分析服务；ASR/LLM 边界为受控提供商，没有通过内部函数直接调用替代页面操作。本次没有设置 `GRADIO_ENV_FILE`。

- 实际页面注册两个服务组件，三个中文标签页仍可访问。
- 缺少输入不会调用提供商；三次有效音频提交各识别一次，共 3 次 ASR、1 次文字建议调用。
- 纠错上传允许空参考，五个报告区域返回识别、来源和不可评估原因，没有演示输出或虚构能力分数。
- 通过实际录音控件开始/停止假麦克风，等待上传 200 和播放器就绪，再用 Enter 提交。
- 375×812 和 812×375 无横向溢出；页面 `pageerrors=[]`，已查看桌面及手机截图。
- 记录到 5 次 `/gradio_api/queue/data` 的 `net::ERR_ABORTED`，属于本次操作过程中关闭的队列流，报告和操作完成；不写成“没有失败网络请求”。

证据目录：`/tmp/chatterpal-finish-20261026-F1d5pa/gradio-browser/`，含 `results.json`、provider 调用计数、日志及截图。

### React 三入口与恢复

驱动：`scripts/verify_transcript_confirmation_browser.cjs`。实际 Chrome → Vite 同源代理 → APIServer socket → SQLite，提供商边界受控，未拦截教学 API。

- 任务、关联重练、复习共 3 次 ASR；预览仅调用 ASR，最终确认不重复识别。
- 核对后文字和机器原文分别保存；原音资产 ID 和字节复用。
- ready 草稿浏览器刷新和 API 重启后恢复，可实际回放。
- 重复请求恢复同 operation，已消费草稿换新请求返回 409；第二身份读取草稿和原音返回 404。
- 复习结果和历史恢复，375px 页面无横向溢出，`errors=[]`；已查看核对区和历史页截图。

最终通过目录：`/tmp/chatterpal-finish-20261026-F1d5pa/transcript-browser-final/`，含完整结果、请求/操作、SQLite、日志及截图。

此前同目录旁的 `transcript-browser/` 首跑在服务重启阶段因共享模块抽取期间 `_words` 暂不可导入而失败，不计为通过。导入兼容恢复后，使用全新目录重新完成全部流程，未放宽浏览器断言。

## 源码识别与复现

本轮证据根目录为 `/tmp/chatterpal-finish-20261026-F1d5pa/`，临时目录不作为长期归档。`final-local.patch` 包含基线以上已跟踪修改和全部 7 个新增文件，`final-patch-files.json` 记录完整补丁清单与 SHA256；这些只是本机审查快照，没有修改 Git 暂存区。`verified-code-accepted.json` 记录 `src/`、`tests/`、`scripts/` 中 179 个 Python/CJS 文件及 `pyproject.toml`、`uv.lock`，共 181 个文件。逐文件 SHA256 后，按排序的 `relative_path + NUL + file_sha256 + newline` 计算 SHA256，最终全量后再次确认 181 个文件均未变化，指纹为：

```text
2381bd2fb81052563faba972b2a83e9f9c1add49b5704136dbf7b8c6d24f26f3
```

浏览器通过时的完整源码快照为 `verified-code.json`；随后仅共享文字建议规则及三个相关测试文件改变，Gradio、前端、录音草稿和浏览器驱动均未变。规则定稿由上述双入口探针、HTTP 持久化测试和最终后端全量验证；没有把浏览器通过写成又执行了一次。

在此工作树根目录，使用已安装完整依赖的 Python、准备好 README 所述离线 wheelhouse 后，可复现后端：

```bash
PYTHONPATH="$PWD/src" \
CHATTERPAL_TEST_WHEELHOUSE=/path/to/wheelhouse \
HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 GRADIO_ANALYTICS_ENABLED=False \
/path/to/python -m pytest -p no:cacheprovider -q \
  -W error::RuntimeWarning \
  -W error::pytest.PytestUnhandledThreadExceptionWarning
```

本机复用 `/Users/dd_y/Code/learn/ChatterPal/.venv/bin/python`（Python 3.12），但 cwd 和 `PYTHONPATH` 始终指向本次工作树；没有修改依赖锁、全局测试 fixture 或覆盖率配置。前端复现命令为 `npm --prefix frontend test -- --reporter=dot`、`npm --prefix frontend run build`、`npm --prefix frontend run lint`。受控浏览器命令如下，输出目录应各用新目录：

```bash
TRANSCRIPT_PYTHON=/path/to/python \
TRANSCRIPT_OUTPUT_DIR=/path/to/new-transcript-evidence \
CHROME_EXECUTABLE_PATH=/path/to/chrome \
node scripts/verify_transcript_confirmation_browser.cjs

env -u GRADIO_ENV_FILE \
GRADIO_PYTHON=/path/to/python \
GRADIO_OUTPUT_DIR=/path/to/new-gradio-evidence \
CHROME_EXECUTABLE_PATH=/path/to/chrome \
HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 GRADIO_ANALYTICS_ENABLED=False \
node scripts/verify_gradio_analysis_browser.cjs
```

## 文档与交付状态

README、文档索引和旧优化计划现已明确当前入口、历史基线与本次验收位置。录音核对旧验收记录补充了实际已进入 `main@73ab2d8`、对应远端 CI 通过的状态。

早前真实提供商记录仍保留在 `live_transcript_gradio_acceptance_2026-10-26.md`，但其临时原始证据在本次会话不可读取。本文不将它记为本次独立复验；本轮未使用私有提供商凭据、调用真实云服务、下载模型或使用实体麦克风。真实人物、设备、真实跨日行为、评分校准和生产容量保持独立验证范围。

本轮运行的浏览器和服务已关闭，17860、18240、13240 端口已释放。没有更改主线、清理旧工作树或更新远端 issue 状态。
