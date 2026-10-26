# ChatterPal 项目文档

本目录包含 ChatterPal 项目的完整文档。

## 当前入口与验收

- [当前功能与运行边界](../README.md) — React + FastAPI 的“今日练习”为任务学习主入口；Gradio 为独立聊天与分析入口。
- [2026-10-26 开源前最终验收](final_acceptance_2026-10-26.md) — 三项任务、六道迁移题、录音恢复修复与最终检查结果。
- [局域网试用环境](trial_environment.md) — 固定版本、手机 HTTPS 证书、持久数据、启停与备份。
- [2026-10-26 试用环境验收](trial_environment_acceptance_2026-10-26.md) — 实际局域网入口、证书指纹、真实提供商浏览器验收与停启/备份结果。
- [2026-10-26 收尾验收](closeout_acceptance_2026-10-26.md) — 当前补丁范围、即时纠正校验、实际页面复验与可追溯结果。
- [录音核对与 Gradio 验收记录](live_transcript_gradio_acceptance_2026-10-26.md) — 早前真实提供商运行、合成语音与设备边界。
- [学习 API 契约](learning_api_contract.md) — 已接通的身份、任务、反馈、重练、复习和操作语义。

下方的 2026-10-23 优化计划保留历史调查与分工，后续工作以当前功能和最新验收为准。

## 文档结构

### 📚 用户文档
- [`api.md`](api.md) - API 接口文档和参考手册
- [`deployment.md`](deployment.md) - 部署和安装指南
- [`chat_module_user_guide.md`](chat_module_user_guide.md) - 聊天模块使用指南

### 🛠️ 开发文档
- [`project_architecture.md`](project_architecture.md) - 完整的项目架构设计和技术栈说明
- [`development.md`](development.md) - 开发环境搭建和贡献指南
- [`optimization_plan.md`](optimization_plan.md) - 基于实际代码的现状梳理、性能基线和分批优化计划
- [`worktree_optimization_plan.md`](worktree_optimization_plan.md) - 多 worktree 任务分工、文件归属、验收标准与集成顺序
- [`integration_round_1.md`](integration_round_1.md) - 聊天基线与 A–E 首次集成验收的历史结果和失败清单
- [`integration_recovery.md`](integration_recovery.md) - 36 项失败修复、覆盖率恢复与重新联合验收
- [`live_transcript_gradio_acceptance_2026-10-26.md`](live_transcript_gradio_acceptance_2026-10-26.md) - 录音核对三入口的真实提供商验收与 Gradio 主页面接线
- [`technical_implementation_summary.md`](technical_implementation_summary.md) - 技术实现总结

### 🔬 技术文档
- [`correction_system_overview.md`](correction_system_overview.md) - 发音纠错系统技术总结
- [`pronunciation_correction_design.md`](pronunciation_correction_design.md) - 英语发音纠错系统设计方案

## 快速导航

### 🚀 新用户
1. 阅读 [主README](../README.md) 了解项目概况
2. 查看 [部署指南](deployment.md) 进行安装配置
3. 参考 [API文档](api.md) 了解功能使用

### 👨‍💻 开发者
1. 阅读 [开发文档](development.md) 搭建开发环境
2. 了解 [项目架构](project_architecture.md) 掌握整体设计
3. 查看 [系统设计](pronunciation_correction_design.md) 
4. 查看 [技术实现总结](technical_implementation_summary.md) 了解核心技术
5. 参考 [发音纠错系统](correction_system_overview.md) 深入理解算法

### 🔧 运维人员
1. 参考 [部署指南](deployment.md) 进行生产环境部署
2. 查看 [API文档](api.md) 了解系统接口
3. 阅读 [技术实现总结](technical_implementation_summary.md) 了解系统架构
4. 参考 [开发文档](development.md) 了解故障排查

## 文档维护

### 更新原则
- 保持文档与代码同步
- 使用清晰的中文描述
- 提供完整的代码示例
- 包含常见问题解答

### 贡献方式
- 发现文档错误请提交 Issue
- 改进建议请提交 Pull Request
- 新功能需要同步更新相关文档

## 获取帮助

如果在使用过程中遇到问题：

1. 首先查看相关文档
2. 搜索 [GitHub Issues](https://github.com/likebeans/ChatterPal/issues)
3. 查看相关文档
4. 参与 [讨论区](https://github.com/likebeans/ChatterPal/discussions) 交流