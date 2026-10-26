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

    def create_interface(self, label: str = "Score") -> gr.Tab:
        """Create the score tab interface."""
        with gr.Tab(label) as tab:
            gr.Markdown(
                """
                ## 发音练习反馈

                根据语音识别、文本对比和可用的分析服务提供练习反馈。
                评分尚未校准，仅供练习参考，不代表专业发音测评。

                **报告内容：**
                - 目标文本与语音识别结果
                - 识别文本一致度及其来源；未测量的发音、流畅度和韵律会标为不可评估
                - 分析反馈与练习建议；缺失的分析会明确标注
                
                **使用方法：**
                1. 在文本框中输入要练习的英文句子
                2. 录制您的发音
                3. 点击"开始评价"查看识别文本、反馈和分析来源
                """
            )

            # Input section
            with gr.Row():
                with gr.Column():
                    text_input = gr.Textbox(
                        label="📝 练习文本",
                        placeholder="请输入您想要练习的英文句子...",
                        lines=3,
                        info="输入参考文本，与机器识别结果比较；文字一致度不代表发音准确率",
                    )

                    audio_input = gr.Audio(
                        sources=["microphone", "upload"], type="numpy",
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

                    识别文本一致度以百分比展示有序词编辑对齐结果，可能受识别错误影响。
                    发音、流畅度、韵律和综合评分未校准，暂不可评估。
                    语言模型仅提供文字练习建议，不能判断真人发音水平或进步。
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
                "练习反馈（能力评分未校准）", result.scope,
                f"目标文本: {result.target_text}",
                f"识别文本: {result.recognized_text}",
                "综合评分: 暂不可评估", "发音准确性: 暂不可评估",
                "流畅度: 暂不可评估", "韵律: 暂不可评估",
            ]
            accuracy = result.dimensions.get("accuracy", {})
            if accuracy.get("source") == "text_sequence_alignment" and accuracy.get("score") is not None:
                lines.append(f"识别文本一致度: {accuracy['score']:.1f}%（有序词编辑对齐，非声学发音准确率）")
            else:
                lines.append("识别文本一致度: 暂不可评估")
            lines.extend(f"{name}: {item['reason']}（来源：{item['source']}）" for name, item in result.dimensions.items())
            lines.extend([
                "\n服务反馈:", result.feedback or "暂无反馈", "\n改进建议:",
                *(f"- {suggestion}" for suggestion in result.suggestions),
            ])
            if self.evaluation_service.assessment is None:
                lines.append("\n声学分析不可用：未配置声学评估模块。")
            if result.phoneme_analysis:
                lines.extend(["\n录音声学摘要（未进行音素对齐）:", *(
                    f"- {item.phoneme}: {item.description}"
                    for item in result.phoneme_analysis
                )])
            else:
                lines.append("\n音素分析不可用：本次报告未提供音素分析结果。")
            return "\n".join(lines)

        except Exception as e:
            return f"❌ 评价过程出错: {safe_str(e)}\n\n请检查输入内容并重试。"
