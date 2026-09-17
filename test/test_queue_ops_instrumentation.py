"""修1 回归：local 模式队列任务成功路径经 record_run 落 task_runs（outcome=success，trigger=queue）。

TDD：先写失败测试再实现。成功/失败各只记一行；后台线程（回单）也在线程内落行不阻塞主流程。
"""
import time

from invoicing.models.ops import TaskRun
from invoicing.workers import queue


def test_enqueue_parse_sync_local_records_success(db, monkeypatch):
    """local 内联解析成功 → task_runs 落一行 success（trigger=queue），不抛异常。"""
    import invoicing.workers.tasks as tasks_mod

    monkeypatch.setattr(queue.settings, "queue_backend", "local")
    calls = []
    monkeypatch.setattr(tasks_mod, "_parse_invoice", lambda invoice_id: calls.append(invoice_id))

    queue.enqueue_parse_sync(42)

    row = db.query(TaskRun).one()
    assert row.task_name == "parse"
    assert row.outcome == "success"
    assert row.trigger == "queue"
    assert row.detail == {"invoice_id": 42}
    assert calls == [42]


def test_enqueue_parse_sync_local_records_error_single_row(db, monkeypatch):
    """local 内联解析异常 → record_run 记一条 error 行（trigger=queue），except 不再补记第二条。"""
    import invoicing.workers.tasks as tasks_mod

    monkeypatch.setattr(queue.settings, "queue_backend", "local")

    def boom(invoice_id):
        raise ValueError("boom")

    monkeypatch.setattr(tasks_mod, "_parse_invoice", boom)

    queue.enqueue_parse_sync(1)  # 内联分支吞异常，不向上抛

    rows = db.query(TaskRun).all()
    assert len(rows) == 1
    assert rows[0].outcome == "error"
    assert rows[0].trigger == "queue"
    assert "ValueError" in rows[0].error


def test_enqueue_verify_sync_local_records_success(db, monkeypatch):
    """local 内联验真成功 → task_runs 落一行 success（trigger=queue）。"""
    import invoicing.workers.tasks as tasks_mod

    monkeypatch.setattr(queue.settings, "queue_backend", "local")
    monkeypatch.setattr(tasks_mod, "_verify_invoice", lambda invoice_id: None)

    queue.enqueue_verify_sync(7)

    row = db.query(TaskRun).one()
    assert row.task_name == "verify"
    assert row.outcome == "success"
    assert row.trigger == "queue"
    assert row.detail == {"invoice_id": 7}


def test_enqueue_receipt_parse_sync_local_records_success_in_thread(db, monkeypatch):
    """回单解析走后台线程：线程内落行（outcome=success），主流程不阻塞即返回。"""
    import invoicing.workers.tasks as tasks_mod

    monkeypatch.setattr(queue.settings, "queue_backend", "local")
    monkeypatch.setattr(tasks_mod, "_parse_receipt_upload", lambda upload_id: None)

    queue.enqueue_receipt_parse_sync(9)  # 立即返回，不等待线程

    row = None
    for _ in range(100):
        row = db.query(TaskRun).filter(TaskRun.task_name == "receipt_parse").first()
        if row:
            break
        time.sleep(0.02)
    assert row is not None, "后台线程应已落 task_runs 行"
    assert row.outcome == "success"
    assert row.trigger == "queue"
    assert row.detail == {"upload_id": 9}
