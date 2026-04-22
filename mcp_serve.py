"""
Hermes MCP Server：暴露消息会话与本地学习资产。

该模块会启动一个基于 stdio 的 MCP 服务端，让任意 MCP 客户端
（Claude Code、Cursor、Codex、Trae 等）通过两类受限能力与 Hermes 交互：

1. 面向会话、消息、事件与审批的消息桥接工具。
2. 面向内置记忆、会话回忆与当前 profile 本地技能的确定性本地学习工具。

消息能力面与 OpenClaw 的 9 个 MCP 通道桥接工具保持一致：
  conversations_list, conversation_get, messages_read, attachments_fetch,
  events_poll, events_wait, messages_send, permissions_list_open,
  permissions_respond

额外提供：
  channels_list
  memory_read, memory_write, session_recall_search
  skills_list, skill_view_safe, skill_create_or_patch
  autopilot, deep_interview, ralph, ralplan
  task_context_bundle, init
  plan_skill_read

用法：
    hermes mcp serve
    hermes mcp serve --verbose

MCP 客户端配置示例（如 claude_desktop_config.json）：
    {
        "mcpServers": {
            "hermes": {
                "command": "hermes",
                "args": ["mcp", "serve"]
            }
        }
    }
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

logger = logging.getLogger("hermes.mcp_serve")

MCP_SERVER_INSTRUCTIONS = (
    "Hermes Agent 的 MCP 桥接服务。可使用这些工具跨消息平台访问会话，"
    "并读取 Hermes 的本地学习资产，例如内置记忆、确定性会话回忆和当前"
    "profile 的本地技能。还可初始化 Trae 项目规则、读取 Hermes 内置 /plan skill，"
    "以及把部分内置 workflow skill（autopilot / deep-interview / ralph / ralplan）"
    "包装成专用 MCP tools，供 Trae 等客户端直接调用。"
)

# ---------------------------------------------------------------------------
# Lazy MCP SDK import
# ---------------------------------------------------------------------------

_MCP_SERVER_AVAILABLE = False
try:
    from mcp.server.fastmcp import Context, FastMCP

    _MCP_SERVER_AVAILABLE = True
except ImportError:
    FastMCP = None  # type: ignore[assignment,misc]
    Context = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_sessions_dir() -> Path:
    """Return the sessions directory using HERMES_HOME."""
    try:
        from hermes_constants import get_hermes_home
        return get_hermes_home() / "sessions"
    except ImportError:
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "sessions"


def _get_session_db():
    """Get a SessionDB instance for reading message transcripts."""
    try:
        from hermes_state import SessionDB
        return SessionDB()
    except Exception as e:
        logger.debug("SessionDB unavailable: %s", e)
        return None


def _load_sessions_index() -> dict:
    """Load the gateway sessions.json index directly.

    Returns a dict of session_key -> entry_dict with platform routing info.
    This avoids importing the full SessionStore which needs GatewayConfig.
    """
    sessions_file = _get_sessions_dir() / "sessions.json"
    if not sessions_file.exists():
        return {}
    try:
        with open(sessions_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.debug("Failed to load sessions.json: %s", e)
        return {}


def _load_channel_directory() -> dict:
    """Load the cached channel directory for available targets."""
    try:
        from hermes_constants import get_hermes_home
        directory_file = get_hermes_home() / "channel_directory.json"
    except ImportError:
        directory_file = Path(
            os.environ.get("HERMES_HOME", Path.home() / ".hermes")
        ) / "channel_directory.json"

    if not directory_file.exists():
        return {}
    try:
        with open(directory_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.debug("Failed to load channel_directory.json: %s", e)
        return {}


MCP_SEND_MESSAGE_MAX_LENGTH = 4096
MCP_MEMORY_CONTENT_MAX_LENGTH = 5000

def _extract_message_content(msg: dict) -> str:
    """Extract text content from a message, handling multi-part content."""
    content = msg.get("content", "")
    if isinstance(content, list):
        text_parts = [
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        ]
        return "\n".join(text_parts)
    return str(content) if content else ""


def _extract_attachments(msg: dict) -> List[dict]:
    """Extract non-text attachments from a message.

    Finds: multi-part image/file content blocks, MEDIA: tags in text,
    image URLs, and file references.
    """
    attachments = []
    content = msg.get("content", "")

    # Multi-part content blocks (image_url, file, etc.)
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type", "")
            if ptype == "image_url":
                url = part.get("image_url", {}).get("url", "") if isinstance(part.get("image_url"), dict) else ""
                if url:
                    attachments.append({"type": "image", "url": url})
            elif ptype == "image":
                url = part.get("url", part.get("source", {}).get("url", ""))
                if url:
                    attachments.append({"type": "image", "url": url})
            elif ptype not in ("text",):
                # Unknown non-text content type
                attachments.append({"type": ptype, "data": part})

    # MEDIA: tags in text content
    text = _extract_message_content(msg)
    if text:
        media_pattern = re.compile(r'MEDIA:\s*(\S+)')
        for match in media_pattern.finditer(text):
            path = match.group(1)
            attachments.append({"type": "media", "path": path})

    return attachments


def _structured_error(message: str, **extra: Any) -> str:
    payload = {"success": False, "error": message}
    payload.update(extra)
    return json.dumps(payload, indent=2)


def _clamp(value: int, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _bounded_excerpt(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[:limit]


def _file_url_to_path(uri: str) -> Optional[Path]:
    raw_uri = str(uri or "")
    if os.name == "nt" and re.match(r"^[a-zA-Z]:[\\/]", raw_uri):
        return Path(raw_uri)

    parsed = urlparse(raw_uri)
    if parsed.scheme and parsed.scheme != "file":
        return None
    if parsed.scheme != "file":
        return Path(str(uri))

    path = url2pathname(unquote(parsed.path or ""))
    if parsed.netloc and parsed.netloc not in ("", "localhost"):
        if os.name == "nt":
            path = f"//{parsed.netloc}{path}"
        else:
            path = f"/{parsed.netloc}{path}"
    if not path:
        return None
    return Path(path)


def _explicit_workspace_root_to_path(workspace_root: str) -> Path:
    """Normalize and validate a caller-provided workspace root path."""
    value = str(workspace_root or "").strip()
    if not value:
        raise ValueError("workspace_root is required when MCP Roots are unavailable.")

    candidate = _file_url_to_path(value)
    if candidate is None:
        raise ValueError("workspace_root must be a local filesystem path or file:// URI.")

    candidate = candidate.expanduser()
    if candidate.exists() and candidate.is_file():
        candidate = candidate.parent
    if not candidate.exists() or not candidate.is_dir():
        raise ValueError(f"workspace_root must be an existing directory: {workspace_root}")
    return candidate


async def _resolve_workspace_root(
    ctx: Optional["Context"] = None,
    *,
    require_client_root: bool = False,
) -> Path:
    """Resolve the caller project root for workspace-scoped artifacts.

    Resolution order:
    1. MCP client-advertised roots.
    2. Server process cwd, unless ``require_client_root`` is true.
    """
    fallback = Path.cwd()
    if ctx is None:
        if require_client_root:
            raise ValueError("MCP Roots are required when the client does not provide workspace roots.")
        return fallback

    session = getattr(ctx, "session", None)
    list_roots = getattr(session, "list_roots", None)
    if list_roots is None:
        if require_client_root:
            raise ValueError("MCP Roots are required when the client session does not expose workspace roots.")
        return fallback

    try:
        roots_result = await list_roots()
    except Exception as exc:
        if require_client_root:
            raise ValueError("MCP Roots are required when workspace roots cannot be resolved from the client.") from exc
        logger.debug("Failed to list client roots, falling back to cwd: %s", exc)
        return fallback

    candidates: list[Path] = []
    for root in getattr(roots_result, "roots", []) or []:
        candidate = _file_url_to_path(str(getattr(root, "uri", "") or ""))
        if not candidate:
            continue
        if candidate.exists() and candidate.is_file():
            candidate = candidate.parent
        candidates.append(candidate)

    if candidates:
        if require_client_root and len(candidates) > 1:
            raise ValueError(
                "Exactly one MCP Root is required for project artifact writes; "
                f"the client advertised {len(candidates)} roots."
            )
        return candidates[0]

    if require_client_root:
        raise ValueError("MCP Roots are required when the client does not advertise any workspace roots.")
    return fallback


def _deterministic_session_recall_search(
    db: Any,
    *,
    query: str,
    limit: int = 5,
) -> dict:
    """Return deterministic session recall hits without invoking any LLM path."""
    if db is None:
        return {"success": False, "error": "Session database unavailable"}

    query = (query or "").strip()
    if not query:
        return {"success": False, "error": "query is required"}

    limit = _clamp(limit, default=5, minimum=1, maximum=10)

    try:
        raw_results = db.search_messages(query=query, limit=limit, offset=0)
    except TypeError:
        # Some test doubles may not accept offset/source_filter kwargs beyond query/limit.
        raw_results = db.search_messages(query=query, limit=limit)
    except Exception as e:
        return {"success": False, "error": f"Failed to search session recall: {e}"}

    results = []
    for row in raw_results[:limit]:
        context_before = None
        context_after = None
        context = row.get("context") or []
        if isinstance(context, list) and context:
            before = []
            after = []
            seen_current = False
            for item in context:
                role = item.get("role", "")
                content = _bounded_excerpt(item.get("content", ""), 150)
                if role == row.get("role") and not seen_current:
                    seen_current = True
                    continue
                if not seen_current:
                    before.append(content)
                else:
                    after.append(content)
            if before:
                context_before = "\n".join(before)[:150]
            if after:
                context_after = "\n".join(after)[:150]

        entry = {
            "session_id": row.get("session_id", ""),
            "source": row.get("source", ""),
            "timestamp": row.get("timestamp"),
            "message_id": row.get("id"),
            "snippet": _bounded_excerpt(row.get("snippet", ""), 300),
        }
        if context_before:
            entry["context_before"] = context_before
        if context_after:
            entry["context_after"] = context_after
        results.append(entry)

    return {"success": True, "query": query, "results": results, "count": len(results)}


# ---------------------------------------------------------------------------
# Event Bridge — polls SessionDB for new messages, maintains event queue
# ---------------------------------------------------------------------------

QUEUE_LIMIT = 1000
POLL_INTERVAL = 0.2  # seconds between DB polls (200ms)
APPROVAL_EXPIRY_SECONDS = 300  # 5 minutes


@dataclass
class QueueEvent:
    """An event in the bridge's in-memory queue."""
    cursor: int
    type: str  # "message", "approval_requested", "approval_resolved"
    session_key: str = ""
    data: dict = field(default_factory=dict)


class EventBridge:
    """Background poller that watches SessionDB for new messages and
    maintains an in-memory event queue with waiter support.

    This is the Hermes equivalent of OpenClaw's WebSocket gateway bridge.
    Instead of WebSocket events, we poll the SQLite database for changes.
    """

    def __init__(self):
        self._queue: List[QueueEvent] = []
        self._cursor = 0
        self._lock = threading.Lock()
        self._new_event = threading.Event()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_poll_timestamps: Dict[str, float] = {}  # session_key -> unix timestamp
        # In-memory approval tracking (populated from events)
        self._pending_approvals: Dict[str, dict] = {}
        # mtime cache — skip expensive work when files haven't changed
        self._sessions_json_mtime: float = 0.0
        self._state_db_mtime: float = 0.0
        self._cached_sessions_index: dict = {}

    def start(self):
        """Start the background polling thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.debug("EventBridge started")

    def stop(self):
        """Stop the background polling thread."""
        self._running = False
        self._new_event.set()  # Wake any waiters
        if self._thread:
            self._thread.join(timeout=5)
        logger.debug("EventBridge stopped")

    def poll_events(
        self,
        after_cursor: int = 0,
        session_key: Optional[str] = None,
        limit: int = 20,
    ) -> dict:
        """Return events since after_cursor, optionally filtered by session_key."""
        with self._lock:
            events = [
                e for e in self._queue
                if e.cursor > after_cursor
                and (not session_key or e.session_key == session_key)
            ][:limit]

        next_cursor = events[-1].cursor if events else after_cursor
        return {
            "events": [
                {"cursor": e.cursor, "type": e.type,
                 "session_key": e.session_key, **e.data}
                for e in events
            ],
            "next_cursor": next_cursor,
        }

    def wait_for_event(
        self,
        after_cursor: int = 0,
        session_key: Optional[str] = None,
        timeout_ms: int = 30000,
    ) -> Optional[dict]:
        """Block until a matching event arrives or timeout expires."""
        deadline = time.monotonic() + (timeout_ms / 1000.0)

        while time.monotonic() < deadline:
            with self._lock:
                for e in self._queue:
                    if e.cursor > after_cursor and (
                        not session_key or e.session_key == session_key
                    ):
                        return {
                            "cursor": e.cursor, "type": e.type,
                            "session_key": e.session_key, **e.data,
                        }

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._new_event.clear()
            self._new_event.wait(timeout=min(remaining, POLL_INTERVAL))

        return None

    def _expire_old_approvals(self) -> None:
        """Remove approvals that have exceeded the expiry timeout."""
        cutoff = time.monotonic() - APPROVAL_EXPIRY_SECONDS
        expired_keys = [
            key for key, approval in self._pending_approvals.items()
            if approval.get("_monotonic_created", cutoff) < cutoff
        ]
        for key in expired_keys:
            del self._pending_approvals[key]
        if expired_keys:
            logger.debug("Expired %d approval(s)", len(expired_keys))

    def list_pending_approvals(self) -> List[dict]:
        """List approval requests observed during this bridge session."""
        with self._lock:
            self._expire_old_approvals()
            return sorted(
                self._pending_approvals.values(),
                key=lambda a: a.get("created_at", ""),
            )

    def respond_to_approval(self, approval_id: str, decision: str) -> dict:
        """Resolve a pending approval (best-effort without gateway IPC)."""
        with self._lock:
            self._expire_old_approvals()
            approval = self._pending_approvals.pop(approval_id, None)

        if not approval:
            return _structured_error(f"Approval not found: {approval_id}")

        self._enqueue(QueueEvent(
            cursor=0,  # Will be set by _enqueue
            type="approval_resolved",
            session_key=approval.get("session_key", ""),
            data={"approval_id": approval_id, "decision": decision},
        ))

        return {"resolved": True, "approval_id": approval_id, "decision": decision}

    def _enqueue(self, event: QueueEvent) -> None:
        """Add an event to the queue and wake any waiters."""
        with self._lock:
            self._cursor += 1
            event.cursor = self._cursor
            self._queue.append(event)
            # Trim queue to limit
            while len(self._queue) > QUEUE_LIMIT:
                self._queue.pop(0)

            # Track approval_requested events for permissions_list_open
            if event.type == "approval_requested":
                approval_id = event.data.get("id", f"auto_{event.cursor}")
                approval_data = {**event.data, "id": approval_id}
                approval_data["_monotonic_created"] = time.monotonic()
                self._pending_approvals[approval_id] = approval_data

        self._new_event.set()

    def _poll_loop(self):
        """Background loop: poll SessionDB for new messages."""
        db = _get_session_db()
        if not db:
            logger.warning("EventBridge: SessionDB unavailable, event polling disabled")
            return

        while self._running:
            try:
                self._poll_once(db)
            except Exception as e:
                logger.debug("EventBridge poll error: %s", e)
            time.sleep(POLL_INTERVAL)

    def _poll_once(self, db):
        """Check for new messages across all sessions.

        Uses mtime checks on sessions.json and state.db to skip work
        when nothing has changed — makes 200ms polling essentially free.
        """
        # Check if sessions.json has changed (mtime check is ~1μs)
        sessions_file = _get_sessions_dir() / "sessions.json"
        try:
            sj_mtime = sessions_file.stat().st_mtime if sessions_file.exists() else 0.0
        except OSError:
            sj_mtime = 0.0

        if sj_mtime != self._sessions_json_mtime:
            self._sessions_json_mtime = sj_mtime
            self._cached_sessions_index = _load_sessions_index()

        # Check if state.db has changed
        try:
            from hermes_constants import get_hermes_home
            db_file = get_hermes_home() / "state.db"
        except ImportError:
            db_file = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "state.db"

        try:
            db_mtime = db_file.stat().st_mtime if db_file.exists() else 0.0
        except OSError:
            db_mtime = 0.0

        if db_mtime == self._state_db_mtime and sj_mtime == self._sessions_json_mtime:
            return  # Nothing changed since last poll — skip entirely

        self._state_db_mtime = db_mtime
        entries = self._cached_sessions_index

        for session_key, entry in entries.items():
            session_id = entry.get("session_id", "")
            if not session_id:
                continue

            last_seen = self._last_poll_timestamps.get(session_key, 0.0)

            try:
                messages = db.get_messages(session_id)
            except Exception:
                continue

            if not messages:
                continue

            # Normalize timestamps to float for comparison
            def _ts_float(ts) -> float:
                if isinstance(ts, (int, float)):
                    return float(ts)
                if isinstance(ts, str) and ts:
                    try:
                        return float(ts)
                    except ValueError:
                        # ISO string — parse to epoch
                        try:
                            from datetime import datetime
                            return datetime.fromisoformat(ts).timestamp()
                        except Exception:
                            return 0.0
                return 0.0

            # Find messages newer than our last seen timestamp
            new_messages = []
            for msg in messages:
                ts = _ts_float(msg.get("timestamp", 0))
                role = msg.get("role", "")
                if role not in ("user", "assistant"):
                    continue
                if ts > last_seen:
                    new_messages.append(msg)

            for msg in new_messages:
                content = _extract_message_content(msg)
                if not content:
                    continue
                self._enqueue(QueueEvent(
                    cursor=0,
                    type="message",
                    session_key=session_key,
                    data={
                        "role": msg.get("role", ""),
                        "content": content[:500],
                        "timestamp": str(msg.get("timestamp", "")),
                        "message_id": str(msg.get("id", "")),
                    },
                ))

            # Update last seen to the most recent message timestamp
            all_ts = [_ts_float(m.get("timestamp", 0)) for m in messages]
            if all_ts:
                latest = max(all_ts)
                if latest > last_seen:
                    self._last_poll_timestamps[session_key] = latest


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

def create_mcp_server(event_bridge: Optional[EventBridge] = None) -> "FastMCP":
    """Create and return the Hermes MCP server with all tools registered."""
    if not _MCP_SERVER_AVAILABLE:
        raise ImportError(
            "MCP 服务端依赖 'mcp' 包。"
            f"可使用以下命令安装：{sys.executable} -m pip install 'mcp'"
        )

    mcp = FastMCP(
        "hermes",
        instructions=MCP_SERVER_INSTRUCTIONS,
    )

    bridge = event_bridge or EventBridge()

    # -- memory_read -------------------------------------------------------

    @mcp.tool()
    def memory_read() -> str:
        """读取当前 profile 下实时生效的内置 MEMORY.md 与 USER.md 条目。"""
        try:
            from tools.memory_tool import read_live_memory_state

            return json.dumps(read_live_memory_state(), indent=2, ensure_ascii=False)
        except Exception as e:
            return _structured_error(f"Failed to read live memory: {e}")

    # -- memory_write ------------------------------------------------------

    @mcp.tool()
    def memory_write(
        action: str,
        target: str,
        content: str,
        old_text: Optional[str] = None,
    ) -> str:
        """通过收敛后的 MCP v1 协议写入持久化内置记忆。

        支持的动作：add、replace。remove 被有意排除。
        """
        if action not in ("add", "replace"):
            return _structured_error(
                f"Unsupported action '{action}' for memory_write. Use add or replace."
            )

        if not target:
            return _structured_error("target is required and cannot be empty")

        if not content:
            return _structured_error("content is required and cannot be empty")

        if len(content) > MCP_MEMORY_CONTENT_MAX_LENGTH:
            return _structured_error(
                f"content exceeds {MCP_MEMORY_CONTENT_MAX_LENGTH} character limit "
                f"({len(content)} characters)"
            )

        if action == "replace" and not old_text:
            return _structured_error(
                "old_text is required when action is 'replace'"
            )

        try:
            from tools.knowledge_quality import (
                blocked_result,
                evaluate_memory_write,
                record_quality_metadata,
                should_allow_write,
            )
            from tools.memory_tool import memory_write_v1, read_live_memory_state

            memory_state = read_live_memory_state()
            existing_entries = memory_state.get(target, [])
            quality_gate = evaluate_memory_write(
                action=action,
                target=target,
                content=content,
                old_text=old_text,
                existing_entries=existing_entries,
            )
            if not should_allow_write(quality_gate):
                return json.dumps(
                    blocked_result(quality_gate),
                    indent=2,
                    ensure_ascii=False,
                )

            result = memory_write_v1(
                action=action,
                target=target,
                content=content,
                old_text=old_text,
            )
            result["quality_gate"] = quality_gate
            if result.get("success"):
                record_quality_metadata(
                    "memory",
                    {"target": target, "action": action},
                    content,
                    quality_gate,
                )
            return json.dumps(result, indent=2, ensure_ascii=False)
        except Exception as e:
            return _structured_error(f"Failed to write memory: {e}")

    # -- session_recall_search --------------------------------------------

    @mcp.tool()
    def session_recall_search(
        query: str,
        limit: int = 5,
    ) -> str:
        """在不依赖任何 LLM 摘要的前提下，确定性搜索历史会话消息。"""
        db = _get_session_db()
        return json.dumps(
            _deterministic_session_recall_search(db, query=query, limit=limit),
            indent=2,
            ensure_ascii=False,
        )

    # -- skills_list -------------------------------------------------------

    @mcp.tool()
    def skills_list(
        query: Optional[str] = None,
        limit: int = 5,
    ) -> str:
        """仅列出当前 profile 本地技能的元数据。"""
        try:
            from tools.skills_tool import local_skills_list

            result = local_skills_list(query=query, limit=limit)
            return json.dumps(result, indent=2, ensure_ascii=False)
        except Exception as e:
            return _structured_error(f"Failed to list local skills: {e}")

    # -- skill_view_safe ---------------------------------------------------

    @mcp.tool()
    def skill_view_safe(
        name: str,
        file_path: Optional[str] = None,
    ) -> str:
        """读取当前 profile 的本地技能，不触发插件/外部查找或初始化副作用。"""
        try:
            from tools.skills_tool import local_skill_view_safe

            result = local_skill_view_safe(name=name, file_path=file_path)
            return json.dumps(result, indent=2, ensure_ascii=False)
        except Exception as e:
            return _structured_error(f"Failed to read local skill safely: {e}")

    # -- skill_create_or_patch --------------------------------------------

    @mcp.tool()
    def skill_create_or_patch(
        action: str,
        name: str,
        content: Optional[str] = None,
        category: Optional[str] = None,
        old_string: Optional[str] = None,
        new_string: Optional[str] = None,
        replace_all: bool = False,
    ) -> str:
        """创建新技能，或仅对 SKILL.md 内容进行补丁修改。"""
        if action not in {"create", "patch"}:
            try:
                from tools.skill_manager_tool import skill_create_or_patch_v1

                return skill_create_or_patch_v1(
                    action=action,
                    name=name,
                    content=content,
                    category=category,
                    old_string=old_string,
                    new_string=new_string,
                    replace_all=replace_all,
                )
            except Exception as e:
                return _structured_error(f"Failed to create or patch local skill: {e}")

        try:
            from tools.knowledge_quality import (
                blocked_result,
                evaluate_skill_change,
                record_quality_metadata,
                should_allow_write,
            )
            from tools.fuzzy_match import fuzzy_find_and_replace
            from tools.skills_tool import local_skill_view_safe
            from tools.skill_manager_tool import skill_create_or_patch_v1

            gate_action = action
            gate_content = content
            if action == "patch":
                viewed = local_skill_view_safe(name=name)
                existing_content = viewed.get("content") if viewed.get("success") else None
                if isinstance(existing_content, str) and old_string is not None and new_string is not None:
                    projected_content, _match_count, _strategy, match_error = fuzzy_find_and_replace(
                        existing_content,
                        old_string,
                        new_string,
                        replace_all,
                    )
                    if not match_error:
                        gate_content = projected_content
                        gate_action = "edit"

            quality_gate = evaluate_skill_change(
                action=gate_action,
                name=name,
                content=gate_content,
                old_string=old_string,
                new_string=new_string,
                file_content=None,
            )
            if not should_allow_write(quality_gate):
                return json.dumps(
                    blocked_result(quality_gate),
                    indent=2,
                    ensure_ascii=False,
                )

            raw_result = skill_create_or_patch_v1(
                action=action,
                name=name,
                content=content,
                category=category,
                old_string=old_string,
                new_string=new_string,
                replace_all=replace_all,
            )
            try:
                result = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
            except json.JSONDecodeError:
                return raw_result
            if isinstance(result, dict):
                result["quality_gate"] = quality_gate
                if result.get("success"):
                    skill_content = content if content is not None else (new_string or "")
                    viewed = local_skill_view_safe(name=name)
                    if viewed.get("success") and viewed.get("content"):
                        skill_content = viewed["content"]
                    record_quality_metadata(
                        "skill",
                        {"name": name, "action": action, "file_path": "SKILL.md"},
                        skill_content,
                        quality_gate,
                    )
                return json.dumps(result, indent=2, ensure_ascii=False)
            return raw_result
        except Exception as e:
            return _structured_error(f"Failed to create or patch local skill: {e}")

    # -- task_context_bundle ----------------------------------------------

    @mcp.tool()
    async def task_context_bundle(
        query: str,
        memory_limit: int = 5,
        session_limit: int = 5,
        skill_limit: int = 5,
        ctx: Optional["Context"] = None,
    ) -> str:
        """返回适用于 Trae 的有界任务前上下文包。

        该上下文包是对实时记忆、确定性会话回忆和本地技能元数据的便捷索引，
        不会内联完整技能正文。
        """
        query = (query or "").strip()
        if not query:
            return _structured_error("query is required")

        memory_limit = _clamp(memory_limit, default=5, minimum=1, maximum=5)
        session_limit = _clamp(session_limit, default=5, minimum=1, maximum=5)
        skill_limit = _clamp(skill_limit, default=5, minimum=1, maximum=5)

        try:
            from tools.knowledge_quality import (
                audit_due_knowledge,
                filter_memory_entries,
                filter_skill_candidates,
            )
            from tools.memory_tool import read_live_memory_state
            from tools.skills_tool import local_skills_list

            workspace_root = await _resolve_workspace_root(
                ctx,
                require_client_root=False,
            )
            memory_state = read_live_memory_state()
            quality_audit = audit_due_knowledge()
            session_result = _deterministic_session_recall_search(
                _get_session_db(),
                query=query,
                limit=session_limit,
            )
            skills_result = local_skills_list(query=query, limit=skill_limit)

            if not skills_result.get("success"):
                return json.dumps(skills_result, indent=2, ensure_ascii=False)

            session_hits = []
            session_recall_status = {
                "success": bool(session_result.get("success")),
                "available": bool(session_result.get("success")),
                "count": 0,
            }
            if session_result.get("success"):
                session_hits = session_result.get("results", [])[:session_limit]
                session_recall_status["count"] = len(session_hits)
            else:
                session_recall_status["error"] = session_result.get(
                    "error",
                    "Session recall unavailable",
                )

            skill_candidates = skills_result.get("skills", [])[:skill_limit]
            hints = [
                "可使用 skill_view_safe(name=...) 查看候选技能的完整内容。",
                "可使用 session_recall_search(query=...) 做更聚焦的后续会话回忆检索。",
                "任务完成沉淀经验可 考虑调用 memory_write(...) 或 skill_create_or_patch(...)。",
            ]

            memory_entries, memory_quality = filter_memory_entries(
                memory_state.get("memory", []),
                "memory",
                quality_audit,
            )
            user_entries, user_quality = filter_memory_entries(
                memory_state.get("user", []),
                "user",
                quality_audit,
            )
            skill_candidates, skill_quality = filter_skill_candidates(
                skill_candidates,
                quality_audit,
            )

            bundle = {
                "success": True,
                "query": query,
                "resolved_project_root": str(workspace_root),
                "memory": memory_entries[-memory_limit:],
                "user": user_entries[-memory_limit:],
                "session_hits": session_hits,
                "session_recall_status": session_recall_status,
                "skill_candidates": skill_candidates,
                "hints": hints,
                "quality_audit": quality_audit,
                "quality_filter": {
                    "memory": memory_quality,
                    "user": user_quality,
                    "skills": skill_quality,
                },
            }
            return json.dumps(bundle, indent=2, ensure_ascii=False)
        except Exception as e:
            return _structured_error(f"Failed to build task context bundle: {e}")

    # -- init --------------------------------------------------------------

    @mcp.tool()
    async def init(
        project_name: Optional[str] = None,
        overwrite: bool = False,
        workspace_root: Optional[str] = None,
        ctx: Optional["Context"] = None,
    ) -> str:
        """初始化 Trae 项目规则，强制其遵守 Hermes MCP 工作流。

        目标项目目录优先来自 MCP Roots；当客户端无法提供 Roots 时，才回退到
        显式传入的 `workspace_root`。不会生成 `.trae/mcp.json`。
        默认只写入 `.trae/rules/hermes-mcp-workflow.md`。规则会要求 Trae：
        - 复杂任务先做 Hermes 检索
        - 必要时读取 plan skill
        - 任务完成后检查 memory_write / skill_create_or_patch
        """
        try:
            from tools.trae_rules_tool import init_trae_project_config

            try:
                resolved_workspace_root = await _resolve_workspace_root(ctx, require_client_root=True)
            except Exception:
                if workspace_root is None:
                    raise
                resolved_workspace_root = _explicit_workspace_root_to_path(workspace_root)

            result = init_trae_project_config(
                project_name=project_name,
                overwrite=overwrite,
                workspace_root=resolved_workspace_root,
            )
            return json.dumps(result, indent=2, ensure_ascii=False)
        except Exception as e:
            return _structured_error(f"Failed to initialize Trae project config: {e}")

    # -- plan -------------------------------------------------------

    @mcp.tool()
    def plan_skill_read() -> str:
        """读取 Hermes MCP 内置的 Codex 风格 plan skill 原文，供宿主规划时参考。"""
        try:
            from tools.plan_tool import read_bundled_plan_skill

            result = read_bundled_plan_skill()
            return json.dumps(result, indent=2, ensure_ascii=False)
        except Exception as e:
            return _structured_error(f"Failed to read bundled plan skill: {e}")

    @mcp.tool()
    def plan(
        instruction: str,
        mode: str = "auto",
        interactive: bool = False,
        deliberate: bool = False,
        review: bool = False,
    ) -> str:
        """生成 Codex plan 工作流调用指令，用于 MCP 宿主进行规划。

        该工具是提示包包装器：它不会在 MCP 服务端内部执行规划、写计划或
        调用子代理，而是返回宿主 agent 下一轮应使用的 `invocation_message`。

        Args:
            instruction: 需要规划、审查或澄清的任务描述
            mode: `auto`、`direct`、`consensus` 或 `review`
            interactive: 是否追加交互式规划标志
            deliberate: 是否追加高风险审议标志
            review: 是否强制 review 模式（等价于 mode=`review`）
        """
        try:
            from tools.mcp_skill_wrappers import plan_invocation

            result = plan_invocation(
                instruction,
                mode=mode,
                interactive=interactive,
                deliberate=deliberate,
                review=review,
            )
            return json.dumps(result, indent=2, ensure_ascii=False)
        except Exception as e:
            return _structured_error(f"Failed to build plan skill invocation: {e}")

    @mcp.tool()
    def autopilot(
        instruction: str,
    ) -> str:
        """生成 Autopilot 工作流调用指令，用于“从想法到实现”的全自动执行。

        适合用户已经给出一个想做的产品/功能方向，希望宿主客户端进入
        Hermes 的 autopilot 流程：需求分析、技术设计、规划、实现、测试、
        验证。

        返回值不会直接在 MCP 服务端执行 workflow，而是返回：
        - `invocation_message`：应交给 MCP 宿主 agent 的下一条指令
        - `client_action`：提示客户端如何消费该结果

        Args:
            instruction: 用户的目标、产品想法或要自动完成的任务描述
        """
        try:
            from tools.mcp_skill_wrappers import autopilot_invocation

            result = autopilot_invocation(instruction)
            return json.dumps(result, indent=2, ensure_ascii=False)
        except Exception as e:
            return _structured_error(f"Failed to build autopilot skill invocation: {e}")

    @mcp.tool()
    def deep_interview(
        instruction: str,
        depth: str = "standard",
        autoresearch: bool = False,
    ) -> str:
        """生成 Deep Interview 工作流调用指令，用于先澄清需求再进入规划/执行。

        适合需求模糊、范围不清、用户强调“不要假设”时使用。该工具会把
        deep-interview skill 组装成可交给宿主 agent 的下一轮指令，并支持
        深度等级与 autoresearch 模式。

        返回值不会直接执行访谈，只返回可供宿主 agent 使用的
        `invocation_message`。

        Args:
            instruction: 需要澄清的想法、需求或任务描述
            depth: 访谈深度，支持 `quick`、`standard`、`deep`
            autoresearch: 是否追加 `--autoresearch` 模式
        """
        try:
            from tools.mcp_skill_wrappers import deep_interview_invocation

            result = deep_interview_invocation(
                instruction,
                depth=depth,
                autoresearch=autoresearch,
            )
            return json.dumps(result, indent=2, ensure_ascii=False)
        except Exception as e:
            return _structured_error(f"Failed to build deep-interview skill invocation: {e}")

    @mcp.tool()
    def ralph(
        instruction: str,
    ) -> str:
        """生成 Ralph 持续执行工作流调用指令，用于“持续做直到完成并验证”。

        适合任务已经足够明确，且宿主客户端需要进入 Hermes 的 ralph
        流程：持续推进、重试、并在结束前要求验证证据。

        返回值不会直接在 MCP 服务端实施修改，而是返回宿主 agent 下一步
        应执行的 `invocation_message`。

        Args:
            instruction: 要求 Ralph 持续推进直到完成的任务描述
        """
        try:
            from tools.mcp_skill_wrappers import ralph_invocation

            result = ralph_invocation(instruction)
            return json.dumps(result, indent=2, ensure_ascii=False)
        except Exception as e:
            return _structured_error(f"Failed to build ralph skill invocation: {e}")

    @mcp.tool()
    def ralplan(
        instruction: str,
        interactive: bool = False,
        deliberate: bool = False,
    ) -> str:
        """生成自包含的 Ralplan 共识规划工作流调用提示包。

        适合在编码前先做高质量方案设计，让宿主客户端进入 Hermes 的
        `ralplan` 流程，也就是 `$plan --consensus` 的专用包装。该工具会
        同时打包 Codex plan skill 与 Planner / Architect / Critic 角色定义，
        让 MCP 宿主不依赖本地 `.codex` 文件也能执行对应提示工作流。可选：
        - `interactive=true`：在关键节点停下来等待用户反馈
        - `deliberate=true`：启用高风险任务的深度审议模式

        返回值不会直接产出最终实现，也不会在 MCP 服务端内部运行多代理；
        它只返回宿主 agent 下一轮应使用的 `invocation_message`。

        Args:
            instruction: 需要进行共识规划的任务描述
            interactive: 是否启用交互式审议
            deliberate: 是否启用 deliberate 高风险规划模式
        """
        try:
            from tools.mcp_skill_wrappers import ralplan_invocation

            result = ralplan_invocation(
                instruction,
                interactive=interactive,
                deliberate=deliberate,
            )
            return json.dumps(result, indent=2, ensure_ascii=False)
        except Exception as e:
            return _structured_error(f"Failed to build ralplan skill invocation: {e}")

    # -- permissions_list_open ---------------------------------------------

    @mcp.tool()
    def permissions_list_open() -> str:
        """列出当前桥接会话中观察到的待处理审批请求。

        返回桥接服务启动后看到的 exec 与插件审批请求。这里只包含当前在线
        会话期间的审批，不包含桥接连接前的历史审批。
        """
        approvals = bridge.list_pending_approvals()
        return json.dumps({
            "count": len(approvals),
            "approvals": approvals,
        }, indent=2)

    # -- permissions_respond -----------------------------------------------

    @mcp.tool()
    def permissions_respond(
        id: str,
        decision: str,
    ) -> str:
        """响应待处理的审批请求。

        Args:
            id: 来自 permissions_list_open 的审批 ID
            decision: "allow-once"、"allow-always" 或 "deny"
        """
        if decision not in ("allow-once", "allow-always", "deny"):
            return _structured_error(
                f"Invalid decision: {decision}. "
                f"Must be allow-once, allow-always, or deny"
            )

        result = bridge.respond_to_approval(id, decision)
        return json.dumps(result, indent=2)

    return mcp


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_mcp_server(verbose: bool = False) -> None:
    """通过 stdio 启动 Hermes MCP 服务端。"""
    if not _MCP_SERVER_AVAILABLE:
        print(
            "错误：MCP 服务端依赖 'mcp' 包。\n"
            f"可使用以下命令安装：{sys.executable} -m pip install 'mcp'",
            file=sys.stderr,
        )
        sys.exit(1)

    if verbose:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)
    else:
        logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    bridge = EventBridge()
    bridge.start()

    server = create_mcp_server(event_bridge=bridge)

    import asyncio

    async def _run():
        try:
            await server.run_stdio_async()
        finally:
            bridge.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        bridge.stop()
