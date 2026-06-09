#!/usr/bin/env python3
"""Lightweight lore.kernel.org MCP server.

This avoids lore HTML/Anubis pages and reads public-inbox thread mbox
endpoints, e.g. https://lore.kernel.org/lkml/<message-id>/t.mbox.gz.
"""

from __future__ import annotations

import zlib
import hashlib
import json
import mailbox
import os
import re
from dataclasses import asdict, dataclass
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import requests
from mcp.server.fastmcp import FastMCP


DEFAULT_LIST = "lkml"
DEFAULT_TIMEOUT = 30
MAX_COMPRESSED_BYTES = int(os.environ.get("LORE_LITE_MAX_COMPRESSED_BYTES", 50 * 1024 * 1024))
MAX_DECOMPRESSED_BYTES = int(os.environ.get("LORE_LITE_MAX_DECOMPRESSED_BYTES", 200 * 1024 * 1024))
MAX_MESSAGES = int(os.environ.get("LORE_LITE_MAX_MESSAGES", 500))
USER_AGENT = "lore-lite-mcp/0.1 (+public-inbox mbox fetch)"
CACHE_DIR = Path(os.environ.get("LORE_LITE_CACHE_DIR", Path.home() / ".cache" / "lore-lite-mcp"))

mcp = FastMCP(
    "lore-lite",
    instructions=(
        "Fetch and summarize lore.kernel.org threads through public-inbox "
        "t.mbox.gz endpoints instead of HTML/raw pages."
    ),
)


@dataclass
class MessageSummary:
    index: int
    message_id: str | None
    in_reply_to: str | None
    references: str | None
    subject: str | None
    from_: str | None
    date: str | None
    to: str | None
    cc: str | None
    body_lines: int
    diff_files: list[str]
    trailers: list[str]
    body_preview: str


def _normalize_message_id(value: str) -> str:
    value = value.strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1]
    return value


def parse_lore_ref(ref: str, default_list: str = DEFAULT_LIST) -> dict[str, str]:
    """Parse a lore URL or message-id into list name and message-id."""
    ref = ref.strip()
    if not ref:
        raise ValueError("empty lore reference")

    if ref.startswith("http://") or ref.startswith("https://"):
        parsed = urlparse(ref)
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            raise ValueError(f"cannot find list/message-id in URL: {ref}")
        list_name = parts[0]
        message_id = _normalize_message_id(parts[1])
        return {"list": list_name, "message_id": message_id}

    return {"list": default_list, "message_id": _normalize_message_id(ref)}


def _thread_url(list_name: str, message_id: str) -> str:
    return f"https://lore.kernel.org/{list_name}/{message_id}/t.mbox.gz"


def _cache_path(list_name: str, message_id: str) -> Path:
    key = hashlib.sha256(f"{list_name}/{message_id}".encode()).hexdigest()[:16]
    safe_list = re.sub(r"[^A-Za-z0-9_.-]", "_", list_name)
    return CACHE_DIR / f"{safe_list}-{key}.mbox"


def _download_thread_mbox(url: str, cache_path: Path) -> int:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=DEFAULT_TIMEOUT, stream=True)
    response.raise_for_status()

    content_length = response.headers.get("Content-Length")
    if content_length and int(content_length) > MAX_COMPRESSED_BYTES:
        raise ValueError(f"compressed mbox exceeds limit: {content_length} > {MAX_COMPRESSED_BYTES}")

    compressed_bytes = 0
    decompressed_bytes = 0
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    tmp_path = cache_path.with_name(f".{cache_path.name}.{os.getpid()}.tmp")

    try:
        with tmp_path.open("wb") as tmp_file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                compressed_bytes += len(chunk)
                if compressed_bytes > MAX_COMPRESSED_BYTES:
                    raise ValueError(f"compressed mbox exceeds limit: {compressed_bytes} > {MAX_COMPRESSED_BYTES}")

                data = decompressor.decompress(chunk, MAX_DECOMPRESSED_BYTES - decompressed_bytes + 1)
                decompressed_bytes += len(data)
                if decompressed_bytes > MAX_DECOMPRESSED_BYTES:
                    raise ValueError(f"decompressed mbox exceeds limit: {decompressed_bytes} > {MAX_DECOMPRESSED_BYTES}")
                tmp_file.write(data)

            data = decompressor.flush()
            decompressed_bytes += len(data)
            if decompressed_bytes > MAX_DECOMPRESSED_BYTES:
                raise ValueError(f"decompressed mbox exceeds limit: {decompressed_bytes} > {MAX_DECOMPRESSED_BYTES}")
            tmp_file.write(data)

        os.replace(tmp_path, cache_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return decompressed_bytes


def fetch_thread_mbox(ref: str, *, default_list: str = DEFAULT_LIST, refresh: bool = False) -> dict[str, Any]:
    parsed = parse_lore_ref(ref, default_list)
    list_name = parsed["list"]
    message_id = parsed["message_id"]
    cache_path = _cache_path(list_name, message_id)

    if cache_path.exists() and not refresh:
        return {
            "list": list_name,
            "message_id": message_id,
            "url": _thread_url(list_name, message_id),
            "path": str(cache_path),
            "bytes": cache_path.stat().st_size,
            "cached": True,
        }

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    url = _thread_url(list_name, message_id)
    bytes_written = _download_thread_mbox(url, cache_path)
    return {
        "list": list_name,
        "message_id": message_id,
        "url": url,
        "path": str(cache_path),
        "bytes": bytes_written,
        "cached": False,
    }


def _message_body(msg: Message) -> str:
    if msg.is_multipart():
        texts: list[str] = []
        for part in msg.walk():
            if part.get_content_type() != "text/plain":
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            texts.append(payload.decode(charset, "replace"))
        return "\n".join(texts)

    payload = msg.get_payload(decode=True)
    if payload is None:
        raw = msg.get_payload()
        return raw if isinstance(raw, str) else ""
    return payload.decode(msg.get_content_charset() or "utf-8", "replace")


def _header(msg: Message, name: str) -> str | None:
    value = msg.get(name)
    if value is None:
        return None
    try:
        decoded = str(make_header(decode_header(value)))
    except Exception:
        decoded = str(value)
    return " ".join(decoded.split())


def _diff_files(body: str) -> list[str]:
    files: list[str] = []
    seen: set[str] = set()
    for line in body.splitlines():
        match = re.match(r"diff --git a/(.*?) b/(.*)", line)
        if match:
            path = match.group(2)
            if path not in seen:
                seen.add(path)
                files.append(path)
    return files


def _trailers(body: str) -> list[str]:
    trailers: list[str] = []
    trailer_re = re.compile(r"^(Signed-off-by|Reviewed-by|Acked-by|Tested-by|Reported-by|Suggested-by|Fixes):\s+.+")
    for line in body.splitlines():
        if trailer_re.match(line):
            trailers.append(line)
    return trailers


def _body_preview(body: str, max_lines: int = 20) -> str:
    lines = []
    for line in body.splitlines():
        if line.startswith("diff --git "):
            break
        lines.append(line)
        if len(lines) >= max_lines:
            break
    return "\n".join(lines).strip()


def summarize_mbox(path: str) -> dict[str, Any]:
    mbox = mailbox.mbox(path)
    if len(mbox) > MAX_MESSAGES:
        raise ValueError(f"thread has too many messages: {len(mbox)} > {MAX_MESSAGES}")
    messages: list[MessageSummary] = []

    for index, msg in enumerate(mbox, 1):
        body = _message_body(msg)
        date = _header(msg, "Date")
        normalized_date = date
        if date:
            try:
                normalized_date = parsedate_to_datetime(date).isoformat()
            except Exception:
                normalized_date = date
        messages.append(
            MessageSummary(
                index=index,
                message_id=_header(msg, "Message-Id"),
                in_reply_to=_header(msg, "In-Reply-To"),
                references=_header(msg, "References"),
                subject=_header(msg, "Subject"),
                from_=_header(msg, "From"),
                date=normalized_date,
                to=_header(msg, "To"),
                cc=_header(msg, "Cc"),
                body_lines=len(body.splitlines()),
                diff_files=_diff_files(body),
                trailers=_trailers(body),
                body_preview=_body_preview(body),
            )
        )

    return {
        "path": path,
        "message_count": len(messages),
        "subjects": sorted({m.subject for m in messages if m.subject}),
        "participants": sorted({m.from_ for m in messages if m.from_}),
        "diff_files": sorted({path for m in messages for path in m.diff_files}),
        "messages": [asdict(m) for m in messages],
    }


@mcp.tool()
def lore_fetch_thread(ref: str, default_list: str = DEFAULT_LIST, refresh: bool = False) -> str:
    """Fetch a lore thread mbox via t.mbox.gz and cache it locally.

    Args:
        ref: A lore URL or message-id.
        default_list: List name to use when ref is only a message-id.
        refresh: Re-download even if cached.
    """
    return json.dumps(fetch_thread_mbox(ref, default_list=default_list, refresh=refresh), ensure_ascii=False, indent=2)


@mcp.tool()
def lore_summarize_thread(ref: str, default_list: str = DEFAULT_LIST, refresh: bool = False) -> str:
    """Fetch and summarize a lore thread's messages, patch files, and participants."""
    fetched = fetch_thread_mbox(ref, default_list=default_list, refresh=refresh)
    summary = summarize_mbox(fetched["path"])
    summary.update({k: fetched[k] for k in ["list", "message_id", "url", "cached", "bytes"]})
    return json.dumps(summary, ensure_ascii=False, indent=2)


@mcp.tool()
def lore_extract_message(ref: str, message_index: int = 1, default_list: str = DEFAULT_LIST, refresh: bool = False) -> str:
    """Return the full plain-text body and headers for one message in a lore thread."""
    fetched = fetch_thread_mbox(ref, default_list=default_list, refresh=refresh)
    mbox = mailbox.mbox(fetched["path"])
    if message_index < 1 or message_index > len(mbox):
        raise ValueError(f"message_index must be between 1 and {len(mbox)}")
    msg = mbox[message_index - 1]
    data = {
        "list": fetched["list"],
        "message_id": fetched["message_id"],
        "url": fetched["url"],
        "index": message_index,
        "headers": {name: _header(msg, name) for name in ["From", "Date", "Subject", "Message-Id", "In-Reply-To", "References", "To", "Cc"]},
        "body": _message_body(msg),
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def main():
    """Entry point for `lore-kernel-mcp` CLI command."""
    mcp.run()


if __name__ == "__main__":
    main()
