# -*- coding: utf-8 -*-
"""Score tab component for pronunciation evaluation."""

import gradio as gr
from ...services.evaluation import EvaluationService
from ...utils.encoding_fix import safe_str


class ScoreTab:
    """Score interface component for pronunciation evaluation."""

    def __init__(self, evaluation_service: EvaluationService):
        """Initialize score tab with evaluation service."""
        self.evaluation_service = evaluation_service

    def create_interface(self) -> gr.Tab:
        """Create the score tab interface."""
        with gr.Tab("Score") as tab:
            gr.Markdown(
                """
                ## 发音练习反馈

                根据语音识别、文本对比和可用的分析服务提供练习反馈。
                评分尚未校准，仅供练习参考，不代表专业发音测评。

                **报告内容：**
                - 目标文本与语音识别结果
                - 服务返回的发音、流畅度、韵律和内容参考评分
                - 分析反馈与练习建议；缺失的分析会明确标注
                
                **使用方法：**
                1. 在文本框中输入要练习的英文句子
                2. 录制您的发音
                3. 点击"进行评价"获得详细反馈和评分
                """
            )

            # Input section
            with gr.Row():
                with gr.Column():
                    text_input = gr.Textbox(
                        label="📝 练习文本",
                        placeholder="请输入您想要练习的英文句子...",
                        lines=3,
                        info="输入标准的英文文本，系统将基于此文本评估您的发音",
                    )

                    audio_input = gr.Audio(
                        sources=["microphone"], type="numpy",
                        label="🎤 录制发音（请清晰地朗读练习文本）",
                    )

                    evaluate_btn = gr.Button(
                        value="🎯 开始评价", variant="primary", size="lg"
                    )

            # Output section
            with gr.Row():
                with gr.Column():
                    evaluation_output = gr.Textbox(
                        label="📊 评价结果", lines=15, info="详细的发音评价和改进建议"
                    )

            # Additional info section
            with gr.Accordion("💡 评分说明", open=False):
                gr.Markdown(
                    """
                    ### 参考评分说明

                    服务报告使用 0–100 分。当前评分尚未经过校准，
                    部分数值可能来自文本对比、模型推断或后端默认值，
                    不能据此判断真人发音水平或认定达到母语水平。
                    未提供的音素或声学分析会标为不可用；没有问题条目不代表没有问题。
                    """
                )

            # Event handler
            evaluate_btn.click(
                fn=self._evaluate_pronunciation,
                inputs=[text_input, audio_input],
                outputs=evaluation_output,
            )

        return tab

    def _evaluate_pronunciation(self, text_input: str, audio_input) -> str:
        """Evaluate pronunciation based on text and audio input."""
        try:
            # Validate inputs
            if not text_input or not text_input.strip():
                return "❌ 请先输入要练习的文本内容"

            if audio_input is None:
                return "❌ 请录制您的发音"

            # Process evaluation
            result = self.evaluation_service.evaluate_pronunciation(
                audio_data=audio_input, target_text=text_input.strip()
            )

            lines = [
                "参考评分（未校准，仅供练习参考；可能包含模型推断或默认值）",
                f"目标文本: {result.target_text}",
                f"识别文本: {result.recognized_text}",
                f"综合评分: {result.overall_score:.1f}/100",
                f"发音准确性: {result.pronunciation_score:.1f}/100",
                f"流畅度: {result.fluency_score:.1f}/100",
                f"韵律: {result.prosody_score:.1f}/100",
                f"内容准确性: {result.accuracy_score:.1f}/100",
                "\n服务反馈:",
                result.feedback or "暂无反馈",
                "\n改进建议:",
                *(f"- {suggestion}" for suggestion in result.suggestions),
            ]
            if self.evaluation_service.assessment is None:
                lines.append("\n声学分析不可用：未配置声学评估模块。")
            if result.phoneme_analysis:
                lines.extend(["\n音素分析:", *(
                    f"- {item.phoneme}: {item.description}"
                    for item in result.phoneme_analysis
                )])
            else:
                lines.append("\n音素分析不可用：本次报告未提供音素分析结果。")
            return "\n".join(lines)

        except Exception as e:
            return f"❌ 评价过程出错: {safe_str(e)}\n\n请检查输入内容并重试。"
