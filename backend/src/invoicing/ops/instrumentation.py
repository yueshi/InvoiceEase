"""任务埋点（运维兜底）：班表/队列/手动任务统一写 task_runs。

记录动作自身吞异常（record_failure / _write_row 永不抛出）——任务挂了埋点必须活着。
注意：既有班表任务与 local 队列在 except 块里吞异常（记日志+notify），wrapper 看不到；
这些位置需显式调 record_failure 补记 error 行（见 scheduler.py / queue.py 接线）。
"""
import asyncio
import inspect
import logging
import time

from invoicing.db import SessionLocal
from invoicing.models.fields import utcnow

logger = logging.getLogger(__name__)

PROCESS_STARTED_AT = utcnow()  # 模块首次导入 ≈ 进程启动（供 /ops/status uptime）


def _write_row(task_name, trigger, started_at, duration_ms, outcome, error, detail=None):
    try:
        from invoicing.models.ops import TaskRun

        with SessionLocal() as db:
            db.add(TaskRun(task_name=task_name, trigger=trigger, started_at=started_at,
                           finished_at=utcnow(), duration_ms=duration_ms,
                           outcome=outcome, error=error, detail=detail))
            db.commit()
    except Exception:
        logger.exception("task_runs 记录失败 task=%s", task_name)


def record_run(task_name, trigger, fn, *args, is_success=None, detail=None, **kwargs):
    """同步执行 fn 并写 task_runs；fn 抛异常记录 error 后原样抛出。"""
    started = time.monotonic()
    started_at = utcnow()
    outcome, error = "success", None
    try:
        result = fn(*args, **kwargs)
        if is_success is not None and not is_success(result):
            outcome = "failed"
        return result
    except Exception as exc:
        outcome, error = "error", f"{type(exc).__name__}: {exc}"
        raise
    finally:
        _write_row(task_name, trigger, started_at,
                   int((time.monotonic() - started) * 1000), outcome, error, detail)


async def record_run_async(task_name, trigger, fn, *args, is_success=None, detail=None, **kwargs):
    """record_run 的协程版：fn 为协程函数直接 await，同步函数走 to_thread 线程执行。"""
    started = time.monotonic()
    started_at = utcnow()
    outcome, error = "success", None
    try:
        if inspect.iscoroutinefunction(fn):
            result = await fn(*args, **kwargs)
        else:
            result = await asyncio.to_thread(fn, *args, **kwargs)
        if is_success is not None and not is_success(result):
            outcome = "failed"
        return result
    except Exception as exc:
        outcome, error = "error", f"{type(exc).__name__}: {exc}"
        raise
    finally:
        _write_row(task_name, trigger, started_at,
                   int((time.monotonic() - started) * 1000), outcome, error, detail)


def record_failure(task_name, exc, detail=None):
    """吞异常场景的补记入口：只写一条 error 行，绝不抛出。"""
    _write_row(task_name, "scheduler", utcnow(), None, "error",
               f"{type(exc).__name__}: {exc}", detail)


def wrap_job(task_id, fn):
    """班表任务统一包装（setup_scheduler 用）：执行 fn 并记 task_runs。"""

    async def _wrapped():
        started = time.monotonic()
        started_at = utcnow()
        outcome, error = "success", None
        try:
            if inspect.iscoroutinefunction(fn):
                return await fn()
            return await asyncio.to_thread(fn)
        except Exception as exc:
            outcome, error = "error", f"{type(exc).__name__}: {exc}"
            raise
        finally:
            _write_row(task_id, "scheduler", started_at,
                       int((time.monotonic() - started) * 1000), outcome, error)

    return _wrapped


def run_manual(task_id):
    """手动触发班表任务（trigger=manual），同步阻塞执行完成。"""
    from invoicing import scheduler as scheduler_mod

    spec = scheduler_mod.TASKS.get(task_id)
    if spec is None:
        raise KeyError(task_id)
    fn = spec["fn"]
    if inspect.iscoroutinefunction(fn):
        return asyncio.run(record_run_async(task_id, "manual", fn))
    return record_run(task_id, "manual", fn)
