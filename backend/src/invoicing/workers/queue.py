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


async def enqueue_receipt_parse(upload_id: int) -> None:
    pool = await create_pool(redis_settings)
    try:
        await pool.enqueue_job(
            "receipt_parse_task", upload_id, _job_id=f"receipt_parse:{upload_id}"
        )
    finally:
        await pool.aclose()


def enqueue_parse_sync(invoice_id: int) -> None:
    if settings.queue_backend == "local":
        # 本地模式：同步内联执行（开发确定性优先；生产 redis 模式走 arq worker）
        # record_run 统一记 success/error（trigger=queue），埋点自身失败绝不阻断业务
        try:
            from invoicing.ops.instrumentation import record_run
            from invoicing.workers.tasks import _parse_invoice

            record_run("parse", "queue", _parse_invoice, invoice_id,
                       detail={"invoice_id": invoice_id})
        except Exception:
            logger.exception("内联解析执行失败 invoice_id=%s", invoice_id)
        return
    try:
        asyncio.run(enqueue_parse(invoice_id))
    except Exception:
        logger.exception("入队解析任务失败 invoice_id=%s", invoice_id)


def enqueue_receipt_parse_sync(upload_id: int) -> None:
    """回单解析入队。local 模式必须后台线程而非内联——解析含逐块 LLM 兜底
    （可达分钟级），内联会重新阻塞上传请求（15s 前端超时的根因之一）。"""
    if settings.queue_backend == "local":
        import threading

        from invoicing.ops.instrumentation import record_run
        from invoicing.workers.tasks import _parse_receipt_upload

        # 埋点在后台线程内落行（成功/异常都记），主流程不等待不阻塞
        def _run_with_instrumentation():
            record_run("receipt_parse", "queue", _parse_receipt_upload, upload_id,
                       detail={"upload_id": upload_id})

        thread = threading.Thread(
            target=_run_with_instrumentation, args=(), daemon=True,
            name=f"receipt-parse-{upload_id}",
        )
        thread.start()
        return
    try:
        asyncio.run(enqueue_receipt_parse(upload_id))
    except Exception as exc:
        from invoicing.ops.instrumentation import record_failure

        record_failure("receipt_parse", exc, {"upload_id": upload_id}, trigger="queue")
        logger.exception("入队回单解析任务失败 upload_id=%s", upload_id)


def enqueue_verify_sync(invoice_id: int) -> None:
    if settings.queue_backend == "local":
        # 本地模式：同步内联执行（开发确定性优先；生产 redis 模式走 arq worker）
        # record_run 统一记 success/error（trigger=queue），埋点自身失败绝不阻断业务
        try:
            from invoicing.ops.instrumentation import record_run
            from invoicing.workers.tasks import _verify_invoice

            record_run("verify", "queue", _verify_invoice, invoice_id,
                       detail={"invoice_id": invoice_id})
        except Exception:
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
        "invoicing.workers.tasks.receipt_parse_task",
    ]
    max_tries = 3
    max_jobs = 10
