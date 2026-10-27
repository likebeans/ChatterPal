# 安全政策

## 报告漏洞

如果问题涉及未授权访问学习记录、身份隔离、私有录音、凭据泄露或代码执行，请通过 GitHub 的 [Report a vulnerability](https://github.com/likebeans/ChatterPal/security/advisories/new) 私下报告。

报告中请提供：

- 受影响的提交或版本、部署方式和运行环境。
- 问题的影响范围与最小复现步骤。
- 使用合成数据的示例，以及可行的临时缓解办法。

请勿在公开 Issue、PR、Actions 日志或附件中提交真实 API Key、cookie、私钥、数据库、个人录音或其他人的学习记录。普通功能错误与使用问题请通过 [Issues](https://github.com/likebeans/ChatterPal/issues/new/choose) 或 [Discussions](https://github.com/likebeans/ChatterPal/discussions) 反馈。

项目由开源维护者维护，没有承诺固定响应时限。安全修复和披露方式会结合影响范围与报告者协商。

## 维护范围

当前维护对象为 `main` 分支的最新代码。项目处于 Alpha 阶段，暂不承诺为历史提交或旧版本提供独立安全回补。部署者应关注仓库更新，结合自己的数据保留和回退计划升级。

## 部署者注意事项

- 将 `.env`、提供商密钥、TLS 私钥、SQLite 与备份保存在公开静态目录和 Git 仓库之外。
- 公网或跨设备部署应使用 HTTPS；HTTPS 下启用 `LEARNING_COOKIE_SECURE=true`，并限制 `LEARNING_ALLOWED_ORIGINS`。
- 当前浏览器匿名身份用于访问学习记录，不能替代公网服务的访问控制、用户账号或配额管理。
- 按[部署指南](docs/deployment.md)配置访问控制、持久卷和备份。当前运行范围是单 API 实例与本地 SQLite。

应用会按所选 ASR、LLM 和 TTS 提供商发送必要的音频或文字。数据保留与第三方处理范围见 [README](README.md#数据与隐私)。
