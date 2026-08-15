import asyncio
import logging

from arq import create_pool
from arq.connections import RedisSettings

from invoicing.config import settings

logger = logging.getLogger(__name__)

redis_settings = RedisSettings.from_dsn(settings.redis_url)


async def enqueue_parse(invoice_id: int) -> None:
    pool = await create_pool(redis_settings)
    try:
        await pool.enqueue_job("parse_invoice_task", invoice_id, _job_id=f"parse:{invoice_id}")
    finally:
        await pool.aclose()


async def enqueue_verify(invoice_id: int) -> None:
    pool = await create_pool(redis_settings)
    try:
        await pool.enqueue_job("verify_invoice_task", invoice_id, _job_id=f"verify:{invoice_id}")
    finally:
        await pool.aclose()


def enqueue_parse_sync(invoice_id: int) -> None:
    if settings.queue_backend == "local":
        # 本地模式：同步内联执行（开发确定性优先；生产 redis 模式走 arq worker）
        from invoicing.workers.tasks import _parse_invoice

        _parse_invoice(invoice_id)
        return
    try:
        asyncio.run(enqueue_parse(invoice_id))
    except Exception:
        logger.exception("入队解析任务失败 invoice_id=%s", invoice_id)


def enqueue_verify_sync(invoice_id: int) -> None:
    if settings.queue_backend == "local":
        try:
            from invoicing.workers.tasks import _verify_invoice

            _verify_invoice(invoice_id)
        except Exception:
            # _verify_invoice 在 Task 11 落地前不存在属预期（Task 10 期间仅记日志）
            logger.exception("内联验真执行失败 invoice_id=%s", invoice_id)
        return
    try:
        asyncio.run(enqueue_verify(invoice_id))
    except Exception:
        logger.exception("入队验真任务失败 invoice_id=%s", invoice_id)


class WorkerSettings:
    """arq 配置类：`uv run arq invoicing.workers.queue.WorkerSettings` 启动 worker。

    `functions` 用全限定字符串路径，arq 启动时自动导入任务函数；
    `max_tries=3` 覆盖全局默认重试，失败任务重试后仍失败则标记为失败任务。
    """

    functions = [
        "invoicing.workers.tasks.parse_invoice_task",
        "invoicing.workers.tasks.verify_invoice_task",
    ]
    max_tries = 3
    max_jobs = 10
