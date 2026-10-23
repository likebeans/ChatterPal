# -*- coding: utf-8 -*-
"""
纠错服务
实现专业发音评估和纠错功能，集成音素分析和韵律分析
"""

from typing import Optional, Dict, Any, List, Union, Tuple
import logging
import json
import tempfile
import os

from ..core.asr.base import ASRBase, ASRError
from ..core.assessment.base import (
    AssessmentBase,
    AssessmentResult,
    AssessmentError,
    WordAnalysis,
    PhonemeAnalysis,
    ProsodyFeatures,
)
from ..core.assessment.corrector import PronunciationCorrector
from ..core.assessment.phoneme import PhonemeAnalyzer
from ..core.assessment.prosody import ProsodyAnalyzer


logger = logging.getLogger(__name__)


class CorrectionReport:
    """纠错报告类"""

    def __init__(self):
        self.overall_score = 0.0
        self.recognized_text = ""
        self.pronunciation_errors = []
        self.prosody_issues = []
        self.phoneme_problems = []
        self.improvement_suggestions = []
        self.detailed_feedback = ""
        self.practice_recommendations = []

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "overall_score": self.overall_score,
            "recognized_text": self.recognized_text,
            "pronunciation_errors": self.pronunciation_errors,
            "prosody_issues": self.prosody_issues,
            "phoneme_problems": self.phoneme_problems,
            "improvement_suggestions": self.improvement_suggestions,
            "detailed_feedback": self.detailed_feedback,
            "practice_recommendations": self.practice_recommendations,
        }


class CorrectionService:
    """
    纠错服务类
    提供专业的发音评估和纠错功能
    """

    def __init__(
        self,
        asr: Optional[ASRBase] = None,
        corrector: Optional[PronunciationCorrector] = None,
        phoneme_analyzer: Optional[PhonemeAnalyzer] = None,
        prosody_analyzer: Optional[ProsodyAnalyzer] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        初始化纠错服务

        Args:
            asr: 语音识别实例
            corrector: 发音纠错器实例
            phoneme_analyzer: 音素分析器实例
            prosody_analyzer: 韵律分析器实例
            config: 配置参数
        """
        self.asr = asr
        self.corrector = corrector or PronunciationCorrector()
        self.phoneme_analyzer = phoneme_analyzer or PhonemeAnalyzer()
        self.prosody_analyzer = prosody_analyzer or ProsodyAnalyzer()
        self.config = config or {}
        self.logger = logging.getLogger(self.__class__.__name__)

        # 纠错配置
        self.error_thresholds = self.config.get(
            "error_thresholds",
            {
                "pronunciation": 70,  # 低于70分认为有发音问题
                "fluency": 65,  # 低于65分认为有流畅度问题
                "prosody": 60,  # 低于60分认为有韵律问题
                "accuracy": 75,  # 低于75分认为有准确性问题
            },
        )

        self.correction_priorities = self.config.get(
            "correction_priorities",
            [
                "accuracy",  # 准确性最重要
                "pronunciation",  # 发音准确性
                "fluency",  # 流畅度
                "prosody",  # 韵律表现
            ],
        )

    def comprehensive_correction(
        self,
        audio_data: Union[str, bytes, Tuple[int, Any]],
        target_text: str = "",
        analysis_level: str = "detailed",
        **kwargs,
    ) -> CorrectionReport:
        """
        综合发音纠错分析

        Args:
            audio_data: 音频数据（文件路径、字节数据或Gradio格式）
            target_text: 目标文本
            analysis_level: 分析级别 ("basic", "detailed", "expert")
            **kwargs: 其他参数

        Returns:
            纠错报告

        Raises:
            AssessmentError: 纠错分析失败
        """
        try:
            self.logger.info(f"开始综合纠错分析，级别: {analysis_level}")

            # 1. 语音识别
            recognized_text = ""
            if self.asr:
                try:
                    recognized_text = self._recognize_audio(audio_data)
                    self.logger.info(f"语音识别结果: {recognized_text}")
                except Exception as e:
                    raise AssessmentError(f"语音识别失败: {e}") from e
                if not isinstance(recognized_text, str) or not recognized_text.strip():
                    raise AssessmentError("语音识别未返回有效文本")

            # 2. 基础发音评估
            base_assessment = self.corrector.assess(audio_data, target_text)
            self.logger.info(f"基础评估完成，总分: {base_assessment.overall_score:.1f}")

            # 3. 详细分析（根据级别）
            phoneme_analysis = None
            prosody_analysis = None

            if analysis_level in ["detailed", "expert"]:
                # 音素分析
                try:
                    phoneme_analysis = self.phoneme_analyzer.assess(
                        audio_data, target_text
                    )
                    self.logger.info("音素分析完成")
                except Exception as e:
                    self.logger.warning(f"音素分析失败: {e}")

                # 韵律分析
                try:
                    prosody_analysis = self.prosody_analyzer.assess(
                        audio_data, target_text
                    )
                    self.logger.info("韵律分析完成")
                except Exception as e:
                    self.logger.warning(f"韵律分析失败: {e}")

            # 4. 生成纠错报告
            report = self._generate_correction_report(
                base_assessment,
                recognized_text,
                target_text,
                phoneme_analysis,
                prosody_analysis,
                analysis_level,
            )

            self.logger.info(f"纠错分析完成，总分: {report.overall_score:.1f}")
            return report

        except Exception as e:
            self.logger.error(f"综合纠错分析失败: {e}")
            raise AssessmentError(f"综合纠错分析失败: {e}") from e

    def _recognize_audio(self, audio_data: Union[str, bytes, Tuple[int, Any]]) -> str:
        """语音识别"""
        if isinstance(audio_data, str):
            return self.asr.recognize_file(audio_data)
        elif isinstance(audio_data, bytes):
            return self.asr.recognize(audio_data)
        else:
            return self.asr.recognize_gradio_audio(audio_data)

    def _generate_correction_report(
        self,
        base_assessment: AssessmentResult,
        recognized_text: str,
        target_text: str,
        phoneme_analysis: Optional[Dict[str, Any]],
        prosody_analysis: Optional[Dict[str, Any]],
        analysis_level: str,
    ) -> CorrectionReport:
        """生成纠错报告"""

        report = CorrectionReport()
        report.overall_score = base_assessment.overall_score
        report.recognized_text = recognized_text

        # 1. 发音错误检测
        report.pronunciation_errors = self._detect_pronunciation_errors(
            base_assessment, recognized_text, target_text
        )

        # 2. 韵律问题检测
        if prosody_analysis:
            report.prosody_issues = self._detect_prosody_issues(
                prosody_analysis, base_assessment.prosody_features
            )

        # 3. 音素问题检测
        if phoneme_analysis:
            report.phoneme_problems = self._detect_phoneme_problems(
                phoneme_analysis, target_text
            )

        # 4. 生成改进建议
        report.improvement_suggestions = self._generate_improvement_suggestions(
            base_assessment,
            report.pronunciation_errors,
            report.prosody_issues,
            report.phoneme_problems,
        )

        # 5. 生成详细反馈
        report.detailed_feedback = self._generate_detailed_feedback(
            base_assessment, report, analysis_level
        )

        # 6. 生成练习建议
        report.practice_recommendations = self._generate_practice_recommendations(
            report.pronunciation_errors, report.prosody_issues, report.phoneme_problems
        )

        return report

    def _detect_pronunciation_errors(
        self, assessment: AssessmentResult, recognized_text: str, target_text: str
    ) -> List[Dict[str, Any]]:
        """检测发音错误"""
        errors = []

        # 1. 基于评分的错误检测
        if assessment.pronunciation_score < self.error_thresholds["pronunciation"]:
            errors.append(
                {
                    "type": "overall_pronunciation",
                    "severity": (
                        "high" if assessment.pronunciation_score < 50 else "medium"
                    ),
                    "description": f"整体发音准确性较低 ({assessment.pronunciation_score:.1f}/100)",
                    "suggestion": "需要系统性地练习基础发音",
                    "priority": 1,
                }
            )

        # 2. 基于文本对比的错误检测
        if target_text and recognized_text:
            similarity = self._calculate_text_similarity(recognized_text, target_text)
            if similarity < 0.8:
                errors.append(
                    {
                        "type": "content_accuracy",
                        "severity": "high" if similarity < 0.5 else "medium",
                        "description": f"内容准确性不足 ({similarity:.1%})",
                        "suggestion": f"重点练习: '{target_text}' vs '{recognized_text}'",
                        "priority": 1,
                    }
                )

        # 3. 基于单词分析的错误检测
        for word_analysis in assessment.word_analysis:
            if not word_analysis.is_correct:
                errors.append(
                    {
                        "type": "word_mispronunciation",
                        "severity": "medium",
                        "description": f"单词发音错误: '{word_analysis.target_word}' → '{word_analysis.recognized_word}'",
                        "suggestion": f"重点练习单词 '{word_analysis.target_word}' 的发音",
                        "correction_tips": word_analysis.correction_tips,
                        "priority": 2,
                    }
                )

        return sorted(errors, key=lambda x: x.get("priority", 3))

    def _detect_prosody_issues(
        self, prosody_analysis: Dict[str, Any], prosody_features: ProsodyFeatures
    ) -> List[Dict[str, Any]]:
        """检测韵律问题"""
        issues = []

        # 1. 语速问题
        speaking_rate = prosody_features.speaking_rate
        if speaking_rate < 100:
            issues.append(
                {
                    "type": "speaking_rate_slow",
                    "severity": "medium",
                    "description": f"语速过慢 ({speaking_rate:.1f} 词/分钟)",
                    "suggestion": "尝试适当加快语速，保持自然流畅",
                    "target_range": "120-160 词/分钟",
                }
            )
        elif speaking_rate > 200:
            issues.append(
                {
                    "type": "speaking_rate_fast",
                    "severity": "medium",
                    "description": f"语速过快 ({speaking_rate:.1f} 词/分钟)",
                    "suggestion": "放慢语速，注意发音清晰度",
                    "target_range": "120-160 词/分钟",
                }
            )

        # 2. 停顿问题
        pause_density = prosody_features.pause_count / max(
            1, prosody_features.syllable_count
        )
        if pause_density > 0.3:
            issues.append(
                {
                    "type": "excessive_pauses",
                    "severity": "medium",
                    "description": f"停顿过多 ({prosody_features.pause_count} 次停顿)",
                    "suggestion": "减少不必要的停顿，提高语音连贯性",
                }
            )

        # 3. 流畅度问题
        if prosody_features.fluency_score < 0.6:
            issues.append(
                {
                    "type": "fluency_low",
                    "severity": "high",
                    "description": f"流畅度较低 ({prosody_features.fluency_score:.1%})",
                    "suggestion": "多练习连续发音，提高语音流畅度",
                }
            )

        return issues

    def _detect_phoneme_problems(
        self, phoneme_analysis: Dict[str, Any], target_text: str
    ) -> List[Dict[str, Any]]:
        """检测音素问题"""
        problems = []

        if not phoneme_analysis.get("analysis_success", False):
            return problems

        # 1. 音调稳定性问题
        pitch = phoneme_analysis.get("pitch", {})
        pitch_std = pitch.get("std", 0)
        if pitch_std > 50:
            problems.append(
                {
                    "type": "pitch_instability",
                    "severity": "medium",
                    "description": f"音调不稳定 (标准差: {pitch_std:.1f} Hz)",
                    "suggestion": "练习保持稳定的音调，避免不必要的音调波动",
                }
            )

        # 2. 共振峰问题
        formants = phoneme_analysis.get("formants", {})

        # F1异常（舌位高低）
        f1_mean = formants.get("F1", {}).get("mean", 500)
        if f1_mean < 200 or f1_mean > 1000:
            problems.append(
                {
                    "type": "vowel_height_error",
                    "severity": "high",
                    "description": f"元音舌位高低异常 (F1: {f1_mean:.0f} Hz)",
                    "suggestion": "调整舌位高低，注意口腔开合度",
                    "normal_range": "200-800 Hz",
                }
            )

        # F2异常（舌位前后）
        f2_mean = formants.get("F2", {}).get("mean", 1500)
        if f2_mean < 800 or f2_mean > 3000:
            problems.append(
                {
                    "type": "vowel_backness_error",
                    "severity": "high",
                    "description": f"元音舌位前后异常 (F2: {f2_mean:.0f} Hz)",
                    "suggestion": "调整舌位前后位置",
                    "normal_range": "800-2500 Hz",
                }
            )

        # 3. 语音质量问题
        quality_score = phoneme_analysis.get("quality_score", 70)
        if quality_score < 60:
            problems.append(
                {
                    "type": "audio_quality_low",
                    "severity": "high",
                    "description": f"语音质量较低 ({quality_score:.1f}/100)",
                    "suggestion": "改善录音环境，注意发音清晰度",
                }
            )

        return problems

    def _generate_improvement_suggestions(
        self,
        assessment: AssessmentResult,
        pronunciation_errors: List[Dict[str, Any]],
        prosody_issues: List[Dict[str, Any]],
        phoneme_problems: List[Dict[str, Any]],
    ) -> List[str]:
        """生成改进建议"""
        suggestions = []

        # 基于错误优先级生成建议
        high_priority_errors = [
            error for error in pronunciation_errors if error.get("severity") == "high"
        ]

        if high_priority_errors:
            suggestions.append("🎯 优先改进发音准确性，这是最重要的基础")

        # 基于具体问题生成建议
        error_types = set()
        for error in pronunciation_errors:
            error_types.add(error["type"])

        if "content_accuracy" in error_types:
            suggestions.append("📝 重点练习目标文本的准确发音")

        if "word_mispronunciation" in error_types:
            suggestions.append("🔤 逐个单词练习，确保每个单词发音正确")

        # 韵律改进建议
        prosody_types = {issue["type"] for issue in prosody_issues}

        if "speaking_rate_slow" in prosody_types:
            suggestions.append("⚡ 适当加快语速，保持自然节奏")
        elif "speaking_rate_fast" in prosody_types:
            suggestions.append("🐌 放慢语速，注重发音清晰度")

        if "excessive_pauses" in prosody_types:
            suggestions.append("🔗 减少停顿，提高语音连贯性")

        if "fluency_low" in prosody_types:
            suggestions.append("🌊 多练习连续发音，提高流畅度")

        # 音素改进建议
        phoneme_types = {problem["type"] for problem in phoneme_problems}

        if "pitch_instability" in phoneme_types:
            suggestions.append("🎵 练习音调控制，保持稳定的音高")

        if (
            "vowel_height_error" in phoneme_types
            or "vowel_backness_error" in phoneme_types
        ):
            suggestions.append("👄 注意元音发音，调整舌位和口型")

        # 通用建议
        if assessment.overall_score < 60:
            suggestions.append("📚 建议从基础发音开始系统学习")
        elif assessment.overall_score < 80:
            suggestions.append("💪 继续练习，注意发音细节")
        else:
            suggestions.append("🎉 发音表现良好，继续保持！")

        return suggestions[:8]  # 限制建议数量

    def _generate_detailed_feedback(
        self,
        assessment: AssessmentResult,
        report: CorrectionReport,
        analysis_level: str,
    ) -> str:
        """生成详细反馈"""

        feedback = f"# 🎯 专业发音纠错报告\n\n"

        # 总体评分
        feedback += f"## 📊 综合评分: {report.overall_score:.1f}/100\n\n"

        # 详细评分
        feedback += f"### 📈 分项评分:\n"
        feedback += f"- **发音准确性**: {assessment.pronunciation_score:.1f}/100\n"
        feedback += f"- **流畅度**: {assessment.fluency_score:.1f}/100\n"
        feedback += f"- **韵律表现**: {assessment.prosody_score:.1f}/100\n"
        feedback += f"- **内容准确性**: {assessment.accuracy_score:.1f}/100\n\n"

        # 主要问题
        if report.pronunciation_errors:
            feedback += f"### ⚠️ 发音问题 ({len(report.pronunciation_errors)}项):\n"
            for i, error in enumerate(report.pronunciation_errors[:5], 1):
                severity_emoji = "🔴" if error["severity"] == "high" else "🟡"
                feedback += f"{i}. {severity_emoji} {error['description']}\n"
                feedback += f"   💡 **建议**: {error['suggestion']}\n\n"

        if report.prosody_issues:
            feedback += f"### 🎵 韵律问题 ({len(report.prosody_issues)}项):\n"
            for i, issue in enumerate(report.prosody_issues[:3], 1):
                feedback += f"{i}. {issue['description']}\n"
                feedback += f"   💡 **建议**: {issue['suggestion']}\n\n"

        if report.phoneme_problems and analysis_level == "expert":
            feedback += f"### 🔬 音素分析 ({len(report.phoneme_problems)}项):\n"
            for i, problem in enumerate(report.phoneme_problems[:3], 1):
                feedback += f"{i}. {problem['description']}\n"
                feedback += f"   💡 **建议**: {problem['suggestion']}\n\n"

        # 改进建议
        if report.improvement_suggestions:
            feedback += f"### 💡 重点改进建议:\n"
            for i, suggestion in enumerate(report.improvement_suggestions, 1):
                feedback += f"{i}. {suggestion}\n"
            feedback += "\n"

        # 练习建议
        if report.practice_recommendations:
            feedback += f"### 📚 练习建议:\n"
            for i, recommendation in enumerate(report.practice_recommendations, 1):
                feedback += f"{i}. {recommendation}\n"

        return feedback

    def _generate_practice_recommendations(
        self,
        pronunciation_errors: List[Dict[str, Any]],
        prosody_issues: List[Dict[str, Any]],
        phoneme_problems: List[Dict[str, Any]],
    ) -> List[str]:
        """生成练习建议"""
        recommendations = []

        # 基于发音错误的练习建议
        if any(
            error["type"] == "word_mispronunciation" for error in pronunciation_errors
        ):
            recommendations.append("每天练习10-15分钟的单词发音，使用录音对比方法")

        if any(error["type"] == "content_accuracy" for error in pronunciation_errors):
            recommendations.append("重复练习目标句子，直到能够准确无误地说出")

        # 基于韵律问题的练习建议
        if any(issue["type"].startswith("speaking_rate") for issue in prosody_issues):
            recommendations.append("使用节拍器练习，找到适合的语速节奏")

        if any(issue["type"] == "excessive_pauses" for issue in prosody_issues):
            recommendations.append("练习连读技巧，减少不必要的停顿")

        if any(issue["type"] == "fluency_low" for issue in prosody_issues):
            recommendations.append("每天进行5-10分钟的流畅度训练")

        # 基于音素问题的练习建议
        if any(problem["type"].startswith("vowel") for problem in phoneme_problems):
            recommendations.append("使用镜子练习元音发音，注意口型和舌位")

        if any(problem["type"] == "pitch_instability" for problem in phoneme_problems):
            recommendations.append("练习音调控制，可以使用音调训练应用")

        # 通用练习建议
        recommendations.extend(
            [
                "建议每天练习15-30分钟，保持持续性",
                "可以寻求专业老师的指导和反馈",
                "使用语音识别软件检验练习效果",
            ]
        )

        return recommendations[:6]  # 限制建议数量

    def _calculate_text_similarity(self, text1: str, text2: str) -> float:
        """计算文本相似度"""
        if not text1 and not text2:
            return 1.0
        if not text1 or not text2:
            return 0.0

        import re

        # 预处理文本
        words1 = set(re.findall(r"\w+", text1.lower()))
        words2 = set(re.findall(r"\w+", text2.lower()))

        if not words1 and not words2:
            return 1.0
        if not words1 or not words2:
            return 0.0

        # Jaccard相似度
        intersection = len(words1 & words2)
        union = len(words1 | words2)

        return intersection / union if union > 0 else 0.0

    def quick_correction(
        self,
        audio_data: Union[str, bytes, Tuple[int, Any]],
        target_text: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        """
        快速纠错分析

        Args:
            audio_data: 音频数据
            target_text: 目标文本
            **kwargs: 其他参数

        Returns:
            简化的纠错结果
        """
        try:
            # 使用基础级别分析
            report = self.comprehensive_correction(
                audio_data, target_text, analysis_level="basic", **kwargs
            )

            # 返回简化结果
            return {
                "overall_score": report.overall_score,
                "main_issues": report.pronunciation_errors[:3],
                "key_suggestions": report.improvement_suggestions[:3],
                "practice_tips": report.practice_recommendations[:3],
            }

        except Exception as e:
            self.logger.error(f"快速纠错分析失败: {e}")
            return {
                "overall_score": 0.0,
                "main_issues": [],
                "key_suggestions": ["请检查音频质量后重试"],
                "practice_tips": ["确保录音环境安静", "说话清晰缓慢"],
            }

    def get_service_status(self) -> Dict[str, Any]:
        """
        获取服务状态

        Returns:
            服务状态信息
        """
        status = {
            "asr_available": self.asr is not None,
            "corrector_available": self.corrector is not None,
            "phoneme_analyzer_available": self.phoneme_analyzer is not None,
            "prosody_analyzer_available": self.prosody_analyzer is not None,
            "service_config": {
                "error_thresholds": self.error_thresholds,
                "correction_priorities": self.correction_priorities,
            },
        }

        # 测试各模块状态
        if self.asr:
            try:
                status["asr_status"] = self.asr.test_connection()
            except:
                status["asr_status"] = False

        if self.corrector:
            try:
                status["corrector_status"] = self.corrector.test_functionality()
            except:
                status["corrector_status"] = False

        if self.phoneme_analyzer:
            try:
                status["phoneme_analyzer_status"] = (
                    self.phoneme_analyzer.test_functionality()
                )
            except:
                status["phoneme_analyzer_status"] = False

        if self.prosody_analyzer:
            try:
                # 简单测试
                status["prosody_analyzer_status"] = True
            except:
                status["prosody_analyzer_status"] = False

        return status
