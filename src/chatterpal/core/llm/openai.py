# -*- coding: utf-8 -*-
"""
OpenAI LLM实现
基于OpenAI API进行对话
"""

import json
from typing import Optional, Dict, Any, List, Iterator, Union

try:
    import requests
except ImportError:
    requests = None

from .base import LLMBase, LLMError, Conversation


class OpenAILLM(LLMBase):
    """
    基于OpenAI API的LLM实现
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化OpenAI LLM

        Args:
            config: 配置参数，支持以下参数：
                - api_key: API密钥
                - model: 模型名称 (默认: gpt-3.5-turbo)
                - base_url: API基础URL
                - max_tokens: 最大输出token数
                - temperature: 温度参数
                - top_p: top_p参数
                - frequency_penalty: 频率惩罚
                - presence_penalty: 存在惩罚
                - timeout: 请求超时时间
        """
        super().__init__(config)

        if not requests:
            raise ImportError("requests库未安装，请运行: pip install requests")

        self.api_key = self.config.get("api_key")
        if not self.api_key:
            raise LLMError("OpenAI API密钥未配置")

        self.model = self.config.get("model", "gpt-3.5-turbo")
        self.base_url = self.config.get(
            "base_url", "https://api.openai.com/v1/chat/completions"
        )
        self.max_tokens = self.config.get("max_tokens", 2000)
        self.temperature = self.config.get("temperature", 0.7)
        self.top_p = self.config.get("top_p", 1.0)
        self.frequency_penalty = self.config.get("frequency_penalty", 0.0)
        self.presence_penalty = self.config.get("presence_penalty", 0.0)
        self.timeout = self.config.get("timeout", 30)

        self.logger.info(f"OpenAI LLM初始化完成，模型: {self.model}")

    def chat(
        self, messages: Union[List[Dict[str, Any]], Conversation, str], **kwargs
    ) -> str:
        """
        单轮对话

        Args:
            messages: 消息列表、对话对象或单个文本
            **kwargs: 其他参数，支持：
                - model: 临时覆盖模型
                - max_tokens: 临时覆盖最大token数
                - temperature: 临时覆盖温度
                - top_p: 临时覆盖top_p
                - frequency_penalty: 临时覆盖频率惩罚
                - presence_penalty: 临时覆盖存在惩罚

        Returns:
            回复文本

        Raises:
            LLMError: 对话过程中的错误
        """
        try:
            # 标准化消息格式
            normalized_messages = self.normalize_messages(messages)

            if not self.validate_messages(normalized_messages):
                raise LLMError("消息格式验证失败")

            # 获取参数
            model = kwargs.get("model", self.model)
            max_tokens = kwargs.get("max_tokens", self.max_tokens)
            temperature = kwargs.get("temperature", self.temperature)
            top_p = kwargs.get("top_p", self.top_p)
            frequency_penalty = kwargs.get("frequency_penalty", self.frequency_penalty)
            presence_penalty = kwargs.get("presence_penalty", self.presence_penalty)

            # 构建请求
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

            payload = {
                "model": model,
                "messages": normalized_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "top_p": top_p,
                "frequency_penalty": frequency_penalty,
                "presence_penalty": presence_penalty,
            }

            # 发送请求
            response = requests.post(
                self.base_url, headers=headers, json=payload, timeout=self.timeout
            )

            # 检查HTTP状态
            response.raise_for_status()

            # 解析响应
            result = response.json()

            if "choices" in result and len(result["choices"]) > 0:
                content = result["choices"][0]["message"]["content"]
                self.logger.info(f"对话成功，回复长度: {len(content)}")

                # 记录使用情况
                if "usage" in result:
                    usage = result["usage"]
                    self.logger.debug(f"Token使用情况: {usage}")

                return content
            else:
                error_msg = result.get("error", {}).get("message", "未知错误")
                error_type = result.get("error", {}).get("type", "UNKNOWN")
                raise LLMError(f"API调用失败 [{error_type}]: {error_msg}")

        except requests.exceptions.RequestException as e:
            self.logger.error(f"网络请求失败: {e}")
            raise LLMError(f"网络请求失败: {e}")
        except json.JSONDecodeError as e:
            self.logger.error(f"响应解析失败: {e}")
            raise LLMError(f"响应解析失败: {e}")
        except LLMError:
            raise
        except Exception as e:
            self.logger.error(f"对话异常: {e}")
            raise LLMError(f"对话异常: {e}")

    def chat_stream(
        self, messages: Union[List[Dict[str, Any]], Conversation, str], **kwargs
    ) -> Iterator[str]:
        """
        流式对话

        Args:
            messages: 消息列表、对话对象或单个文本
            **kwargs: 其他参数

        Yields:
            回复文本片段

        Raises:
            LLMError: 对话过程中的错误
        """
        try:
            # 标准化消息格式
            normalized_messages = self.normalize_messages(messages)

            if not self.validate_messages(normalized_messages):
                raise LLMError("消息格式验证失败")

            # 获取参数
            model = kwargs.get("model", self.model)
            max_tokens = kwargs.get("max_tokens", self.max_tokens)
            temperature = kwargs.get("temperature", self.temperature)
            top_p = kwargs.get("top_p", self.top_p)
            frequency_penalty = kwargs.get("frequency_penalty", self.frequency_penalty)
            presence_penalty = kwargs.get("presence_penalty", self.presence_penalty)

            # 构建请求
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            }

            payload = {
                "model": model,
                "messages": normalized_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "top_p": top_p,
                "frequency_penalty": frequency_penalty,
                "presence_penalty": presence_penalty,
                "stream": True,
            }

            # 发送流式请求
            response = requests.post(
                self.base_url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
                stream=True,
            )

            # 检查HTTP状态
            response.raise_for_status()

            # 处理流式响应
            for line in response.iter_lines():
                if line:
                    line_str = line.decode("utf-8")
                    if line_str.startswith("data: "):
                        data_str = line_str[6:]  # 移除 'data: ' 前缀

                        if data_str.strip() == "[DONE]":
                            break

                        try:
                            data = json.loads(data_str)
                            if "error" in data:
                                error = data["error"]
                                if isinstance(error, dict):
                                    error_msg = error.get("message") or "未知错误"
                                    error_type = error.get("type") or "UNKNOWN"
                                else:
                                    error_msg = (
                                        error if isinstance(error, str) and error
                                        else "未知错误"
                                    )
                                    error_type = "UNKNOWN"
                                raise LLMError(
                                    f"API调用失败 [{error_type}]: {error_msg}"
                                )
                            if "choices" in data and len(data["choices"]) > 0:
                                delta = data["choices"][0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    yield content
                        except json.JSONDecodeError:
                            continue

        except requests.exceptions.RequestException as e:
            self.logger.error(f"流式请求失败: {e}")
            raise LLMError(f"流式请求失败: {e}")
        except LLMError:
            raise
        except Exception as e:
            self.logger.error(f"流式对话异常: {e}")
            raise LLMError(f"流式对话异常: {e}")

    def get_supported_models(self) -> List[str]:
        """
        获取支持的模型列表

        Returns:
            支持的模型列表
        """
        return [
            "gpt-4",
            "gpt-4-turbo",
            "gpt-4-turbo-preview",
            "gpt-4-0125-preview",
            "gpt-4-1106-preview",
            "gpt-4-vision-preview",
            "gpt-3.5-turbo",
            "gpt-3.5-turbo-0125",
            "gpt-3.5-turbo-1106",
            "gpt-3.5-turbo-16k",
        ]

    def test_connection(self) -> bool:
        """
        测试OpenAI服务连接

        Returns:
            连接是否正常
        """
        try:
            # 尝试发送一个简单的测试消息
            test_message = (
                "Hello, this is a test message. Please reply with 'Test successful'."
            )
            response = self.chat(test_message)

            # 检查是否有回复
            if response and len(response.strip()) > 0:
                self.logger.info("OpenAI连接测试成功")
                return True
            else:
                self.logger.error("OpenAI连接测试失败：无回复")
                return False

        except Exception as e:
            self.logger.error(f"OpenAI连接测试失败: {e}")
            return False

    def get_model_info(self) -> Dict[str, Any]:
        """
        获取模型信息

        Returns:
            模型信息字典
        """
        return {
            "provider": "OpenAI",
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "frequency_penalty": self.frequency_penalty,
            "presence_penalty": self.presence_penalty,
            "base_url": self.base_url,
            "supported_models": self.get_supported_models(),
        }

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> Dict[str, float]:
        """
        估算调用成本（基于OpenAI定价）

        Args:
            input_tokens: 输入token数
            output_tokens: 输出token数

        Returns:
            成本信息字典
        """
        # OpenAI定价（2024年价格，仅供参考，美元/千token）
        pricing = {
            "gpt-4": {"input": 0.03, "output": 0.06},
            "gpt-4-turbo": {"input": 0.01, "output": 0.03},
            "gpt-4-turbo-preview": {"input": 0.01, "output": 0.03},
            "gpt-4-0125-preview": {"input": 0.01, "output": 0.03},
            "gpt-4-1106-preview": {"input": 0.01, "output": 0.03},
            "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
            "gpt-3.5-turbo-0125": {"input": 0.0005, "output": 0.0015},
            "gpt-3.5-turbo-1106": {"input": 0.001, "output": 0.002},
        }

        model_pricing = pricing.get(self.model, pricing["gpt-3.5-turbo"])

        input_cost = (input_tokens / 1000) * model_pricing["input"]
        output_cost = (output_tokens / 1000) * model_pricing["output"]
        total_cost = input_cost + output_cost

        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_cost_usd": input_cost,
            "output_cost_usd": output_cost,
            "total_cost_usd": total_cost,
            "model": self.model,
        }

    def list_models(self) -> List[Dict[str, Any]]:
        """
        列出可用的模型（需要API调用）

        Returns:
            模型信息列表
        """
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

            models_url = self.base_url.replace("/chat/completions", "/models")
            response = requests.get(models_url, headers=headers, timeout=self.timeout)
            response.raise_for_status()

            result = response.json()
            return result.get("data", [])

        except Exception as e:
            self.logger.error(f"获取模型列表失败: {e}")
            return []


# 便捷函数
def create_openai_llm(
    api_key: str, model: str = "gpt-3.5-turbo", **kwargs
) -> OpenAILLM:
    """
    创建OpenAI LLM实例的便捷函数

    Args:
        api_key: API密钥
        model: 模型名称
        **kwargs: 其他配置参数

    Returns:
        OpenAILLM实例
    """
    config = {"api_key": api_key, "model": model, **kwargs}
    return OpenAILLM(config)


def openai_chat(input_text: str, api_key: str, model: str = "gpt-3.5-turbo") -> str:
    """
    OpenAI对话的便捷函数

    Args:
        input_text: 输入文本
        api_key: API密钥
        model: 模型名称

    Returns:
        回复文本
    """
    llm = create_openai_llm(api_key, model)
    return llm.chat(input_text)
