"""日志落盘（运维兜底）：dictConfig + 按大小轮转 + 敏感值打码。

INVOICING_LOG_LEVEL 首次被消费；uvicorn / apscheduler logger 汇入同一文件。
不做 JSON 格式（客户运维读文本更直接，YAGNI）。
"""
import logging
import logging.config
import re
from pathlib import Path

from invoicing.config import settings

# password/token/secret/key 字样的键值打码（覆盖 password=xxx / token: xxx 两种形态）
_SENSITIVE_RE = re.compile(r"((?:password|token|secret|key)\s*[=:]\s*)\S+", re.IGNORECASE)


class SensitiveFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _SENSITIVE_RE.sub(r"\1***", record.msg)
        if record.args:
            record.args = tuple(
                _SENSITIVE_RE.sub(r"\1***", a) if isinstance(a, str) else a for a in record.args
            )
        return True


def setup_logging() -> None:
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"sensitive": {"()": SensitiveFilter}},
        "formatters": {
            "standard": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
        },
        "handlers": {
            "console": {"class": "logging.StreamHandler", "formatter": "standard",
                        "filters": ["sensitive"]},
            "file": {"class": "logging.handlers.RotatingFileHandler", "formatter": "standard",
                     "filters": ["sensitive"], "filename": str(log_dir / "invoicing.log"),
                     "maxBytes": settings.log_max_bytes, "backupCount": settings.log_backup_count,
                     "encoding": "utf-8"},
        },
        "root": {"level": level, "handlers": ["console", "file"]},
        "loggers": {
            "uvicorn": {"level": "INFO", "handlers": ["console", "file"], "propagate": False},
            "uvicorn.access": {"level": "INFO", "handlers": ["console", "file"], "propagate": False},
            "apscheduler": {"level": "WARNING", "handlers": ["console", "file"], "propagate": False},
        },
    })
