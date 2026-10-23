# -*- coding: utf-8 -*-
"""
AI驱动的发音评估服务
基于Gemini API实现智能语法纠正、情景对话练习、针对性发音指导和智能场景切换
"""

from typing import Optional, Dict, Any, List, Union, Tuple
import logging
import json
import asyncio
from dataclasses import dataclass
from enum import Enum

try:
    import google.generativeai as genai
except ImportError:
    genai = None

from .correction import CorrectionService, CorrectionReport
from ..core.asr.base import ASRBase
from ..core.assessment.base import AssessmentError
from ..core.assessment.corrector import PronunciationCorrector
from ..core.assessment.phoneme import PhonemeAnalyzer
from ..core.assessment.prosody import ProsodyAnalyzer


logger = logging.getLogger(__name__)


class ScenarioType(Enum):
    """对话场景类型"""
    DAILY_CONVERSATION = "daily_conversation"
    BUSINESS_MEETING = "business_meeting"
    ACADEMIC_DISCUSSION = "academic_discussion"
    TRAVEL_SITUATION = "travel_situation"
    JOB_INTERVIEW = "job_interview"
    RESTAURANT_ORDERING = "restaurant_ordering"
    SHOPPING = "shopping"
    MEDICAL_CONSULTATION = "medical_consultation"


class DifficultyLevel(Enum):
    """难度级别"""
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


@dataclass
class AIAnalysisResult:
    """AI分析结果"""
    grammar_corrections: List[Dict[str, Any]]
    pronunciation_feedback: List[Dict[str, Any]]
    scenario_suggestions: List[Dict[str, Any]]
    personalized_tips: List[str]
    difficulty_assessment: DifficultyLevel
    next_scenario: Optional[ScenarioType]
    confidence_score: float
    detailed_analysis: str
    overall_score: float = 0.0
    recognized_text: str = ""


@dataclass
class ScenarioContext:
    """场景上下文"""
    scenario_type: ScenarioType
    difficulty_level: DifficultyLevel
    context_description: str
    sample_dialogues: List[str]
    key_vocabulary: List[str]
    grammar_focus: List[str]
    pronunciation_targets: List[str]


class AICorrectionService:
    """
    AI驱动的发音评估服务
    集成Gemini API实现智能分析和个性化指导
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_correction_service: Optional[CorrectionService] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        初始化AI纠错服务

        Args:
            api_key: Gemini API密钥
            base_correction_service: 基础纠错服务
            config: 配置参数
        """
        self.api_key = api_key
        self.base_service = base_correction_service or CorrectionService()
        self.config = config or {}
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # 初始化Gemini API
        if genai and api_key:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel('gemini-pro')
        else:
            self.model = None
            self.logger.warning("Gemini API未配置，将使用基础分析功能")
        
        # 场景库
        self.scenarios = self._initialize_scenarios()
        
        # 用户历史记录
        self.user_history = {}
        
        # AI分析提示模板
        self.prompts = self._initialize_prompts()

    def _initialize_scenarios(self) -> Dict[ScenarioType, Dict[DifficultyLevel, ScenarioContext]]:
        """初始化场景库"""
        scenarios = {}
        
        # 日常对话场景
        scenarios[ScenarioType.DAILY_CONVERSATION] = {
            DifficultyLevel.BEGINNER: ScenarioContext(
                scenario_type=ScenarioType.DAILY_CONVERSATION,
                difficulty_level=DifficultyLevel.BEGINNER,
                context_description="基础日常对话练习",
                sample_dialogues=[
                    "Hello, how are you today?",
                    "I'm fine, thank you. And you?",
                    "What's your name?",
                    "Nice to meet you!"
                ],
                key_vocabulary=["hello", "fine", "thank you", "name", "meet"],
                grammar_focus=["present tense", "question formation", "polite expressions"],
                pronunciation_targets=["/θ/", "/ð/", "/r/", "/l/"]
            ),
            DifficultyLevel.INTERMEDIATE: ScenarioContext(
                scenario_type=ScenarioType.DAILY_CONVERSATION,
                difficulty_level=DifficultyLevel.INTERMEDIATE,
                context_description="中级日常对话练习",
                sample_dialogues=[
                    "What did you do over the weekend?",
                    "I went hiking with some friends. It was really enjoyable.",
                    "That sounds like fun! Where did you go?",
                    "We explored a trail in the mountains near the city."
                ],
                key_vocabulary=["weekend", "hiking", "enjoyable", "explored", "trail", "mountains"],
                grammar_focus=["past tense", "descriptive adjectives", "prepositions"],
                pronunciation_targets=["/ɪ/", "/iː/", "/aɪ/", "/aʊ/"]
            )
        }
        
        # 商务会议场景
        scenarios[ScenarioType.BUSINESS_MEETING] = {
            DifficultyLevel.INTERMEDIATE: ScenarioContext(
                scenario_type=ScenarioType.BUSINESS_MEETING,
                difficulty_level=DifficultyLevel.INTERMEDIATE,
                context_description="商务会议讨论",
                sample_dialogues=[
                    "Let's discuss the quarterly sales report.",
                    "Our revenue has increased by 15% this quarter.",
                    "That's excellent news! What factors contributed to this growth?",
                    "We expanded our marketing efforts and improved customer service."
                ],
                key_vocabulary=["quarterly", "revenue", "increased", "factors", "contributed", "expanded", "marketing"],
                grammar_focus=["present perfect", "passive voice", "formal expressions"],
                pronunciation_targets=["/ɜː/", "/ɔː/", "/eɪ/", "/əʊ/"]
            )
        }
        
        return scenarios

    def _initialize_prompts(self) -> Dict[str, str]:
        """初始化AI分析提示模板"""
        return {
            "grammar_analysis": """
你是一位专业的英语语法老师。请分析以下语音识别文本中的语法错误，并提供详细的纠正建议。

识别文本: {recognized_text}
参考文本: {reference_text}

请按以下格式返回JSON:
{{
  "grammar_errors": [
    {{
      "error_type": "错误类型",
      "original": "错误部分",
      "corrected": "正确形式",
      "explanation": "详细解释",
      "rule": "相关语法规则"
    }}
  ],
  "overall_assessment": "整体语法水平评估",
  "improvement_suggestions": ["具体改进建议"]
}}
""",
            
            "pronunciation_analysis": """
你是一位专业的英语发音教练。请分析以下发音评估结果，并提供个性化的发音指导。

基础评估结果: {base_assessment}
识别文本: {recognized_text}
参考文本: {reference_text}

请按以下格式返回JSON:
{{
  "pronunciation_issues": [
    {{
      "phoneme": "音素",
      "issue_description": "问题描述",
      "correction_technique": "纠正技巧",
      "practice_words": ["练习单词"]
    }}
  ],
  "personalized_tips": ["个性化建议"],
  "difficulty_level": "评估的难度级别"
}}
""",
            
            "scenario_recommendation": """
你是一位英语学习顾问。基于用户的发音和语法表现，推荐合适的练习场景。

用户表现:
- 整体评分: {overall_score}
- 发音评分: {pronunciation_score}
- 语法评分: {grammar_score}
- 当前场景: {current_scenario}
- 难度级别: {difficulty_level}

请按以下格式返回JSON:
{{
  "recommended_scenario": "推荐场景",
  "difficulty_adjustment": "难度调整建议",
  "focus_areas": ["重点练习领域"],
  "next_steps": ["下一步学习建议"]
}}
"""
        }

    async def comprehensive_ai_analysis(
        self,
        audio_data: Union[str, bytes, Tuple[int, Any]],
        reference_text: str = "",
        current_scenario: Optional[ScenarioType] = None,
        user_id: Optional[str] = None,
        **kwargs
    ) -> AIAnalysisResult:
        """
        综合AI分析
        
        Args:
            audio_data: 音频数据
            reference_text: 参考文本
            current_scenario: 当前场景
            user_id: 用户ID
            **kwargs: 其他参数
            
        Returns:
            AI分析结果
        """
        self.logger.info("开始综合AI分析")
        # 基础分析失败不能回退为成功报告；同步分析在工作线程中执行。
        base_report = await asyncio.to_thread(
            self.base_service.comprehensive_correction,
            audio_data, reference_text, **kwargs
        )
        if not self.model:
            return self._create_fallback_result(base_report, "AI增强功能未配置")
        if not base_report.recognized_text.strip():
            return self._create_fallback_result(base_report, "识别文本不可用，未执行AI增强")

        try:
            # 2. AI语法分析
            grammar_corrections = await self._analyze_grammar(
                base_report.recognized_text, reference_text
            )
            
            # 3. AI发音分析
            pronunciation_feedback = await self._analyze_pronunciation(
                base_report, reference_text
            )
            
            # 4. 场景推荐
            scenario_suggestions = await self._recommend_scenarios(
                base_report, current_scenario, user_id
            )
            
            # 5. 个性化建议
            personalized_tips = await self._generate_personalized_tips(
                base_report, grammar_corrections, pronunciation_feedback
            )
            
            # 6. 难度评估
            difficulty_assessment = self._assess_difficulty_level(base_report)
            
            # 7. 下一个场景推荐
            next_scenario = self._suggest_next_scenario(
                current_scenario, difficulty_assessment, base_report.overall_score
            )
            
            # 8. 置信度评分
            confidence_score = self._calculate_confidence_score(base_report)
            
            # 9. 详细分析报告
            detailed_analysis = await self._generate_detailed_analysis(
                base_report, grammar_corrections, pronunciation_feedback
            )
            
            result = AIAnalysisResult(
                grammar_corrections=grammar_corrections,
                pronunciation_feedback=pronunciation_feedback,
                scenario_suggestions=scenario_suggestions,
                personalized_tips=personalized_tips,
                difficulty_assessment=difficulty_assessment,
                next_scenario=next_scenario,
                confidence_score=confidence_score,
                detailed_analysis=detailed_analysis,
                overall_score=base_report.overall_score,
                recognized_text=base_report.recognized_text
            )
            
            # 更新用户历史
            if user_id:
                self._update_user_history(user_id, result)
            
            self.logger.info("综合AI分析完成")
            return result
            
        except Exception as e:
            self.logger.error(f"综合AI分析失败: {e}")
            # 返回基础分析结果
            return self._create_fallback_result(base_report)

    async def _analyze_grammar(
        self, recognized_text: str, reference_text: str
    ) -> List[Dict[str, Any]]:
        """AI语法分析"""
        if not self.model or not recognized_text:
            return []
            
        try:
            prompt = self.prompts["grammar_analysis"].format(
                recognized_text=recognized_text,
                reference_text=reference_text
            )
            
            response = await self._call_gemini_api(prompt)
            result = json.loads(response)
            
            errors = self._validated_items(
                result, "grammar_errors", ("error_type", "original", "corrected", "explanation")
            )
            return [dict(error, type=error["error_type"]) for error in errors]

        except Exception as e:
            self.logger.error(f"语法分析失败: {e}")
            raise AssessmentError(f"AI语法分析不可用: {e}") from e

    async def _analyze_pronunciation(
        self, base_report: CorrectionReport, reference_text: str
    ) -> List[Dict[str, Any]]:
        """AI发音分析"""
        if not self.model:
            return []
            
        try:
            prompt = self.prompts["pronunciation_analysis"].format(
                base_assessment=base_report.to_dict(),
                recognized_text=base_report.recognized_text,
                reference_text=reference_text
            )
            
            response = await self._call_gemini_api(prompt)
            result = json.loads(response)
            
            issues = self._validated_items(
                result, "pronunciation_issues",
                ("phoneme", "issue_description", "correction_technique")
            )
            return [dict(
                issue,
                type="pronunciation",
                original=issue["phoneme"],
                corrected=issue["correction_technique"],
                explanation=issue["issue_description"],
            ) for issue in issues]

        except Exception as e:
            self.logger.error(f"发音分析失败: {e}")
            raise AssessmentError(f"AI发音分析不可用: {e}") from e

    @staticmethod
    def _validated_items(
        result: Any, key: str, fields: Tuple[str, ...]
    ) -> List[Dict[str, Any]]:
        """只接受当前报告能够显示的结构化条目。"""
        if not isinstance(result, dict) or not isinstance(result.get(key), list):
            raise ValueError(f"AI响应缺少列表字段 {key}")
        items = result[key]
        for item in items:
            if not isinstance(item, dict) or any(
                not isinstance(item.get(field), str) for field in fields
            ):
                raise ValueError(f"AI响应字段 {key} 的条目格式无效")
        return items

    async def _recommend_scenarios(
        self,
        base_report: CorrectionReport,
        current_scenario: Optional[ScenarioType],
        user_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        """智能场景推荐"""
        try:
            recommendations = []
            score = getattr(base_report, 'overall_score', 0)
            
            # 获取用户历史表现
            user_progress = self.get_user_progress(user_id) if user_id else {"sessions": 0, "average_score": 0}
            
            # 基于当前表现和历史数据的智能推荐
            if score >= 85 and user_progress.get("average_score", 0) >= 80:
                # 高水平用户推荐挑战性场景
                recommendations.extend([
                    {
                        "scenario_type": ScenarioType.ACADEMIC_DISCUSSION.value,
                        "difficulty_level": DifficultyLevel.ADVANCED.value,
                        "reason": "🎓 发音水平优秀，建议尝试学术讨论场景，提升专业表达能力",
                        "focus_areas": ["专业词汇", "逻辑表达", "学术语调"]
                    },
                    {
                        "scenario_type": ScenarioType.JOB_INTERVIEW.value,
                        "difficulty_level": DifficultyLevel.ADVANCED.value,
                        "reason": "💼 可以挑战求职面试场景，练习正式场合的表达",
                        "focus_areas": ["正式用语", "自信表达", "专业形象"]
                    }
                ])
            elif score >= 75:
                # 中高水平用户
                if current_scenario == ScenarioType.DAILY_CONVERSATION:
                    recommendations.append({
                        "scenario_type": ScenarioType.BUSINESS_MEETING.value,
                        "difficulty_level": DifficultyLevel.INTERMEDIATE.value,
                        "reason": "📈 日常对话表现良好，可以尝试商务会议场景",
                        "focus_areas": ["商务词汇", "正式语调", "会议表达"]
                    })
                else:
                    recommendations.append({
                        "scenario_type": ScenarioType.TRAVEL_SITUATION.value,
                        "difficulty_level": DifficultyLevel.INTERMEDIATE.value,
                        "reason": "✈️ 尝试旅行场景，练习实用对话技能",
                        "focus_areas": ["旅行词汇", "问路表达", "服务对话"]
                    })
            elif score >= 60:
                # 中等水平用户
                if current_scenario != ScenarioType.DAILY_CONVERSATION:
                    recommendations.append({
                        "scenario_type": ScenarioType.DAILY_CONVERSATION.value,
                        "difficulty_level": DifficultyLevel.INTERMEDIATE.value,
                        "reason": "💬 建议回到日常对话场景，巩固基础表达能力",
                        "focus_areas": ["日常词汇", "自然语调", "流畅表达"]
                    })
                
                recommendations.append({
                    "scenario_type": ScenarioType.SHOPPING.value,
                    "difficulty_level": DifficultyLevel.INTERMEDIATE.value,
                    "reason": "🛍️ 购物场景适合练习实用对话和数字表达",
                    "focus_areas": ["购物词汇", "价格表达", "礼貌用语"]
                })
            elif score >= 40:
                # 初中级水平用户
                recommendations.extend([
                    {
                        "scenario_type": ScenarioType.DAILY_CONVERSATION.value,
                        "difficulty_level": DifficultyLevel.BEGINNER.value,
                        "reason": "🌱 建议从基础日常对话开始，打好发音基础",
                        "focus_areas": ["基础词汇", "简单句型", "清晰发音"]
                    },
                    {
                        "scenario_type": ScenarioType.RESTAURANT_ORDERING.value,
                        "difficulty_level": DifficultyLevel.BEGINNER.value,
                        "reason": "🍽️ 餐厅点餐场景词汇简单，适合初学者练习",
                        "focus_areas": ["食物词汇", "基本礼貌用语", "简单对话"]
                    }
                ])
            else:
                # 初级水平用户
                recommendations.append({
                    "scenario_type": ScenarioType.DAILY_CONVERSATION.value,
                    "difficulty_level": DifficultyLevel.BEGINNER.value,
                    "reason": "🎯 建议专注于基础日常对话，重点提升发音准确性",
                    "focus_areas": ["音标练习", "单词发音", "基础语调"]
                })
            
            # 基于语法和发音问题的特殊推荐
            if hasattr(base_report, 'pronunciation_errors'):
                error_count = len(base_report.pronunciation_errors)
                if error_count > 5:
                    recommendations.insert(0, {
                        "scenario_type": "pronunciation_focus",
                        "difficulty_level": DifficultyLevel.BEGINNER.value,
                        "reason": "🎯 发现较多发音问题，建议进行专项发音练习",
                        "focus_areas": ["音素纠正", "发音技巧", "口型练习"]
                    })
            
            return recommendations[:3]  # 返回最多3个推荐
            
        except Exception as e:
            self.logger.error(f"场景推荐失败: {e}")
            return [{
                "scenario_type": ScenarioType.DAILY_CONVERSATION.value,
                "difficulty_level": DifficultyLevel.BEGINNER.value,
                "reason": "🔄 系统推荐：从日常对话开始练习",
                "focus_areas": ["基础发音", "简单对话"]
            }]

    async def _generate_personalized_tips(
        self,
        base_report: CorrectionReport,
        grammar_corrections: List[Dict[str, Any]],
        pronunciation_feedback: List[Dict[str, Any]]
    ) -> List[str]:
        """生成个性化建议"""
        tips = []
        
        # 基于发音评分的详细建议
        if hasattr(base_report, 'overall_score'):
            score = base_report.overall_score
            if score < 50:
                tips.extend([
                    "🎯 建议从基础音标开始，每天练习20-30分钟",
                    "📚 重点学习26个字母的标准发音",
                    "🔊 使用语音识别软件进行发音对比练习",
                    "👂 多听标准英语发音，培养语感"
                ])
            elif score < 60:
                tips.extend([
                    "📖 建议每天练习15-20分钟基础发音",
                    "🎵 重点关注元音和辅音的准确性",
                    "🗣️ 练习单词重音和句子节奏",
                    "📱 使用发音练习APP进行日常训练"
                ])
            elif score < 70:
                tips.extend([
                    "⚡ 加强语音连读和弱读练习",
                    "🎭 练习不同语调表达不同情感",
                    "📝 录制自己的发音并与标准发音对比",
                    "👥 寻找语言交换伙伴进行口语练习"
                ])
            elif score < 80:
                tips.extend([
                    "🎪 继续练习语调和节奏的自然性",
                    "📈 可以尝试更复杂的句子结构",
                    "🎬 通过观看英语电影提升语音表现力",
                    "💬 参与英语讨论，提高口语流畅度"
                ])
            else:
                tips.extend([
                    "🌟 发音水平很好，可以挑战更高难度的内容",
                    "🎨 注重语音的自然流畅性和表现力",
                    "🎤 尝试英语演讲或朗诵提升表达能力",
                    "🌍 探索不同英语口音，丰富语音技能"
                ])
        
        # 基于语法错误的具体建议
        if grammar_corrections:
            error_types = set()
            for correction in grammar_corrections:
                error_types.add(correction.get('error_type', ''))
            
            tips.append(f"📝 发现{len(grammar_corrections)}个语法问题，建议重点练习:")
            
            if 'tense' in str(error_types).lower():
                tips.append("⏰ 加强时态练习，注意动词变化规则")
            if 'article' in str(error_types).lower():
                tips.append("📄 重点练习冠词(a/an/the)的正确使用")
            if 'preposition' in str(error_types).lower():
                tips.append("🔗 加强介词搭配练习")
            if 'subject-verb' in str(error_types).lower():
                tips.append("👥 注意主谓一致的语法规则")
        
        # 基于发音反馈的专项建议
        if pronunciation_feedback:
            phoneme_issues = set()
            for feedback in pronunciation_feedback:
                phoneme = feedback.get('phoneme', '')
                if phoneme:
                    phoneme_issues.add(phoneme)
            
            tips.append("🎯 针对特定音素进行专项练习:")
            
            if '/θ/' in phoneme_issues or '/ð/' in phoneme_issues:
                tips.append("👅 th音练习: 舌尖轻触上齿，练习think, that, three")
            if '/r/' in phoneme_issues:
                tips.append("🌀 r音练习: 舌尖上卷，练习red, right, very")
            if '/l/' in phoneme_issues:
                tips.append("📍 l音练习: 舌尖抵住上齿龈，练习light, love, well")
            if '/v/' in phoneme_issues or '/w/' in phoneme_issues:
                tips.append("💋 v/w音练习: 注意唇齿接触，练习very, water, wave")
        
        # 基于错误严重程度的建议
        if hasattr(base_report, 'pronunciation_errors'):
            severe_errors = [e for e in base_report.pronunciation_errors if e.get('severity') == 'high']
            if severe_errors:
                tips.append("🚨 发现严重发音错误，建议寻求专业指导")
        
        # 学习策略建议
        tips.extend([
            "📅 制定每日练习计划，保持学习连续性",
            "🎧 利用碎片时间进行听力和跟读练习",
            "📊 定期记录学习进度，及时调整学习方法"
        ])
        
        return tips

    def _assess_difficulty_level(self, base_report: CorrectionReport) -> DifficultyLevel:
        """评估难度级别"""
        if hasattr(base_report, 'overall_score'):
            score = base_report.overall_score
            if score >= 85:
                return DifficultyLevel.ADVANCED
            elif score >= 70:
                return DifficultyLevel.INTERMEDIATE
            else:
                return DifficultyLevel.BEGINNER
        return DifficultyLevel.BEGINNER

    def _suggest_next_scenario(
        self,
        current_scenario: Optional[ScenarioType],
        difficulty_level: DifficultyLevel,
        overall_score: float
    ) -> Optional[ScenarioType]:
        """智能建议下一个场景"""
        # 基于表现和当前场景的智能切换逻辑
        
        if overall_score >= 85:
            # 高分用户的进阶路径
            progression_map = {
                ScenarioType.DAILY_CONVERSATION: ScenarioType.BUSINESS_MEETING,
                ScenarioType.BUSINESS_MEETING: ScenarioType.ACADEMIC_DISCUSSION,
                ScenarioType.SHOPPING: ScenarioType.TRAVEL_SITUATION,
                ScenarioType.RESTAURANT_ORDERING: ScenarioType.SHOPPING,
                ScenarioType.TRAVEL_SITUATION: ScenarioType.JOB_INTERVIEW,
                ScenarioType.JOB_INTERVIEW: ScenarioType.ACADEMIC_DISCUSSION,
                ScenarioType.ACADEMIC_DISCUSSION: ScenarioType.MEDICAL_CONSULTATION
            }
            return progression_map.get(current_scenario, ScenarioType.BUSINESS_MEETING)
        
        elif overall_score >= 70:
            # 中高分用户的稳步提升
            if current_scenario == ScenarioType.DAILY_CONVERSATION:
                return ScenarioType.SHOPPING
            elif current_scenario == ScenarioType.SHOPPING:
                return ScenarioType.TRAVEL_SITUATION
            elif current_scenario == ScenarioType.RESTAURANT_ORDERING:
                return ScenarioType.DAILY_CONVERSATION
            else:
                return ScenarioType.BUSINESS_MEETING
        
        elif overall_score >= 50:
            # 中等分数用户的基础巩固
            basic_scenarios = [
                ScenarioType.DAILY_CONVERSATION,
                ScenarioType.RESTAURANT_ORDERING,
                ScenarioType.SHOPPING
            ]
            
            if current_scenario in basic_scenarios:
                # 在基础场景间轮换
                current_index = basic_scenarios.index(current_scenario)
                next_index = (current_index + 1) % len(basic_scenarios)
                return basic_scenarios[next_index]
            else:
                # 回到基础场景
                return ScenarioType.DAILY_CONVERSATION
        
        else:
            # 低分用户专注基础
            if current_scenario != ScenarioType.DAILY_CONVERSATION:
                return ScenarioType.DAILY_CONVERSATION
            else:
                return ScenarioType.RESTAURANT_ORDERING  # 简单的实用场景
        
        # 默认返回日常对话
        return ScenarioType.DAILY_CONVERSATION

    def _calculate_confidence_score(self, base_report: CorrectionReport) -> float:
        """计算置信度评分"""
        # 基于多个因素计算置信度
        factors = []
        
        if hasattr(base_report, 'overall_score'):
            factors.append(base_report.overall_score / 100)
        
        if hasattr(base_report, 'recognized_text') and base_report.recognized_text:
            # 文本长度因素
            text_length_factor = min(len(base_report.recognized_text.split()) / 10, 1.0)
            factors.append(text_length_factor)
        
        return sum(factors) / len(factors) if factors else 0.5

    async def _generate_detailed_analysis(
        self,
        base_report: CorrectionReport,
        grammar_corrections: List[Dict[str, Any]],
        pronunciation_feedback: List[Dict[str, Any]]
    ) -> str:
        """生成详细分析报告"""
        analysis = "# 🤖 AI驱动的综合分析报告\n\n"
        
        # 总体评估
        if hasattr(base_report, 'overall_score'):
            analysis += f"## 📊 总体评分: {base_report.overall_score:.1f}/100\n\n"
        
        # 语法分析
        if grammar_corrections:
            analysis += "## 📝 语法分析\n"
            for i, correction in enumerate(grammar_corrections[:3], 1):
                analysis += f"{i}. **{correction.get('error_type', '语法错误')}**: "
                analysis += f"{correction.get('original', '')} → {correction.get('corrected', '')}\n"
                analysis += f"   💡 {correction.get('explanation', '')}\n\n"
        
        # 发音分析
        if pronunciation_feedback:
            analysis += "## 🗣️ 发音分析\n"
            for i, feedback in enumerate(pronunciation_feedback[:3], 1):
                analysis += f"{i}. **音素 {feedback.get('phoneme', '')}**: "
                analysis += f"{feedback.get('issue_description', '')}\n"
                analysis += f"   💡 {feedback.get('correction_technique', '')}\n\n"
        
        return analysis

    async def _call_gemini_api(self, prompt: str) -> str:
        """调用Gemini API"""
        if not self.model:
            raise Exception("Gemini API未配置")
        
        try:
            response = await asyncio.to_thread(self.model.generate_content, prompt)
            return response.text
        except Exception as e:
            self.logger.error(f"Gemini API调用失败: {e}")
            raise

    def _create_fallback_result(
        self, base_report: CorrectionReport, reason: str = "AI增强功能暂时不可用"
    ) -> AIAnalysisResult:
        """保留已完成的基础报告，明确标识未完成的AI增强。"""
        return AIAnalysisResult(
            grammar_corrections=[],
            pronunciation_feedback=[],
            scenario_suggestions=[],
            personalized_tips=list(base_report.improvement_suggestions),
            difficulty_assessment=self._assess_difficulty_level(base_report),
            next_scenario=None,
            confidence_score=0.0,
            detailed_analysis=f"基础分析完成，{reason}。\n\n{base_report.detailed_feedback}",
            overall_score=base_report.overall_score,
            recognized_text=base_report.recognized_text
        )

    def _update_user_history(self, user_id: str, result: AIAnalysisResult):
        """更新用户历史记录"""
        if user_id not in self.user_history:
            self.user_history[user_id] = []
        
        self.user_history[user_id].append({
            "timestamp": asyncio.get_event_loop().time(),
            "difficulty_level": result.difficulty_assessment.value,
            "overall_performance": result.confidence_score,
            "grammar_issues": len(result.grammar_corrections),
            "pronunciation_issues": len(result.pronunciation_feedback)
        })
        
        # 保持最近50条记录
        if len(self.user_history[user_id]) > 50:
            self.user_history[user_id] = self.user_history[user_id][-50:]

    def get_scenario_context(self, scenario_type: ScenarioType, difficulty_level: DifficultyLevel) -> Optional[ScenarioContext]:
        """获取场景上下文"""
        return self.scenarios.get(scenario_type, {}).get(difficulty_level)

    def get_user_progress(self, user_id: str) -> Dict[str, Any]:
        """获取用户进度"""
        if user_id not in self.user_history:
            return {"sessions": 0, "average_score": 0, "improvement_trend": "无数据"}
        
        history = self.user_history[user_id]
        sessions = len(history)
        avg_score = sum(h["overall_performance"] for h in history) / sessions
        
        # 计算改进趋势
        if sessions >= 2:
            recent_avg = sum(h["overall_performance"] for h in history[-5:]) / min(5, sessions)
            early_avg = sum(h["overall_performance"] for h in history[:5]) / min(5, sessions)
            trend = "提升" if recent_avg > early_avg else "稳定" if recent_avg == early_avg else "需要加强"
        else:
            trend = "数据不足"
        
        return {
            "sessions": sessions,
            "average_score": avg_score,
            "improvement_trend": trend
        }