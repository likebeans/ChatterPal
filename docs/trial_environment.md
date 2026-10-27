# 局域网试用环境

此环境供同一局域网内的电脑和手机使用。学习主入口是 React「今日练习」，脚本固定的目标应用版本为 `173f9f3fe17555f63c14fa6cb18705516b2db49e`。实际运行版本以 `status` 输出为准；脚本同时支持管理和回退此前的 `a253d92` 冻结版本。试用环境脚本与应用版本分别管理，日常编辑工作树不改变运行中的应用源码。

## 运行方式

使用 macOS 原生 Python、静态前端和 Caddy HTTPS 代理。API 仅监听 `127.0.0.1:18300`；HTTPS 页面和同源 `/api/` 由端口 `8443` 提供。HTTPS 与证书安装服务只绑定指定局域网 IPv4 和本机回环地址，不绑定公网 IPv6。证书安装说明与公共根证书使用 HTTP `18080`，不在 HTTP 入口提供学习页面或数据库。Caddy 管理接口仅在 `127.0.0.1:12019`。

用户级 launchd 分别管理 API、HTTPS 和防休眠进程，关闭当前终端后继续运行。Mac 需要保持登录、接通电源、开盖，并与试用手机保持同一网络；防休眠不能使关机或合盖设备继续提供服务。试用不依赖 Vite 开发服务器或 Claude 会话。

状态根目录默认为 `~/Library/Application Support/ChatterPal/trial/`：

- `release/`：固定应用源码与静态前端；后续开发不直接修改这里。
- `data/`：独立 SQLite 学习数据，重启服务不会清空。
- `backups/`：SQLite 在线备份；可在服务运行时创建。
- `logs/`：本机进程日志。
- `caddy-data/`：本机 CA 和证书私钥，只留本机。
- `public/`：仅用于分发公共根证书与安装说明。

状态文件权限为当前 macOS 用户私有。提供商密钥继续从已有私有 dotenv 文件读取，脚本和版本清单只保存路径，不保存密钥内容。API 使用 Secure、HttpOnly 学习 cookie。原音保留仍由参与者在学习设置中明确选择，默认临时回放 1 小时，未替参与者开启 7 天保留。

应用源码和前端构建已冻结；Python 依赖继续使用准备时指定的现有 venv。清单记录解释器、依赖包版本与 Caddy 版本/摘要，但不会复制整个 venv。两天试用期间请保留这个 venv、原配置文件与 Caddy 路径，避免升级或删除这些依赖。用户级 launchd 任务需登录后手动执行 `start`；当前配置保证同一次登录中关闭终端仍运行，退出登录或重启 Mac 后应重新启动并检查状态。

## 首次准备

需要已安装完整项目依赖的 Python 3.12、Node.js、Caddy，以及原有私有提供商配置。本机已有配置可复用，不需要复制到新工作树。安装 Caddy 的命令为 `brew install caddy`，不使用全局 `brew services`。

先在工作树根目录构建静态前端：

```bash
npm --prefix frontend ci
npm --prefix frontend run build
```

然后准备冻结版本。以下选项放在 `prepare` 后：

```bash
/path/to/python scripts/trial_environment.py prepare \
  --python /path/to/installed/python \
  --env-file /path/to/original/private.env \
  --caddy /opt/homebrew/bin/caddy \
  --hostname your-mac.local \
  --lan-ip 192.168.1.100 \
  --frontend-dist frontend/dist

/path/to/python scripts/trial_environment.py start
/path/to/python scripts/trial_environment.py status
```

`--state-dir` 可放在子命令前或后；`start` 和 `restart-api` 可使用 `--timeout 120` 调整就绪等待，范围为 1–600 秒。脚本保留原私有配置中的提供商超时，未配置时才使用 60 秒。固定 `.local` 主机名优先用于两天试用，避免 DHCP 改变 IP 时换域名丢失浏览器身份；不能解析 `.local` 的设备可固定使用 IP 入口。两个入口的浏览器 cookie 不共享，参与者应始终使用第一次练习的同一入口和同一浏览器。

## 手机首次连接

本环境使用本机生成的试用 CA，不自动安装系统根证书或修改防火墙。仅下载根证书还不够，手机需要在系统设置中安装并信任该证书，HTTPS 页面才可正常使用麦克风。

1. 将手机与 Mac 连到同一网络，打开 `http://Mac局域网IP:18080/`，下载公共根证书。
2. 对照本机验收记录中的 SHA256 指纹，再安装证书。
3. iPhone/iPad：在「设置 → 通用 → VPN 与设备管理」中安装已下载描述文件，然后在「设置 → 通用 → 关于本机 → 证书信任设置」中，为本次试用根证书启用完全信任。
4. Android：在「设置 → 安全/隐私 → 更多安全设置 → 加密与凭据 → 安装证书 → CA 证书」安装；名称随系统版本不同。若 Chrome 仍不信任，使用该设备能信任的固定入口排查，不能跳过错误后声称麦克风已可用。
5. 打开固定的 `https://Mac主机名:8443/` 或 `https://Mac局域网IP:8443/`，选择「今日练习」，允许麦克风。

Android 部分浏览器不能解析 `.local`，部分访客 Wi-Fi/手机热点不允许设备间互访；两种情况均需在该手机实际验证。安装成功的判据是没有证书错误，并且「开始录音 → 停止录音 → 试听 → 识别并核对录音」可完成。仅手机尺寸截图通过不代表实体手机通过。

试用结束后，可在系统证书设置中移除本次试用 CA。不要分享或安装 `root.key`；任何安装入口只能分发 `root.crt`。

## 两天试用前的核查

首日用真实手机录一段话，核对识别结果并明确确认，实际播放教练回复，结束后查看反馈，并保存一个表达或尝试到复习。重新打开同一入口，确认历史仍可读取。当前匿名身份没有跨设备账号找回功能，试用期间不要清除浏览器站点数据或使用无痕窗口。

复习默认在保存后约 24 小时到期。次日使用同一设备、浏览器和入口返回「到期复习」；本环境使用真实时钟，不为了让复习提前出现而修改系统时间、数据库或业务时钟。

试用组织者应在首日结束与次日开始前各执行一次备份，并检查 `status`。两天试用的真人录音、返回行为和实体设备权限仍需参与者实际完成；自动验收使用合成输入时会明确标注。

## 管理命令

在试用工作树执行；若使用其他状态目录，将 `--state-dir /path/to/state` 放在子命令前：

```bash
/path/to/python scripts/trial_environment.py status
/path/to/python scripts/trial_environment.py backup
/path/to/python scripts/trial_environment.py restart-api
/path/to/python scripts/trial_environment.py stop
/path/to/python scripts/trial_environment.py start
```

停止只卸载本环境进程，不删除学习记录、备份或证书。重启 API 保留 SQLite 和 cookie 身份。备份为 SQLite backup API 的一致性快照，包含当时仍保存的私有音频字节；备份需与学习数据一样保留在本机私有目录，不上传到代码仓库。

若 Mac 的 IP 改变，应先结束当次录音并备份，更新网络配置和证书，再使用原来的固定主机名验证；不要直接删除状态目录重建。不要在有人使用时替换冻结版本。

## 升级既有试用环境

`prepare` 继续拒绝覆盖已有环境。`upgrade` 仅支持将 `a253d92` 升至上述已验收版本，且检查应用 `src/` 与 `uv.lock` 不变；涉及其他后端或数据库结构变更时需要单独评估。先确认所有录音和提交已结束，再在目标提交的干净前端源码上构建：

```bash
npm --prefix frontend run build
/path/to/python scripts/trial_environment.py backup
/path/to/python scripts/trial_environment.py stop
/path/to/python scripts/trial_environment.py upgrade --frontend-dist frontend/dist
/path/to/python scripts/trial_environment.py start
/path/to/python scripts/trial_environment.py status
```

升级要求本环境的三个 launchd 任务均已停止，并在切换前再次创建 SQLite 一致性备份。源码和前端复制到新的 `release/173f9f3/`，旧发布目录保留；启动配置和版本清单的前后快照保存在本机私有 `upgrades/` 目录。数据库、原提供商配置路径、CA、访问地址和学习身份继续使用原值。应在启动后用原入口、原浏览器检查历史并实际录音核对。

若新版本启动或验收失败，使用升级输出的 `rollback_snapshot` 路径恢复配置，再启动：

```bash
/path/to/python scripts/trial_environment.py stop
/path/to/python scripts/trial_environment.py rollback --snapshot /path/to/state/upgrades/TIMESTAMP/previous
/path/to/python scripts/trial_environment.py start
/path/to/python scripts/trial_environment.py status
```

回退只恢复应用版本与启动配置，保留升级后新增的学习记录，不自动覆盖数据库。升级记录中的 SQLite 备份供需要时另行恢复。中断切换后的配置也可通过保存的快照恢复；不要删除状态目录。
