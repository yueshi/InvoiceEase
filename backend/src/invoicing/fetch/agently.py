# fetch/agently.py
"""Agently Mail 收取适配：subprocess 调 agently-cli（输出契约见设计 V0.2 §六）。

限流硬约束（10 req/min、200 req/hr）：单轮批上限 BATCH_LIMIT 封带附件消息，
请求间隔 REQUEST_INTERVAL 秒，保证任意 60 秒窗口请求数 ≤ 10。
"""
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from time import monotonic, sleep

import httpx

from invoicing.fetch.crypto import decrypt_secret
from invoicing.fetch.protocol import MailFetcher, RawAttachment, RawMailMessage
from invoicing.models import Mailbox

logger = logging.getLogger(__name__)

BATCH_LIMIT = 8
REQUEST_INTERVAL = 8.0  # 间隔 8s：任意 60 秒窗口请求数 ≤ 10
LIST_LIMIT = 50
MAX_PAGES = 10  # +list 分页循环硬上限

MAX_PER_MIN = 10
MAX_PER_HOUR = 200
WINDOW_MIN = 60.0
WINDOW_HOUR = 3600.0

_hour_request_times: deque[float] = deque()


def _throttle_hour() -> None:
    """200 次/小时硬约束：窗口已满则休眠至最早请求滑出窗口。"""
    global _hour_request_times
    now = monotonic()
    while _hour_request_times and now - _hour_request_times[0] > WINDOW_HOUR:
        _hour_request_times.popleft()
    if len(_hour_request_times) >= MAX_PER_HOUR:
        wait = WINDOW_HOUR - (now - _hour_request_times[0]) + 1.0
        logger.warning("agently 请求达到小时限流预算，休眠 %.0f 秒", wait)
        sleep(wait)
    _hour_request_times.append(monotonic())


class AgentlyCliError(Exception):
    """agently-cli 不可用（未安装/未授权/超时/输出非法）。"""


def _cli_env(mailbox: Mailbox) -> dict:
    env = dict(os.environ)
    if mailbox.agently_token_encrypted:
        env["AGENTLY_ACCESS_TOKEN"] = decrypt_secret(mailbox.agently_token_encrypted)
    if mailbox.agently_workspace:
        env["AGENTLY_WORKSPACE"] = mailbox.agently_workspace
    return env


def run_cli(mailbox: Mailbox, args: list[str], timeout: int = 30, cwd: str | os.PathLike | None = None) -> dict:
    if not shutil.which("agently-cli"):
        raise AgentlyCliError("agently-cli 未安装")
    _throttle_hour()  # 200 次/小时硬约束（所有 CLI 调用统一计数）
    try:
        proc = subprocess.run(
            ["agently-cli", *args],
            capture_output=True, text=True, timeout=timeout, env=_cli_env(mailbox), cwd=cwd,
        )
    except subprocess.TimeoutExpired as e:
        raise AgentlyCliError(f"agently-cli 超时: {e}") from e
    if proc.returncode != 0:
        raise AgentlyCliError(f"agently-cli 退出码 {proc.returncode}: {(proc.stderr or '').strip()[:200]}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise AgentlyCliError(f"agently-cli 输出非 JSON: {e}") from e
    if not payload.get("ok"):
        raise AgentlyCliError(f"agently-cli 返回失败: {json.dumps(payload, ensure_ascii=False)[:200]}")
    return payload


def _to_unix(iso: str) -> int:
    """ISO8601 → unix 秒。秒级游标：同秒多条靠 (rfc_message_id, file_url) 唯一索引兜底。"""
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


class AgentlyFetcher(MailFetcher):
    def __init__(self, mailbox: Mailbox):
        self.mailbox = mailbox

    def fetch_new(self, last_uid: int) -> list[RawMailMessage]:
        messages = self._list_messages(last_uid)
        candidates = [
            m for m in messages
            if m.get("created_at") is not None  # 缺时间戳消息跳过，防裸 KeyError
            and _to_unix(m["created_at"]) >= last_uid
            and self._subject_matches_keywords(m.get("subject") or "")
        ]
        candidates.sort(key=lambda m: m["created_at"])  # 旧→新，游标只推进到已处理处
        # 游标（last_uid）由 service 层以返回消息的最大 uid 推进（poll_mailbox 内 max(uid)）
        result: list[RawMailMessage] = []
        processed = 0
        with tempfile.TemporaryDirectory(prefix="agently-att-") as tmp:
            for msg in candidates:
                ts = _to_unix(msg["created_at"])
                if msg.get("has_attachments"):
                    if processed >= BATCH_LIMIT:
                        logger.info("本轮达到批上限 %s，剩余消息下轮续拉", BATCH_LIMIT)
                        break
                    attachments = self._download_attachments(msg, Path(tmp))
                    processed += 1
                else:
                    continue  # 无附件消息跳过（设计 §五：无附件 → 忽略），不占用批上限
                result.append(
                    RawMailMessage(
                        uid=ts,
                        message_id=msg.get("rfc_message_id") or msg["message_id"],
                        provider_message_id=msg["message_id"],
                        subject=msg.get("subject") or "",
                        sender=(msg.get("from") or {}).get("email") or "",
                        attachments=attachments,
                    )
                )
        return result

    def _list_messages(self, last_uid: int) -> list[dict]:
        """分页收集 >= 游标的消息。契约：+list 按新→旧排序——
        遇到整页早于游标即停止翻页（>500 封存量时不会空转也不漏新消息）。"""
        collected: list[dict] = []
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            time.sleep(REQUEST_INTERVAL)  # 每次请求前限流（分钟窗口）
            args = ["message", "+list", "--limit", str(LIST_LIMIT)]
            if cursor:
                args += ["--cursor", cursor]
            payload = run_cli(self.mailbox, args)
            page = (payload.get("data") or {}).get("data") or []
            reached_old = False
            for m in page:
                if _to_unix(m["created_at"]) >= last_uid:
                    collected.append(m)
                else:
                    reached_old = True
            pagination = (payload.get("data") or {}).get("pagination") or {}
            if reached_old or not pagination.get("has_more") or not pagination.get("next_cursor"):
                break
            cursor = pagination["next_cursor"]
        else:
            logger.warning("agently 分页超过 %s 页，截断", MAX_PAGES)
        return collected

    def _subject_matches_keywords(self, subject: str) -> bool:
        keywords = [k.strip() for k in (self.mailbox.keywords or "").split(",") if k.strip()]
        if not keywords:
            return True
        return any(k in subject for k in keywords)

    def _download_attachments(self, msg: dict, tmp: Path) -> list[RawAttachment]:
        time.sleep(REQUEST_INTERVAL)  # 每次请求前限流
        payload = run_cli(self.mailbox, ["message", "+read", "--id", msg["message_id"]])
        attachments: list[RawAttachment] = []
        for att in (payload.get("data") or {}).get("attachments") or []:
            if att.get("attachment_id"):
                time.sleep(REQUEST_INTERVAL)  # 每次请求前限流
                # 契约（实测）：--output 必须为相对路径目录，CLI 以自身 cwd 解析；
                # 故子进程 cwd 指向临时目录并传相对路径 "."。saved_to 返回绝对路径。
                dl = run_cli(
                    self.mailbox,
                    ["attachment", "+download", "--msg", msg["message_id"],
                     "--att", att["attachment_id"], "--output", "."],
                    cwd=str(tmp),
                )
                saved = dl["data"]["saved_to"]
                p = Path(saved)
                if not p.is_absolute():  # 防御：契约返回绝对路径，若未来变相对则按 cwd 归位
                    p = tmp / p
                content = p.read_bytes()
            elif att.get("download_url"):
                content = self._download_url(att["download_url"])
            else:
                logger.warning("附件无 attachment_id/download_url，跳过: %s", att.get("filename"))
                continue
            attachments.append(
                RawAttachment(
                    filename=att.get("filename") or "attachment.bin",
                    content_type=att.get("content_type") or "application/octet-stream",
                    data=content,
                )
            )
        return attachments

    def _download_url(self, url: str) -> bytes:
        if not url.startswith("https://"):
            raise AgentlyCliError(f"download_url 非 https，拒绝: {url[:50]}")
        _throttle_hour()  # download_url 不经过 run_cli，单独计入小时限流
        time.sleep(REQUEST_INTERVAL)  # 每次请求前限流（分钟窗口）
        headers = {}
        if self.mailbox.agently_token_encrypted:
            headers["Authorization"] = f"Bearer {decrypt_secret(self.mailbox.agently_token_encrypted)}"
        resp = httpx.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.content

    def mark_seen(self, uids: list[int]) -> None:
        pass  # Agently 无已读标记；防重靠 (rfc_message_id, file_url) 唯一索引
