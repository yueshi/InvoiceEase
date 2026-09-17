"""日志落盘：文件生成、级别消费、敏感值打码。"""
# 顶部 import：与仓库测试惯例一致（本文件无模型，不涉及 create_all 时机）。
import logging
import re

from invoicing.config import settings
from invoicing.ops.logging_setup import _SENSITIVE_RE, SensitiveFilter, setup_logging


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


def test_sensitive_filter_dict_args_keeps_dict():
    """字典式 %(key)s 占位：打码后保留 dict 结构，格式化不再抛 TypeError。"""
    rec = logging.LogRecord(
        name="invoicing.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="token: %(token)s secret: %(secret)s",
        args=({"token": "abc123", "secret": "xyz"},), exc_info=None,
    )
    assert SensitiveFilter().filter(rec)
    assert isinstance(rec.args, dict)
    assert rec.args == {"token": "***", "secret": "***"}
    assert rec.getMessage() == "token: *** secret: ***"


def test_create_app_survives_logging_failure(monkeypatch):
    """日志落盘自身失败绝不阻断业务：create_app 降级 stderr 后照常返回 app。"""
    def boom():
        raise PermissionError("log dir not writable")

    monkeypatch.setattr("invoicing.ops.logging_setup.setup_logging", boom)
    from invoicing.main import create_app

    app = create_app()
    assert app.title == "发票易 InvoiceEase"
