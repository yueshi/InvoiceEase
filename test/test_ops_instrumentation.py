"""任务埋点：成功/异常/failed/missed/记录动作自身失败不传染。"""
# 顶部 import：让模型在收集期注册进 Base.metadata，db fixture 的 create_all 才能建表（本文件单独跑时也确定）。
import asyncio

import pytest

from invoicing.models.ops import TaskRun
from invoicing.ops import instrumentation
from invoicing.ops.instrumentation import record_failure, record_run, run_manual, wrap_job


def test_record_run_success(db):
    assert record_run("t1", "manual", lambda: 42) == 42
    run = db.query(TaskRun).one()
    assert run.outcome == "success" and run.duration_ms is not None and run.trigger == "manual"


def test_record_run_error_reraises_and_records(db):
    def boom():
        raise ValueError("x")

    with pytest.raises(ValueError):
        record_run("t1", "enqueue", boom)
    run = db.query(TaskRun).one()
    assert run.outcome == "error" and "ValueError" in run.error


def test_record_run_failed_outcome(db):
    record_run("t", "manual", lambda: 0, is_success=lambda r: r > 0)
    assert db.query(TaskRun).one().outcome == "failed"


def test_record_failure_never_raises(db, monkeypatch):
    def broken_session():
        raise RuntimeError("db down")

    monkeypatch.setattr(instrumentation, "SessionLocal", broken_session)
    record_failure("parse", ValueError("boom"))  # 不应抛出


def test_record_failure_default_trigger_scheduler(db):
    """默认 trigger=scheduler（班表补记入口）；显式 trigger 分流（队列入口）。"""
    record_failure("parse", ValueError("boom"))
    assert db.query(TaskRun).one().trigger == "scheduler"


def test_record_failure_trigger_param(db):
    """queue.py 调用传 trigger='queue' → 错误行 trigger 分流为 queue。"""
    record_failure("parse", ValueError("boom"), trigger="queue")
    row = db.query(TaskRun).one()
    assert row.trigger == "queue"
    assert row.outcome == "error"


def test_wrap_job_records(db):
    asyncio.run(wrap_job("t_job", lambda: 7)())
    assert db.query(TaskRun).one().outcome == "success"


def test_run_manual_unknown_task(db):
    with pytest.raises(KeyError):
        run_manual("__no_such_task__")
