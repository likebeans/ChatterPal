# 参与 ChatterPal 开发

你可以通过修复问题、改进学习流程、补充测试或完善文档参与项目。ChatterPal 目前处于 Alpha 阶段；开始前先阅读 [README](README.md) 了解功能边界，再按[开发指南](docs/development.md)准备环境。

## 从问题到 Pull Request

一次贡献尽量解决一个具体问题，让复现、实现和验证能够一起审阅：

1. 搜索已有 [Issues](https://github.com/likebeans/ChatterPal/issues) 和 [Pull Requests](https://github.com/likebeans/ChatterPal/pulls)，避免重复工作。较大的功能或接口调整可以先提交提案，说明使用场景和取舍。
2. 从最新 `main` 创建短期分支，例如 `fix/audio-playback`、`feat/review-filter` 或 `docs/setup-guide`。没有仓库写权限时，先 Fork，再在自己的仓库中创建分支。
3. 完成改动，为行为变化补充能复现问题的测试；配置、接口或启动方式变化时同步修改相关文档。保持改动范围集中。
4. 运行与改动相关的验证，在 PR 中写清问题、改动后行为、验证命令和实际结果。尚未执行的检查也要说明，尤其是实体设备和真实提供商验收。
5. 向本仓库的 `main` 提交 Pull Request，关联已有 Issue，并根据审阅反馈更新分支。

开发环境使用 Python 3.12、Node.js 20+ 和仓库锁文件。Python 使用 `uv sync --frozen --python 3.12`，前端使用 `npm ci`；完整命令与原生依赖见[开发指南](docs/development.md#准备开发环境)。

## 按改动范围选择验证

先运行能覆盖改动行为的检查；持续集成（CI）的配置以 [tests.yml](.github/workflows/tests.yml) 为准。局部测试用于定位问题，完整后端回归仍保留全包覆盖率 80% 的门槛。

| 改动范围 | 建议验证 |
| --- | --- |
| 文档、Issue 或 PR 模板 | 核对相对链接、路径和命令参数；命令变化时对照实际入口与配置，不必为纯文字修改运行完整业务测试 |
| Python 配置、服务、API 或存储 | 运行相关 `tests/` 用例，为错误恢复、资源释放或数据一致性变化补充对应回归 |
| React 逻辑、组件或样式 | 在 `frontend/` 运行 `npm test`、`npm run build`、`npm run lint`；交互、录音或布局变化时补充对应浏览器检查 |
| 前后端契约、录音确认或学习流程 | 同时验证相关后端和前端测试，选择真实 HTTP / SQLite 与受控提供商的浏览器驱动验证跨层行为 |
| Python 依赖、打包或 CLI | 检查 `pyproject.toml` 与 `uv.lock` 一致，准备 wheelhouse 后运行 `tests/test_packaged_cli.py`；前端依赖变更同步更新 `package-lock.json` 并运行前端检查 |
| GitHub Actions | 对照工作流检查安装顺序、命令和报告路径；新增检查应说明执行范围与外部依赖 |

局部测试、wheelhouse 准备、完整回归和浏览器命令见[开发指南的验证说明](docs/development.md#选择验证范围)。依赖变更有意更新锁文件时，在 PR 中解释原因；普通开发使用现有锁文件。

## 保持测试可重复与数据私密

默认 Python 测试隔离真实凭据和运行数据，并阻止外部网络、模型加载与实体录音设备访问。新增测试优先复用 `tests/conftest.py` 的 fixture、临时目录和 provider double，在外部服务边界模拟成功、超时或失败。

浏览器 fixture 测试会拦截 API；真实 HTTP / SQLite 测试仍可使用受控提供商。两者都不能证明真实服务可用。真实服务脚本可能下载模型、访问外网并产生费用，按开发指南单独执行和报告；合成语音、手机尺寸视口也不能替代真人发音或实体手机验收。

示例、测试和公开附件请使用合成数据：

- 用虚构练习文字，例如 `I went hiking last weekend.`，避免复制用户的真实对话、识别文本和学习记录
- 用生成的测试音频或项目 fixture；需要真人录音才能复现时，先在 Issue 中说明条件，避免直接公开原音
- 用明确的假凭据，例如 `test_api_key`；不要提交 `.env`、API Key、AccessKey、cookie、浏览器存储状态或证书私钥
- 提交截图、日志和导出文件前检查账号信息、私有路径与请求头；SQLite 数据库、备份和浏览器验收目录可能包含文字、音频或身份信息

## 提交信息与代码风格

提交信息建议采用简短的 Conventional Commits 格式，说明实际变化即可。常用前缀有 `feat`、`fix`、`docs`、`test`、`refactor`、`perf` 和 `ci`：

```text
fix: preserve transcript edits after a failed request
docs: clarify offline test prerequisites
```

Python 的 Black、isort 和 mypy 配置位于 `pyproject.toml`；前端检查由 `frontend/package.json` 定义。沿用所修改模块的风格，避免在功能 PR 中夹带无关的全仓格式化；新增公共接口时补充必要的类型和行为说明。

## 报告问题或提出功能建议

普通问题请使用 [GitHub Issues](https://github.com/likebeans/ChatterPal/issues) 中对应的模板，提供以下信息：

- 版本或 commit、操作系统、Python / uv 版本；前端问题补充 Node.js 和浏览器版本
- 使用的入口与相关配置名称，例如 React / Gradio、ASR / LLM / TTS 提供商名称，不附凭据值
- 最小复现步骤、执行命令、预期行为与实际结果
- 脱敏后的错误信息或截图，以及是否能用合成文字、测试音频和受控提供商复现

功能建议请描述要完成的任务、当前障碍和期望结果；有界面或接口变化时给出示例。安全漏洞和凭据泄露请按 [SECURITY.md](SECURITY.md) 的方式处理，避免在公开 Issue 中发布敏感信息。
