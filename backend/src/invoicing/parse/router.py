"""解析分级路由（策略链实现见 parse/pipeline.py；本模块保留兼容导出）。

历史消费方（mcp/extract.py、workers/tasks.py）继续 `from invoicing.parse.router import parse_file`。
"""
from invoicing.parse.pipeline import parse_file  # noqa: F401
