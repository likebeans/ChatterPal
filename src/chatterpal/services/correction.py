# -*- coding: utf-8 -*-
"""Correction reports distinguish transcript edits and measured recording features."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, TYPE_CHECKING, Tuple, Union

from ..core.assessment.base import (
    AssessmentError, AssessmentResult, ProsodyFeatures, dimension_result, finite_number,
)
from ..core.assessment.corrector import PronunciationCorrector

if TYPE_CHECKING:
    from ..core.asr.base import ASRBase
    from ..core.assessment.phoneme import PhonemeAnalyzer
    from ..core.assessment.prosody import ProsodyAnalyzer


class CorrectionReport:
    """Backward compatible report fields with explicit unknowns and provenance."""

    def __init__(self):
        self.overall_score = None
        self.recognized_text = ""
        self.pronunciation_errors = []
        self.prosody_issues = []
        self.phoneme_problems = []
        self.improvement_suggestions = []
        self.detailed_feedback = ""
        self.practice_recommendations = []
        self.dimensions = {}
        self.feature_metadata = {}
        self.calibrated = False
        self.scope = "识别文本对齐和录音声学摘要；未校准，不代表音素准确率、语言能力或学习进步。"

    def to_dict(self) -> Dict[str, Any]:
        return dict(vars(self))


class CorrectionService:
    """Keep useful ASR text feedback available without local acoustic packages."""

    def __init__(
        self,
        asr: Optional[ASRBase] = None,
        corrector: Optional[PronunciationCorrector] = None,
        phoneme_analyzer: Optional[PhonemeAnalyzer] = None,
        prosody_analyzer: Optional[ProsodyAnalyzer] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.asr = asr
        self.corrector = corrector or PronunciationCorrector()
        self._phoneme_analyzer = phoneme_analyzer
        self._prosody_analyzer = prosody_analyzer
        self.config = config or {}
        self.logger = logging.getLogger(self.__class__.__name__)
        self.error_thresholds = self.config.get("error_thresholds", {})
        self.correction_priorities = self.config.get("correction_priorities", ["accuracy"])

    @property
    def phoneme_analyzer(self):
        if self._phoneme_analyzer is None:
            from ..core.assessment.phoneme import PhonemeAnalyzer

            self._phoneme_analyzer = PhonemeAnalyzer()
        return self._phoneme_analyzer

    @property
    def prosody_analyzer(self):
        if self._prosody_analyzer is None:
            from ..core.assessment.prosody import ProsodyAnalyzer

            self._prosody_analyzer = ProsodyAnalyzer()
        return self._prosody_analyzer

    def comprehensive_correction(
        self,
        audio_data: Union[str, bytes, Tuple[int, Any]],
        target_text: str = "",
        analysis_level: str = "detailed",
        **kwargs,
    ) -> CorrectionReport:
        if not self.corrector.validate_audio_data(audio_data):
            raise AssessmentError("音频数据验证失败")
        recognized_text = ""
        if self.asr:
            try:
                recognized_text = self._recognize_audio(audio_data)
            except Exception as error:
                raise AssessmentError(f"语音识别失败: {error}") from error
            if not isinstance(recognized_text, str) or not recognized_text.strip():
                raise AssessmentError("语音识别未返回有效文本")

        # Reuse one acoustic extraction, including the explicit unavailable result.
        try:
            acoustic = self.phoneme_analyzer.assess(audio_data, target_text)
        except Exception as error:
            acoustic = {"analysis_success": False, "error": str(error)}
        base = self.corrector.assess(
            audio_data, target_text, phoneme_analysis=acoustic, recognized_text=recognized_text,
        )
        prosody = None
        if analysis_level in ("detailed", "expert"):
            try:
                prosody = self.prosody_analyzer.assess(audio_data, target_text)
            except Exception as error:
                prosody = {"analysis_success": False, "error": str(error)}
        return self._generate_correction_report(
            base, recognized_text, target_text, acoustic, prosody, analysis_level,
        )

    def _recognize_audio(self, audio_data) -> str:
        if isinstance(audio_data, str):
            return self.asr.recognize_file(audio_data)
        if isinstance(audio_data, bytes):
            return self.asr.recognize(audio_data)
        return self.asr.recognize_gradio_audio(audio_data)

    def _generate_correction_report(
        self, base_assessment: AssessmentResult, recognized_text: str, target_text: str,
        phoneme_analysis: Optional[Dict[str, Any]], prosody_analysis: Optional[Dict[str, Any]],
        analysis_level: str,
    ) -> CorrectionReport:
        report = CorrectionReport()
        report.overall_score = base_assessment.overall_score
        report.recognized_text = recognized_text
        report.dimensions = dict(base_assessment.dimensions)
        report.feature_metadata = dict(base_assessment.feature_metadata)
        report.pronunciation_errors = self._detect_pronunciation_errors(
            base_assessment, recognized_text, target_text,
        )
        if prosody_analysis:
            report.prosody_issues = self._detect_prosody_issues(
                prosody_analysis, base_assessment.prosody_features,
            )
            features = prosody_analysis.get("prosody_features")
            feature_metadata = prosody_analysis.get("feature_metadata", {})
            report.feature_metadata.update({
                name: item for name, item in feature_metadata.items()
                if item.get("status") == "measured"
                or report.feature_metadata.get(name, {}).get("status") != "measured"
            })
            evidence = {
                name: {
                    "value": getattr(features, name), "source": source,
                    "unit": feature_metadata.get(name, {}).get("unit"),
                }
                for name, source in prosody_analysis.get("feature_sources", {}).items()
                if isinstance(features, ProsodyFeatures) and source in ("myprosody", "parselmouth")
                and finite_number(getattr(features, name, None))
            }
            report.dimensions["prosody"] = dimension_result(
                source="prosody_features" if evidence else "none",
                reason="韵律特征已测量；尚无校准评分方法" if evidence else (
                    prosody_analysis.get("error") or "韵律测量不可用"
                ),
                evidence=evidence,
            )
        report.improvement_suggestions = self._generate_improvement_suggestions(
            base_assessment, report.pronunciation_errors, report.prosody_issues, [],
        )
        report.practice_recommendations = self._generate_practice_recommendations(
            report.pronunciation_errors, report.prosody_issues, [],
        )
        report.detailed_feedback = self._generate_detailed_feedback(base_assessment, report, analysis_level)
        return report

    def _detect_pronunciation_errors(self, assessment, recognized_text, target_text):
        return self.corrector.detect_pronunciation_errors({}, recognized_text, target_text)

    def _detect_prosody_issues(self, prosody_analysis, prosody_features=None):
        """Use this analysis's measured values, never the base assessor's defaults."""
        features = prosody_analysis.get("prosody_features")
        sources = prosody_analysis.get("feature_sources", {})
        if prosody_analysis.get("analysis_success") is not True or not isinstance(features, ProsodyFeatures):
            return []
        issues = []
        # myprosody rates are syllables/second, not words/minute. The old
        # 100/200 WPM thresholds cannot be applied to those measurements.
        pause_count, syllables = features.pause_count, features.syllable_count
        if (
            sources.get("pause_count") == sources.get("syllable_count") == "myprosody"
            and finite_number(pause_count, 0) and finite_number(syllables, 1)
            and pause_count / syllables > 0.3
        ):
            issues.append({
                "type": "excessive_pauses", "severity": "medium", "source": "myprosody",
                "description": f"检测到 {pause_count} 次停顿、{syllables} 个音节；停顿密度较高",
                "suggestion": "回听这些停顿，结合句意判断是否需要调整",
                "reason": "来自本次韵律测量；0.3 为未校准的提示阈值，不能判断语言能力",
                "evidence": {"pause_count": pause_count, "syllable_count": syllables},
                "calibrated": False,
            })
        return issues

    def _detect_phoneme_problems(self, phoneme_analysis, target_text):
        # Whole-recording pitch/formants are not aligned phoneme observations.
        return []

    def _generate_improvement_suggestions(self, assessment, pronunciation_errors, prosody_issues, phoneme_problems):
        suggestions = []
        if pronunciation_errors:
            suggestions.append("核对识别文本中的漏词、多词和词序差异；回听确认是否为识别错误")
        suggestions.extend(issue["suggestion"] for issue in prosody_issues)
        suggestions.extend(assessment.suggestions)
        return suggestions[:8]

    def _generate_detailed_feedback(self, assessment, report, analysis_level):
        lines = ["发音练习分析报告", report.scope, assessment.feedback]
        for name, dimension in report.dimensions.items():
            if dimension.get("score") is not None and dimension.get("source") == "text_sequence_alignment":
                lines.append(f"识别文本一致度：{dimension['score']:.1f}%（文本有序词编辑指标）")
            lines.append(f"{name}：{dimension['reason']}")
        lines.extend(item["description"] for item in report.pronunciation_errors + report.prosody_issues)
        return "\n\n".join(lines)

    def _generate_practice_recommendations(self, pronunciation_errors, prosody_issues, phoneme_problems):
        return ["结合参考音频回听和跟读", "需要发音判断时，可寻求老师的具体反馈"]

    def _calculate_text_similarity(self, text1, text2):
        return self.corrector.calculate_text_similarity(text1, text2)

    def quick_correction(self, audio_data, target_text="", **kwargs):
        try:
            report = self.comprehensive_correction(audio_data, target_text, analysis_level="basic", **kwargs)
            return {
                "overall_score": report.overall_score,
                "main_issues": report.pronunciation_errors[:3],
                "key_suggestions": report.improvement_suggestions[:3],
                "practice_tips": report.practice_recommendations[:3],
                "dimensions": report.dimensions, "calibrated": report.calibrated, "scope": report.scope,
            }
        except Exception as error:
            return {
                "overall_score": None, "main_issues": [],
                "key_suggestions": ["请检查音频质量后重试"], "practice_tips": [],
                "dimensions": {"overall": dimension_result(reason=f"分析不可用：{error}")},
                "calibrated": False, "scope": CorrectionReport().scope,
            }

    def get_service_status(self):
        # Status checks are local and do not call provider connection probes.
        from importlib.util import find_spec

        return {
            "asr_available": self.asr is not None,
            "corrector_available": self.corrector is not None,
            "phoneme_analyzer_available": self._phoneme_analyzer is not None or find_spec("parselmouth") is not None,
            "prosody_analyzer_available": self._prosody_analyzer is not None or find_spec("parselmouth") is not None,
            "service_config": {"error_thresholds": self.error_thresholds, "correction_priorities": self.correction_priorities},
        }
