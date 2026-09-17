"""调度器任务目录测试。"""
from invoicing import scheduler as sched_mod


def test_task_registry_contains_mailbox_poll():
    assert "mailbox_poll" in sched_mod.TASKS
    spec = sched_mod.TASKS["mailbox_poll"]
    assert spec["trigger"] == "interval"
    # tick 粒度 = 默认收信间隔（300s）：tick 只是轻量检查，实际收信由 per-mailbox 门控
    assert spec["trigger_kwargs"]["seconds"] == sched_mod.TICK_SECONDS == 300


def test_register_task_supports_cron():
    """注册表结构预留 cron 时刻表（P3 周报/月报），现在仅验证结构。"""
    sched_mod.register_task("test_task", lambda: None, trigger="cron", hour=9, day_of_week="mon")
    spec = sched_mod.TASKS["test_task"]
    assert spec["trigger"] == "cron"
    assert spec["trigger_kwargs"]["hour"] == 9
    sched_mod.TASKS.pop("test_task")  # 清理，避免影响其他测试


def test_task_registry_contains_review_predict():
    assert "review_predict" in sched_mod.TASKS
    assert sched_mod.TASKS["review_predict"]["trigger"] == "interval"
    assert sched_mod.TASKS["review_predict"]["trigger_kwargs"]["seconds"] == sched_mod.TICK_SECONDS


def test_receipt_classify_is_manual_only():
    """重分类是手动任务：注册为 manual 触发（不设定时，避免每日空跑）。"""
    assert sched_mod.TASKS["receipt_classify"]["trigger"] == "manual"
