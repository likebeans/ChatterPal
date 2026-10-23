# ChatterPal 前端开发与 B 任务验收

本 worktree 对应 `fix/frontend-audio-state`。修改限于 `frontend/`，保持现有 HTTP 字段和页面布局。聊天与发音分析使用共用的录音、播放、对象 URL 和请求生命周期逻辑。

关键实现位于 `src/hooks/useAudioRecording.ts`、`useAudioPlayback.ts`、`useLatestRequest.ts` 和 `useObjectUrls.ts`；`src/audio/` 负责实际重采样、PCM16 WAV 编码及可取消 Worker；`src/api/` 明确现有分析响应字段与失败显示。两页保留组件状态，隐藏时停止录音与播放；分析请求随输入和页面变化失效，已提交聊天在切页后保留直至完成，清空和卸载仍取消。

## 开发与回归

在本目录执行：

```bash
npm ci
npm test
npm run build
npm run lint
npm run dev -- --host 127.0.0.1 --port 3012 --strictPort
```

开发服务器仍把 `/api` 代理到 `http://localhost:8010`。浏览器自动验收会按冻结契约替换 API 响应，无需运行 Python、配置密钥或下载 ASR 模型；真实后端联调应在 A+B 集成后另行执行。

## 浏览器录音验收

开发服务器启动后，在另一个终端执行：

```bash
npx playwright install chromium
npm run test:browser -- http://127.0.0.1:3012
```

也可以使用本机现有 Chrome，避免额外下载浏览器：

```bash
CHROME_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
  npm run test:browser -- http://127.0.0.1:3012
```

`BASE_URL` 可以指定页面地址；命令行地址优先。`BROWSER_OUTPUT_DIR` 可指定截图、上传 WAV 和 `results.json` 的输出目录；默认创建独立临时目录，并在命令输出中打印其路径。`PLAYWRIGHT_MODULE_PATH` 仅用于复用已有 Playwright 安装，通常无需设置。

浏览器使用 Chromium 的测试麦克风设备，实际执行 `getUserMedia`、Web Audio 采集、录音按钮键盘操作、音频播放与 multipart 上传。测试强制一路真实采集采用 48 kHz，再验证输出是单声道 PCM16 16 kHz WAV，并用 `decodeAudioData` 解码、核对采集和输出时长。上传文件也会保存，方便独立检查：

```bash
ffprobe -v error \
  -show_entries stream=codec_name,sample_rate,channels,bits_per_sample:format=duration \
  -of json /path/to/output/upload-1.wav
```

其余自动流程覆盖：

- 录音中切页后流和 AudioContext 释放，返回后保留聊天与已完成分析。
- 清空后的晚响应不恢复消息或 session；新聊天请求不携带旧 session。已提交的聊天在切页后继续完成，返回时可见回复，避免与服务端会话历史脱节。
- 超过 1 秒的录音在可取消的 Worker 内转换；处理结束、取消、切页和卸载均终止 Worker。60 秒 PCM 段落用采集边界注入验证停止与切页仍可交互。
- 场景列表和播放失败后重试成功，清除对应的旧错误提示。
- 重录使旧分析失效；场景 A 的晚响应不覆盖场景 B。
- 录音 URL 直接回放，清空或重录时撤销对象 URL。
- 权限晚返回和录音初始化失败后的清理，再次录音能够恢复。
- 基础与 AI 分析使用当前字段展示真实转写、建议及“评分尚未校准”说明。
- 375px 与横屏视口无横向溢出，减少动态效果模式可用，发音页录音切页后释放资源，浏览器无未捕获异常。

## 本轮验证记录

2026-10-23，基于 `a90b85c` 的 B worktree：

- 原始页面的 10 项回归测试均复现失败；修复后扩展为 58 项，5 个测试文件全部通过。
- `npm ci --no-audit --no-fund` 干净安装通过；`npm run build`、`npm run lint`、浏览器 `.cjs` 脚本的 ESLint 与 `git diff --check` 通过。
- 对最终生产构建运行 4 组 Chromium 浏览器验收通过，使用测试麦克风与契约 API 响应，无未捕获页面异常。
- 独立审查确认的会话同步、长段落阻塞、场景恢复提示和播放恢复提示四项问题已修复并加入回归。60 秒 PCM 输入的停止点击在开发与生产构建中实测约 0.8–1.1ms，转换期间切页能终止 Worker；该样本在采集边界注入，不代表等待了 60 秒真人录音。
- 强制 48 kHz 采集后的上传文件经浏览器解码、ffprobe 检查和 ffmpeg 解码，确认为 `pcm_s16le`、16 kHz、单声道、16 bit，采集与输出时长一致。
- 已查看桌面与移动截图，保留聊天 800px、分析 900px 的原有最大宽度；375px 与横屏视口无横向溢出。

## 手工复查

1. 打开对话练习，使用 Tab、Enter 或 Space 开始和停止录音；发送完成后播放用户录音。
2. 再次开始录音，直接切到口语纠正，确认浏览器麦克风指示停止；返回对话练习，历史仍在。
3. 发出请求后清空，再发新消息；旧请求完成后不应插入回复，也不应恢复旧会话。
4. 在口语纠正输入参考文本，录音、停止、回放，分别执行基础和 AI 分析；切页往返后保留结果。
5. 慢分析期间重录，或在两个场景之间快速切换；旧结果不能覆盖新选择。请求失败后可以重试。
6. 等待麦克风授权时切页或取消，随后允许权限；不应留下活动流，也不应自动上传。

测试麦克风不代表已验证实体麦克风、真人发音识别质量或所有移动浏览器。真实服务端 ASR/LLM/TTS 和分析服务可用性仍需集成验收。现阶段分数保留接口数值并明确标示尚未校准，不代表完成了评分可信度任务 G。

本 worktree 从 `a90b85c` 创建，与先前已创建的 A worktree 一致；创建时主目录的聊天后端优化尚未提交，未被复制或改动。集成人仍需保存并保留那批后端基线改动，再执行 A+B 的真实接口联调。
