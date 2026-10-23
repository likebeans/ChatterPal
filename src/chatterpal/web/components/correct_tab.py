# -*- coding: utf-8 -*-
"""Correct tab component for professional pronunciation assessment."""

import gradio as gr
from typing import Tuple, Optional
from ...services.correction import CorrectionService
from ...utils.encoding_fix import safe_str


class CorrectTab:
    """Correction interface component for professional pronunciation assessment."""

    def __init__(self, correction_service: CorrectionService):
        """Initialize correct tab with correction service."""
        self.correction_service = correction_service

    def create_interface(self) -> gr.Tab:
        """Create the correction tab interface."""
        with gr.Tab("Correct") as tab:
            gr.Markdown(
                """
                # 发音练习分析

                结合语音识别与可用的声学分析，展示服务返回的反馈和练习建议。
                **评分尚未校准，仅供练习参考，不代表专业发音测评。**
                部分数值可能为后端估算或默认值；没有问题条目不代表没有发音问题。

                1. 输入目标文本（可选）
                2. 清晰地录制发音
                3. 查看识别文本、参考报告及练习建议

                单词或音素分析未提供时会明确标注，不会补造分析结果。
                """
            )

            # Input section
            with gr.Row():
                with gr.Column():
                    target_text_input = gr.Textbox(
                        label="🎯 目标文本",
                        placeholder="输入您想要练习的句子（如：I love you），留空则进行通用发音分析",
                        lines=2,
                        info="可选：输入标准文本进行对比分析",
                    )

                    audio_input = gr.Audio(
                        sources=["microphone"], type="numpy",
                        label="🎤 录制您的发音（请清晰地朗读内容）",
                    )

                    assess_btn = gr.Button(
                        value="开始练习分析", variant="primary", size="lg"
                    )

            # Output section - Main results
            with gr.Row():
                with gr.Column(scale=1):
                    asr_result = gr.Textbox(
                        label="🔊 语音识别结果", lines=3, info="系统识别出的语音内容"
                    )

                    overall_feedback = gr.Textbox(
                        label="📋 综合评价与指导建议",
                        lines=8,
                        info="服务返回的未校准参考评分和反馈",
                    )

                with gr.Column(scale=1):
                    word_analysis = gr.Textbox(
                        label="📝 单词级别分析", lines=8, info="每个单词的详细发音分析"
                    )

                    phoneme_analysis = gr.Textbox(
                        label="🔬 音素级别分析", lines=8, info="音素层面的深度分析"
                    )

            # Practice suggestions
            with gr.Row():
                practice_suggestions = gr.Textbox(
                    label="💡 练习建议", lines=5, info="针对性的发音练习建议和方法"
                )

            # Technical information
            with gr.Accordion("🔧 技术说明", open=False):
                gr.Markdown(
                    """
                    ### 报告说明

                    报告展示纠错服务返回的识别文本、参考评分、问题条目和练习建议。
                    当前评分尚未校准，部分数值可能来自后端估算或默认值。
                    声学特征与发音错误之间的关联尚未验证，不能替代专业教师的判断。

                    未提供问题条目时，只能说明本次报告没有返回相应结果；
                    不能据此认定发音正确，也不会填入虚构的单词、音素或置信度。
                    """
                )

            # Event handler
            assess_btn.click(
                fn=self._assess_pronunciation,
                inputs=[audio_input, target_text_input],
                outputs=[
                    asr_result,
                    overall_feedback,
                    word_analysis,
                    phoneme_analysis,
                    practice_suggestions,
                ],
            )

        return tab

    def _assess_pronunciation(
        self, audio_input, target_text: Optional[str] = ""
    ) -> Tuple[str, str, str, str, str]:
        """Perform professional pronunciation assessment."""
        try:
            # Validate audio input
            if audio_input is None:
                error_msg = "❌ 请录制音频进行分析"
                return error_msg, error_msg, "", "", ""

            # Process assessment
            report = self.correction_service.comprehensive_correction(
                audio_data=audio_input,
                target_text=target_text.strip() if target_text else "",
                analysis_level="expert",
            )

            feedback = [
                "参考评分（未校准，仅供练习参考；可能包含后端估算或默认值）",
                f"综合评分: {report.overall_score:.1f}/100",
                report.detailed_feedback or "本次报告未提供详细评分和反馈。",
            ]
            if report.prosody_issues:
                feedback.append("\n韵律问题:\n" + self._format_issues(report.prosody_issues))
            else:
                feedback.append("\n本次报告未提供韵律问题条目，不能据此判断韵律正常。")
            practice = ["改进建议:"]
            practice.extend(f"- {item}" for item in report.improvement_suggestions)
            practice.append("\n练习建议:")
            practice.extend(f"- {item}" for item in report.practice_recommendations)
            if not report.improvement_suggestions and not report.practice_recommendations:
                practice.append("本次报告未提供练习建议。")

            return (
                report.recognized_text or "语音识别结果不可用。",
                "\n".join(feedback),
                self._format_issues(report.pronunciation_errors)
                or "单词分析不可用：本次报告未提供发音问题条目，不代表发音无误。",
                self._format_issues(report.phoneme_problems)
                or "音素分析不可用：本次报告未提供音素问题条目，不代表音素发音无误。",
                "\n".join(practice),
            )

        except Exception as e:
            error_msg = f"❌ 评估过程出错: {safe_str(e)}"
            return error_msg, error_msg, "", "", ""

    @staticmethod
    def _format_issues(issues) -> str:
        """Render only descriptions and guidance supplied in the service report."""
        lines = []
        for issue in issues:
            lines.append(f"- {issue['description']}")
            if issue.get("suggestion"):
                lines.append(f"  建议: {issue['suggestion']}")
            lines.extend(f"  - {tip}" for tip in issue.get("correction_tips", []))
        return "\n".join(lines)
