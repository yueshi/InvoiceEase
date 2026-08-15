"""AgentlyFetcher 测试：monkeypatch run_cli 模拟 CLI 输出（契约见设计 §六）。"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from invoicing.fetch.agently import AgentlyCliError, AgentlyFetcher, BATCH_LIMIT
from invoicing.fetch.protocol import RawMailMessage
from invoicing.models import Mailbox

FIXTURES = Path(__file__).parent / "fixtures" / "agently"


def _mailbox(db, keywords: str = "") -> Mailbox:
    # keywords 默认空串：关键词过滤语义由 test_fetch_new_keyword_filter_before_download 单独覆盖，
    # 其余测试（批上限/分页/计数）不受默认「发票,Invoice」干扰。
    mb = Mailbox(name="Agently", mailbox_type="agently", agently_workspace=None, keywords=keywords)
    db.add(mb)
    db.flush()
    return mb


def _mock_cli(monkeypatch, tmp_path, list_messages=None, read_attachments=None):
    """返回按命令序列应答的 run_cli 假实现；+download 真实写文件到 tmp_path。"""
    import invoicing.fetch.agently as agently_mod

    calls = []

    def fake_run_cli(mailbox, args, timeout=30, cwd=None):
        calls.append(args)
        if args[:2] == ["message", "+list"]:
            payload = {"ok": True, "data": {"data": list_messages or [], "pagination": {"has_more": False}}}
            return payload
        if args[:2] == ["message", "+read"]:
            return {"ok": True, "data": {"attachments": read_attachments or []}}
        if args[:2] == ["attachment", "+download"]:
            name = args[args.index("--att") + 1]
            out = args[args.index("--output") + 1]
            assert not Path(out).is_absolute(), f"--output 必须为相对路径（真实 CLI 契约）: {out}"
            base = Path(cwd) if cwd else tmp_path  # 真实 CLI 以自身 cwd 解析相对输出目录
            path = base / f"{name}.xml"
            path.write_bytes(b"<eInvoice/>")
            return {"ok": True, "data": {"filename": f"{name}.xml", "saved_to": str(path), "size": 11}}
        raise AssertionError(f"unexpected args: {args}")

    monkeypatch.setattr(agently_mod, "run_cli", fake_run_cli)
    monkeypatch.setattr(agently_mod, "REQUEST_INTERVAL", 0.0)  # 测试不等待
    return calls


def test_fetch_new_skips_no_attachment_and_maps_fields(db, monkeypatch, tmp_path):
    list_messages = json.loads((FIXTURES / "list_page.json").read_text())["data"]["data"]
    calls = _mock_cli(
        monkeypatch, tmp_path, list_messages=list_messages,
        read_attachments=[{"attachment_id": "att_x", "content_type": "text/xml", "filename": "dianzi.xml", "size": "888"}],
    )
    fetcher = AgentlyFetcher(_mailbox(db))
    result = fetcher.fetch_new(0)
    assert len(result) == 1  # 无附件消息跳过
    msg: RawMailMessage = result[0]
    assert msg.message_id == "<tencent_20D41DB7025E2102E1FC56AC5024638EFF08@qq.com>"
    assert msg.provider_message_id == "msg_V6HF_D-vUXxlEs_csGIpRYhgppPIzpGr703DUqi32v-bYA"
    assert msg.sender == "aken123@agent.qq.com"
    assert msg.attachments[0].filename == "dianzi.xml"
    assert msg.attachments[0].data == b"<eInvoice/>"
    expected_uid = int(datetime.fromisoformat("2026-08-15T08:11:27Z").timestamp())
    assert msg.uid == expected_uid
    # +read 与 +download 各被调用一次
    assert sum(1 for c in calls if c[:2] == ["message", "+read"]) == 1
    assert sum(1 for c in calls if c[:2] == ["attachment", "+download"]) == 1


def test_fetch_new_cursor_filters_older(db, monkeypatch, tmp_path):
    list_messages = json.loads((FIXTURES / "list_page.json").read_text())["data"]["data"]
    _mock_cli(monkeypatch, tmp_path, list_messages=list_messages)
    fetcher = AgentlyFetcher(_mailbox(db))
    newest = int(datetime.fromisoformat("2026-08-15T08:11:27Z").timestamp())
    result = fetcher.fetch_new(newest + 1)  # 游标已过最新消息
    assert result == []


def test_fetch_new_batch_limit(db, monkeypatch, tmp_path):
    base = "2026-08-15T08:%02d:00Z"
    list_messages = [
        {
            "created_at": base % (i % 60), "from": {"email": f"u{i}@agent.qq.com", "name": ""},
            "has_attachments": True, "is_read": False, "message_id": f"msg_{i}",
            "rfc_message_id": f"<m{i}@qq.com>", "subject": f"票{i}", "snippet": "",
        }
        for i in range(BATCH_LIMIT + 2)
    ]
    _mock_cli(monkeypatch, tmp_path, list_messages=list_messages)
    fetcher = AgentlyFetcher(_mailbox(db))
    result = fetcher.fetch_new(0)
    assert len(result) == BATCH_LIMIT  # 批上限截断，剩余下轮续拉


def test_cli_missing_raises(db, monkeypatch):
    import invoicing.fetch.agently as agently_mod

    monkeypatch.setattr(agently_mod.shutil, "which", lambda _: None)
    monkeypatch.setattr(agently_mod, "REQUEST_INTERVAL", 0.0)  # 测试不等待
    fetcher = AgentlyFetcher(_mailbox(db))
    with pytest.raises(AgentlyCliError, match="未安装"):
        fetcher.fetch_new(0)


def test_run_cli_nonzero_raises(db, monkeypatch):
    import subprocess

    import invoicing.fetch.agently as agently_mod

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(agently_mod.shutil, "which", lambda _: "/usr/local/bin/agently-cli")
    monkeypatch.setattr(agently_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(agently_mod, "REQUEST_INTERVAL", 0.0)  # 测试不等待
    fetcher = AgentlyFetcher(_mailbox(db))
    with pytest.raises(AgentlyCliError, match="退出码"):
        fetcher.fetch_new(0)


def test_list_messages_early_break_on_old_page(db, monkeypatch, tmp_path):
    import invoicing.fetch.agently as agently_mod

    old = "2026-08-14T00:00:00Z"
    new = "2026-08-15T08:11:27Z"
    calls = []

    def fake_run_cli(mailbox, args, timeout=30):
        calls.append(args)
        if "--cursor" in args:
            return {"ok": True, "data": {"data": [{"created_at": old, "from": {"email": "u@x.com"}, "has_attachments": False, "message_id": "msg_old", "rfc_message_id": "<old@qq.com>", "subject": "s"}], "pagination": {"has_more": False}}}
        return {"ok": True, "data": {"data": [{"created_at": new, "from": {"email": "u@x.com"}, "has_attachments": True, "message_id": "msg_new", "rfc_message_id": "<new@qq.com>", "subject": "s"}], "pagination": {"has_more": True, "next_cursor": "cur2"}}}

    monkeypatch.setattr(agently_mod, "run_cli", fake_run_cli)
    monkeypatch.setattr(agently_mod, "REQUEST_INTERVAL", 0.0)
    fetcher = AgentlyFetcher(_mailbox(db))
    result = fetcher.fetch_new(int(datetime.fromisoformat("2026-08-15T00:00:00Z").timestamp()))
    assert len(result) == 1
    assert result[0].provider_message_id == "msg_new"
    # 第二页整页早于游标即停（第三页不会请求）。按 +list 调用计数：
    # 消息处理还会产生 +read 请求，故不数总调用数。
    assert sum(1 for c in calls if c[:2] == ["message", "+list"]) == 2


def test_fetch_new_keyword_filter_before_download(db, monkeypatch, tmp_path):
    import invoicing.fetch.agently as agently_mod

    list_messages = json.loads((FIXTURES / "list_page.json").read_text())["data"]["data"]
    calls = []
    read_attachments = [{"attachment_id": "att_x", "content_type": "text/xml", "filename": "dianzi.xml", "size": "888"}]

    def fake_run_cli(mailbox, args, timeout=30):
        calls.append(args)
        if args[:2] == ["message", "+list"]:
            return {"ok": True, "data": {"data": list_messages, "pagination": {"has_more": False}}}
        if args[:2] == ["message", "+read"]:
            return {"ok": True, "data": {"attachments": read_attachments}}
        if args[:2] == ["attachment", "+download"]:
            name = args[args.index("--att") + 1]
            path = tmp_path / f"{name}.xml"
            path.write_bytes(b"<eInvoice/>")
            return {"ok": True, "data": {"filename": f"{name}.xml", "saved_to": str(path), "size": 11}}
        raise AssertionError(f"unexpected args: {args}")

    monkeypatch.setattr(agently_mod, "run_cli", fake_run_cli)
    monkeypatch.setattr(agently_mod, "REQUEST_INTERVAL", 0.0)
    mb = _mailbox(db)
    mb.keywords = "报销"  # 与「发票测试-数电票」不匹配
    db.flush()
    fetcher = AgentlyFetcher(mb)
    result = fetcher.fetch_new(0)
    assert result == []
    assert sum(1 for c in calls if c[:2] == ["message", "+read"]) == 0  # 未下载即被过滤
