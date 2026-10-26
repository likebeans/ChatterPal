# -*- coding: utf-8 -*-
"""Evidence-based recording summaries; no calibrated pronunciation scorer exists."""

from typing import Any, Dict, List, Optional, Tuple, Union

from .base import (
    AssessmentBase,
    AssessmentError,
    AssessmentResult,
    PhonemeAnalysis,
    ProsodyFeatures,
    dimension_result,
    finite_number,
)


class PronunciationCorrector(AssessmentBase):
    """Retain recording measurements without inventing learner ability scores."""

    def assess(
        self,
        audio_data: Union[bytes, str, Tuple],
        target_text: str = "",
        *,
        phoneme_analysis: Optional[Dict[str, Any]] = None,
        recognized_text: str = "",
        **kwargs,
    ) -> AssessmentResult:
        if not self.validate_audio_data(audio_data):
            raise AssessmentError("音频数据验证失败")
        try:
            if phoneme_analysis is None:
                # Optional acoustic dependencies are needed only for analysis.
                from .phoneme import PhonemeAnalyzer

                phoneme_analysis = PhonemeAnalyzer(self.config).assess(audio_data, target_text)
            if not isinstance(phoneme_analysis, dict):
                raise AssessmentError("音频分析结果格式错误")
        except ImportError as error:
            phoneme_analysis = {"analysis_success": False, "error": str(error)}
        except AssessmentError:
            raise
        except Exception as error:
            raise AssessmentError(f"发音评估失败: {error}") from error

        features = ProsodyFeatures()
        feature_metadata = {
            name: dimension_result(reason="未取得该特征的实测数据")
            for name in vars(features)
        }
        measured = phoneme_analysis.get("analysis_success") is True
        pitch = phoneme_analysis.get("pitch", {})
        if measured and pitch.get("available") is True:
            for name in ("mean", "std", "min", "max"):
                value = pitch.get(name)
                if finite_number(value, 0) and (name == "std" or value > 0):
                    key = f"f0_{name}"
                    setattr(features, key, float(value))
                    feature_metadata[key] = dimension_result(
                        status="measured", source="parselmouth", unit="Hz",
                        reason="整段录音有效有声帧的基频统计，不代表音素准确性",
                        evidence={"value": float(value)},
                    )
        duration = phoneme_analysis.get("duration")
        duration = float(duration) if measured and finite_number(duration, 0) else None
        dimensions = {
            "overall": dimension_result(reason="没有经过校准的综合评分方法；缺失维度不计为零"),
            "pronunciation": dimension_result(
                reason="未执行声学音素对齐，没有可用的发音准确率测量"
            ),
            "fluency": dimension_result(reason="没有经过校准的流畅度评分方法"),
            "prosody": dimension_result(reason="声学统计不能直接转换为韵律能力分数"),
            "accuracy": dimension_result(reason="需要有效的参考文本和实际识别文本"),
            "quality": dimension_result(reason="声学统计没有经过录音质量评分校准"),
        }
        result = AssessmentResult(
            overall_score=None,
            fluency_score=None,
            pronunciation_score=None,
            prosody_score=None,
            accuracy_score=None,
            prosody_features=features,
            phoneme_analysis=[self._recording_summary(phoneme_analysis, features)],
            recognized_text=recognized_text,
            target_text=target_text,
            audio_duration=duration,
            quality_score=None,
            dimensions=dimensions,
            feature_metadata=feature_metadata,
        )
        if target_text and recognized_text:
            from .alignment import align_text

            alignment = align_text(recognized_text, target_text)
            if alignment["score"] is not None:
                result.accuracy_score = alignment.pop("score")
                result.dimensions["accuracy"] = dimension_result(
                    result.accuracy_score, status="measured", source="text_sequence_alignment",
                    unit="percent", reason="识别文本与参考文本的有序词编辑一致度；并非声学发音准确率，差异可能来自识别错误",
                    evidence=alignment,
                )
        result.feedback = self._generate_comprehensive_feedback(result, target_text)
        result.suggestions = self._generate_improvement_suggestions(result)
        return result

    def _recording_summary(
        self, analysis: Dict[str, Any], features: ProsodyFeatures
    ) -> PhonemeAnalysis:
        scope = "整段录音声学摘要；未进行音素对齐，不能判断具体音素或单词的发音正确性。"
        if analysis.get("analysis_success") is not True:
            reason = analysis.get("error") or "未取得实测声学特征"
            return PhonemeAnalysis(
                phoneme="whole_recording", error_type="analysis_unavailable",
                description=f"{scope}声学分析不可用：{reason}。", severity="low",
            )
        measurements = []
        for key, label, unit in (("duration", "时长", "秒"), ("sample_rate", "采样率", "Hz")):
            if finite_number(analysis.get(key), 0):
                measurements.append(f"{label} {analysis[key]:.2f} {unit}")
        if features.f0_mean is not None:
            measurements.append(f"基频均值 {features.f0_mean:.1f} Hz")
        else:
            measurements.append("基频不可用（无有效有声帧或提取失败）")
        formants = analysis.get("formants", {})
        actual_formants = {
            name: float(formants[name]["mean"])
            for name in ("F1", "F2", "F3")
            if formants.get("available") is True
            and formants.get(name, {}).get("values")
            and finite_number(formants[name].get("mean"), 0)
        }
        if actual_formants:
            measurements.append("共振峰 " + "，".join(
                f"{name} {value:.1f} Hz" for name, value in actual_formants.items()
            ))
        else:
            measurements.append("共振峰不可用")
        for component, key, label, unit in (
            ("intensity", "mean", "平均强度", "dB"),
            ("spectral", "centroid", "频谱重心", "Hz"),
        ):
            data = analysis.get(component, {})
            if data.get("available") is True and finite_number(data.get(key)):
                measurements.append(f"{label} {data[key]:.1f} {unit}")
        return PhonemeAnalysis(
            phoneme="whole_recording", error_type="acoustic_summary",
            description=scope + "；".join(measurements) + "。能力评分未校准。",
            severity="low", actual_formants=actual_formants,
        )

    def detect_pronunciation_errors(
        self, phoneme_analysis: Dict[str, Any], recognized_text: str, target_text: str = ""
    ) -> List[Dict[str, Any]]:
        # Recording-wide F1/F2 and pitch variation cannot diagnose a phoneme.
        if not target_text or not recognized_text:
            return []
        return self._detect_text_based_errors(recognized_text, target_text)

    def _detect_text_based_errors(
        self, recognized_text: str, target_text: str
    ) -> List[Dict[str, Any]]:
        from .alignment import align_text

        errors = []
        labels = {"delete": "识别文本缺词", "insert": "识别文本多词或重复", "substitute": "识别文本词语不同"}
        for edit in align_text(recognized_text, target_text)["alignment"]:
            if edit["operation"] == "equal":
                continue
            errors.append({
                "type": f"text_{edit['operation']}",
                "severity": "medium",
                "category": "text_alignment",
                "source": "text_sequence_alignment",
                "description": f"{labels[edit['operation']]}：参考「{edit['target_word']}」，识别「{edit['recognized_word']}」",
                "suggestion": "回听并核对该处文本；识别差异不能确认具体发音错误",
                "correction_tips": ["对照参考文本回听录音"],
                "evidence": edit,
            })
        return errors

    def _generate_comprehensive_feedback(
        self, result: AssessmentResult, target_text: str = ""
    ) -> str:
        lines = ["发音练习分析报告", result.scope, "能力评分未校准，暂不提供综合或发音分数。"]
        lines.extend(summary.description for summary in result.phoneme_analysis)
        for name, metadata in result.dimensions.items():
            if metadata["status"] == "unavailable":
                lines.append(f"{name}：{metadata['reason']}")
        return "\n\n".join(lines)

    def _generate_improvement_suggestions(self, result: AssessmentResult) -> List[str]:
        return [
            "回听录音并核对识别文本；文本差异也可能来自语音识别错误",
            "结合参考音频练习，可向老师寻求发音反馈",
        ]


def correct_pronunciation(
    audio_data: Union[bytes, str, Tuple], target_text: str = "", **kwargs
) -> AssessmentResult:
    return PronunciationCorrector().assess(audio_data, target_text, **kwargs)
