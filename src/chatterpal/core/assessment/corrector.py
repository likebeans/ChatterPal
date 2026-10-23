# -*- coding: utf-8 -*-
"""
发音纠错模块
基于音素分析结果进行发音错误检测和纠错建议
"""

import re
import logging
from typing import Dict, List, Tuple, Optional, Any, Union
import numpy as np

from .base import (
    AssessmentBase,
    AssessmentError,
    AssessmentResult,
    PhonemeAnalysis,
    ProsodyFeatures,
)
from .phoneme import PhonemeAnalyzer

logger = logging.getLogger(__name__)


class PronunciationCorrector(AssessmentBase):
    """
    发音纠错器
    基于语音分析结果提供专业的发音纠错建议
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化发音纠错器

        Args:
            config: 配置参数
        """
        super().__init__(config)

        # 中国学习者常见发音错误模式
        self.common_errors = {
            "chinese_learners": {
                # 辅音替换错误
                "/θ/": {
                    "common_substitutions": ["/s/", "/f/", "/t/"],
                    "error_description": "th音发成s音、f音或t音",
                    "correction_tips": [
                        "舌尖轻触上齿，气流从舌齿间通过",
                        "不要完全堵住气流通道",
                        "练习单词: think, thank, three",
                    ],
                },
                "/ð/": {
                    "common_substitutions": ["/d/", "/z/", "/l/"],
                    "error_description": "th音(浊音)发成d音、z音或l音",
                    "correction_tips": [
                        "舌尖轻触上齿，声带振动",
                        "气流从舌齿间通过，有振动感",
                        "练习单词: this, that, the",
                    ],
                },
                "/r/": {
                    "common_substitutions": ["/l/"],
                    "error_description": "r音发成l音",
                    "correction_tips": [
                        "舌尖向后卷，不接触口腔任何部位",
                        "嘴唇略微收圆",
                        "练习单词: red, right, very",
                    ],
                },
                "/l/": {
                    "common_substitutions": ["/r/", "/n/"],
                    "error_description": "l音发成r音或n音",
                    "correction_tips": [
                        "舌尖接触上齿龈",
                        "气流从舌侧通过",
                        "练习单词: light, love, hello",
                    ],
                },
                "/v/": {
                    "common_substitutions": ["/w/", "/f/"],
                    "error_description": "v音发成w音或f音",
                    "correction_tips": [
                        "下唇轻触上齿",
                        "声带振动，气流通过",
                        "练习单词: very, voice, love",
                    ],
                },
                "/w/": {
                    "common_substitutions": ["/v/", "/u/"],
                    "error_description": "w音发成v音或u音",
                    "correction_tips": [
                        "双唇收圆，快速滑向下一个音",
                        "不要让下唇接触上齿",
                        "练习单词: water, what, we",
                    ],
                },
            }
        }

        # 常见单词的音标和发音要点
        self.word_phonetics = {
            "love": {
                "ipa": "/lʌv/",
                "breakdown": ["l", "ʌ", "v"],
                "tips": [
                    "l音：舌尖接触上齿龈",
                    "ʌ音：嘴巴半开，舌头放松在中央",
                    "v音：上齿轻触下唇，声带振动",
                ],
            },
            "you": {
                "ipa": "/juː/",
                "breakdown": ["j", "uː"],
                "tips": [
                    "j音：舌头接近硬腭，快速滑向下一个音",
                    "uː音：嘴唇收圆，舌头后缩，长音",
                ],
            },
            "think": {
                "ipa": "/θɪŋk/",
                "breakdown": ["θ", "ɪ", "ŋ", "k"],
                "tips": [
                    "θ音：舌尖轻触上齿，气流通过",
                    "ɪ音：嘴巴略开，舌头在中前位置",
                    "ŋ音：舌后部接触软腭，鼻音",
                    "k音：舌后部接触软腭，爆破音",
                ],
            },
            "water": {
                "ipa": "/ˈwɔːtər/",
                "breakdown": ["w", "ɔː", "t", "ə", "r"],
                "tips": [
                    "w音：双唇收圆，快速滑向下一个音",
                    "ɔː音：嘴巴张开，舌头后缩，长音",
                    "t音：舌尖接触上齿龈，爆破音",
                    "ə音：中性元音，舌头放松",
                    "r音：舌尖向后卷，不接触口腔",
                ],
            },
        }

        # 元音错误检测标准
        self.vowel_error_thresholds = {
            "F1_tolerance": 100,  # Hz
            "F2_tolerance": 200,  # Hz
            "F3_tolerance": 300,  # Hz
        }

        # 发音质量评级标准
        self.quality_standards = {
            "excellent": {"min_score": 90, "description": "发音优秀"},
            "good": {"min_score": 75, "description": "发音良好"},
            "fair": {"min_score": 60, "description": "发音一般"},
            "poor": {"min_score": 0, "description": "需要改进"},
        }

        self.logger.info("发音纠错器初始化完成")

    def assess(
        self,
        audio_data: Union[bytes, str, Tuple],
        target_text: str = "",
        *,
        phoneme_analysis: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> AssessmentResult:
        """
        综合发音评估和纠错

        Args:
            audio_data: 音频数据
            target_text: 目标文本
            **kwargs: 其他参数

        Returns:
            评估结果

        Raises:
            AssessmentError: 评估过程中的错误
        """
        try:
            if not self.validate_audio_data(audio_data):
                raise AssessmentError("音频数据验证失败")

            # A service can supply the analysis it already owns. Failed supplied
            # analysis is also authoritative: do not retry an unavailable backend.
            if phoneme_analysis is None:
                phoneme_analysis = PhonemeAnalyzer(self.config).assess(
                    audio_data, target_text
                )
            if not isinstance(phoneme_analysis, dict):
                raise AssessmentError("音频分析结果格式错误")

            measured = phoneme_analysis.get("analysis_success") is True
            duration = (
                float(phoneme_analysis["duration"])
                if measured
                else self.estimate_audio_duration(audio_data)
            )
            features = ProsodyFeatures()
            summary = self._recording_summary(phoneme_analysis, features)

            # Retain the existing numeric contract until scores are calibrated.
            result = AssessmentResult(
                overall_score=75.0,
                fluency_score=80.0,
                pronunciation_score=70.0,
                prosody_score=75.0,
                accuracy_score=80.0,
                prosody_features=features,
                phoneme_analysis=[summary],
                recognized_text="",  # No ASR is performed by this assessor.
                target_text=target_text,
                audio_duration=duration,
                quality_score=(
                    float(phoneme_analysis["quality_score"]) if measured else 75.0
                ),
            )

            # 生成反馈和建议
            result.feedback = self._generate_comprehensive_feedback(result, target_text)
            result.suggestions = self._generate_improvement_suggestions(result)

            return result

        except AssessmentError:
            raise
        except Exception as e:
            self.logger.error(f"发音评估失败: {e}")
            raise AssessmentError(f"发音评估失败: {e}")

    def _recording_summary(
        self, analysis: Dict[str, Any], features: ProsodyFeatures
    ) -> PhonemeAnalysis:
        """Store recording statistics in the legacy result without IPA mapping."""
        scope = "整段录音声学摘要；未进行音素对齐，不能判断具体音素或单词的发音正确性。"
        if analysis.get("analysis_success") is not True:
            reason = analysis.get("error") or "Parselmouth不可用，未取得实测声学特征"
            return PhonemeAnalysis(
                phoneme="whole_recording",
                error_type="analysis_unavailable",
                description=f"{scope}声学分析不可用：{reason}。兼容数值为默认值，非实测。",
                severity="low",
            )

        measurements = [
            f"时长 {analysis['duration']:.2f} 秒",
            f"采样率 {analysis['sample_rate']:.0f} Hz",
        ]
        pitch = analysis.get("pitch", {})
        if pitch.get("available", True) and all(
            np.isfinite(pitch.get(name, float("nan")))
            for name in ("mean", "std", "min", "max")
        ):
            for name in ("mean", "std", "min", "max"):
                setattr(features, f"f0_{name}", float(pitch[name]))
            measurements.append(
                f"基频均值 {features.f0_mean:.1f} Hz，"
                f"范围 {features.f0_min:.1f}–{features.f0_max:.1f} Hz"
            )
        else:
            measurements.append("基频不可用（无有效有声帧或提取失败），兼容默认值非实测")

        formants = analysis.get("formants", {})
        actual_formants = {
            name: float(formants[name]["mean"])
            for name in ("F1", "F2", "F3")
            if formants.get("available", True)
            and formants.get(name, {}).get("values")
            and np.isfinite(formants[name].get("mean", float("nan")))
        }
        if actual_formants:
            measurements.append(
                "共振峰 "
                + "，".join(
                    f"{name} {value:.1f} Hz" for name, value in actual_formants.items()
                )
            )
        else:
            measurements.append("共振峰不可用，兼容默认值非实测")
        intensity = analysis.get("intensity", {})
        if intensity.get("available", True) and np.isfinite(
            intensity.get("mean", float("nan"))
        ):
            measurements.append(f"平均强度 {intensity['mean']:.1f} dB")
        spectral = analysis.get("spectral", {})
        if spectral.get("available", True) and np.isfinite(
            spectral.get("centroid", float("nan"))
        ):
            measurements.append(f"频谱重心 {spectral['centroid']:.1f} Hz")
        measurements.append("语速、停顿及准确性数值保留兼容默认值，非实测；质量分为未校准启发式值")
        return PhonemeAnalysis(
            phoneme="whole_recording",
            error_type="acoustic_summary",
            description=scope + "；".join(measurements) + "。",
            severity="low",
            actual_formants=actual_formants,
        )

    def detect_pronunciation_errors(
        self,
        phoneme_analysis: Dict[str, Any],
        recognized_text: str,
        target_text: str = "",
    ) -> List[Dict[str, Any]]:
        """
        检测发音错误

        Args:
            phoneme_analysis: 音素分析结果
            recognized_text: 识别文本
            target_text: 目标文本

        Returns:
            错误检测结果列表
        """
        errors = []

        try:
            # 1. 基于音素分析的错误检测
            phoneme_errors = self._detect_phoneme_errors(phoneme_analysis)
            errors.extend(phoneme_errors)

            # 2. 基于文本对比的错误检测
            if target_text and recognized_text:
                text_errors = self._detect_text_based_errors(
                    recognized_text, target_text
                )
                errors.extend(text_errors)

            # 3. 基于语音质量的错误检测
            quality_errors = self._detect_quality_errors(phoneme_analysis)
            errors.extend(quality_errors)

            # 4. 错误优先级排序
            errors = self._prioritize_errors(errors)

            return errors

        except Exception as e:
            self.logger.error(f"发音错误检测失败: {e}")
            return []

    def _detect_phoneme_errors(
        self, phoneme_analysis: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        基于音素分析检测错误

        Args:
            phoneme_analysis: 音素分析结果

        Returns:
            错误列表
        """
        errors = []

        try:
            # 检测音调问题
            pitch = phoneme_analysis.get("pitch", {})
            if pitch.get("std", 0) > 50:
                errors.append(
                    {
                        "type": "pitch_instability",
                        "severity": "medium",
                        "category": "prosody",
                        "description": f'音调不稳定，变化幅度过大 ({pitch["std"]:.1f} Hz)',
                        "suggestion": "练习保持稳定的音调，避免不必要的音调波动",
                        "correction_tips": [
                            "放慢语速，专注于音调控制",
                            "练习单调朗读，保持音调平稳",
                            "录音对比，注意音调一致性",
                        ],
                    }
                )

            # 检测共振峰问题
            formants = phoneme_analysis.get("formants", {})

            # F1异常检测（舌位高低）
            f1_mean = formants.get("F1", {}).get("mean", 500)
            if f1_mean < 200 or f1_mean > 1000:
                errors.append(
                    {
                        "type": "vowel_height_error",
                        "severity": "high",
                        "category": "vowel",
                        "description": f"元音舌位高低异常 (F1: {f1_mean:.0f} Hz)",
                        "suggestion": "调整舌位高低，注意口腔开合度",
                        "correction_tips": [
                            "练习/i/和/a/的对比，感受舌位变化",
                            "使用镜子观察口型变化",
                            "慢速练习，注意舌位控制",
                        ],
                    }
                )

            # F2异常检测（舌位前后）
            f2_mean = formants.get("F2", {}).get("mean", 1500)
            if f2_mean < 800 or f2_mean > 3000:
                errors.append(
                    {
                        "type": "vowel_backness_error",
                        "severity": "high",
                        "category": "vowel",
                        "description": f"元音舌位前后异常 (F2: {f2_mean:.0f} Hz)",
                        "suggestion": "调整舌位前后位置",
                        "correction_tips": [
                            "练习/i/和/u/的对比，感受舌位前后移动",
                            "注意舌尖和舌根的位置",
                            "练习元音序列: /i/-/e/-/a/-/o/-/u/",
                        ],
                    }
                )

            # 检测强度问题
            intensity = phoneme_analysis.get("intensity", {})
            intensity_std = intensity.get("std", 5)
            if intensity_std > 10:
                errors.append(
                    {
                        "type": "intensity_variation",
                        "severity": "low",
                        "category": "prosody",
                        "description": f"音量变化过大 ({intensity_std:.1f} dB)",
                        "suggestion": "保持稳定的音量输出",
                        "correction_tips": [
                            "练习均匀的呼吸控制",
                            "保持稳定的发声力度",
                            "注意麦克风距离一致性",
                        ],
                    }
                )

        except Exception as e:
            self.logger.warning(f"音素错误检测失败: {e}")

        return errors

    def _detect_text_based_errors(
        self, recognized_text: str, target_text: str
    ) -> List[Dict[str, Any]]:
        """
        基于文本对比检测错误

        Args:
            recognized_text: 识别文本
            target_text: 目标文本

        Returns:
            错误列表
        """
        errors = []

        try:
            # 文本预处理
            recognized_words = self._preprocess_text(recognized_text)
            target_words = self._preprocess_text(target_text)

            # 计算相似度
            similarity = self.calculate_text_similarity(recognized_text, target_text)

            if similarity < 0.8:
                errors.append(
                    {
                        "type": "content_accuracy",
                        "severity": "high" if similarity < 0.5 else "medium",
                        "category": "accuracy",
                        "description": f"内容准确性较低 ({similarity:.1%})",
                        "suggestion": "重点练习发音不准确的单词",
                        "correction_tips": [
                            f'目标文本: "{target_text}"',
                            f'识别结果: "{recognized_text}"',
                            "逐词对比练习，找出发音差异",
                        ],
                    }
                )

            # 检测具体单词错误
            word_errors = self._detect_word_errors(recognized_words, target_words)
            errors.extend(word_errors)

        except Exception as e:
            self.logger.warning(f"文本错误检测失败: {e}")

        return errors

    def _detect_quality_errors(
        self, phoneme_analysis: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        基于语音质量检测错误

        Args:
            phoneme_analysis: 音素分析结果

        Returns:
            错误列表
        """
        errors = []

        try:
            quality_score = phoneme_analysis.get("quality_score", 70)

            if quality_score < 60:
                errors.append(
                    {
                        "type": "overall_quality",
                        "severity": "high",
                        "category": "quality",
                        "description": f"整体语音质量较低 ({quality_score:.1f}/100)",
                        "suggestion": "从基础发音开始系统练习",
                        "correction_tips": [
                            "练习基础音素发音",
                            "注意呼吸和发声方法",
                            "保持良好的录音环境",
                        ],
                    }
                )
            elif quality_score < 75:
                errors.append(
                    {
                        "type": "quality_improvement",
                        "severity": "medium",
                        "category": "quality",
                        "description": f"语音质量有待提高 ({quality_score:.1f}/100)",
                        "suggestion": "继续练习，注意发音细节",
                        "correction_tips": [
                            "专注于音素准确性",
                            "练习语音连贯性",
                            "注意语调自然度",
                        ],
                    }
                )

        except Exception as e:
            self.logger.warning(f"质量错误检测失败: {e}")

        return errors

    def _detect_word_errors(
        self, recognized_words: List[str], target_words: List[str]
    ) -> List[Dict[str, Any]]:
        """
        检测具体单词错误

        Args:
            recognized_words: 识别单词列表
            target_words: 目标单词列表

        Returns:
            错误列表
        """
        errors = []

        try:
            # 简单的单词对比
            max_len = max(len(recognized_words), len(target_words))

            for i in range(max_len):
                target_word = target_words[i] if i < len(target_words) else ""
                recognized_word = (
                    recognized_words[i] if i < len(recognized_words) else ""
                )

                if (
                    target_word
                    and recognized_word
                    and target_word.lower() != recognized_word.lower()
                ):
                    # 检测可能的发音错误类型
                    error_type = self._analyze_word_error(target_word, recognized_word)

                    errors.append(
                        {
                            "type": "word_mispronunciation",
                            "severity": "medium",
                            "category": "accuracy",
                            "description": f'单词发音错误: "{target_word}" → "{recognized_word}"',
                            "suggestion": f'重点练习单词 "{target_word}" 的发音',
                            "correction_tips": self._get_word_correction_tips(
                                target_word, recognized_word, error_type
                            ),
                        }
                    )

        except Exception as e:
            self.logger.warning(f"单词错误检测失败: {e}")

        return errors

    def _analyze_word_error(self, target_word: str, recognized_word: str) -> str:
        """
        分析单词错误类型

        Args:
            target_word: 目标单词
            recognized_word: 识别单词

        Returns:
            错误类型
        """
        # 简单的错误类型分析
        if len(target_word) != len(recognized_word):
            return "length_mismatch"

        # 检查是否为常见替换错误
        for phoneme, error_info in self.common_errors["chinese_learners"].items():
            if any(
                sub in recognized_word.lower()
                for sub in error_info["common_substitutions"]
            ):
                return f"common_substitution_{phoneme}"

        return "general_mispronunciation"

    def _get_word_correction_tips(
        self, target_word: str, recognized_word: str, error_type: str
    ) -> List[str]:
        """
        获取单词纠错建议

        Args:
            target_word: 目标单词
            recognized_word: 识别单词
            error_type: 错误类型

        Returns:
            纠错建议列表
        """
        tips = []

        # 通用建议
        tips.append(f'慢速练习单词 "{target_word}"')
        tips.append("注意每个音素的准确发音")

        # 根据错误类型给出具体建议
        if "common_substitution" in error_type:
            phoneme = error_type.split("_")[-1]
            if phoneme in self.common_errors["chinese_learners"]:
                tips.extend(
                    self.common_errors["chinese_learners"][phoneme]["correction_tips"]
                )

        # 添加单词特定的发音建议
        if target_word.lower() in self.word_phonetics:
            word_info = self.word_phonetics[target_word.lower()]
            tips.append(f'音标: {word_info["ipa"]}')
            tips.extend(word_info["tips"])

        tips.append("对比标准发音，反复练习")

        return tips

    def _preprocess_text(self, text: str) -> List[str]:
        """
        文本预处理

        Args:
            text: 输入文本

        Returns:
            处理后的单词列表
        """
        # 移除标点符号，转换为小写，分割单词
        text = re.sub(r"[^\w\s]", "", text.lower())
        return text.split()

    def _prioritize_errors(self, errors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        错误优先级排序

        Args:
            errors: 错误列表

        Returns:
            排序后的错误列表
        """
        # 按严重程度排序
        severity_order = {"high": 0, "medium": 1, "low": 2}

        return sorted(
            errors,
            key=lambda x: (
                severity_order.get(x.get("severity", "low"), 2),
                x.get("type", ""),
            ),
        )

    def _generate_comprehensive_feedback(
        self, result: AssessmentResult, target_text: str = ""
    ) -> str:
        """
        生成综合反馈

        Args:
            result: 评估结果
            target_text: 目标文本

        Returns:
            反馈文本
        """
        feedback = f"## 🎯 发音评估报告\n\n"
        feedback += f"### 📊 综合评分：{result.overall_score:.1f}/100\n\n"
        feedback += "评分为兼容保留的演示值，尚未校准，不能作为发音准确性的测量。\n\n"
        for summary in result.phoneme_analysis:
            if summary.phoneme == "whole_recording":
                feedback += f"{summary.description}\n\n"

        feedback += f"### 📈 详细评分：\n"
        feedback += f"- **流畅度**: {result.fluency_score:.1f}/100\n"
        feedback += f"- **发音准确性**: {result.pronunciation_score:.1f}/100\n"
        feedback += f"- **韵律表现**: {result.prosody_score:.1f}/100\n"
        feedback += f"- **内容准确性**: {result.accuracy_score:.1f}/100\n\n"

        feedback += f"### 🔍 音频特征分析：\n"
        feedback += f"- **语速**: {result.prosody_features.speaking_rate:.1f} 词/分钟\n"
        feedback += f"- **清晰度**: {result.prosody_features.articulation_rate:.1f}\n"
        feedback += f"- **音调范围**: {result.prosody_features.f0_min:.1f} - {result.prosody_features.f0_max:.1f} Hz\n"
        feedback += (
            f"- **停顿时长**: {result.prosody_features.pause_duration:.2f} 秒\n\n"
        )

        # 根据评分给出具体建议
        feedback += f"### 💡 改进建议：\n"

        if result.fluency_score < 70:
            feedback += "\n**流畅度改进**：\n- 尝试保持稳定的语速，避免过快或过慢\n- 减少不必要的停顿和犹豫\n- 多练习连读和语音连接\n"

        if result.pronunciation_score < 70:
            feedback += "\n**发音改进**：\n- 注意元音的准确发音\n- 练习辅音的清晰度\n- 使用国际音标(IPA)辅助学习\n"

        if result.prosody_score < 70:
            feedback += "\n**韵律改进**：\n- 增加语调的变化和起伏\n- 注意句子的重音和节奏\n- 练习不同情感的表达方式\n"

        if target_text and result.accuracy_score < 70:
            feedback += f"\n**内容准确性**：\n- 目标文本: '{target_text}'\n- 识别结果: '{result.recognized_text}'\n- 建议重点练习发音不准确的单词\n"

        # 总体建议
        if result.overall_score >= 85:
            feedback += "\n🎉 **总体表现优秀！** 继续保持，可以尝试更有挑战性的内容。\n"
        elif result.overall_score >= 70:
            feedback += "\n👍 **总体表现良好！** 继续练习，注意上述改进建议。\n"
        else:
            feedback += "\n💪 **需要加强练习！** 建议从基础发音开始，逐步提高。\n"

        return feedback

    def _generate_improvement_suggestions(self, result: AssessmentResult) -> List[str]:
        """
        生成改进建议

        Args:
            result: 评估结果

        Returns:
            建议列表
        """
        suggestions = []

        speaking_rate = result.prosody_features.speaking_rate
        if np.isfinite(speaking_rate) and 0 < speaking_rate < 100:
            suggestions.append("适当加快语速，保持自然流畅的节奏")
        elif np.isfinite(speaking_rate) and speaking_rate > 200:
            suggestions.append("放慢语速，注意发音清晰度")

        if result.fluency_score < 70:
            suggestions.append("练习流畅的连续发音")
            suggestions.append("减少不必要的停顿")

        if result.pronunciation_score < 70:
            suggestions.append("重点练习音素准确性")
            suggestions.append("使用IPA音标辅助学习")

        if result.prosody_score < 70:
            suggestions.append("练习语调和节奏变化")
            suggestions.append("注意重音和语调自然度")

        if result.accuracy_score < 70:
            suggestions.append("提高发音准确性")
            suggestions.append("逐词练习，确保清晰度")

        # 通用建议
        suggestions.extend(
            [
                "每天坚持15-30分钟的发音练习",
                "使用录音对比方法检查发音效果",
                "可以寻求专业老师的指导",
            ]
        )

        return suggestions


# 便捷函数
def correct_pronunciation(
    audio_data: Union[bytes, str, Tuple], target_text: str = "", **kwargs
) -> AssessmentResult:
    """
    发音纠错的便捷函数

    Args:
        audio_data: 音频数据
        target_text: 目标文本
        **kwargs: 其他参数

    Returns:
        评估结果
    """
    corrector = PronunciationCorrector()
    return corrector.assess(audio_data, target_text, **kwargs)
