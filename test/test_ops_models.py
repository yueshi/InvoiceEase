"""运维兜底两表：task_runs / ops_alerts 读写与语义。"""
# 顶部 import：让模型在收集期注册进 Base.metadata，db fixture 的 create_all 才能建表（本文件单独跑时也确定）。
# 函数体内的 import 保留 brief 原文，二者等价（模块缓存）。
from invoicing.models.ops import OpsAlert, TaskRun


def test_task_run_roundtrip(db):
    from invoicing.models.ops import OpsAlert, TaskRun

    db.add(TaskRun(task_name="mailbox_poll", trigger="scheduler", outcome="success", duration_ms=12))
    db.add(OpsAlert(rule_key="disk.low", severity="warning", message="磁盘剩余 8%"))
    db.commit()
    run = db.query(TaskRun).one()
    assert run.outcome == "success"
    assert db.query(OpsAlert).one().severity == "warning"


def test_missed_run_allows_null_duration_and_error(db):
    from invoicing.models.ops import TaskRun

    db.add(TaskRun(task_name="review_predict", trigger="scheduler", outcome="missed"))
    db.commit()
    run = db.query(TaskRun).one()
    assert run.duration_ms is None and run.error is None
