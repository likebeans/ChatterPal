# ChatterPal

<div align="center">

**🎯 AI-Powered English Pronunciation Practice System**

*一个基于人工智能的英语口语练习和发音纠错系统*

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Gradio](https://img.shields.io/badge/Interface-Gradio-orange.svg)](https://gradio.app)
[![React](https://img.shields.io/badge/Frontend-React-61dafb.svg)](https://reactjs.org)

</div>

## 🌟 项目简介

**ChatterPal** 是一个基于人工智能的智能口语练习与发音纠错系统，旨在为语言学习者提供个性化、专业化的口语训练体验。通过集成先进的语音识别、语音合成和大语言模型技术，为用户打造沉浸式的语言学习环境。

## 🎯 核心理念

ChatterPal 基于以下核心理念设计：

- **AI驱动的个性化学习**：基于用户的发音特点和学习进度，提供定制化的练习内容
- **多维度评估体系**：从发音准确性、流利度、韵律等多个维度进行综合评估
- **实时反馈机制**：即时的发音纠错和改进建议，加速学习进程
- **沉浸式对话体验**：与AI导师进行自然对话，模拟真实语言环境

## 🚀 三大核心功能

### 1. 智能对话练习
ChatterPal 提供基于AI的智能对话练习功能：

#### 🎯 多轮对话系统
- **上下文记忆**: AI导师能够记住整个对话历史，保持话题连贯性
- **智能话题转换**: 根据对话流程自然地引导话题发展
- **个性化响应**: 基于用户的语言水平和兴趣调整回复内容
- **情境感知**: 理解对话背景，提供符合场景的回应

#### 💬 现代化聊天界面
- **实时文本对话**: 支持打字输入，即时获得AI回复
- **语音输入支持**: 点击录音按钮，直接语音与AI对话
- **消息历史**: 完整保存对话记录，支持回顾和复习
- **多音色选择**: 可选择不同的AI语音音色进行对话

#### 🔊 智能语音回复
- **自然语音合成**: 使用先进的TTS技术，生成自然流畅的语音回复
- **语音播放控制**: 支持播放、暂停、重播语音回复
- **语速调节**: 根据学习需要调整AI回复的语速
- **音色个性化**: 支持多种音色选择，包括男声、女声等不同风格

#### 🎨 智能主题生成
- **场景覆盖**: 涵盖日常生活、商务交流、学术讨论、旅游出行等多种场景
- **难度自适应**: 根据用户水平自动调整对话难度和词汇复杂度
- **兴趣导向**: 基于用户偏好生成个性化话题
- **学习目标**: 针对特定学习目标（如商务英语、雅思口语等）定制对话内容

### 2. 发音评估打分
ChatterPal 集成专业的发音评估系统：

- 专业级别的发音质量评估
- 流利度和语速分析
- 音素级别的准确性检测
- 韵律特征评价（重音、语调、节奏）

### 3. 发音纠错指导
ChatterPal 提供智能化的发音纠错服务：

#### 🎭 场景选择系统
- **多样化场景**: 提供商务会议、日常对话、学术演讲、面试准备等多种练习场景
- **难度分级**: 每个场景分为初级、中级、高级三个难度等级
- **情境模拟**: 真实场景模拟，提供相应的背景描述和对话提示
- **个性化推荐**: 基于用户水平和学习目标推荐最适合的练习场景

#### 🔬 发音纠错底层原理

##### 语音识别与分析
- **多引擎融合**: 结合OpenAI Whisper和阿里云ASR，确保识别准确性
- **音素级分析**: 将语音分解到最小音素单位进行精确分析
- **时间对齐**: 精确定位每个音素在时间轴上的位置
- **特征提取**: 提取音高、音强、音长等关键语音特征

##### 发音质量评估算法
- **音素准确性**: 基于IPA国际音标标准，评估每个音素的发音准确度
- **韵律分析**: 评估重音、语调、节奏等韵律特征
- **流利度计算**: 分析语速、停顿、连读等流利度指标
- **置信度评分**: 为每个评估结果提供可信度分数

##### 智能纠错机制
- **错误检测**: 自动识别发音错误的具体位置和类型
- **原因分析**: 分析发音错误的可能原因（如母语干扰、发音习惯等）
- **改进建议**: 提供具体的发音改进方法和练习建议
- **进度跟踪**: 记录用户的发音改进历程，提供个性化反馈

#### 🎯 核心功能详解

##### 实时发音评分
- **即时反馈**: 录音结束后立即给出发音评分和详细分析
- **多维度评估**: 从准确性、流利度、韵律等多个维度综合评分
- **可视化展示**: 通过图表和色彩编码直观展示发音质量
- **历史对比**: 对比历次练习结果，展示进步趋势

##### 音素级纠错
- **精确定位**: 准确标出发音错误的具体音素位置
- **IPA标注**: 提供标准IPA音标对比，显示正确发音
- **发音示范**: 播放标准发音示例，供用户模仿学习
- **练习建议**: 针对特定音素提供专门的练习方法

##### 韵律优化指导
- **重音分析**: 检测词汇和句子重音的准确性
- **语调评估**: 分析升调、降调等语调模式的使用
- **节奏优化**: 评估语音节奏的自然性和流畅性
- **情感表达**: 分析语音中的情感色彩和表达效果

##### 个性化学习路径
- **弱点识别**: 自动识别用户的发音薄弱环节
- **定制练习**: 根据个人特点生成针对性练习内容
- **进度追踪**: 实时跟踪学习进度和改进效果
- **智能推荐**: 推荐最适合的练习材料和学习策略

## 快速开始

### 环境要求

- Python **3.12+**；推荐使用 `.python-version` 指定的 **3.12**，与 `pyproject.toml` 保持一致
- [uv](https://docs.astral.sh/uv/getting-started/installation/)、Git；运行 React 前端还需 Node.js 20+ 和 npm
- 4GB+ RAM，以及下载 Whisper 模型和访问 API 服务所需的网络连接
- 原生音频依赖：FFmpeg 和 PortAudio。macOS 使用 `brew install ffmpeg portaudio`；Ubuntu/Debian 使用 `sudo apt-get install build-essential portaudio19-dev ffmpeg libsndfile1`。Windows 请安装 FFmpeg 并加入 PATH，使用 64 位 Python 3.12 以便安装 PyAudio wheel；若仍需源码编译，请参考[部署指南](docs/deployment.md#原生依赖与安装失败排查)

也可以使用下方的 Docker Compose 部署，由镜像安装 Linux 原生依赖。

### 安装步骤

#### 1. 克隆项目并安装依赖

以下命令在 macOS、Linux 和 Windows PowerShell 中均可使用，不需要手动激活虚拟环境：

```bash
git clone https://github.com/likebeans/ChatterPal.git
cd ChatterPal
uv python install 3.12
uv sync --locked --python 3.12
```

#### 2. 配置环境变量

macOS / Linux：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，填入 `DASHSCOPE_API_KEY`。默认组合是 Whisper ASR、Edge TTS 和阿里百炼 LLM，仅需百炼 API Key；无需设置阿里云 ASR 的 AccessKey Secret。模板是环境变量的起点，字段含义、覆盖顺序和高级聊天配置见[部署指南](docs/deployment.md#配置契约)。

#### 3. 启动后端与前端

在仓库根目录启动后端：

```bash
uv run python scripts/run_api.py
```

另开终端，在仓库根目录启动 React 前端：

```bash
cd frontend
npm ci
npm run dev
```

访问 http://localhost:3010；后端 API 地址为 http://localhost:8010，API 文档为 http://localhost:8010/docs，健康检查为 http://localhost:8010/health。

如需传统 Gradio 界面，可在仓库根目录单独启动：

```bash
uv run python -m chatterpal.web.app
```

Gradio 地址为 http://localhost:7860。首次使用本地 Whisper 会下载所选模型。

## 核心功能

## 🏗️ 技术架构

### 核心技术栈

#### 🎤 语音处理引擎
- **ASR (语音识别)**
  - 🔹 **OpenAI Whisper**: 本地高精度语音识别，支持多种模型规模
  - 🔹 **阿里云语音识别**: 云端实时语音识别，低延迟高准确率
  - 🔹 **策略模式设计**: 可根据场景动态切换识别引擎

- **TTS (语音合成)**
  - 🔹 **Microsoft Edge TTS**: 高质量多语音合成，支持多种音色
  - 🔹 **阿里百炼 TTS**: 专业级语音合成，自然流畅
  - 🔹 **流式音频生成**: 实时音频流处理，降低延迟

#### 🧠 AI 大模型集成
- **🔹 阿里云通义千问**: 强大的中文理解和生成能力
- **🔹 OpenAI GPT**: 先进的对话和推理能力
- **🔹 统一LLM接口**: 支持多模型无缝切换
- **🔹 上下文管理**: 智能对话历史和会话状态维护

#### 🎯 专业发音评估
- **音素级分析**: 基于IPA音标的精确发音检测
- **韵律评估**: 重音、语调、节奏的综合分析
- **流利度评价**: 语速、停顿、连贯性评估
- **个性化反馈**: 基于用户特点的定制化建议

### 🔧 技术特色

- **🏗️ 模块化架构**: 松耦合设计，支持依赖注入和插件化扩展
- **⚡ 实时音频处理**: 低延迟音频流处理和分析
- **🔍 专业音素分析**: 基于语音学原理的精确发音纠错
- **🌐 多引擎支持**: 灵活的服务提供商切换机制
- **📱 现代化界面**: Gradio + React 双端支持
- **🛡️ 类型安全**: 基于Pydantic的配置管理和数据验证
- **🧪 完整测试**: 单元测试、集成测试、端到端测试覆盖

## 📁 项目架构

### 整体架构设计

```
ChatterPal/
├── 🎯 src/chatterpal/           # 核心应用代码
│   ├── 🔧 config/                   # 配置管理层
│   │   ├── settings.py             # 统一配置管理 (Pydantic)
│   │   └── loader.py               # 组件工厂和加载器
│   ├── 🧠 core/                     # 核心功能模块
│   │   ├── asr/                    # 语音识别引擎
│   │   │   ├── base.py             # ASR基类和接口
│   │   │   ├── whisper.py          # OpenAI Whisper实现
│   │   │   └── aliyun.py           # 阿里云ASR实现
│   │   ├── tts/                    # 语音合成引擎
│   │   │   ├── base.py             # TTS基类和接口
│   │   │   ├── edge.py             # Edge TTS实现
│   │   │   └── alibaba.py          # 阿里百炼TTS实现
│   │   ├── llm/                    # 大语言模型接口
│   │   │   ├── base.py             # LLM基类和对话管理
│   │   │   ├── alibaba.py          # 阿里百炼LLM实现
│   │   │   └── openai.py           # OpenAI GPT实现
│   │   └── assessment/             # 发音评估模块
│   │       ├── base.py             # 评估基类和数据结构
│   │       ├── corrector.py        # 发音纠错器
│   │       ├── phoneme.py          # 音素分析
│   │       └── prosody.py          # 韵律分析
│   ├── 🎪 services/                 # 业务服务层
│   │   ├── chat.py                 # 对话服务 (ChatService)
│   │   ├── evaluation.py           # 评估服务 (EvaluationService)
│   │   ├── correction.py           # 纠错服务 (CorrectionService)
│   │   ├── ai_correction.py        # AI驱动纠错
│   │   └── topic_generator.py      # 智能主题生成
│   ├── 🌐 web/                      # Web界面层
│   │   ├── app.py                  # 主Gradio应用
│   │   ├── api_server.py           # API服务器
│   │   └── components/             # UI组件
│   └── 🛠️ utils/                    # 工具模块
│       ├── audio.py                # 音频处理工具
│       ├── encoding_fix.py         # 编码修复
│       ├── logger.py               # 日志管理
│       └── preferences.py          # 用户偏好管理
├── 🎨 frontend/                     # React前端
│   ├── src/
│   │   ├── components/             # React组件
│   │   ├── App.tsx                 # 主应用组件
│   │   └── main.tsx                # 应用入口
│   ├── package.json                # 前端依赖配置
│   └── vite.config.ts              # Vite构建配置
├── 🧪 tests/                        # 测试套件
│   ├── test_services.py            # 服务层测试
│   ├── test_core_functionality.py  # 核心功能测试
│   └── test_integration.py         # 集成测试
├── 📜 scripts/                      # 脚本工具
│   ├── run.py                      # 启动脚本
│   ├── setup.py                    # 安装脚本
│   └── verify_deployment.py        # 部署验证
├── 📚 docs/                         # 项目文档
│   ├── project_architecture.md     # 架构文档
│   ├── api.md                      # API文档
│   ├── deployment.md               # 部署指南
│   └── development.md              # 开发文档
├── 📊 data/                         # 数据目录
│   ├── audio/                      # 音频文件
│   ├── models/                     # 模型文件
│   └── configs/                    # 配置文件
└── ⚙️ config/                       # 配置模板
    └── chat_config.yaml.example    # 聊天配置示例
```

### 🏗️ 架构分层说明

#### 1. **配置管理层** (`config/`)
- **统一配置**: 基于Pydantic的类型安全配置管理
- **环境变量**: 支持.env文件和环境变量配置
- **组件工厂**: 统一的组件创建和依赖注入

#### 2. **核心模块层** (`core/`)
- **插件化设计**: 统一接口，可插拔实现
- **策略模式**: 支持多种AI服务提供商
- **错误处理**: 统一的异常处理和重试机制

#### 3. **业务服务层** (`services/`)
- **高层抽象**: 封装复杂的业务逻辑
- **服务编排**: 协调多个核心模块协同工作
- **状态管理**: 会话和用户状态维护

#### 4. **Web界面层** (`web/`)
- **双端支持**: Gradio后端 + React前端
- **组件化**: 可复用的UI组件设计
- **API服务**: RESTful API接口

## 文档导航

- [API 文档](docs/api.md) - 完整的API参考手册
- [部署指南](docs/deployment.md) - 安装和部署说明
- [开发文档](docs/development.md) - 开发环境搭建和贡献指南
- [纠错系统概述](docs/correction_system_overview.md) - 发音纠错技术详解
- [系统设计](docs/pronunciation_correction_design.md) - 架构和设计决策

## 🎯 核心功能实现

### ✅ 已完成功能

#### 🎤 语音处理能力
- [x] **多引擎ASR支持**: OpenAI Whisper (本地) + 阿里云ASR (云端)
- [x] **高质量TTS合成**: Microsoft Edge TTS + 阿里百炼TTS
- [x] **实时音频处理**: 流式音频处理和低延迟响应
- [x] **音频格式支持**: WAV, MP3, FLAC等多种格式

#### 🧠 AI智能服务
- [x] **大模型集成**: OpenAI GPT + 阿里云通义千问
- [x] **智能对话管理**: 多轮对话上下文维护
- [x] **主题智能生成**: 基于难度和场景的动态主题生成
- [x] **个性化响应**: 根据用户水平调整对话复杂度

#### 🔍 专业评估系统
- [x] **发音准确性评估**: 音素级别的精确分析
- [x] **流利度评价**: 语速、停顿、连贯性综合评估
- [x] **韵律分析**: 重音、语调、节奏特征分析
- [x] **IPA音标支持**: 国际音标标准的发音纠错
- [x] **个性化建议**: 基于评估结果的改进建议

#### 🌐 用户界面
- [x] **现代化Web界面**: Gradio + React双端支持
- [x] **三大功能模块**: 对话练习、发音评估、纠错指导
- [x] **响应式设计**: 适配不同屏幕尺寸
- [x] **实时交互**: 音频录制、播放、可视化

#### 🏗️ 系统架构
- [x] **模块化设计**: 松耦合、高内聚的架构设计
- [x] **插件化支持**: 可插拔的AI服务提供商
- [x] **统一配置管理**: 基于Pydantic的类型安全配置
- [x] **完整测试覆盖**: 单元测试、集成测试、端到端测试
- [x] **错误处理机制**: 统一的异常处理和用户友好提示

### 🚧 开发中功能
- [ ] **多语言支持扩展**: 支持更多语言的发音练习
- [ ] **用户学习进度跟踪**: 学习历史和进度可视化
- [ ] **个性化学习路径**: AI驱动的学习计划推荐
- [ ] **移动端原生应用**: iOS/Android原生应用开发
- [ ] **社交学习功能**: 用户互动和学习社区

## 🛠️ 技术栈详解

### 后端技术栈
- **🐍 核心语言**: Python 3.12+ (类型提示、异步支持)
- **🌐 Web框架**: Gradio 4.0+ (快速AI应用开发)
- **⚙️ 配置管理**: Pydantic + pydantic-settings (类型安全配置)
- **📦 依赖管理**: uv (现代Python包管理器)
- **🧪 测试框架**: pytest + pytest-asyncio (异步测试支持)

### AI服务集成
- **🎤 语音识别**: 
  - OpenAI Whisper (本地高精度识别)
  - 阿里云语音识别 (云端实时识别)
- **🔊 语音合成**: 
  - Microsoft Edge TTS (多音色支持)
  - 阿里百炼TTS (专业级合成)
- **🧠 大语言模型**: 
  - OpenAI GPT-3.5/4 (强大对话能力)
  - 阿里云通义千问 (中文优化)

### 音频处理
- **📊 音频分析**: librosa (音频特征提取)
- **🎵 音频处理**: soundfile, scipy (音频I/O和处理)
- **🔍 语音分析**: myprosody, praat-parselmouth (韵律分析)
- **🎙️ 录音支持**: pyaudio, speech_recognition (音频录制)

### 前端技术栈
- **⚛️ 前端框架**: React 18.2+ + TypeScript
- **🏗️ 构建工具**: Vite 5.0+ (快速构建和热重载)
- **🌐 HTTP客户端**: Axios (API通信)
- **🎨 开发工具**: ESLint, TypeScript ESLint (代码质量)

### 开发工具
- **🔧 代码格式化**: Black, isort (Python代码格式化)
- **📝 类型检查**: mypy (静态类型检查)
- **🔍 代码质量**: flake8 (代码规范检查)
- **🚀 CI/CD**: pre-commit hooks (提交前检查)

## 🚀 部署和使用

### Docker Compose

先按[快速开始](#快速开始)克隆仓库并复制、编辑 `.env`，再在仓库根目录运行：

```bash
docker compose up --build -d
docker compose ps
```

仓库中的 [docker-compose.yml](docker-compose.yml)、[Dockerfile](Dockerfile) 和前端容器配置定义了实际部署方式：默认启动后端 API（8010）和静态 React 前端（3010）。具体服务、持久化目录、可选 Gradio 启动方式与检查命令见[部署指南](docs/deployment.md#docker-compose-部署)。

### 本地开发

使用[快速开始](#快速开始)中的 Python 和前端命令。在仓库根目录执行 `uv sync --dev` 可安装测试与格式化工具。

- **React 前端**：http://localhost:3010
- **后端 API 文档**：http://localhost:8010/docs
- **后端健康检查**：http://localhost:8010/health
- **Gradio**：http://localhost:7860（需单独启动）

## 🤝 贡献指南

我们热烈欢迎社区贡献！请查看 [开发文档](docs/development.md) 了解详细的开发指南。

### 🎯 贡献方式
- **🐛 Bug报告**: 发现问题请提交详细的Issue
- **💡 功能建议**: 提出新功能想法和改进建议
- **📝 文档改进**: 完善项目文档和使用指南
- **🔧 代码贡献**: 提交Bug修复和新功能实现
- **🌟 用户反馈**: 分享使用体验和改进建议
- **🎨 UI/UX优化**: 改进用户界面和交互体验

### 📋 开发流程
1. **Fork项目** 并创建功能分支
2. **遵循代码规范** (Black, isort, mypy)
3. **编写测试** 确保代码质量
4. **提交PR** 并描述变更内容
5. **代码审查** 通过后合并到主分支

### 🧪 测试指南
```bash
# 运行所有测试
pytest

# 运行特定测试
pytest tests/test_services.py

# 生成覆盖率报告
pytest --cov=chatterpal --cov-report=html
```

## 📊 项目状态

- **🔥 活跃开发中**: 持续更新和功能增强
- **✅ 生产就绪**: 核心功能稳定可用
- **🧪 测试覆盖**: 90%+ 代码覆盖率
- **📚 文档完善**: 详细的API和使用文档
- **🌍 社区驱动**: 欢迎社区参与和贡献

## 📄 许可证

本项目采用 **MIT 许可证**，允许自由使用、修改和分发。详情请查看 [LICENSE](LICENSE) 文件。

## 🔗 相关链接

- **📂 项目主页**: [GitHub Repository](https://github.com/likebeans/ChatterPal)
- **🐛 问题反馈**: [GitHub Issues](https://github.com/likebeans/ChatterPal/issues)
- **📖 在线文档**: [Documentation](https://chatterpal.readthedocs.io)
- **🎮 在线演示**: [Live Demo](https://aistudio.baidu.com/aistudio/projectdetail/6566149)
- **💬 讨论社区**: [GitHub Discussions](https://github.com/likebeans/ChatterPal/discussions)

## 🏆 致谢

### 核心贡献者
感谢所有为项目做出贡献的开发者：

- [@Liyulingyue](https://github.com/Liyulingyue/) - 思路学习
- [@likebeans](https://github.com/likebeans/) - 整体开发


### 技术支持
- **OpenAI**: Whisper语音识别技术
- **阿里云**: 通义千问大模型和语音服务
- **Microsoft**: Edge TTS语音合成
- **Gradio**: 快速AI应用开发框架

---

<div align="center">

**🌟 如果这个项目对你有帮助，请给我们一个Star！**

**⭐ Star this project if it helps you! ⭐**

</div>

