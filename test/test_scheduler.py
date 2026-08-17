"""调度器任务目录测试。"""
from invoicing import scheduler as sched_mod


def test_task_registry_contains_mailbox_poll():
    assert "mailbox_poll" in sched_mod.TASKS
    spec = sched_mod.TASKS["mailbox_poll"]
    assert spec["trigger"] == "interval"
    assert spec["trigger_kwargs"]["seconds"] == 60


def test_register_task_supports_cron():
    """注册表结构预留 cron 时刻表（P3 周报/月报），现在仅验证结构。"""
    sched_mod.register_task("test_task", lambda: None, trigger="cron", hour=9, day_of_week="mon")
    spec = sched_mod.TASKS["test_task"]
    assert spec["trigger"] == "cron"
    assert spec["trigger_kwargs"]["hour"] == 9
    sched_mod.TASKS.pop("test_task")  # 清理，避免影响其他测试
