"""Bound public chat input before expensive parsing or model processing."""

from __future__ import annotations

import json
from typing import Any


def normalize_history(history: Any) -> list[dict[str, str]]:
    """Keep eight recent user/assistant messages, each at most 1,200 characters.

    Only plain strings and up to eight flat Gradio text blocks are accepted.
    Files, components, arbitrary objects, nested content and system messages are
    ignored. No recursive traversal, object coercion or full-input text cleanup
    is performed, including when this function receives a very large string.
    """
    if not isinstance(history, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in history[-8:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role not in ("user", "assistant"):
            continue
        content = item.get("content")
        if isinstance(content, str):
            text = content[:1200].strip()
        elif isinstance(content, list):
            parts: list[str] = []
            remaining = 1200
            for block in content[:8]:
                if isinstance(block, str):
                    block_text = block
                elif isinstance(block, dict) and block.get("type") == "text":
                    block_text = block.get("text")
                else:
                    continue
                if not isinstance(block_text, str):
                    continue
                separator_size = 1 if parts else 0
                if remaining <= separator_size:
                    break
                piece = block_text[: remaining - separator_size].strip()
                if piece:
                    parts.append(piece)
                    remaining -= len(piece) + separator_size
            text = "\n".join(parts)
        else:
            continue
        if text:
            normalized.append({"role": role, "content": text})
    return normalized


class RequestSizeLimit:
    """Reject HTTP POST/PUT/PATCH bodies exceeding max_bytes (default 64 KiB).

    This pure ASGI middleware checks bytes actually received, even for chunked
    transfers or inaccurate Content-Length headers. It buffers at most one
    bounded request body before calling the app, so downstream code cannot start
    an expensive operation or response before an oversized body is rejected.
    GET/SSE, WebSocket and lifespan traffic pass through without buffering.
    """

    def __init__(self, app: Any, max_bytes: int = 65_536) -> None:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        self.app = app
        self.max_bytes = max_bytes

    @staticmethod
    async def _reject(send: Any) -> None:
        body = json.dumps(
            {"detail": "요청 내용이 너무 큽니다. 대화 내용을 줄인 뒤 다시 시도해 주세요."},
            ensure_ascii=False,
        ).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("method", "").upper() not in {
            "POST", "PUT", "PATCH"
        }:
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    declared_length = int(value)
                except (ValueError, TypeError):
                    continue
                if declared_length > self.max_bytes:
                    await self._reject(send)
                    return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.max_bytes:
                await self._reject(send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_receive() -> dict[str, Any]:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)
