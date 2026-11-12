# -*- coding: utf-8 -*-
"""
AI 大模型调用客户端。
- 默认对接智谱 GLM（OpenAI 兼容格式）
- 统一处理超时、内容安全拦截、未知异常
- 日志中绝不打印用户简历内容
"""
import httpx

from . import config


class AIClientError(Exception):
    """AI 调用异常基类，携带友好文案与 HTTP 状态码。"""
    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class AITimeoutError(AIClientError):
    def __init__(self):
        super().__init__("AI处理超时了，稍等几秒再试一次", status_code=504)


class AISafetyError(AIClientError):
    def __init__(self):
        super().__init__("内容触发了安全检查，请调整一下表述", status_code=422)


class AIRateLimitError(AIClientError):
    def __init__(self):
        super().__init__("AI 服务当前繁忙，请稍后再试", status_code=429)


class AIUnknownError(AIClientError):
    def __init__(self, detail: str = ""):
        super().__init__("AI服务开小差了，请稍后再试", status_code=500)
        self.detail = detail  # 仅内部调试，不返回给前端


# 内容安全拦截的常见关键词（智谱等厂商返回信息中可能包含）
SAFETY_KEYWORDS = ["敏感", "安全", "违规", "content_policy", "safety", "moderation"]


def call_llm(prompt: str, system_prompt: str) -> str:
    """
    调用大模型，返回文本内容。
    抛出 AIClientError 子类供上层统一处理。
    """
    if not config.AI_API_KEY:
        raise AIUnknownError("服务端未配置 AI_API_KEY")

    url = config.AI_BASE_URL.rstrip("/") + "/chat/completions"

    # 在 system_prompt 末尾追加 NOT_A_RESUME 护栏
    guarded_system = (system_prompt or "你是专业的简历优化顾问。") + (
        "\n\n重要规则：如果用户提供的内容明显不是简历（如代码、歌词、闲聊、小说等），"
        "只返回固定字符串 " + config.NOT_A_RESUME_FLAG + "，不要输出任何其他内容。"
    )

    body = {
        "model": config.AI_MODEL,
        "messages": [
            {"role": "system", "content": guarded_system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": config.MAX_TOKENS,
    }

    try:
        with httpx.Client(timeout=config.AI_TIMEOUT) as client:
            resp = client.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {config.AI_API_KEY}",
                },
                json=body,
            )
    except httpx.TimeoutException:
        raise AITimeoutError()
    except Exception as e:
        # TODO: 按真实报错细化异常分类（如连接错误、DNS 错误等）
        raise AIUnknownError(str(e))

    if resp.status_code == 429:
        raise AIRateLimitError()

    if resp.status_code != 200:
        err_text = resp.text or ""
        # 内容安全拦截通常返回 400/401/403 并带安全相关关键词
        lowered = err_text.lower()
        if any(kw in lowered for kw in SAFETY_KEYWORDS):
            raise AISafetyError()
        # 非 2xx 且非安全/限流，归为未知错误
        # TODO: 根据智谱真实错误码（如 1301 内容安全、1302 限流等）细化
        raise AIUnknownError(f"status={resp.status_code}")

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except Exception as e:
        raise AIUnknownError(f"解析响应失败: {e}")

    return content


def call_vision_ocr(image_base64: str) -> str:
    """
    调用智谱 GLM-4V 视觉模型，从图片中提取文字（用于 JD 截图识别）。
    image_base64: 纯 base64 字符串（不含 data:image/... 前缀）。
    图片仅在当次请求内存中存在，不写盘、不缓存。
    """
    if not config.AI_API_KEY:
        raise AIUnknownError("服务端未配置 AI_API_KEY")

    url = config.AI_BASE_URL.rstrip("/") + "/chat/completions"
    body = {
        "model": config.AI_VISION_MODEL,
        "messages": [
            {"role": "system", "content": "你是一个精准的文字识别助手。请识别图片中的全部文字，按原文顺序输出，不要添加任何解释、总结或格式转换。"},
            {"role": "user", "content": [
                {"type": "text", "text": "请提取这张图片中的所有文字内容，保持原始换行。"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
            ]},
        ],
        "temperature": 0.1,
        "max_tokens": config.MAX_TOKENS,
    }

    try:
        with httpx.Client(timeout=config.AI_TIMEOUT) as client:
            resp = client.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {config.AI_API_KEY}",
                },
                json=body,
            )
    except httpx.TimeoutException:
        raise AITimeoutError()
    except Exception as e:
        raise AIUnknownError(str(e))

    if resp.status_code == 429:
        raise AIRateLimitError()
    if resp.status_code != 200:
        err_text = resp.text or ""
        lowered = err_text.lower()
        if any(kw in lowered for kw in SAFETY_KEYWORDS):
            raise AISafetyError()
        raise AIUnknownError(f"status={resp.status_code}")

    try:
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        raise AIUnknownError(f"解析响应失败: {e}")
