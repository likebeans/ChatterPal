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
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import replace
from functools import wraps
from threading import Lock
from typing import Optional, Dict, Any, List
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

# 设置编码环境变量
os.environ["PYTHONIOENCODING"] = "utf-8"
if os.name == "nt":  # Windows
    os.environ["PYTHONLEGACYWINDOWSSTDIO"] = "1"

from ..config.settings import Settings, get_settings
from ..config.loader import create_tts, create_asr, create_llm
from ..services.chat import ChatService
from ..services.correction import CorrectionService
from ..services.ai_correction import (
    AICorrectionService,
    AIAnalysisResult,
    ScenarioType,
    DifficultyLevel,
)
from ..utils.encoding_fix import safe_str
from .audio_assets import AudioDownload, GeneratedAudioStore


# Pydantic模型定义
class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    voice: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    session_id: str
    audio_url: Optional[str] = None


class PronunciationAnalysisResponse(BaseModel):
    overall_score: Optional[float]
    recognized_text: str
    corrections: List[Dict[str, Any]]
    suggestions: List[str]
    dimensions: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    calibrated: bool = False
    scope: str = "未校准；文字反馈不能替代声学发音测量。"


class AIAnalysisRequest(BaseModel):
    reference_text: str
    current_scenario: Optional[str] = None
    user_id: Optional[str] = None


class AIAnalysisResponse(BaseModel):
    overall_score: Optional[float]
    recognized_text: str
    grammar_corrections: List[Dict[str, Any]]
    pronunciation_feedback: List[Dict[str, Any]]
    scenario_suggestions: List[Dict[str, Any]]
    personalized_tips: List[str]
    difficulty_level: Optional[str]
    next_scenario: Optional[str]
    confidence_score: Optional[float]
    detailed_analysis: str
    dimensions: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    calibrated: bool = False
    scope: str = "未校准；文字反馈不能替代声学发音测量。"


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
        self.temp_dir = Path(
            getattr(
                self.settings,
                "audio_temp_dir",
                Path(tempfile.gettempdir()) / "chatterpal",
            )
        )
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self._audio_store = None
        self._audio_store_lock = Lock()
        self._request_tasks = set()
        self._analysis_tasks = set()
        self._executor_lock = Lock()
        self._upload_executor = None
        self._health_executor = None
        self._upload_workers = set()
        self._closing = False

        # 初始化FastAPI应用
        self.app = FastAPI(
            title="ChatterPal API",
            description="智能口语练习助手API",
            version="1.0.0",
            lifespan=self._lifespan,
        )

        # 配置CORS
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                "http://localhost:3000",
                "http://127.0.0.1:3000",
                "http://localhost:3010",
                "http://127.0.0.1:3010",
            ],
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

        if getattr(self.settings, "learning_enabled", False):
            self._register_learning()

    @asynccontextmanager
    async def _lifespan(self, app):
        self._closing = False
        self._generated_audio().cleanup()
        try:
            yield
        finally:
            self._closing = True
            tasks = self._request_tasks | self._analysis_tasks
            if tasks:
                _, pending = await asyncio.wait(
                    tasks, timeout=getattr(self.settings, "api_shutdown_timeout", 10)
                )
                if pending:
                    self.logger.warning(
                        "关闭时仍有 %d 个请求在完成后台工作", len(pending)
                    )
            self._generated_audio().cleanup()
            with self._executor_lock:
                executors = self._upload_executor, self._health_executor
                self._upload_executor = self._health_executor = None
            for executor in executors:
                if executor is not None:
                    executor.shutdown(wait=False)

    def _generated_audio(self):
        directory = Path(self.temp_dir)
        with self._audio_store_lock:
            if self._audio_store is None or self._audio_store.directory != directory:
                self._audio_store = GeneratedAudioStore(
                    directory,
                    ttl=getattr(self.settings, "generated_audio_ttl", 3600),
                    max_files=getattr(self.settings, "generated_audio_max_files", 256),
                    max_bytes=getattr(
                        self.settings, "generated_audio_max_bytes", 64 * 1024 * 1024
                    ),
                )
            return self._audio_store

    def _save_audio(self, content: bytes, *, format=None, sample_rate=0):
        from ..core.tts import prepare_audio_output

        content, info = prepare_audio_output(
            content, format=format, sample_rate=sample_rate or 0
        )
        filename = self._generated_audio().save(content, info.extension.lstrip("."))
        return f"/api/audio/{filename}"

    def _tts_options(self, voice=None):
        if not voice:
            return {}
        from ..core.tts import SynthesisOptions

        resolve = getattr(type(self.tts), "resolve_synthesis_options", None)
        if resolve is not None:
            options = resolve(self.tts)
            if isinstance(options, SynthesisOptions):
                return {"options": replace(options, voice=voice)}
        return {"voice": voice}

    def _synthesize_audio(self, text, voice=None):
        result = self.tts.synthesize_with_error_handling(
            text, **self._tts_options(voice)
        )
        return self._save_audio(
            result.audio_data, format=result.format, sample_rate=result.sample_rate
        )

    async def _health_snapshot(self):
        with self._executor_lock:
            if self._health_executor is None:
                self._health_executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="chatterpal-health"
                )
            executor = self._health_executor
        return await asyncio.get_running_loop().run_in_executor(
            executor, self._runtime_snapshot
        )

    async def _run_upload(self, path, work):
        def owned():
            try:
                return work()
            finally:
                self._cleanup_analysis_upload(path)

        with self._executor_lock:
            if self._closing:
                raise HTTPException(
                    status_code=503, detail="服务正在关闭，请稍后重试。"
                )
            if self._upload_executor is None:
                self._upload_executor = ThreadPoolExecutor(
                    max_workers=getattr(self.settings, "api_max_pending", 8),
                    thread_name_prefix="chatterpal-upload",
                )
            future = self._upload_executor.submit(copy_context().run, owned)
            self._upload_workers.add(path)

        def complete(completed):
            with self._executor_lock:
                self._upload_workers.discard(path)

        future.add_done_callback(complete)
        # Cancelling an asyncio waiter cannot cancel or clean a live thread's input.
        result = asyncio.wrap_future(future)
        result.add_done_callback(
            lambda completed: (
                completed.exception() if not completed.cancelled() else None
            )
        )
        return await asyncio.shield(result)

    def _cleanup_unowned_upload(self, path):
        with self._executor_lock:
            if path not in self._upload_workers:
                self._cleanup_analysis_upload(path)

    async def _run_request(self, handler, args, kwargs):
        if self._closing or len(self._request_tasks) >= getattr(
            self.settings, "api_max_pending", 8
        ):
            raise HTTPException(
                status_code=503,
                detail="处理队列已满，请稍后重试。",
                headers={"Retry-After": "1"},
            )
        task = asyncio.create_task(handler(*args, **kwargs))
        self._request_tasks.add(task)

        def complete(completed):
            self._request_tasks.discard(completed)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(complete)
        # A disconnected caller does not release capacity or delete an active upload.
        return await asyncio.shield(task)

    def _runtime_snapshot(self):
        chat = getattr(self, "chat_service", None)
        chat_snapshot = getattr(type(chat), "runtime_snapshot", None)
        learning_snapshot = getattr(self.app.state, "learning_runtime_snapshot", None)
        return {
            "requests": {
                "active": len(self._request_tasks),
                "max_pending": getattr(self.settings, "api_max_pending", 8),
                "closing": self._closing,
            },
            "chat": chat_snapshot(chat) if chat_snapshot else None,
            "learning": learning_snapshot() if learning_snapshot else None,
            "generated_audio": self._generated_audio().snapshot(),
        }

    def _register_learning(self):
        from ..learning.adapters import ExistingProviders
        from .routes.learning_records import mount_learning_api
        from .routes.learning_tasks import mount_learning_tasks
        from .routes.learning_review import mount_learning_review

        origins = {
            origin.strip()
            for origin in self.settings.learning_allowed_origins.split(",")
            if origin.strip()
        }
        mount_learning_api(
            self.app, self.settings, allowed_origins=origins, allow_record_writes=False
        )
        mount_learning_tasks(
            self.app, self.settings, ExistingProviders(self.asr, self.llm, self.tts)
        )
        mount_learning_review(self.app, self.settings)

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
            self._cleanup_unowned_upload(audio_path)
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
            self.logger.info(
                f"TTS initialized: {type(self.tts).__name__ if self.tts else 'None'}"
            )

            # 初始化LLM
            self.llm = create_llm(self.settings)

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
                llm=self.llm,
                config={
                    "max_history_length": getattr(
                        self.settings, "max_history_length", 20
                    ),
                    "session_timeout": getattr(self.settings, "session_timeout", 3600),
                    "max_sessions": getattr(self.settings, "max_sessions", 1000),
                },
            )
            self.evaluation_service = None

            # 初始化纠错服务
            self.correction_service = CorrectionService(asr=self.asr)

            # 初始化AI纠错服务
            self.ai_correction_service = AICorrectionService(
                base_correction_service=self.correction_service,
                llm=self.llm,
            )

            self.logger.info("服务初始化完成")

        except Exception as e:
            self.logger.error(f"服务初始化失败: {e}")
            raise

    def _register_routes(self):
        """注册API路由"""
        self.logger.info("开始注册路由...")

        def bounded(handler):
            @wraps(handler)
            async def run(*args, **kwargs):
                return await self._run_request(handler, args, kwargs)

            return run

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
                    "ai_correction": self.ai_correction_service is not None,
                },
                "runtime": await self._health_snapshot(),
            }

        self.logger.info("健康检查路由已注册")

        @self.app.post("/api/chat", response_model=ChatResponse)
        @bounded
        async def chat(request: ChatRequest):
            """聊天接口"""
            try:
                # 处理文本聊天
                response_text, session_id = await asyncio.to_thread(
                    self.chat_service.chat_with_text,
                    text=request.message,
                    session_id=request.session_id,
                )

                # 生成语音
                audio_url = None
                if self.tts and response_text:
                    try:
                        audio_url = await asyncio.to_thread(
                            self._synthesize_audio, response_text, request.voice
                        )

                    except Exception as e:
                        self.logger.warning(f"语音合成失败: {e}")

                return ChatResponse(
                    response=response_text, session_id=session_id, audio_url=audio_url
                )

            except Exception as e:
                self.logger.error(f"聊天处理失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        self.logger.info("聊天路由已注册")

        @self.app.post("/api/chat/audio")
        @bounded
        async def chat_with_audio(
            audio: UploadFile = File(...),
            session_id: Optional[str] = Form(None),
            voice: Optional[str] = Form(None),
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

                # 处理语音聊天获取AI回复（已包含TTS处理）
                result = await self._run_upload(
                    audio_path,
                    lambda: self.chat_service.chat_with_audio_result(
                        audio_data=str(audio_path),
                        session_id=session_id,
                        return_audio=True,
                        tts_options=self._tts_options(voice) or None,
                    ),
                )

                # 生成响应音频URL（如果返回了音频数据）
                audio_url = None
                if result.response_audio:
                    try:
                        audio_url = await asyncio.to_thread(
                            self._save_audio,
                            result.response_audio,
                            format=getattr(result, "response_audio_format", None),
                            sample_rate=getattr(
                                result, "response_audio_sample_rate", 0
                            ),
                        )

                    except Exception as e:
                        self.logger.warning(f"保存音频文件失败: {e}")

                return {
                    "recognized_text": result.recognized_text,  # 用户语音识别结果
                    "response": result.response_text,  # AI回复文本
                    "session_id": result.session_id,
                    "audio_url": audio_url,  # AI回复语音URL
                }

            except Exception as e:
                self.logger.error(f"语音聊天处理失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                if audio_path is not None:
                    self._cleanup_unowned_upload(audio_path)

        self.logger.info("语音聊天路由已注册")

        @self.app.post(
            "/api/pronunciation/analyze", response_model=PronunciationAnalysisResponse
        )
        @bounded
        async def analyze_pronunciation(
            audio: UploadFile = File(...), reference_text: str = Form(...)
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
                    self._run_upload(
                        audio_path,
                        lambda: self.correction_service.comprehensive_correction(
                            audio_data=str(audio_path), target_text=reference_text
                        ),
                    ),
                    audio_path,
                )
                report = await asyncio.shield(analysis_task)

                # 解析结果
                # 构建纠错信息
                corrections = []
                for error in report.pronunciation_errors:
                    corrections.append(
                        {
                            "type": error.get("type", "pronunciation"),
                            "original": error.get("word", ""),
                            "corrected": error.get(
                                "correction", error.get("suggestion", "")
                            ),
                            "explanation": error.get("description", ""),
                        }
                    )

                for issue in report.prosody_issues:
                    corrections.append(
                        {
                            "type": "prosody",
                            "original": issue.get("aspect", ""),
                            "corrected": issue.get("suggestion", ""),
                            "explanation": issue.get("description", ""),
                        }
                    )

                return PronunciationAnalysisResponse(
                    overall_score=report.overall_score,
                    recognized_text=report.recognized_text,
                    corrections=corrections,
                    suggestions=report.improvement_suggestions,
                    dimensions=getattr(report, "dimensions", {}),
                    calibrated=getattr(report, "calibrated", False),
                    scope=getattr(
                        report,
                        "scope",
                        PronunciationAnalysisResponse.model_fields["scope"].default,
                    ),
                )

            except Exception as e:
                self.logger.error(f"发音分析失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                if audio_path is not None and analysis_task is None:
                    self._cleanup_analysis_upload(audio_path)

        self.logger.info("发音分析路由已注册")

        @self.app.post(
            "/api/pronunciation/ai-analyze", response_model=AIAnalysisResponse
        )
        @bounded
        async def ai_analyze_pronunciation(
            audio: UploadFile = File(...),
            reference_text: str = Form(...),
            current_scenario: Optional[str] = Form(None),
            user_id: Optional[str] = Form(None),
        ):
            """AI驱动的发音分析接口"""
            audio_path = None
            analysis_task = None
            try:
                scenario = None
                if current_scenario:
                    try:
                        scenario_value, separator, difficulty = (
                            current_scenario.partition("-")
                        )
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
                    self._run_upload(
                        audio_path,
                        lambda: asyncio.run(
                            self.ai_correction_service.comprehensive_ai_analysis(
                                audio_data=str(audio_path),
                                reference_text=reference_text,
                                current_scenario=scenario,
                                user_id=user_id,
                            )
                        ),
                    ),
                    audio_path,
                )
                ai_result = await asyncio.shield(analysis_task)

                next_scenario = None
                if (
                    ai_result.next_scenario is not None
                    and ai_result.difficulty_assessment is not None
                ):
                    context = self.ai_correction_service.get_scenario_context(
                        ai_result.next_scenario, ai_result.difficulty_assessment
                    )
                    if context is not None:
                        next_scenario = f"{context.scenario_type.value}-{context.difficulty_level.value}"

                return AIAnalysisResponse(
                    overall_score=ai_result.overall_score,
                    recognized_text=ai_result.recognized_text,
                    grammar_corrections=ai_result.grammar_corrections,
                    pronunciation_feedback=ai_result.pronunciation_feedback,
                    scenario_suggestions=ai_result.scenario_suggestions,
                    personalized_tips=ai_result.personalized_tips,
                    difficulty_level=(
                        ai_result.difficulty_assessment.value
                        if ai_result.difficulty_assessment is not None
                        else None
                    ),
                    next_scenario=next_scenario,
                    confidence_score=ai_result.confidence_score,
                    detailed_analysis=ai_result.detailed_analysis,
                    dimensions=getattr(ai_result, "dimensions", {}),
                    calibrated=getattr(ai_result, "calibrated", False),
                    scope=getattr(
                        ai_result,
                        "scope",
                        AIAnalysisResponse.model_fields["scope"].default,
                    ),
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

        @self.app.get(
            "/api/scenarios/{scenario_type}/{difficulty_level}",
            response_model=ScenarioResponse,
        )
        async def get_scenario(scenario_type: str, difficulty_level: str):
            """获取特定场景信息"""
            try:
                try:
                    scenario = ScenarioType(scenario_type)
                    difficulty = DifficultyLevel(difficulty_level)
                except ValueError as e:
                    raise HTTPException(
                        status_code=422, detail="无效的场景或难度"
                    ) from e
                scenario_context = self.ai_correction_service.get_scenario_context(
                    scenario_type=scenario, difficulty_level=difficulty
                )
                if scenario_context is None:
                    raise HTTPException(
                        status_code=404, detail="该场景与难度的练习内容尚未提供"
                    )

                return ScenarioResponse(
                    scenario_type=scenario_context.scenario_type.value,
                    difficulty_level=scenario_context.difficulty_level.value,
                    context_description=scenario_context.context_description,
                    sample_dialogues=scenario_context.sample_dialogues,
                    key_vocabulary=scenario_context.key_vocabulary,
                    grammar_focus=scenario_context.grammar_focus,
                    pronunciation_targets=scenario_context.pronunciation_targets,
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
                        if (
                            self.ai_correction_service.get_scenario_context(
                                scenario_type, difficulty_level
                            )
                            is None
                        ):
                            continue
                        scenarios.append(
                            {
                                "scenario_type": scenario_type.value,
                                "difficulty_level": difficulty_level.value,
                                "display_name": f"{scenario_type.value.title()} - {difficulty_level.value.title()}",
                            }
                        )
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
                if self.tts and hasattr(self.tts, "get_supported_voices"):
                    supported_voices = self.tts.get_supported_voices()
                    for voice_id in supported_voices:
                        voice_info = self.tts.get_voice_info(voice_id)
                        voices.append(
                            VoiceInfo(
                                id=voice_id,
                                name=voice_info.get("display_name", voice_id),
                                description=voice_info.get("description", ""),
                                language=voice_info.get("language", "zh-CN"),
                                gender=voice_info.get("gender", "female"),
                            )
                        )
                else:
                    # 默认音色
                    voices = [
                        VoiceInfo(
                            id="longxiaochun",
                            name="龙小春",
                            description="温柔甜美的女声",
                            language="zh-CN",
                            gender="female",
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
            lease = self._generated_audio().download(filename)
            try:
                path, media_type = lease.__enter__()
            except FileNotFoundError:
                raise HTTPException(
                    status_code=404, detail="音频已过期或不存在"
                ) from None
            try:
                return AudioDownload(lease, path, media_type)
            except BaseException:
                lease.__exit__(None, None, None)
                raise

        self.logger.info("音频文件路由已注册")

    def run(self, host: str = "0.0.0.0", port: int = 8000, debug: bool = False):
        """运行API服务器"""
        uvicorn.run(
            self.app, host=host, port=port, log_level="debug" if debug else "info"
        )


def create_api_server(settings: Optional[Settings] = None) -> APIServer:
    """创建API服务器实例"""
    return APIServer(settings)


if __name__ == "__main__":
    # 直接运行API服务器
    server = create_api_server()
    server.run(debug=True)
