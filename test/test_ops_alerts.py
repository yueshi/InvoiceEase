"""告警：record_alert 冷却契约、规则触发与 evaluate_alerts 全量评估。

补丁挂点说明：record_alert 无模块级 _push，通过延迟 import 直接调
`invoicing.notify.notify`——测试统一 monkeypatch 该挂点（延迟 import 保证补丁生效）。
"""
from datetime import timedelta

from invoicing.config import settings
from invoicing.models import Invoice, Mailbox, OpsAlert, TaskRun
from invoicing.models.fields import utcnow
from invoicing.ops import alerts
from invoicing.ops.alerts import (
    _rule_backup_failed,
    _rule_backup_missing,
    _rule_disk_low,
    _rule_mailbox_stalled,
    _rule_parse_error_rate,
    _rule_review_backlog,
    _rule_task_failed,
)


def test_record_alert_cooldown_suppresses_push(db, monkeypatch):
    """同 rule_key 冷却期内：只落库不推送，返回 False。"""
    pushed = []
    monkeypatch.setattr("invoicing.notify.notify", lambda text: pushed.append(text) or True)
    assert alerts.record_alert(db, "disk.low", "warning", "磁盘 8%") is True
    assert alerts.record_alert(db, "disk.low", "warning", "磁盘 7%") is False  # 冷却内不推
    assert len(pushed) == 1


def test_record_alert_cooldown_expired_pushes(db, monkeypatch):
    """冷却过期后再次触发：返回 True 并推送。"""
    pushed = []
    monkeypatch.setattr("invoicing.notify.notify", lambda text: pushed.append(text) or True)
    # 首条冷却期为负 → cooldown_until 已是过去，第二次触发不再被抑制
    assert alerts.record_alert(db, "disk.low", "warning", "磁盘 8%",
                               cooldown=timedelta(seconds=-1)) is True
    assert alerts.record_alert(db, "disk.low", "warning", "磁盘 7%") is True
    assert len(pushed) == 2


def test_record_alert_db_error_returns_false(db, monkeypatch):
    """DB 异常时：返回 False 不抛出（告警基座自身绝不阻断调用方）。"""
    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "query", boom)
    assert alerts.record_alert(db, "disk.low", "warning", "磁盘 8%") is False


def test_rule_task_failed_window(db):
    """规则1：近 1h error 记录 ≥3 即触发（时间窗计数，非「连续3次」）。"""
    now = utcnow()
    for i in range(3):
        db.add(TaskRun(task_name="parse", trigger="enqueue",
                       started_at=now - timedelta(minutes=i),
                       outcome="error", error="ValueError: x"))
    db.commit()
    fired = _rule_task_failed(db)
    assert "task_failed.parse" in fired


def test_rule_task_failed_excludes_daily_cron_tasks(db):
    """修4：每日/每月 cron 任务（结构上不可能 1h 内 ≥3 次）剔除出 task_failed 集合；
    interval 高频任务仍在集合内且照常计数触发。"""
    assert set(alerts._TASK_FAILED_NAMES) == {
        "mailbox_poll", "review_predict", "parse", "verify", "receipt_parse", "ops_check",
    }
    now = utcnow()
    for name in ("ops_backup", "audit_retention", "monthly_health"):
        for i in range(3):
            db.add(TaskRun(task_name=name, trigger="scheduler",
                           started_at=now - timedelta(minutes=i),
                           outcome="error", error="x"))
    db.commit()
    fired = _rule_task_failed(db)
    assert not any(k.startswith("task_failed.") for k in fired)
    for i in range(3):
        db.add(TaskRun(task_name="mailbox_poll", trigger="scheduler",
                       started_at=now - timedelta(minutes=i),
                       outcome="error", error="x"))
    db.commit()
    assert "task_failed.mailbox_poll" in _rule_task_failed(db)


def test_rule_backup_failed_fires(db):
    """修4：近 24h 内 ops_backup 存在 error 行 → 触发 backup.failed（critical）。"""
    now = utcnow()
    db.add(TaskRun(task_name="ops_backup", trigger="scheduler",
                   started_at=now - timedelta(hours=2), outcome="error",
                   error="IOError: disk full"))
    db.commit()
    assert "backup.failed" in _rule_backup_failed(db)


def test_rule_backup_failed_silent(db):
    """修4：无 error 行（成功/空）不触发。"""
    assert _rule_backup_failed(db) == []
    db.add(TaskRun(task_name="ops_backup", trigger="scheduler",
                   started_at=utcnow(), outcome="success"))
    db.commit()
    assert _rule_backup_failed(db) == []


def test_rule_review_backlog(db):
    """规则5：待复核积压 > 阈值（50）即触发。"""
    for i in range(51):
        db.add(Invoice(file_url=f"r{i}.xml", file_type="XML", status="pending_review"))
    db.commit()
    assert "review.backlog" in _rule_review_backlog(db)


def test_rule_mailbox_stalled(db):
    """规则2：enabled 邮箱超过 2×轮询间隔未更新（含从未轮询过）才判定停滞。"""
    db.add(Mailbox(name="收件箱A", enabled=True, last_polled_at=None, poll_interval_seconds=300))
    db.add(Mailbox(name="收件箱B", enabled=True, last_polled_at=utcnow(),
                   poll_interval_seconds=300))
    db.commit()
    assert "mailbox.stalled" in _rule_mailbox_stalled(db)
    row = db.query(OpsAlert).filter(OpsAlert.rule_key == "mailbox.stalled").one()
    assert "收件箱A" in row.message and "收件箱B" not in row.message


def test_rule_backup_missing_fires(db, tmp_path, monkeypatch):
    """规则3：备份目录无任何备份 → 触发。"""
    monkeypatch.setattr(settings, "ops_backup_dir", str(tmp_path / "backups"))
    assert "backup.missing" in _rule_backup_missing(db)


def test_rule_backup_missing_silent_when_fresh(db, tmp_path, monkeypatch):
    """规则3：26 小时内有新备份 → 不触发。"""
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "invoiceease-backup-20260917-000000.tar.gz").write_bytes(b"x")
    monkeypatch.setattr(settings, "ops_backup_dir", str(backups))
    assert _rule_backup_missing(db) == []


def test_rule_disk_low_fires(db, monkeypatch):
    """规则4：磁盘剩余低于阈值百分比 → 触发。"""
    monkeypatch.setattr("invoicing.ops.metrics.storage_usage",
                        lambda: {"disk_free_percent": 5.0})
    assert "disk.low" in _rule_disk_low(db)


def test_rule_disk_low_silent_when_healthy(db, monkeypatch):
    """规则4：磁盘剩余充足 → 不触发。"""
    monkeypatch.setattr("invoicing.ops.metrics.storage_usage",
                        lambda: {"disk_free_percent": 60.0})
    assert _rule_disk_low(db) == []


def test_rule_parse_error_rate_fires(db):
    """规则6：近 1h 解析 error 占比超阈值（样本数达标）→ 触发。"""
    now = utcnow()
    for i in range(6):  # 2/6 ≈ 33% > 20%
        outcome = "error" if i < 2 else "success"
        db.add(TaskRun(task_name="parse", trigger="enqueue",
                       started_at=now - timedelta(minutes=i),
                       outcome=outcome, error="ValueError: x" if outcome == "error" else None))
    db.commit()
    assert "parse.error_rate" in _rule_parse_error_rate(db)


def test_rule_parse_error_rate_below_min_samples(db):
    """规则6：样本数低于 alert_parse_error_min → 不触发。"""
    now = utcnow()
    for i in range(3):
        db.add(TaskRun(task_name="parse", trigger="enqueue",
                       started_at=now - timedelta(minutes=i), outcome="error", error="x"))
    db.commit()
    assert _rule_parse_error_rate(db) == []


def test_evaluate_alerts_runs_all(db, monkeypatch):
    """evaluate_alerts 跑全部规则并汇总本次触发（含冷却抑制未推送）的 rule_key。"""
    monkeypatch.setattr("invoicing.notify.notify", lambda text: True)
    monkeypatch.setattr(alerts, "_rule_task_failed", lambda db: [])
    monkeypatch.setattr(alerts, "_rule_mailbox_stalled", lambda db: [])
    monkeypatch.setattr(alerts, "_rule_backup_missing", lambda db: [])
    monkeypatch.setattr(alerts, "_rule_disk_low", lambda db: [])
    monkeypatch.setattr(alerts, "_rule_review_backlog", lambda db: ["review.backlog"])
    monkeypatch.setattr(alerts, "_rule_parse_error_rate", lambda db: [])
    # _RULES 持有模块加载期的原函数引用；按现值逐名映射到补丁后的函数，
    # 避免硬编码规则名——未来新增规则时自动纳入、静默漂移归零
    monkeypatch.setattr(alerts, "_RULES", [
        getattr(alerts, rule.__name__) for rule in alerts._RULES
    ])
    assert alerts.evaluate_alerts(db) == ["review.backlog"]


def test_evaluate_alerts_rule_failure_isolated(db, monkeypatch):
    """全局约束锁定：单规则抛异常仅记录日志，其余规则照常评估、异常不外抛。"""
    boom_rule = alerts._RULES[0].__name__
    expected = [rule.__name__ for rule in alerts._RULES[1:]]
    called = []

    def _boom(db):
        raise RuntimeError("rule boom")

    def _record(rule_name):
        def _rule(db):
            called.append(rule_name)
            return [rule_name]

        return _rule

    # 首条规则抛异常；其余规则按 _RULES 现值逐名替换为「记录+返回」，自动覆盖全部规则
    patched = {boom_rule: _boom}
    for rule in alerts._RULES[1:]:
        patched[rule.__name__] = _record(rule.__name__)
    for name, fn in patched.items():
        monkeypatch.setattr(alerts, name, fn)
    monkeypatch.setattr(alerts, "_RULES", [
        getattr(alerts, rule.__name__) for rule in alerts._RULES
    ])

    fired = alerts.evaluate_alerts(db)  # 不抛异常即通过
    assert called == expected
    assert fired == expected
