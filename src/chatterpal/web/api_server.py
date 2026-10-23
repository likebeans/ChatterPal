# -*- coding: utf-8 -*-
"""
FastAPI服务器，为前端提供REST API接口
"""

import os
import sys
import logging
import asyncio
import tempfile
import uuid
from typing import Optional, Dict, Any, List
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn

# 设置编码环境变量
os.environ['PYTHONIOENCODING'] = 'utf-8'
if os.name == 'nt':  # Windows
    os.environ['PYTHONLEGACYWINDOWSSTDIO'] = '1'

from ..config.settings import Settings, get_settings
from ..config.loader import create_tts, create_asr, create_llm
from ..core.assessment.corrector import PronunciationCorrector
from ..services.chat import ChatService
from ..services.evaluation import EvaluationService
from ..services.correction import CorrectionService
from ..services.ai_correction import AICorrectionService, AIAnalysisResult, ScenarioType, DifficultyLevel
from ..utils.encoding_fix import safe_str


# Pydantic模型定义
class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    voice: Optional[str] = "longxiaochun"

class ChatResponse(BaseModel):
    response: str
    session_id: str
    audio_url: Optional[str] = None

class PronunciationAnalysisResponse(BaseModel):
    overall_score: float
    recognized_text: str
    corrections: List[Dict[str, Any]]
    suggestions: List[str]

class AIAnalysisRequest(BaseModel):
    reference_text: str
    current_scenario: Optional[str] = None
    user_id: Optional[str] = None

class AIAnalysisResponse(BaseModel):
    overall_score: float
    recognized_text: str
    grammar_corrections: List[Dict[str, Any]]
    pronunciation_feedback: List[Dict[str, Any]]
    scenario_suggestions: List[Dict[str, Any]]
    personalized_tips: List[str]
    difficulty_level: str
    next_scenario: Optional[str]
    confidence_score: float
    detailed_analysis: str

class ScenarioRequest(BaseModel):
    scenario_type: str
    difficulty_level: str

class ScenarioResponse(BaseModel):
    scenario_type: str
    difficulty_level: str
    context_description: str
    sample_dialogues: List[str]
    key_vocabulary: List[str]
    grammar_focus: List[str]
    pronunciation_targets: List[str]

class VoiceInfo(BaseModel):
    id: str
    name: str
    description: str
    language: str
    gender: str


class APIServer:
    """FastAPI服务器类"""
    
    def __init__(self, settings: Optional[Settings] = None):
        """初始化API服务器"""
        self.settings = settings or get_settings()
        self.logger = logging.getLogger(__name__)
        
        # 初始化FastAPI应用
        self.app = FastAPI(
            title="ChatterPal API",
            description="智能口语练习助手API",
            version="1.0.0"
        )
        
        # 配置CORS
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:3010", "http://127.0.0.1:3010"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        # 初始化核心组件
        self._init_core_components()
        self._init_services()
        
        # 注册路由
        try:
            self._register_routes()
            self.logger.info("路由注册完成")
        except Exception as e:
            self.logger.error(f"路由注册失败: {e}")
            raise
        
        # 创建临时文件目录
        self.temp_dir = Path(tempfile.gettempdir()) / "chatterpal"
        self.temp_dir.mkdir(exist_ok=True)
        self._analysis_tasks = set()

    def _cleanup_analysis_upload(self, audio_path: Path) -> None:
        try:
            audio_path.unlink(missing_ok=True)
        except OSError as e:
            self.logger.warning(f"清理分析上传失败: {e}")

    def _start_analysis(self, analysis, audio_path: Path) -> asyncio.Task:
        """分析任务拥有上传文件，外层请求取消后仍完成清理。"""
        task = asyncio.create_task(analysis)
        self._analysis_tasks.add(task)

        def complete(completed):
            self._analysis_tasks.discard(completed)
            self._cleanup_analysis_upload(audio_path)
            if not completed.cancelled():
                error = completed.exception()
                if error is not None:
                    self.logger.error(f"分析任务失败: {error}")

        task.add_done_callback(complete)
        return task

    def _init_core_components(self):
        """初始化核心组件"""
        try:
            # 通过统一工厂初始化，保持API与其他入口的配置行为一致
            self.asr = create_asr(self.settings)
            
            # 初始化TTS
            self.tts = create_tts(self.settings)
            self.logger.info(f"TTS initialized: {type(self.tts).__name__ if self.tts else 'None'}")
            
            # 初始化LLM
            self.llm = create_llm(self.settings)
            
            # 初始化评估组件
            self.corrector = PronunciationCorrector()
            
            self.logger.info("核心组件初始化完成")
            
        except Exception as e:
            self.logger.error(f"核心组件初始化失败: {e}")
            raise
    
    def _init_services(self):
        """初始化服务"""
        try:
            # 初始化聊天服务
            self.chat_service = ChatService(
                asr=self.asr,
                tts=self.tts,
                llm=self.llm
            )
            
            # 初始化评估服务
            self.evaluation_service = EvaluationService(
                asr=self.asr,
                llm=self.llm
            )
            
            # 初始化纠错服务
            self.correction_service = CorrectionService(
                asr=self.asr
            )
            
            # 初始化AI纠错服务
            gemini_api_key = getattr(self.settings, 'gemini_api_key', '')
            self.ai_correction_service = AICorrectionService(
                api_key=gemini_api_key if gemini_api_key else None,
                base_correction_service=self.correction_service
            )
            
            self.logger.info("服务初始化完成")
            
        except Exception as e:
            self.logger.error(f"服务初始化失败: {e}")
            raise
    
    def _register_routes(self):
        """注册API路由"""
        self.logger.info("开始注册路由...")
        
        @self.app.get("/")
        async def root():
            return {"message": "ChatterPal API Server", "version": "1.0.0"}
        
        self.logger.info("根路径路由已注册")
        
        @self.app.get("/health")
        async def health_check():
            """健康检查接口"""
            return {
                "status": "healthy",
                "services": {
                    "asr": self.asr is not None,
                    "tts": self.tts is not None,
                    "llm": self.llm is not None,
                    "chat": self.chat_service is not None,
                    "evaluation": self.evaluation_service is not None,
                    "correction": self.correction_service is not None,
                    "ai_correction": self.ai_correction_service is not None
                }
            }
        
        self.logger.info("健康检查路由已注册")
        
        @self.app.post("/api/chat", response_model=ChatResponse)
        async def chat(request: ChatRequest):
            """聊天接口"""
            try:
                # 处理文本聊天
                response_text, session_id = await asyncio.to_thread(
                    self.chat_service.chat_with_text,
                    text=request.message,
                    session_id=request.session_id
                )
                
                # 生成语音
                audio_url = None
                if self.tts and response_text:
                    try:
                        # 生成音频文件
                        audio_filename = f"response_{uuid.uuid4().hex}.wav"
                        audio_path = self.temp_dir / audio_filename
                        
                        # 设置音色
                        if hasattr(self.tts, 'set_voice'):
                            self.tts.set_voice(request.voice)
                        
                        # 合成语音到文件
                        success = await asyncio.to_thread(
                            self.tts.synthesize_to_file, response_text, str(audio_path)
                        )
                        if success:
                            audio_url = f"/api/audio/{audio_filename}"
                        else:
                            self.logger.warning("语音合成到文件失败")
                        
                    except Exception as e:
                        self.logger.warning(f"语音合成失败: {e}")
                
                return ChatResponse(
                    response=response_text,
                    session_id=session_id,
                    audio_url=audio_url
                )
                
            except Exception as e:
                self.logger.error(f"聊天处理失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        self.logger.info("聊天路由已注册")
        
        @self.app.post("/api/chat/audio")
        async def chat_with_audio(
            audio: UploadFile = File(...),
            session_id: Optional[str] = Form(None),
            voice: str = Form("longxiaochun")
        ):
            """语音聊天接口"""
            audio_path = None
            try:
                # 保存上传的音频文件（前端现在直接发送WAV格式）
                audio_filename = f"input_{uuid.uuid4().hex}.wav"
                audio_path = self.temp_dir / audio_filename
                
                with open(audio_path, "wb") as f:
                    content = await audio.read()
                    f.write(content)
                
                self.logger.info(f"音频文件已保存: {audio_filename}")
                
                # 设置音色（在调用语音对话服务之前）
                if hasattr(self.tts, 'set_voice'):
                    self.tts.set_voice(voice)
                
                # 处理语音聊天获取AI回复（已包含TTS处理）
                result = await asyncio.to_thread(
                    self.chat_service.chat_with_audio_result,
                    audio_data=str(audio_path),
                    session_id=session_id,
                    return_audio=True
                )

                # 生成响应音频URL（如果返回了音频数据）
                audio_url = None
                if result.response_audio:
                    try:
                        response_audio_filename = f"response_{uuid.uuid4().hex}.wav"
                        response_audio_path = self.temp_dir / response_audio_filename
                        
                        # 保存音频数据到文件
                        with open(response_audio_path, 'wb') as f:
                            f.write(result.response_audio)
                        audio_url = f"/api/audio/{response_audio_filename}"
                        
                    except Exception as e:
                        self.logger.warning(f"保存音频文件失败: {e}")
                
                return {
                    "recognized_text": result.recognized_text,  # 用户语音识别结果
                    "response": result.response_text,           # AI回复文本
                    "session_id": result.session_id,
                    "audio_url": audio_url               # AI回复语音URL
                }
                
            except Exception as e:
                self.logger.error(f"语音聊天处理失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                if audio_path is not None:
                    try:
                        audio_path.unlink(missing_ok=True)
                    except OSError as e:
                        self.logger.warning(f"清理上传音频失败: {e}")

        self.logger.info("语音聊天路由已注册")
        
        @self.app.post("/api/pronunciation/analyze", response_model=PronunciationAnalysisResponse)
        async def analyze_pronunciation(
            audio: UploadFile = File(...),
            reference_text: str = Form(...)
        ):
            """发音分析接口"""
            audio_path = None
            analysis_task = None
            try:
                # 每个请求拥有独立的上传副本。
                audio_filename = f"analyze_{uuid.uuid4().hex}.wav"
                audio_path = self.temp_dir / audio_filename
                
                with open(audio_path, "wb") as f:
                    content = await audio.read()
                    f.write(content)
                
                # 进行发音分析
                analysis_task = self._start_analysis(
                    asyncio.to_thread(
                        self.correction_service.comprehensive_correction,
                        audio_data=str(audio_path),
                        target_text=reference_text
                    ),
                    audio_path
                )
                report = await asyncio.shield(analysis_task)
                
                # 解析结果
                # 构建纠错信息
                corrections = []
                for error in report.pronunciation_errors:
                    corrections.append({
                        "type": error.get("type", "pronunciation"),
                        "original": error.get("word", ""),
                        "corrected": error.get("correction", error.get("suggestion", "")),
                        "explanation": error.get("description", "")
                    })
                
                for issue in report.prosody_issues:
                    corrections.append({
                        "type": "prosody",
                        "original": issue.get("aspect", ""),
                        "corrected": issue.get("suggestion", ""),
                        "explanation": issue.get("description", "")
                    })
                
                return PronunciationAnalysisResponse(
                    overall_score=report.overall_score,
                    recognized_text=report.recognized_text,
                    corrections=corrections,
                    suggestions=report.improvement_suggestions
                )
                
            except Exception as e:
                self.logger.error(f"发音分析失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                if audio_path is not None and analysis_task is None:
                    self._cleanup_analysis_upload(audio_path)

        self.logger.info("发音分析路由已注册")
        
        @self.app.post("/api/pronunciation/ai-analyze", response_model=AIAnalysisResponse)
        async def ai_analyze_pronunciation(
            audio: UploadFile = File(...),
            reference_text: str = Form(...),
            current_scenario: Optional[str] = Form(None),
            user_id: Optional[str] = Form(None)
        ):
            """AI驱动的发音分析接口"""
            audio_path = None
            analysis_task = None
            try:
                scenario = None
                if current_scenario:
                    try:
                        scenario_value, separator, difficulty = current_scenario.partition("-")
                        scenario = ScenarioType(scenario_value)
                        if separator:
                            DifficultyLevel(difficulty)
                    except ValueError:
                        raise HTTPException(status_code=422, detail="不支持的练习场景")

                audio_filename = f"ai_analyze_{uuid.uuid4().hex}.wav"
                audio_path = self.temp_dir / audio_filename
                
                with open(audio_path, "wb") as f:
                    content = await audio.read()
                    f.write(content)
                
                # 进行AI驱动的综合分析
                analysis_task = self._start_analysis(
                    self.ai_correction_service.comprehensive_ai_analysis(
                        audio_data=str(audio_path),
                        reference_text=reference_text,
                        current_scenario=scenario,
                        user_id=user_id
                    ),
                    audio_path
                )
                ai_result = await asyncio.shield(analysis_task)

                next_scenario = None
                if ai_result.next_scenario is not None:
                    context = self.ai_correction_service.get_scenario_context(
                        ai_result.next_scenario, ai_result.difficulty_assessment
                    )
                    if context is not None:
                        next_scenario = (
                            f"{context.scenario_type.value}-{context.difficulty_level.value}"
                        )

                return AIAnalysisResponse(
                    overall_score=ai_result.overall_score,
                    recognized_text=ai_result.recognized_text,
                    grammar_corrections=ai_result.grammar_corrections,
                    pronunciation_feedback=ai_result.pronunciation_feedback,
                    scenario_suggestions=ai_result.scenario_suggestions,
                    personalized_tips=ai_result.personalized_tips,
                    difficulty_level=ai_result.difficulty_assessment.value,
                    next_scenario=next_scenario,
                    confidence_score=ai_result.confidence_score,
                    detailed_analysis=ai_result.detailed_analysis
                )
                
            except HTTPException:
                raise
            except Exception as e:
                self.logger.error(f"AI发音分析失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                if audio_path is not None and analysis_task is None:
                    self._cleanup_analysis_upload(audio_path)

        self.logger.info("AI发音分析路由已注册")
        
        @self.app.get("/api/scenarios/{scenario_type}/{difficulty_level}", response_model=ScenarioResponse)
        async def get_scenario(
            scenario_type: str,
            difficulty_level: str
        ):
            """获取特定场景信息"""
            try:
                try:
                    scenario = ScenarioType(scenario_type)
                    difficulty = DifficultyLevel(difficulty_level)
                except ValueError as e:
                    raise HTTPException(status_code=422, detail="无效的场景或难度") from e
                scenario_context = self.ai_correction_service.get_scenario_context(
                    scenario_type=scenario,
                    difficulty_level=difficulty
                )
                if scenario_context is None:
                    raise HTTPException(status_code=404, detail="该场景与难度的练习内容尚未提供")

                return ScenarioResponse(
                    scenario_type=scenario_context.scenario_type.value,
                    difficulty_level=scenario_context.difficulty_level.value,
                    context_description=scenario_context.context_description,
                    sample_dialogues=scenario_context.sample_dialogues,
                    key_vocabulary=scenario_context.key_vocabulary,
                    grammar_focus=scenario_context.grammar_focus,
                    pronunciation_targets=scenario_context.pronunciation_targets
                )
                
            except HTTPException:
                raise
            except Exception as e:
                self.logger.error(f"获取场景信息失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        self.logger.info("场景管理路由已注册")
        
        @self.app.get("/api/scenarios", response_model=List[Dict[str, str]])
        async def list_scenarios():
            """获取所有可用场景列表"""
            try:
                scenarios = []
                for scenario_type in ScenarioType:
                    for difficulty_level in DifficultyLevel:
                        if self.ai_correction_service.get_scenario_context(
                            scenario_type, difficulty_level
                        ) is None:
                            continue
                        scenarios.append({
                            "scenario_type": scenario_type.value,
                            "difficulty_level": difficulty_level.value,
                            "display_name": f"{scenario_type.value.title()} - {difficulty_level.value.title()}"
                        })
                return scenarios
                
            except Exception as e:
                self.logger.error(f"获取场景列表失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        self.logger.info("场景列表路由已注册")
        
        @self.app.get("/api/voices", response_model=List[VoiceInfo])
        def get_voices():
            """获取可用音色列表（同步TTS调用由FastAPI在线程池中运行）"""
            try:
                voices = []
                if self.tts and hasattr(self.tts, 'get_supported_voices'):
                    supported_voices = self.tts.get_supported_voices()
                    for voice_id in supported_voices:
                        voice_info = self.tts.get_voice_info(voice_id)
                        voices.append(VoiceInfo(
                            id=voice_id,
                            name=voice_info.get('display_name', voice_id),
                            description=voice_info.get('description', ''),
                            language=voice_info.get('language', 'zh-CN'),
                            gender=voice_info.get('gender', 'female')
                        ))
                else:
                    # 默认音色
                    voices = [
                        VoiceInfo(
                            id="longxiaochun",
                            name="龙小春",
                            description="温柔甜美的女声",
                            language="zh-CN",
                            gender="female"
                        )
                    ]
                
                return voices
                
            except Exception as e:
                self.logger.error(f"获取音色列表失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        self.logger.info("音色列表路由已注册")
        
        @self.app.get("/api/audio/{filename}")
        async def get_audio(filename: str):
            """获取音频文件"""
            try:
                audio_path = self.temp_dir / filename
                if not audio_path.exists():
                    raise HTTPException(status_code=404, detail="音频文件不存在")
                
                return FileResponse(
                    path=str(audio_path),
                    media_type="audio/wav",
                    filename=filename
                )
                
            except Exception as e:
                self.logger.error(f"获取音频文件失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        self.logger.info("音频文件路由已注册")
    
    def run(self, host: str = "0.0.0.0", port: int = 8000, debug: bool = False):
        """运行API服务器"""
        uvicorn.run(
            self.app,
            host=host,
            port=port,
            log_level="debug" if debug else "info"
        )


def create_api_server(settings: Optional[Settings] = None) -> APIServer:
    """创建API服务器实例"""
    return APIServer(settings)


if __name__ == "__main__":
    # 直接运行API服务器
    server = create_api_server()
    server.run(debug=True)