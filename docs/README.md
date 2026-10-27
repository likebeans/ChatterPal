# ChatterPal 文档

React「今日练习」是任务学习主入口，FastAPI 提供业务接口，Gradio 保留独立的对话与分析界面。当前功能和运行边界以[项目首页](../README.md)、接口契约和对应版本的验收记录为准。

## 从这里开始

| 需要 | 文档 |
| --- | --- |
| 了解产品、快速运行 | [项目首页](../README.md) |
| 安装与提供商配置、Docker 部署 | [部署指南](deployment.md) |
| 参与贡献与提交反馈 | [贡献指南](../CONTRIBUTING.md) |
| 开发环境、测试与 CI | [开发指南](development.md) |
| React 页面、录音和草稿恢复 | [前端说明](../frontend/README.md) |
| 局域网 HTTPS、持久数据与回退 | [macOS 试用环境](trial_environment.md) |
| 报告安全漏洞 | [安全政策](../SECURITY.md) |

## 接口与数据契约

- [学习 API 契约](learning_api_contract.md)：浏览器身份、任务、录音核对、反馈、重练、复习和异步操作。
- [SQLite 学习存储契约](learning_store_contract.md)：事务、版本、私有资产及删除语义。
- [通用 API 参考](api.md)：自由对话、音色和表达分析接口。
- [任务内容说明](../src/chatterpal/learning/content/README.md)：人工情境、内容版本与迁移问题。

## 验证记录

验收结果对应文档中注明的提交、输入与环境，不代表所有平台、真人学习效果或生产负载均已验证。最新提交的自动检查请查看 [GitHub Actions](https://github.com/likebeans/ChatterPal/actions)。

| 记录 | 覆盖范围 |
| --- | --- |
| [最终功能验收 · 2026-10-26](final_acceptance_2026-10-26.md) | 三项任务、六道迁移题、录音核对、多标签页恢复、工程回归与合并后部署收尾。 |
| [局域网环境验收 · 2026-10-26](trial_environment_acceptance_2026-10-26.md) | HTTPS、进程管理、持久数据、备份与合成语音提供商流程。 |
| [Gradio 与即时建议收尾 · 2026-10-26](closeout_acceptance_2026-10-26.md) | 页面接线、建议校验、音频边界与当轮回归。 |
| [录音核对与 Gradio · 2026-10-26](live_transcript_gradio_acceptance_2026-10-26.md) | 真实提供商、修改确认、原音复用与草稿恢复的历史记录。 |
| [CI 与转写核对 · 2026-10-26](transcript_confirmation_acceptance_2026-10-26.md) | 配置、打包、离线 CI 与三入口转写核对。 |
| [运行与复习优化 · 2026-10-26](remaining_optimizations_acceptance_2026-10-26.md) | runtime、复习排程与较早版本的提供商验收。 |
| [最初任务流程验收](int2_weekend_acceptance.md) | 周末任务的早期离线、HTTP 和浏览器验收。 |
| [真实提供商验收 · 2026-10-25](int2_live_provider_acceptance_2026-10-25.md) | 合成输入下的周末对话、反馈、重练与恢复。 |

## 历史设计与计划

以下文档保留早期设计、实现调查与开发过程，可能包含尚未实现或后来调整的设想；能力说明以当前代码和上方契约为准。

- [项目架构](project_architecture.md)
- [聊天模块使用说明](chat_module_user_guide.md)
- [纠错系统概述](correction_system_overview.md)
- [发音纠错设计](pronunciation_correction_design.md)
- [技术实现总结](technical_implementation_summary.md)
- [优化计划](optimization_plan.md)
- [多 worktree 工作计划](worktree_optimization_plan.md)
- [第一轮集成记录](integration_round_1.md)
- [集成恢复记录](integration_recovery.md)

## 帮助与文档改进

先搜索 [Issues](https://github.com/likebeans/ChatterPal/issues)；使用交流可到 [Discussions](https://github.com/likebeans/ChatterPal/discussions)。发现过期命令或文档与行为不一致，欢迎按[贡献指南](../CONTRIBUTING.md)提交最小修正。
