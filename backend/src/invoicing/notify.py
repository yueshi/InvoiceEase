"""通知机器人（数字员工 P2/M7）：企微群机器人 webhook。

失败降级绝不阻断主流程：URL 未配置 → no-op；HTTP/网络异常 → 仅日志。
"""
import logging

import httpx

from invoicing.config import settings

logger = logging.getLogger(__name__)


def notify(text: str) -> bool:
    """推送 markdown 到企微群机器人。返回是否成功推送；任何失败不抛异常。"""
    url = settings.notify_webhook_url
    if not url:
        return False
    try:
        resp = httpx.post(
            url,
            json={"msgtype": "markdown", "markdown": {"content": text}},
            timeout=5,
        )
        if resp.status_code != 200:
            logger.warning("通知推送 HTTP %s", resp.status_code)
            return False
        data = resp.json()
        if data.get("errcode") != 0:
            logger.warning("通知推送失败 errcode=%s", data.get("errcode"))
            return False
        return True
    except Exception:
        logger.warning("通知推送异常", exc_info=True)
        return False
