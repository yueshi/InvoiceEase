"""Agent LLM 客户端：与 parse/llm.py 共用同一套 INVOICING_LLM_* 配置。

用 AsyncOpenAI（真异步流式）——同步 client 在 async def 里迭代会阻塞
event loop，SSE token 会堆积到流末一次性冲出（AuditMind 已踩过）。
"""
from invoicing.config import settings

_client = None
_tried = False


def get_agent_client():
    """惰性单例 AsyncOpenAI；llm_enabled=False → None（run_agent 走静态兜底）。"""
    global _client, _tried
    if _client is None and not _tried:
        if settings.llm_enabled:
            from openai import AsyncOpenAI

            _client = AsyncOpenAI(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                timeout=settings.llm_timeout_seconds,
                max_retries=settings.llm_max_retries,
            )
        _tried = True
    return _client


def agent_model() -> str:
    """Agent 用对话模型：agent_llm_model 为空时复用 llm_model_text。"""
    return settings.agent_llm_model or settings.llm_model_text


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（历史裁剪用，非精确）.

    中文按 1.5 字符/token；英文按 4 字符/token；混合按中文比例线性插值。
    """
    if not text:
        return 0
    chinese = sum(1 for c in text if "一" <= c <= "鿿")
    other = len(text) - chinese
    return int(chinese / 1.5 + other / 4 + 0.999)  # 向上取整
