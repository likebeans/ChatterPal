# ChatterPal 开发文档

## 开发环境搭建

### 系统要求

- Python >=3.12
- Git
- 网络连接（用于下载依赖和API调用）

### 开发工具推荐

- **IDE**: VS Code, PyCharm
- **Python包管理**: uv (推荐) 或 pip
- **代码格式化**: black, isort
- **代码检查**: flake8, mypy
- **测试**: pytest

### 环境配置

#### 1. 克隆项目
```bash
git clone https://github.com/likebeans/ChatterPal.git
cd ChatterPal
```

#### 2. 安装开发依赖
```bash
# 使用 uv (推荐)
uv sync --dev

# 或使用 pip
pip install -e ".[dev]"
```

#### 3. 配置开发环境
```bash
# 复制环境变量模板
cp .env.example .env

# 安装 pre-commit hooks
pre-commit install
```

#### 4. 验证安装
```bash
# 运行测试
pytest

# 启动应用
python scripts/run.py
```

## 项目架构

### 目录结构详解

```
src/chatterpal/
├── __init__.py
├── config/                 # 配置管理
│   ├── __init__.py
│   ├── settings.py        # 配置类定义
│   └── loader.py          # 配置加载器
├── core/                  # 核心功能模块
│   ├── __init__.py
│   ├── asr/              # 语音识别
│   │   ├── __init__.py
│   │   ├── base.py       # ASR基类
│   │   ├── whisper.py    # Whisper实现
│   │   └── aliyun.py     # 阿里云ASR
│   ├── tts/              # 语音合成
│   │   ├── __init__.py
│   │   ├── base.py       # TTS基类
│   │   └── edge.py       # Edge TTS
│   ├── llm/              # 大语言模型
│   │   ├── __init__.py
│   │   ├── base.py       # LLM基类
│   │   ├── openai.py     # OpenAI接口
│   │   └── alibaba.py    # 阿里云通义千问
│   └── assessment/       # 发音评估
│       ├── __init__.py
│       ├── base.py       # 评估基类
│       ├── prosody.py    # 韵律分析
│       ├── phoneme.py    # 音素分析
│       └── corrector.py  # 发音纠错
├── services/             # 业务服务层
│   ├── __init__.py
│   ├── chat.py          # 对话服务
│   ├── evaluation.py    # 评估服务
│   └── correction.py    # 纠错服务
├── web/                 # Web界面
│   ├── __init__.py
│   ├── app.py          # 主应用
│   └── components/     # UI组件
│       ├── __init__.py
│       ├── chat_tab.py
│       ├── score_tab.py
│       └── correct_tab.py
└── utils/              # 工具函数
    ├── __init__.py
    ├── audio.py        # 音频处理
    ├── text.py         # 文本处理
    └── logger.py       # 日志工具
```

### 设计模式

#### 1. 策略模式 (Strategy Pattern)
用于ASR、TTS、LLM模块，支持多种实现的切换：

```python
# 基类定义接口
class BaseASR:
    def transcribe(self, audio_path: str) -> str:
        raise NotImplementedError

# 具体实现
class WhisperASR(BaseASR):
    def transcribe(self, audio_path: str) -> str:
        # Whisper实现
        pass

class AliyunASR(BaseASR):
    def transcribe(self, audio_path: str) -> str:
        # 阿里云实现
        pass
```

#### 2. 依赖注入 (Dependency Injection)
服务层通过构造函数注入依赖：

```python
class ChatService:
    def __init__(self, asr: BaseASR, tts: BaseTTS, llm: BaseLLM):
        self.asr = asr
        self.tts = tts
        self.llm = llm
```

#### 3. 工厂模式 (Factory Pattern)
用于创建不同类型的服务实例：

```python
def create_asr_engine(engine_type: str) -> BaseASR:
    if engine_type == "whisper":
        return WhisperASR()
    elif engine_type == "aliyun":
        return AliyunASR()
    else:
        raise ValueError(f"Unknown ASR engine: {engine_type}")
```

## 开发规范

### 代码风格

#### Python代码规范
- 遵循 PEP 8 标准
- 使用 black 进行代码格式化
- 使用 isort 进行导入排序
- 使用 flake8 进行代码检查

```bash
# 格式化代码
black src/ tests/
isort src/ tests/

# 检查代码
flake8 src/ tests/
```

#### 类型注解
所有公共函数和方法都应该有类型注解：

```python
from typing import List, Dict, Optional

def process_audio(audio_path: str, config: Dict[str, str]) -> Optional[str]:
    """处理音频文件并返回转录结果"""
    pass
```

#### 文档字符串
使用 Google 风格的文档字符串：

```python
def transcribe_audio(audio_path: str, language: str = "en") -> str:
    """转录音频文件为文本
    
    Args:
        audio_path: 音频文件路径
        language: 语言代码，默认为英语
        
    Returns:
        转录的文本内容
        
    Raises:
        FileNotFoundError: 音频文件不存在
        ASRError: 转录过程中发生错误
    """
    pass
```

### Git 工作流

#### 分支策略
- `main`: 主分支，保持稳定
- `develop`: 开发分支
- `feature/*`: 功能分支
- `bugfix/*`: 修复分支
- `hotfix/*`: 紧急修复分支

#### 提交规范
使用 Conventional Commits 规范：

```bash
# 功能添加
git commit -m "feat: 添加语音识别模块"

# 修复bug
git commit -m "fix: 修复音频处理内存泄漏问题"

# 文档更新
git commit -m "docs: 更新API文档"

# 代码重构
git commit -m "refactor: 重构配置管理模块"

# 测试相关
git commit -m "test: 添加ASR模块单元测试"
```

## 测试指南

### 测试结构

```
tests/
├── __init__.py
├── conftest.py              # pytest配置
├── test_asr.py             # ASR模块测试
├── test_tts.py             # TTS模块测试
├── test_llm.py             # LLM模块测试
├── test_assessment.py      # 评估模块测试
├── test_services.py        # 服务层测试
├── test_web_components.py  # Web组件测试
└── test_config.py          # 配置测试
```

### 运行测试

```bash
# 运行所有测试
pytest

# 运行特定模块测试
pytest tests/test_asr.py

# 运行测试并生成覆盖率报告
pytest --cov=src/chatterpal --cov-report=html

# 运行测试（详细输出）
pytest -v
```

### 编写测试

#### 单元测试示例
```python
import pytest
from unittest.mock import Mock, patch
from chatterpal.core.asr.whisper import WhisperASR

class TestWhisperASR:
    def setup_method(self):
        self.asr = WhisperASR()
    
    @patch('whisper.load_model')
    def test_transcribe_success(self, mock_load_model):
        # 模拟Whisper模型
        mock_model = Mock()
        mock_model.transcribe.return_value = {"text": "Hello world"}
        mock_load_model.return_value = mock_model
        
        result = self.asr.transcribe("test.wav")
        assert result == "Hello world"
    
    def test_transcribe_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            self.asr.transcribe("nonexistent.wav")
```

#### 集成测试示例
```python
def test_chat_service_integration():
    """测试聊天服务的完整流程"""
    # 创建真实的服务实例
    chat_service = ChatService(
        asr=WhisperASR(),
        tts=EdgeTTS(),
        llm=OpenAILLM()
    )
    
    # 测试完整的对话流程
    response = chat_service.process_conversation("test_audio.wav")
    assert response is not None
    assert "audio_path" in response
```

### Mock和Fixture

#### 使用Fixture
```python
# conftest.py
import pytest
from chatterpal.config.settings import Settings

@pytest.fixture
def test_settings():
    """测试用的配置"""
    return Settings(
        openai_api_key="test_key",
        whisper_model="tiny",
        log_level="DEBUG"
    )

@pytest.fixture
def sample_audio_file(tmp_path):
    """创建测试用的音频文件"""
    audio_file = tmp_path / "test.wav"
    # 创建一个简单的音频文件
    return str(audio_file)
```

#### Mock外部依赖
```python
@patch('chatterpal.core.llm.openai.openai.ChatCompletion.create')
def test_openai_llm(mock_create):
    mock_create.return_value = {
        "choices": [{"message": {"content": "Test response"}}]
    }
    
    llm = OpenAILLM()
    response = llm.chat([{"role": "user", "content": "Hello"}])
    assert response == "Test response"
```

## 调试指南

### 日志配置

```python
# 在代码中使用日志
from chatterpal.utils.logger import get_logger

logger = get_logger(__name__)

def process_audio(audio_path: str):
    logger.info(f"开始处理音频文件: {audio_path}")
    try:
        # 处理逻辑
        result = do_processing(audio_path)
        logger.info("音频处理完成")
        return result
    except Exception as e:
        logger.error(f"音频处理失败: {e}")
        raise
```

### 调试技巧

#### 1. 使用断点调试
```python
import pdb

def debug_function():
    pdb.set_trace()  # 设置断点
    # 调试代码
```

#### 2. 性能分析
```python
import cProfile
import pstats

def profile_function():
    profiler = cProfile.Profile()
    profiler.enable()
    
    # 要分析的代码
    your_function()
    
    profiler.disable()
    stats = pstats.Stats(profiler)
    stats.sort_stats('cumulative')
    stats.print_stats()
```

#### 3. 内存使用监控
```python
import tracemalloc

tracemalloc.start()

# 你的代码

current, peak = tracemalloc.get_traced_memory()
print(f"Current memory usage: {current / 1024 / 1024:.1f} MB")
print(f"Peak memory usage: {peak / 1024 / 1024:.1f} MB")
tracemalloc.stop()
```

## 性能优化

### 音频处理优化

#### 1. 异步处理
```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

async def process_audio_async(audio_path: str):
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as executor:
        result = await loop.run_in_executor(
            executor, process_audio_sync, audio_path
        )
    return result
```

#### 2. 缓存机制
```python
from functools import lru_cache

@lru_cache(maxsize=128)
def load_model(model_name: str):
    """缓存模型加载"""
    return expensive_model_loading(model_name)
```

#### 3. 批处理
```python
def process_audio_batch(audio_paths: List[str]) -> List[str]:
    """批量处理音频文件"""
    results = []
    for batch in chunk_list(audio_paths, batch_size=10):
        batch_results = model.process_batch(batch)
        results.extend(batch_results)
    return results
```

## 部署和发布

### 本地开发部署
```bash
# 启动开发服务器
python scripts/run.py --debug

# 指定端口
python scripts/run.py --port 8080
```

### 生产环境部署
参考 [部署文档](deployment.md) 了解详细的生产环境部署步骤。

### 版本发布流程

1. **更新版本号**
```bash
# 在 pyproject.toml 中更新版本
version = "1.2.0"
```

2. **创建发布分支**
```bash
git checkout -b release/v1.2.0
git push origin release/v1.2.0
```

3. **创建Pull Request**
- 从 release 分支到 main 分支
- 进行代码审查
- 运行完整测试套件

4. **合并和标记**
```bash
git checkout main
git merge release/v1.2.0
git tag v1.2.0
git push origin main --tags
```

## 贡献指南

### 如何贡献

1. **Fork项目**
2. **创建功能分支**
```bash
git checkout -b feature/new-feature
```

3. **提交更改**
```bash
git commit -m "feat: 添加新功能"
```

4. **推送分支**
```bash
git push origin feature/new-feature
```

5. **创建Pull Request**

### 代码审查清单

- [ ] 代码符合项目规范
- [ ] 添加了适当的测试
- [ ] 更新了相关文档
- [ ] 通过了所有测试
- [ ] 没有引入新的安全问题
- [ ] 性能没有明显下降

### 问题报告

使用GitHub Issues报告问题时，请包含：

- 问题描述
- 复现步骤
- 期望行为
- 实际行为
- 环境信息（Python版本、操作系统等）
- 相关日志或错误信息

## 常见问题

### Q: 如何添加新的ASR引擎？

A: 继承 `BaseASR` 类并实现 `transcribe` 方法：

```python
from chatterpal.core.asr.base import BaseASR

class NewASR(BaseASR):
    def transcribe(self, audio_path: str) -> str:
        # 实现转录逻辑
        return transcribed_text
```

### Q: 如何自定义配置？

A: 在 `.env` 文件中添加配置项，或在 `settings.py` 中添加新的配置字段。

### Q: 如何调试音频处理问题？

A: 启用详细日志并检查临时音频文件：

```bash
export LOG_LEVEL=DEBUG
python scripts/run.py
```

### Q: 如何优化内存使用？

A: 
- 使用较小的Whisper模型
- 启用音频文件缓存清理
- 调整批处理大小

## 资源链接

- [Python官方文档](https://docs.python.org/)
- [Gradio文档](https://gradio.app/docs/)
- [OpenAI API文档](https://platform.openai.com/docs/)
- [Whisper文档](https://github.com/openai/whisper)
- [pytest文档](https://docs.pytest.org/)