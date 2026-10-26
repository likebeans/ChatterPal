# ChatterPal API 文档

## 概述

ChatterPal 提供英语口语练习、文本反馈和可选录音声学摘要 API，支持多种 ASR 引擎、TTS 服务和 LLM 提供商。

本文保留了部分早期模块示例，不应将其视为当前 HTTP 契约。任务学习接口以[学习 API 契约](learning_api_contract.md)为准；当前分析响应与评分限制见下方[评估结果与评分边界](#评估结果与评分边界)，完整 HTTP 结构可在运行后的 `http://localhost:8010/docs` 查看。

## Core Modules

### ASR (Automatic Speech Recognition)

#### Base ASR Interface
```python
from chatterpal.core.asr.base import BaseASR

class BaseASR:
    def transcribe(self, audio_path: str) -> str:
        """Convert audio to text"""
        pass
```

#### Whisper ASR
```python
from chatterpal.core.asr.whisper import WhisperASR

asr = WhisperASR()
transcript = asr.transcribe("audio.wav")
```

#### Aliyun ASR
```python
from chatterpal.core.asr.aliyun import AliyunASR

asr = AliyunASR()
transcript = asr.transcribe("audio.wav")
```

### TTS (Text-to-Speech)

#### Base TTS Interface
```python
from chatterpal.core.tts.base import BaseTTS

class BaseTTS:
    def synthesize(self, text: str, output_path: str) -> str:
        """Convert text to speech"""
        pass
```

#### Edge TTS
```python
from chatterpal.core.tts.edge import EdgeTTS

tts = EdgeTTS()
audio_path = tts.synthesize("Hello world", "output.wav")
```

### LLM (Large Language Model)

#### Base LLM Interface
```python
from chatterpal.core.llm.base import BaseLLM

class BaseLLM:
    def chat(self, messages: List[Dict]) -> str:
        """Generate response from conversation"""
        pass
```

#### 阿里百炼 LLM (OpenAI 兼容接口)
```python
from chatterpal.core.llm.alibaba import AlibabaBailianLLM

# 基本使用 - 使用环境变量 DASHSCOPE_API_KEY
llm = AlibabaBailianLLM({
    "model": "qwen-plus"
})
response = llm.chat([{"role": "user", "content": "Hello"}])

# 显式指定 API 密钥
llm = AlibabaBailianLLM({
    "api_key": "your_dashscope_api_key",
    "model": "qwen-plus"
})

# 高级配置
llm = AlibabaBailianLLM({
    "api_key": "your_dashscope_api_key",
    "model": "qwen-max",
    "temperature": 0.7,
    "top_p": 0.8,
    "max_tokens": 2000,
    "enable_search": True,  # 启用搜索增强
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"
})

# 流式对话
for chunk in llm.chat_stream("请介绍一下人工智能"):
    print(chunk, end="")

# 搜索增强对话
response = llm.chat("最新的AI发展趋势是什么？", enable_search=True)

# 获取模型信息
model_info = llm.get_model_info()
print(f"API类型: {model_info['api_type']}")
print(f"当前模型: {model_info['model']}")

# 获取支持的模型列表
models = llm.get_supported_models()
print(f"支持的模型: {models}")

# 获取模型能力
capabilities = llm.get_model_capabilities("qwen-max")
print(f"最大上下文长度: {capabilities['max_context_length']}")

# 估算成本
cost = llm.estimate_cost(input_tokens=100, output_tokens=50)
print(f"预估成本: {cost['total_cost_yuan']:.4f} 元")

# 测试连接
if llm.test_connection():
    print("连接正常")
```

#### OpenAI LLM
```python
from chatterpal.core.llm.openai import OpenAILLM

llm = OpenAILLM()
response = llm.chat([{"role": "user", "content": "Hello"}])
```

### Assessment

#### Pronunciation Assessment
```python
from chatterpal.core.assessment.base import BaseAssessment

class BaseAssessment:
    def assess(self, audio_path: str, reference_text: str) -> Dict:
        """Assess pronunciation quality"""
        pass
```

#### Prosody Analysis
```python
from chatterpal.core.assessment.prosody import ProsodyAnalyzer

analyzer = ProsodyAnalyzer()
results = analyzer.analyze("audio.wav")
```

#### Phoneme Analysis
```python
from chatterpal.core.assessment.phoneme import PhonemeAnalyzer

analyzer = PhonemeAnalyzer()
results = analyzer.analyze("audio.wav", "reference text")
```

#### Pronunciation Corrector
```python
from chatterpal.core.assessment.corrector import PronunciationCorrector

corrector = PronunciationCorrector()
corrections = corrector.correct("audio.wav", "reference text")
```

## Services

### 聊天服务 (ChatService)

#### 基本使用

```python
from chatterpal.services.chat import ChatService

# 初始化聊天服务
chat_service = ChatService(asr=asr_instance, tts=tts_instance, llm=llm_instance)

# 创建会话
session_id = chat_service.create_session()
```

#### 统一聊天处理接口

```python
# 文本输入模式
audio_output, chat_history = chat_service.process_chat(
    text_input="Hello, how are you?",
    use_text_input=True,
    session_id=session_id
)

# 语音输入模式
audio_output, chat_history = chat_service.process_chat(
    audio=audio_data,
    use_text_input=False,
    session_id=session_id
)
```

#### 文本对话

```python
# 基本文本对话
response_text, session_id = chat_service.chat_with_text(
    text="What's your favorite hobby?",
    session_id=session_id
)
```

#### 语音对话（增强错误处理）

```python
# 语音对话，支持多种音频格式
response_text, response_audio, session_id = chat_service.chat_with_audio(
    audio_data=audio_file_path,  # 支持文件路径、字节数据或Gradio格式
    session_id=session_id,
    return_audio=True,
    max_retries=3
)
```

#### 主题生成功能

```python
# 生成随机主题
topic = chat_service.generate_topic(
    session_id=session_id,
    difficulty="intermediate",  # "beginner", "intermediate", "advanced"
    category="travel"  # 可选：指定主题分类
)

# 获取主题建议列表
suggestions = chat_service.get_topic_suggestions(
    difficulty="intermediate",
    category="hobby",
    count=5
)

# 为会话设置主题
success = chat_service.set_topic_for_session(session_id, "Let's talk about travel experiences")

# 获取当前会话主题
current_topic = chat_service.get_current_topic(session_id)
```

#### 会话管理

```python
# 获取对话历史
history = chat_service.get_conversation_history(session_id, limit=10)

# 清除对话上下文
success = chat_service.clear_context(session_id)

# 设置系统提示词
success = chat_service.set_system_prompt(session_id, "You are a helpful English teacher...")

# 生成对话摘要
summary = chat_service.generate_conversation_summary(session_id)

# 提取对话主题
topics = chat_service.get_conversation_topics(session_id)
```

#### 服务状态监控

```python
# 获取服务状态
status = chat_service.get_service_status()
# 返回：
# {
#     "asr_available": True,
#     "tts_available": True, 
#     "llm_available": True,
#     "topic_generator_available": True,
#     "active_sessions": 5,
#     "service_config": {...}
# }
```

### 主题生成服务 (TopicGenerator)

```python
from chatterpal.services.topic_generator import TopicGenerator

# 初始化主题生成器
topic_generator = TopicGenerator(llm=llm_instance, config=topic_config)

# 生成随机主题
random_topic = topic_generator.generate_random_topic(difficulty="intermediate")

# 基于对话历史生成相关主题
contextual_topic = topic_generator.generate_contextual_topic(
    chat_history=conversation_messages,
    difficulty="advanced"
)

# 获取主题分类
categories = topic_generator.get_topic_categories(difficulty="intermediate")

# 按分类获取主题
topics = topic_generator.get_topics_by_category(
    category="travel",
    difficulty="intermediate"
)

# 添加自定义主题
success = topic_generator.add_custom_topic(
    topic="What's your favorite programming language?",
    category="tech",
    difficulty="advanced"
)

# 获取统计信息
stats = topic_generator.get_statistics()
```

### 评估服务 (EvaluationService)

```python
from chatterpal.services.evaluation import EvaluationService

eval_service = EvaluationService(asr=asr_instance)  # 已初始化的 ASR
result = eval_service.evaluate_pronunciation("audio.wav", "reference text")
analysis = result.to_dict()  # 文本对齐与来源说明；未校准的能力分数为 None
```

### 纠错服务 (CorrectionService)

```python
from chatterpal.services.correction import CorrectionService

correction_service = CorrectionService(asr=asr_instance)
report = correction_service.comprehensive_correction("audio.wav", "reference text")
analysis = report.to_dict()
```

## 配置管理

### 聊天模块配置管理

```python
from chatterpal.services.chat_config import ChatConfigManager, AudioConfig, TopicGenerationConfig

# 初始化配置管理器
config_manager = ChatConfigManager()

# 获取当前配置
current_config = config_manager.get_config()

# 更新音频配置
audio_config = AudioConfig(
    max_recording_duration=60,
    min_recording_duration=1,
    sample_rate=16000,
    auto_play=True,
    playback_speed=1.0,
    volume=0.8
)
config_manager.update_audio_config(audio_config)

# 更新主题生成配置
topic_config = TopicGenerationConfig(
    difficulty_levels=["beginner", "intermediate", "advanced"],
    categories=["daily", "hobby", "travel", "work", "tech"],
    default_difficulty="intermediate",
    preferred_categories=["travel", "hobby"],
    context_aware=True
)
config_manager.update_topic_config(topic_config)

# 保存配置
config_manager.save_config()

# 重置为默认配置
config_manager.reset_to_defaults()
```

### 用户偏好管理

```python
from chatterpal.utils.preferences import get_preferences_manager

# 获取偏好管理器
prefs = get_preferences_manager()

# 设置用户偏好
prefs.set_preference("chat.default_input_mode", "voice")
prefs.set_preference("chat.auto_play_response", True)
prefs.set_preference("audio.playback_speed", 1.2)

# 获取用户偏好
input_mode = prefs.get_preference("chat.default_input_mode", "text")
auto_play = prefs.get_preference("chat.auto_play_response", False)

# 保存偏好设置
prefs.save_preferences()
```

### 基础设置管理

```python
from chatterpal.config.settings import Settings

settings = Settings()
# 访问配置值
api_key = settings.openai_api_key
model_name = settings.whisper_model
```

### 环境变量配置

```bash
# ASR 配置
WHISPER_MODEL=base
ALIYUN_ASR_APP_KEY=your_app_key
ALIYUN_ASR_ACCESS_KEY_ID=your_access_key_id
ALIYUN_ASR_ACCESS_KEY_SECRET=your_access_key_secret

# TTS 配置
EDGE_TTS_VOICE=en-US-JennyNeural

# LLM 配置
OPENAI_API_KEY=your_openai_key
OPENAI_BASE_URL=https://api.openai.com/v1
ALIBABA_API_KEY=your_alibaba_key
ALIBABA_MODEL=qwen-turbo

# 评估配置
ASSESSMENT_LANGUAGE=en-US

# 聊天模块配置
CHAT_MAX_HISTORY_LENGTH=50
CHAT_SESSION_TIMEOUT=3600
CHAT_AUTO_SAVE=true

# 音频配置
AUDIO_MAX_RECORDING_DURATION=60
AUDIO_MIN_RECORDING_DURATION=1
AUDIO_SAMPLE_RATE=16000
AUDIO_AUTO_PLAY=true

# 主题生成配置
TOPIC_DEFAULT_DIFFICULTY=intermediate
TOPIC_MAX_RETRIES=3
TOPIC_CONTEXT_AWARE=true

# 缓存配置
TTS_CACHE_SIZE=1000
TTS_CACHE_TTL=3600
CHAT_CACHE_SIZE=500
CHAT_CACHE_TTL=1800
```

## Web Interface

### Gradio Components
```python
from chatterpal.web.components.chat_tab import create_chat_tab
from chatterpal.web.components.score_tab import create_score_tab
from chatterpal.web.components.correct_tab import create_correct_tab

# Create individual tabs
chat_tab = create_chat_tab()
score_tab = create_score_tab()
correct_tab = create_correct_tab()
```

### Main Application
```python
from chatterpal.web.app import create_app

app = create_app()
app.launch()
```

## 工具模块

### 音频处理优化

```python
from chatterpal.utils.audio_optimizer import AudioProcessor, AudioBuffer

# 音频处理器
processor = AudioProcessor()

# 验证音频输入
is_valid = processor.validate_audio_input(audio_data)

# 转换音频格式
converted_audio = processor.convert_audio_format(audio_data, target_format="wav")

# 获取音频时长
duration = processor.get_audio_duration(audio_data)

# 检测音频质量
quality_info = processor.analyze_audio_quality(audio_data)

# 音频缓冲区管理
buffer = AudioBuffer(max_size=10)
buffer.put(audio_data)
audio = buffer.get(timeout=1.0)
```

### 缓存管理

```python
from chatterpal.utils.cache import get_tts_cache, get_chat_cache

# TTS 缓存
tts_cache = get_tts_cache()

# 缓存语音合成结果
tts_cache.set("hello_world", audio_data, ttl=3600)

# 获取缓存的语音
cached_audio = tts_cache.get("hello_world")

# 聊天缓存
chat_cache = get_chat_cache()

# 缓存会话数据
chat_cache.set_session_data(session_id, session_data)

# 获取会话数据
session_data = chat_cache.get_session_data(session_id)
```

### 基础音频处理

```python
from chatterpal.utils.audio import AudioProcessor

processor = AudioProcessor()
processed_audio = processor.preprocess("input.wav")
```

### Text Processing
```python
from chatterpal.utils.text import TextProcessor

processor = TextProcessor()
cleaned_text = processor.clean("input text")
```

### Logging
```python
from chatterpal.utils.logger import get_logger

logger = get_logger(__name__)
logger.info("Processing started")
```

## 错误处理

所有模块都实现了一致的错误处理机制：

### 基础错误类型

```python
from chatterpal.core.errors import (
    ChatModuleError,
    AudioInputError,
    SpeechRecognitionError,
    SpeechSynthesisError,
    TopicGenerationError,
    error_handler
)

# 基本错误处理
try:
    result = asr.transcribe("audio.wav")
except SpeechRecognitionError as e:
    logger.error(f"语音识别失败: {e}")
    # 获取用户友好的错误信息
    user_message = error_handler.format_user_error_message(e)
```

### 增强错误处理

```python
# 聊天服务的错误处理
try:
    response = chat_service.process_chat(
        audio=audio_data,
        use_text_input=False,
        session_id=session_id
    )
except AudioInputError as e:
    # 音频输入错误
    print(f"音频输入问题: {e.message}")
    print(f"建议: {', '.join(e.suggestions)}")
    
except SpeechRecognitionError as e:
    # 语音识别错误
    print(f"语音识别失败: {e.message}")
    if e.confidence_score:
        print(f"置信度: {e.confidence_score}")
        
except TopicGenerationError as e:
    # 主题生成错误
    print(f"主题生成失败: {e.message}")
    # 使用备用主题
    fallback_topic = "What's something interesting about your day?"
```

### 错误恢复机制

```python
# 带重试的语音识别
try:
    asr_result = asr.recognize_with_error_handling(
        audio_data, 
        max_retries=3
    )
    print(f"识别结果: {asr_result.text}")
    print(f"置信度: {asr_result.confidence}")
except SpeechRecognitionError as e:
    print(f"最终识别失败: {e}")

# 带降级的语音合成
try:
    audio_output = tts.synthesize_with_error_handling(
        text="Hello world",
        max_retries=2
    )
except SpeechSynthesisError as e:
    print(f"语音合成失败，使用文本输出: {e}")
    # 降级到纯文本模式
```

## 响应格式

### 聊天响应格式

```python
# process_chat 方法返回格式
audio_output, chat_history = chat_service.process_chat(...)

# audio_output 格式: (sample_rate, audio_data)
sample_rate, audio_data = audio_output
# sample_rate: int (通常为 16000)
# audio_data: List[float] 或 bytes

# chat_history 格式: List[List[str]]
# [
#     ["用户消息1", "助手回复1"],
#     ["用户消息2", "助手回复2"],
#     ...
# ]
```

### 主题生成响应

```json
{
    "topic": "What's your favorite way to spend weekends?",
    "category": "daily",
    "difficulty": "intermediate",
    "generated_at": "2024-01-15T10:30:00Z",
    "context_based": false
}
```

### 服务状态响应

```json
{
    "asr_available": true,
    "tts_available": true,
    "llm_available": true,
    "topic_generator_available": true,
    "active_sessions": 5,
    "service_config": {
        "max_history_length": 50,
        "session_timeout": 3600
    },
    "topic_statistics": {
        "total_generated": 150,
        "categories_used": ["daily", "hobby", "travel"],
        "average_difficulty": "intermediate"
    }
}
```

### 音频处理响应

```json
{
    "duration": 5.2,
    "sample_rate": 16000,
    "channels": 1,
    "format": "wav",
    "quality_score": 0.85,
    "noise_level": 0.1,
    "speech_detected": true
}
```

### 评估结果与评分边界

当前分析提供 ASR 转写、与参考文本的有序词编辑对齐、文本练习建议，以及可选工具取得的整段录音声学摘要。文本差异可能来自识别错误；ASR 置信度、文本一致度、基频或共振峰统计均不能替代发音准确率或语言能力判断。当前未执行声学音素对齐，没有经过人工标注数据校准的总分、发音分、流畅度分或韵律分，也不据此推断能力等级、学习进步或排名。

`EvaluationService.evaluate_pronunciation(...).to_dict()` 的评分字段如下（节选，未提供参考文本时）：

```json
{
    "overall_score": null,
    "detailed_scores": {
        "fluency": null,
        "pronunciation": null,
        "prosody": null,
        "accuracy": null
    },
    "calibrated": false
}
```

提供有效参考文本和识别文本后，`detailed_scores.accuracy` 可为文本一致度百分比；它不是声学发音准确率。`dimensions` 中的 `score`、`status`、`source`、`reason`、`evidence`、`unit` 和 `calibrated` 说明每个维度的来源与限制。Python 分析结果另有 `feature_metadata` 描述实测声学特征。不可用分数为 `null`，并附原因，不能按零分或默认分数处理。

### 纠错 HTTP 响应

`POST /api/pronunciation/analyze` 接收 multipart 表单字段 `audio` 和 `reference_text`，返回 `overall_score`、`recognized_text`、`corrections`、`suggestions`、`dimensions`、`calibrated` 和 `scope`；当前 `overall_score` 为 `null`，`calibrated` 为 `false`。`corrections` 项使用 `type`、`original`、`corrected` 和 `explanation`，描述识别文本差异或带限制说明的韵律提示，不构成具体音素错误诊断。

旧例中的 `content_accuracy`、`word_scores`、`phoneme_errors`、`ipa_actual` 和 `ipa_expected` 不是该 HTTP 接口当前返回的字段。Python 的 `phoneme_analysis` 也只包含整段录音摘要或不可用说明，不代表已识别出音素错误。AI 分析中的文本建议和可选发音练习同样不能当作听过原音后的声学结论；完整字段以运行后的 OpenAPI 为准。

## 使用示例

### 完整的聊天对话示例

```python
from chatterpal.services.chat import ChatService
from chatterpal.core.asr.whisper import WhisperASR
from chatterpal.core.tts.edge import EdgeTTS
from chatterpal.core.llm.openai import OpenAILLM

# 初始化组件
asr = WhisperASR()
tts = EdgeTTS()
llm = OpenAILLM()

# 创建聊天服务
chat_service = ChatService(asr=asr, tts=tts, llm=llm)

# 创建会话
session_id = chat_service.create_session()

# 生成对话主题
topic = chat_service.generate_topic(session_id, difficulty="intermediate")
print(f"今天的话题: {topic}")

# 文本对话
audio_output, history = chat_service.process_chat(
    text_input="I love traveling to new places!",
    use_text_input=True,
    session_id=session_id
)

# 语音对话
with open("user_audio.wav", "rb") as f:
    audio_data = f.read()

audio_output, history = chat_service.process_chat(
    audio=audio_data,
    use_text_input=False,
    session_id=session_id
)

# 播放回复音频
if audio_output[1]:  # 检查是否有音频数据
    # 保存音频文件
    with open("response.wav", "wb") as f:
        f.write(audio_output[1])
```

### 主题管理示例

```python
# 获取不同难度的主题建议
beginner_topics = chat_service.get_topic_suggestions(
    difficulty="beginner", 
    category="daily", 
    count=3
)

intermediate_topics = chat_service.get_topic_suggestions(
    difficulty="intermediate", 
    category="hobby", 
    count=3
)

# 添加自定义主题
chat_service.add_custom_topic(
    topic="What's your experience with remote work?",
    category="work",
    difficulty="advanced"
)

# 为会话设置特定主题
chat_service.set_topic_for_session(
    session_id, 
    "Let's discuss your favorite books and reading habits"
)
```

### 配置管理示例

```python
from chatterpal.services.chat_config import ChatConfigManager, AudioConfig

# 配置音频设置
config_manager = ChatConfigManager()
audio_config = AudioConfig(
    max_recording_duration=90,  # 90秒最大录音时长
    auto_play=True,             # 自动播放回复
    playback_speed=1.1,         # 1.1倍播放速度
    volume=0.9                  # 90%音量
)

config_manager.update_audio_config(audio_config)
config_manager.save_config()
```

### 错误处理示例

```python
from chatterpal.core.errors import SpeechRecognitionError, AudioInputError

try:
    audio_output, history = chat_service.process_chat(
        audio=audio_data,
        use_text_input=False,
        session_id=session_id
    )
except AudioInputError as e:
    print(f"音频输入问题: {e.message}")
    # 切换到文本输入模式
    audio_output, history = chat_service.process_chat(
        text_input="Could you repeat that?",
        use_text_input=True,
        session_id=session_id
    )
except SpeechRecognitionError as e:
    print(f"语音识别失败: {e.message}")
    if e.confidence_score and e.confidence_score < 0.5:
        print("建议：请在安静环境中重新录音")
```

## 快速开始

1. 安装依赖：
```bash
pip install -e .
```

2. 设置环境变量：
```bash
cp .env.example .env
# 编辑 .env 文件，填入您的 API 密钥
```

3. 运行应用：
```bash
python scripts/run.py
```

4. 测试聊天功能：
```bash
python scripts/test_prompt.py
```

更详细的设置说明请参见 [deployment.md](deployment.md) 和 [development.md](development.md)。