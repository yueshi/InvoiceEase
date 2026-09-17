"""日志落盘：文件生成、级别消费、敏感值打码。"""
# 顶部 import：与仓库测试惯例一致（本文件无模型，不涉及 create_all 时机）。
import logging
import re

from invoicing.config import settings
from invoicing.ops.logging_setup import _SENSITIVE_RE, setup_logging


def test_setup_logging_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "log_dir", str(tmp_path))
    setup_logging()
    logging.getLogger("invoicing.test").info("hello log")
    for h in logging.getLogger().handlers:
        h.flush()
    assert (tmp_path / "invoicing.log").exists()


def test_log_level_setting_consumed(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "log_dir", str(tmp_path))
    monkeypatch.setattr(settings, "log_level", "DEBUG")
    setup_logging()
    assert logging.getLogger().level == logging.DEBUG


def test_sensitive_masking():
    masked = _SENSITIVE_RE.sub(r"\1***", "password=abc123 token: xyz secret=9 key=val")
    assert masked == "password=*** token: *** secret=*** key=***"
    assert re.search(r"abc123|xyz", masked) is None
