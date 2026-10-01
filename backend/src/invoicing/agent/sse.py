# backend/src/invoicing/agent/sse.py
"""Agent 专用 SSE 事件流（per-request emitter）。

帧契约：``data: {json}\\n\\n`` 裸帧（无具名 event 行——EventSource onmessage
对具名事件不触发）；溢出丢最旧（token 流宁丢旧不丢新）。
机制移植自 auditmind/core/sse.py 的 SSEChannel（只取 agent 需要的子集，
不移植 SSEHub/heartbeat——它们服务 pipeline/recon，与本场景无关）。
"""
import asyncio
import json
from typing import AsyncGenerator

EVENT_TYPES = {"token", "tool_call", "reasoning", "done", "error"}

_CHANNEL_END = object()


def format_sse_frame(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class AgentSSEEmitter:
    """单请求 SSE 事件发射器（emit 同步写队列；stream 异步消费）。"""

    def __init__(self, max_queue_size: int = 200):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._closed = False

    def emit(self, event_type: str, data: dict) -> None:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event type: {event_type}")
        if self._closed:
            return
        event = {"type": event_type, "data": data}
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            try:  # 丢最旧保最新
                self._queue.get_nowait()
                self._queue.put_nowait(event)
            except asyncio.QueueEmpty:
                pass

    def close(self) -> None:
        """结束流：入队 sentinel 让 stream 优雅退出（队满时挤掉最旧重试，sentinel 必须落位）。"""
        if self._closed:
            return
        self._closed = True
        try:
            self._queue.put_nowait(_CHANNEL_END)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(_CHANNEL_END)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    async def stream(self) -> AsyncGenerator[str, None]:
        while True:
            item = await self._queue.get()
            if item is _CHANNEL_END:
                return
            yield format_sse_frame(item)
