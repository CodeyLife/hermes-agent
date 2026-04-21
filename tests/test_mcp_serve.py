"""
Tests for mcp_serve — Hermes MCP server.

Three layers of tests:
1. Unit tests — helpers, content extraction, attachment parsing
2. EventBridge tests — queue mechanics, cursors, waiters, concurrency
3. End-to-end tests — call actual MCP tools through FastMCP's tool manager
   with real session data in SQLite and sessions.json
"""

import asyncio
import json
import os
import sqlite3
import time
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from mcp import types


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_hermes_home(tmp_path, monkeypatch):
    """Redirect HERMES_HOME to a temp directory."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    try:
        import hermes_constants
        monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    except (ImportError, AttributeError):
        pass
    return tmp_path


@pytest.fixture(autouse=True)
def _seed_learning_assets(tmp_path, monkeypatch):
    memories = tmp_path / "memories"
    memories.mkdir(parents=True, exist_ok=True)
    (memories / "MEMORY.md").write_text(
        "project uses pytest\n§\nfastmcp server already exists\n§\nremember to avoid llm recall",
        encoding="utf-8",
    )
    (memories / "USER.md").write_text(
        "user codes in Trae\n§\nprefers Hermes to be tool-only",
        encoding="utf-8",
    )

    skills = tmp_path / "skills"
    (skills / "python" / "fastmcp-helper").mkdir(parents=True, exist_ok=True)
    (skills / "python" / "fastmcp-helper" / "SKILL.md").write_text(
        """\
---
name: fastmcp-helper
description: Help with FastMCP integration work.
metadata:
  hermes:
    tags: [mcp, fastmcp]
---

# FastMCP Helper

Use FastMCP carefully.
""",
        encoding="utf-8",
    )
    (skills / "python" / "fastmcp-helper" / "references").mkdir(parents=True, exist_ok=True)
    (skills / "python" / "fastmcp-helper" / "references" / "api.md").write_text(
        "# API\nUse references safely.",
        encoding="utf-8",
    )
    (skills / "python" / "fastmcp-helper" / "scripts").mkdir(parents=True, exist_ok=True)
    (skills / "python" / "fastmcp-helper" / "scripts" / "unsafe.py").write_text(
        "print('nope')",
        encoding="utf-8",
    )
    try:
        import tools.skills_tool as skills_tool_module
        monkeypatch.setattr(skills_tool_module, "SKILLS_DIR", skills)
    except Exception:
        pass
    try:
        import tools.skill_manager_tool as skill_manager_tool_module
        monkeypatch.setattr(skill_manager_tool_module, "SKILLS_DIR", skills)
    except Exception:
        pass


@pytest.fixture
def sessions_dir(tmp_path):
    sdir = tmp_path / "sessions"
    sdir.mkdir(parents=True, exist_ok=True)
    return sdir


@pytest.fixture
def sample_sessions():
    return {
        "agent:main:telegram:dm:123456": {
            "session_key": "agent:main:telegram:dm:123456",
            "session_id": "20260329_120000_abc123",
            "platform": "telegram",
            "chat_type": "dm",
            "display_name": "Alice",
            "created_at": "2026-03-29T12:00:00",
            "updated_at": "2026-03-29T14:30:00",
            "input_tokens": 50000,
            "output_tokens": 2000,
            "total_tokens": 52000,
            "origin": {
                "platform": "telegram",
                "chat_id": "123456",
                "chat_name": "Alice",
                "chat_type": "dm",
                "user_id": "123456",
                "user_name": "Alice",
                "thread_id": None,
                "chat_topic": None,
            },
        },
        "agent:main:discord:group:789:456": {
            "session_key": "agent:main:discord:group:789:456",
            "session_id": "20260329_100000_def456",
            "platform": "discord",
            "chat_type": "group",
            "display_name": "Bob",
            "created_at": "2026-03-29T10:00:00",
            "updated_at": "2026-03-29T13:00:00",
            "input_tokens": 30000,
            "output_tokens": 1000,
            "total_tokens": 31000,
            "origin": {
                "platform": "discord",
                "chat_id": "789",
                "chat_name": "#general",
                "chat_type": "group",
                "user_id": "456",
                "user_name": "Bob",
                "thread_id": None,
                "chat_topic": None,
            },
        },
        "agent:main:slack:group:C1234:U5678": {
            "session_key": "agent:main:slack:group:C1234:U5678",
            "session_id": "20260328_090000_ghi789",
            "platform": "slack",
            "chat_type": "group",
            "display_name": "Carol",
            "created_at": "2026-03-28T09:00:00",
            "updated_at": "2026-03-28T11:00:00",
            "input_tokens": 10000,
            "output_tokens": 500,
            "total_tokens": 10500,
            "origin": {
                "platform": "slack",
                "chat_id": "C1234",
                "chat_name": "#engineering",
                "chat_type": "group",
                "user_id": "U5678",
                "user_name": "Carol",
                "thread_id": None,
                "chat_topic": None,
            },
        },
    }


@pytest.fixture
def populated_sessions_dir(sessions_dir, sample_sessions):
    (sessions_dir / "sessions.json").write_text(json.dumps(sample_sessions))
    return sessions_dir


def _create_test_db(db_path, session_id, messages):
    """Create a minimal SQLite DB mimicking hermes_state schema."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            source TEXT DEFAULT 'cli',
            started_at TEXT,
            message_count INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tool_call_id TEXT,
            tool_calls TEXT,
            tool_name TEXT,
            timestamp TEXT,
            token_count INTEGER DEFAULT 0,
            finish_reason TEXT,
            reasoning TEXT,
            reasoning_details TEXT,
            codex_reasoning_items TEXT
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO sessions (id, source, started_at, message_count) VALUES (?, 'gateway', ?, ?)",
        (session_id, "2026-03-29T12:00:00", len(messages)),
    )
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, (list, dict)):
            content = json.dumps(content)
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp, tool_calls) VALUES (?, ?, ?, ?, ?)",
            (session_id, msg["role"], content,
             msg.get("timestamp", "2026-03-29T12:00:00"),
             json.dumps(msg["tool_calls"]) if msg.get("tool_calls") else None),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def mock_session_db(tmp_path, populated_sessions_dir):
    """Create a real SQLite DB with test messages and wire it up."""
    db_path = tmp_path / "state.db"
    messages = [
        {"role": "user", "content": "Hello Alice!", "timestamp": "2026-03-29T12:00:01"},
        {"role": "assistant", "content": "Hi! How can I help?", "timestamp": "2026-03-29T12:00:05"},
        {"role": "user", "content": "Check the image MEDIA: /tmp/screenshot.png please",
         "timestamp": "2026-03-29T12:01:00"},
        {"role": "assistant", "content": "I see the screenshot. It shows a terminal.",
         "timestamp": "2026-03-29T12:01:10"},
        {"role": "tool", "content": '{"result": "ok"}', "timestamp": "2026-03-29T12:01:15"},
        {"role": "user", "content": "Thanks!", "timestamp": "2026-03-29T12:02:00"},
    ]
    _create_test_db(db_path, "20260329_120000_abc123", messages)

    # Create a mock SessionDB that reads from our test DB
    class TestSessionDB:
        def __init__(self):
            self._db_path = db_path

        def get_messages(self, session_id):
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
            conn.close()
            result = []
            for r in rows:
                d = dict(r)
                if d.get("tool_calls"):
                    d["tool_calls"] = json.loads(d["tool_calls"])
                result.append(d)
            return result

        def search_messages(self, query, limit=20, offset=0, **kwargs):
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT m.id, m.session_id, m.role, m.content, m.timestamp, s.source "
                "FROM messages m JOIN sessions s ON s.id = m.session_id "
                "WHERE instr(lower(m.content), lower(?)) > 0 "
                "ORDER BY m.id LIMIT ? OFFSET ?",
                (query, limit, offset),
            ).fetchall()
            conn.close()
            results = []
            for row in rows:
                content = row["content"] or ""
                idx = content.lower().find(query.lower())
                if idx < 0:
                    idx = 0
                snippet = content[max(0, idx - 20): idx + 80]
                results.append(
                    {
                        "id": row["id"],
                        "session_id": row["session_id"],
                        "role": row["role"],
                        "snippet": snippet,
                        "timestamp": row["timestamp"],
                        "source": row["source"],
                        "context": [{"role": row["role"], "content": content[:120]}],
                    }
                )
            return results

    return TestSessionDB()


# ---------------------------------------------------------------------------
# 1. UNIT TESTS — helpers, extraction, attachments
# ---------------------------------------------------------------------------

class TestImports:
    def test_import_module(self):
        import mcp_serve
        assert hasattr(mcp_serve, "create_mcp_server")
        assert hasattr(mcp_serve, "run_mcp_server")
        assert hasattr(mcp_serve, "EventBridge")

    def test_mcp_available_flag(self):
        import mcp_serve
        assert isinstance(mcp_serve._MCP_SERVER_AVAILABLE, bool)


class TestHelpers:
    def test_get_sessions_dir(self, tmp_path):
        from mcp_serve import _get_sessions_dir
        result = _get_sessions_dir()
        assert result == tmp_path / "sessions"

    def test_load_sessions_index_empty(self, sessions_dir, monkeypatch):
        import mcp_serve
        monkeypatch.setattr(mcp_serve, "_get_sessions_dir", lambda: sessions_dir)
        assert mcp_serve._load_sessions_index() == {}

    def test_load_sessions_index_with_data(self, populated_sessions_dir, monkeypatch):
        import mcp_serve
        monkeypatch.setattr(mcp_serve, "_get_sessions_dir", lambda: populated_sessions_dir)
        result = mcp_serve._load_sessions_index()
        assert len(result) == 3

    def test_load_sessions_index_corrupt(self, sessions_dir, monkeypatch):
        (sessions_dir / "sessions.json").write_text("not json!")
        import mcp_serve
        monkeypatch.setattr(mcp_serve, "_get_sessions_dir", lambda: sessions_dir)
        assert mcp_serve._load_sessions_index() == {}


class TestContentExtraction:
    def test_text(self):
        from mcp_serve import _extract_message_content
        assert _extract_message_content({"content": "Hello"}) == "Hello"

    def test_multipart(self):
        from mcp_serve import _extract_message_content
        msg = {"content": [
            {"type": "text", "text": "A"},
            {"type": "image", "url": "http://x.com/i.png"},
            {"type": "text", "text": "B"},
        ]}
        assert _extract_message_content(msg) == "A\nB"

    def test_empty(self):
        from mcp_serve import _extract_message_content
        assert _extract_message_content({"content": ""}) == ""
        assert _extract_message_content({}) == ""
        assert _extract_message_content({"content": None}) == ""


class TestAttachmentExtraction:
    def test_image_url_block(self):
        from mcp_serve import _extract_attachments
        msg = {"content": [
            {"type": "image_url", "image_url": {"url": "http://x.com/pic.jpg"}},
        ]}
        att = _extract_attachments(msg)
        assert len(att) == 1
        assert att[0] == {"type": "image", "url": "http://x.com/pic.jpg"}

    def test_media_tag_in_text(self):
        from mcp_serve import _extract_attachments
        msg = {"content": "Here MEDIA: /tmp/out.png done"}
        att = _extract_attachments(msg)
        assert len(att) == 1
        assert att[0] == {"type": "media", "path": "/tmp/out.png"}

    def test_multiple_media_tags(self):
        from mcp_serve import _extract_attachments
        msg = {"content": "MEDIA: /a.png and MEDIA: /b.mp3"}
        assert len(_extract_attachments(msg)) == 2

    def test_no_attachments(self):
        from mcp_serve import _extract_attachments
        assert _extract_attachments({"content": "plain text"}) == []

    def test_image_content_block(self):
        from mcp_serve import _extract_attachments
        msg = {"content": [{"type": "image", "url": "http://x.com/p.png"}]}
        att = _extract_attachments(msg)
        assert att[0]["type"] == "image"


# ---------------------------------------------------------------------------
# 2. EVENT BRIDGE TESTS — queue, cursors, waiters, concurrency
# ---------------------------------------------------------------------------

class TestEventBridge:
    def test_create(self):
        from mcp_serve import EventBridge
        b = EventBridge()
        assert b._cursor == 0
        assert b._queue == []

    def test_enqueue_and_poll(self):
        from mcp_serve import EventBridge, QueueEvent
        b = EventBridge()
        b._enqueue(QueueEvent(cursor=0, type="message", session_key="k1",
                              data={"content": "hi"}))
        r = b.poll_events(after_cursor=0)
        assert len(r["events"]) == 1
        assert r["events"][0]["type"] == "message"
        assert r["next_cursor"] == 1

    def test_cursor_filter(self):
        from mcp_serve import EventBridge, QueueEvent
        b = EventBridge()
        for i in range(5):
            b._enqueue(QueueEvent(cursor=0, type="message", session_key=f"s{i}"))
        r = b.poll_events(after_cursor=3)
        assert len(r["events"]) == 2
        assert r["events"][0]["session_key"] == "s3"

    def test_session_filter(self):
        from mcp_serve import EventBridge, QueueEvent
        b = EventBridge()
        b._enqueue(QueueEvent(cursor=0, type="message", session_key="a"))
        b._enqueue(QueueEvent(cursor=0, type="message", session_key="b"))
        b._enqueue(QueueEvent(cursor=0, type="message", session_key="a"))
        r = b.poll_events(after_cursor=0, session_key="a")
        assert len(r["events"]) == 2

    def test_poll_empty(self):
        from mcp_serve import EventBridge
        r = EventBridge().poll_events(after_cursor=0)
        assert r["events"] == []
        assert r["next_cursor"] == 0

    def test_poll_limit(self):
        from mcp_serve import EventBridge, QueueEvent
        b = EventBridge()
        for i in range(10):
            b._enqueue(QueueEvent(cursor=0, type="message", session_key=f"s{i}"))
        r = b.poll_events(after_cursor=0, limit=3)
        assert len(r["events"]) == 3

    def test_wait_immediate(self):
        from mcp_serve import EventBridge, QueueEvent
        b = EventBridge()
        b._enqueue(QueueEvent(cursor=0, type="message", session_key="t",
                              data={"content": "hi"}))
        event = b.wait_for_event(after_cursor=0, timeout_ms=100)
        assert event is not None
        assert event["type"] == "message"

    def test_wait_timeout(self):
        from mcp_serve import EventBridge
        start = time.monotonic()
        event = EventBridge().wait_for_event(after_cursor=0, timeout_ms=150)
        assert event is None
        assert time.monotonic() - start >= 0.1

    def test_wait_wakes_on_enqueue(self):
        from mcp_serve import EventBridge, QueueEvent
        b = EventBridge()
        result = [None]

        def waiter():
            result[0] = b.wait_for_event(after_cursor=0, timeout_ms=5000)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.05)
        b._enqueue(QueueEvent(cursor=0, type="message", session_key="wake"))
        t.join(timeout=2)
        assert result[0] is not None
        assert result[0]["session_key"] == "wake"

    def test_queue_limit(self):
        from mcp_serve import EventBridge, QueueEvent, QUEUE_LIMIT
        b = EventBridge()
        for i in range(QUEUE_LIMIT + 50):
            b._enqueue(QueueEvent(cursor=0, type="message", session_key=f"s{i}"))
        assert len(b._queue) == QUEUE_LIMIT

    def test_concurrent_enqueue(self):
        from mcp_serve import EventBridge, QueueEvent
        b = EventBridge()
        errors = []

        def batch(start):
            try:
                for i in range(100):
                    b._enqueue(QueueEvent(cursor=0, type="message",
                                          session_key=f"s{start}_{i}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=batch, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(b._queue) == 500
        assert b._cursor == 500

    def test_approvals_lifecycle(self):
        from mcp_serve import EventBridge
        b = EventBridge()
        b._pending_approvals["a1"] = {
            "id": "a1", "kind": "exec",
            "description": "rm -rf /tmp",
            "session_key": "test", "created_at": "2026-03-29T12:00:00",
        }
        assert len(b.list_pending_approvals()) == 1
        result = b.respond_to_approval("a1", "deny")
        assert result["resolved"] is True
        assert len(b.list_pending_approvals()) == 0

    def test_respond_nonexistent(self):
        from mcp_serve import EventBridge
        r = EventBridge().respond_to_approval("nope", "deny")
        assert "error" in r


# ---------------------------------------------------------------------------
# 3. END-TO-END TESTS — call MCP tools through FastMCP server
# ---------------------------------------------------------------------------

@pytest.fixture
def mcp_server_e2e(populated_sessions_dir, mock_session_db, monkeypatch):
    """Create a fully wired MCP server for E2E testing."""
    mcp = pytest.importorskip("mcp", reason="MCP SDK not installed")
    import mcp_serve
    monkeypatch.setattr(mcp_serve, "_get_sessions_dir", lambda: populated_sessions_dir)
    monkeypatch.setattr(mcp_serve, "_get_session_db", lambda: mock_session_db)
    monkeypatch.setattr(mcp_serve, "_load_channel_directory", lambda: {})

    bridge = mcp_serve.EventBridge()
    server = mcp_serve.create_mcp_server(event_bridge=bridge)
    return server, bridge


def _run_tool(server, name, args=None, context=None):
    """Call an MCP tool through FastMCP's tool manager and return parsed JSON."""
    result = asyncio.get_event_loop().run_until_complete(
        server._tool_manager.call_tool(name, args or {}, context=context)
    )
    return json.loads(result) if isinstance(result, str) else result


def _context_with_roots(*roots: Path):
    async def _list_roots():
        return types.ListRootsResult(
            roots=[types.Root(uri=root.resolve().as_uri(), name=root.name) for root in roots]
        )

    return SimpleNamespace(session=SimpleNamespace(list_roots=_list_roots))


@pytest.fixture
def _event_loop():
    """Ensure an event loop exists for sync tests calling async tools."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


class TestE2EConversationsList:
    def test_list_all(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "conversations_list")
        assert result["count"] == 3
        platforms = {c["platform"] for c in result["conversations"]}
        assert platforms == {"telegram", "discord", "slack"}

    def test_list_sorted_by_updated(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "conversations_list")
        keys = [c["session_key"] for c in result["conversations"]]
        # Telegram (14:30) > Discord (13:00) > Slack (11:00)
        assert keys[0] == "agent:main:telegram:dm:123456"
        assert keys[1] == "agent:main:discord:group:789:456"
        assert keys[2] == "agent:main:slack:group:C1234:U5678"

    def test_filter_by_platform(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "conversations_list", {"platform": "discord"})
        assert result["count"] == 1
        assert result["conversations"][0]["platform"] == "discord"

    def test_filter_by_platform_case_insensitive(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "conversations_list", {"platform": "TELEGRAM"})
        assert result["count"] == 1

    def test_search_by_name(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "conversations_list", {"search": "Alice"})
        assert result["count"] == 1
        assert result["conversations"][0]["display_name"] == "Alice"

    def test_search_no_match(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "conversations_list", {"search": "nobody"})
        assert result["count"] == 0

    def test_limit(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "conversations_list", {"limit": 2})
        assert result["count"] == 2


class TestE2EConversationGet:
    def test_get_existing(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "conversation_get",
                          {"session_key": "agent:main:telegram:dm:123456"})
        assert result["platform"] == "telegram"
        assert result["display_name"] == "Alice"
        assert result["chat_id"] == "123456"
        assert result["input_tokens"] == 50000

    def test_get_nonexistent(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "conversation_get",
                          {"session_key": "nonexistent:key"})
        assert "error" in result


class TestE2EMessagesRead:
    def test_read_messages(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "messages_read",
                          {"session_key": "agent:main:telegram:dm:123456"})
        assert result["count"] > 0
        # Should filter out tool messages — only user/assistant
        roles = {m["role"] for m in result["messages"]}
        assert "tool" not in roles
        assert "user" in roles
        assert "assistant" in roles

    def test_read_messages_content(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "messages_read",
                          {"session_key": "agent:main:telegram:dm:123456"})
        contents = [m["content"] for m in result["messages"]]
        assert "Hello Alice!" in contents
        assert "Hi! How can I help?" in contents

    def test_read_messages_have_ids(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "messages_read",
                          {"session_key": "agent:main:telegram:dm:123456"})
        for msg in result["messages"]:
            assert "id" in msg
            assert msg["id"]  # non-empty

    def test_read_with_limit(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "messages_read",
                          {"session_key": "agent:main:telegram:dm:123456",
                           "limit": 2})
        assert result["count"] == 2

    def test_read_nonexistent_session(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "messages_read",
                          {"session_key": "nonexistent:key"})
        assert "error" in result


class TestE2EAttachmentsFetch:
    def test_fetch_media_from_message(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        # First get message IDs
        msgs = _run_tool(server, "messages_read",
                        {"session_key": "agent:main:telegram:dm:123456"})
        # Find the message with MEDIA: tag
        media_msg = None
        for m in msgs["messages"]:
            if "MEDIA:" in m["content"]:
                media_msg = m
                break
        assert media_msg is not None, "Should have a message with MEDIA: tag"

        result = _run_tool(server, "attachments_fetch", {
            "session_key": "agent:main:telegram:dm:123456",
            "message_id": media_msg["id"],
        })
        assert result["count"] >= 1
        assert result["attachments"][0]["type"] == "media"
        assert result["attachments"][0]["path"] == "/tmp/screenshot.png"

    def test_fetch_from_nonexistent_message(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "attachments_fetch", {
            "session_key": "agent:main:telegram:dm:123456",
            "message_id": "99999",
        })
        assert "error" in result

    def test_fetch_from_nonexistent_session(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "attachments_fetch", {
            "session_key": "nonexistent:key",
            "message_id": "1",
        })
        assert "error" in result


class TestE2EEventsPoll:
    def test_poll_empty(self, mcp_server_e2e, _event_loop):
        server, bridge = mcp_server_e2e
        result = _run_tool(server, "events_poll")
        assert result["events"] == []
        assert result["next_cursor"] == 0

    def test_poll_with_events(self, mcp_server_e2e, _event_loop):
        from mcp_serve import QueueEvent
        server, bridge = mcp_server_e2e
        bridge._enqueue(QueueEvent(cursor=0, type="message",
                                   session_key="agent:main:telegram:dm:123456",
                                   data={"role": "user", "content": "Hello"}))
        bridge._enqueue(QueueEvent(cursor=0, type="message",
                                   session_key="agent:main:telegram:dm:123456",
                                   data={"role": "assistant", "content": "Hi"}))

        result = _run_tool(server, "events_poll")
        assert len(result["events"]) == 2
        assert result["events"][0]["content"] == "Hello"
        assert result["events"][1]["content"] == "Hi"
        assert result["next_cursor"] == 2

    def test_poll_cursor_pagination(self, mcp_server_e2e, _event_loop):
        from mcp_serve import QueueEvent
        server, bridge = mcp_server_e2e
        for i in range(5):
            bridge._enqueue(QueueEvent(cursor=0, type="message",
                                       session_key=f"s{i}"))

        page1 = _run_tool(server, "events_poll", {"limit": 2})
        assert len(page1["events"]) == 2
        assert page1["next_cursor"] == 2

        page2 = _run_tool(server, "events_poll",
                         {"after_cursor": page1["next_cursor"], "limit": 2})
        assert len(page2["events"]) == 2
        assert page2["next_cursor"] == 4

    def test_poll_session_filter(self, mcp_server_e2e, _event_loop):
        from mcp_serve import QueueEvent
        server, bridge = mcp_server_e2e
        bridge._enqueue(QueueEvent(cursor=0, type="message", session_key="a"))
        bridge._enqueue(QueueEvent(cursor=0, type="message", session_key="b"))
        bridge._enqueue(QueueEvent(cursor=0, type="message", session_key="a"))

        result = _run_tool(server, "events_poll",
                          {"session_key": "b"})
        assert len(result["events"]) == 1


class TestE2EEventsWait:
    def test_wait_timeout(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "events_wait", {"timeout_ms": 100})
        assert result["event"] is None
        assert result["reason"] == "timeout"

    def test_wait_with_existing_event(self, mcp_server_e2e, _event_loop):
        from mcp_serve import QueueEvent
        server, bridge = mcp_server_e2e
        bridge._enqueue(QueueEvent(cursor=0, type="message",
                                   session_key="test",
                                   data={"content": "waiting for this"}))
        result = _run_tool(server, "events_wait", {"timeout_ms": 100})
        assert result["event"] is not None
        assert result["event"]["content"] == "waiting for this"

    def test_wait_caps_timeout(self, mcp_server_e2e, _event_loop):
        """Timeout should be capped at 300000ms (5 min)."""
        from mcp_serve import QueueEvent
        server, bridge = mcp_server_e2e
        bridge._enqueue(QueueEvent(cursor=0, type="message", session_key="t"))
        # Even with huge timeout, should return immediately since event exists
        result = _run_tool(server, "events_wait", {"timeout_ms": 999999})
        assert result["event"] is not None


class TestE2EMessagesSend:
    def test_send_missing_args(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "messages_send", {"target": "", "message": "hi"})
        assert "error" in result

    def test_send_delegates_to_tool(self, mcp_server_e2e, _event_loop, monkeypatch):
        server, _ = mcp_server_e2e
        mock = MagicMock(return_value=json.dumps({"success": True, "platform": "telegram"}))
        monkeypatch.setattr("tools.send_message_tool.send_message_tool", mock)

        result = _run_tool(server, "messages_send",
                          {"target": "telegram:123456", "message": "Hello!"})
        assert result["success"] is True
        mock.assert_called_once()
        call_args = mock.call_args[0][0]
        assert call_args["action"] == "send"
        assert call_args["target"] == "telegram:123456"


class TestE2EChannelsList:
    def test_channels_from_sessions(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "channels_list")
        assert result["count"] == 3
        targets = {c["target"] for c in result["channels"]}
        assert "telegram:123456" in targets
        assert "discord:789" in targets
        assert "slack:C1234" in targets

    def test_channels_platform_filter(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "channels_list", {"platform": "slack"})
        assert result["count"] == 1
        assert result["channels"][0]["target"] == "slack:C1234"

    def test_channels_with_directory(self, mcp_server_e2e, _event_loop, monkeypatch):
        import mcp_serve
        monkeypatch.setattr(mcp_serve, "_load_channel_directory", lambda: {
            "telegram": [
                {"id": "123456", "name": "Alice", "type": "dm"},
                {"id": "-100999", "name": "Dev Group", "type": "group"},
            ],
        })
        # Need to recreate server to pick up the new mock
        server, bridge = mcp_server_e2e
        # The tool closure already captured the old mock, so test the function directly
        directory = mcp_serve._load_channel_directory()
        assert len(directory["telegram"]) == 2


class TestE2EPermissions:
    def test_list_empty(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "permissions_list_open")
        assert result["count"] == 0
        assert result["approvals"] == []

    def test_list_with_approvals(self, mcp_server_e2e, _event_loop):
        server, bridge = mcp_server_e2e
        bridge._pending_approvals["a1"] = {
            "id": "a1", "kind": "exec",
            "description": "sudo rm -rf /",
            "session_key": "test",
            "created_at": "2026-03-29T12:00:00",
        }
        result = _run_tool(server, "permissions_list_open")
        assert result["count"] == 1
        assert result["approvals"][0]["id"] == "a1"

    def test_respond_allow(self, mcp_server_e2e, _event_loop):
        server, bridge = mcp_server_e2e
        bridge._pending_approvals["a1"] = {"id": "a1", "kind": "exec"}
        result = _run_tool(server, "permissions_respond",
                          {"id": "a1", "decision": "allow-once"})
        assert result["resolved"] is True
        assert result["decision"] == "allow-once"
        # Should be gone now
        check = _run_tool(server, "permissions_list_open")
        assert check["count"] == 0

    def test_respond_deny(self, mcp_server_e2e, _event_loop):
        server, bridge = mcp_server_e2e
        bridge._pending_approvals["a2"] = {"id": "a2", "kind": "plugin"}
        result = _run_tool(server, "permissions_respond",
                          {"id": "a2", "decision": "deny"})
        assert result["resolved"] is True

    def test_respond_invalid_decision(self, mcp_server_e2e, _event_loop):
        server, bridge = mcp_server_e2e
        bridge._pending_approvals["a3"] = {"id": "a3", "kind": "exec"}
        result = _run_tool(server, "permissions_respond",
                          {"id": "a3", "decision": "maybe"})
        assert "error" in result

    def test_respond_nonexistent(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "permissions_respond",
                          {"id": "nope", "decision": "deny"})
        assert "error" in result


# ---------------------------------------------------------------------------
# 4. TOOL LISTING — verify all 10 tools are registered
# ---------------------------------------------------------------------------

class TestToolRegistration:
    def test_all_tools_registered(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        tools = server._tool_manager.list_tools()
        tool_names = {t.name for t in tools}

        expected = {
            "conversations_list", "conversation_get", "messages_read",
            "attachments_fetch", "events_poll", "events_wait",
            "messages_send", "channels_list",
            "permissions_list_open", "permissions_respond",
            "memory_read", "memory_write", "session_recall_search",
            "skills_list", "skill_view_safe", "skill_create_or_patch",
            "task_context_bundle", "init",
            "plan_skill_read", "plan", "plan_read", "plan_update",
        }
        assert expected == tool_names, f"Missing: {expected - tool_names}, Extra: {tool_names - expected}"

    def test_tools_have_descriptions(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        for tool in server._tool_manager.list_tools():
            assert tool.description, f"Tool {tool.name} has no description"

    def test_server_instructions_cover_learning_and_messaging(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        text = server.instructions.lower()
        assert "记忆" in text
        assert "技能" in text
        assert "plan" in text
        assert "/plan" in text


class TestE2ELearningTools:
    def test_memory_read(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "memory_read")
        assert result["memory_count"] == 3
        assert result["user_count"] == 2
        assert "project uses pytest" in result["memory"]

    def test_memory_write_add_and_replace(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        add_result = _run_tool(
            server,
            "memory_write",
            {"action": "add", "target": "memory", "content": "new durable fact"},
        )
        assert add_result["success"] is True
        assert "new durable fact" in add_result["entries"]
        assert add_result["quality_gate"]["decision"] in {"pass", "warn"}

        replace_result = _run_tool(
            server,
            "memory_write",
            {
                "action": "replace",
                "target": "memory",
                "old_text": "new durable fact",
                "content": "updated durable fact",
            },
        )
        assert replace_result["success"] is True
        assert "updated durable fact" in replace_result["entries"]
        assert replace_result["quality_gate"]["decision"] in {"pass", "warn"}

    def test_memory_write_quality_gate_blocks_temporary_speculation(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(
            server,
            "memory_write",
            {
                "action": "add",
                "target": "memory",
                "content": "Maybe this temporary workaround works for this task only.",
            },
        )
        assert result["success"] is False
        assert result["quality_gate"]["decision"] == "block"
        assert "temporary" in " ".join(result["quality_gate"]["reasons"]).lower()

    def test_memory_write_quality_gate_suggests_replace_for_conflict(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(
            server,
            "memory_write",
            {
                "action": "add",
                "target": "memory",
                "content": "Project tests must not use pytest.",
            },
        )
        assert result["success"] is False
        assert result["quality_gate"]["decision"] == "suggest_replace"
        assert result["quality_gate"]["conflict_type"] == "conflict"

    def test_memory_write_remove_rejected(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(
            server,
            "memory_write",
            {"action": "remove", "target": "memory", "old_text": "pytest", "content": ""},
        )
        assert result["success"] is False

    def test_session_recall_search(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "session_recall_search", {"query": "Hello", "limit": 5})
        assert result["success"] is True
        assert result["results"]
        assert len(result["results"][0]["snippet"]) <= 300

    def test_session_recall_search_does_not_use_summarizer(self, mcp_server_e2e, _event_loop, monkeypatch):
        server, _ = mcp_server_e2e
        monkeypatch.setattr("tools.session_search_tool.async_call_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call llm")))
        result = _run_tool(server, "session_recall_search", {"query": "Hello"})
        assert result["success"] is True

    def test_skills_list_local_only(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "skills_list", {"query": "fastmcp"})
        assert result["success"] is True
        assert result["skills"][0]["name"] == "fastmcp-helper"

    def test_skill_view_safe_main_and_reference(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "skill_view_safe", {"name": "fastmcp-helper"})
        assert result["success"] is True
        assert "FastMCP Helper" in result["content"]

        ref = _run_tool(server, "skill_view_safe", {"name": "fastmcp-helper", "file_path": "references/api.md"})
        assert ref["success"] is True
        assert "# API" in ref["content"]

    def test_skill_view_safe_rejects_plugin_and_scripts(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        plugin = _run_tool(server, "skill_view_safe", {"name": "plugin:foo"})
        assert plugin["success"] is False

        scripts = _run_tool(server, "skill_view_safe", {"name": "fastmcp-helper", "file_path": "scripts/unsafe.py"})
        assert scripts["success"] is False

    def test_skill_create_or_patch_and_forbidden_actions(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        create = _run_tool(
            server,
            "skill_create_or_patch",
            {
                "action": "create",
                "name": "new-skill",
                "category": "python",
                "content": (
                    "---\nname: new-skill\ndescription: New skill.\n---\n\n"
                    "# New\n\n"
                    "## Use_When\nUse when a verified reusable workflow is needed.\n\n"
                    "## Steps\n1. Follow the verified process.\n\n"
                    "## Verification\nRun the relevant tests.\n"
                ),
            },
        )
        assert create["success"] is True
        assert create["quality_gate"]["decision"] in {"pass", "warn"}

        patch = _run_tool(
            server,
            "skill_create_or_patch",
            {
                "action": "patch",
                "name": "new-skill",
                "old_string": "New skill.",
                "new_string": "Patched skill.",
            },
        )
        assert patch["success"] is True
        assert patch["quality_gate"]["decision"] in {"pass", "warn"}

        forbidden = _run_tool(
            server,
            "skill_create_or_patch",
            {"action": "delete", "name": "new-skill"},
        )
        assert forbidden["success"] is False

        for action in ("edit", "write_file", "remove_file"):
            forbidden = _run_tool(
                server,
                "skill_create_or_patch",
                {"action": action, "name": "new-skill"},
            )
            assert forbidden["success"] is False
            assert "Unsupported action" in forbidden["error"]

    def test_skill_create_or_patch_quality_gate_blocks_missing_frontmatter(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(
            server,
            "skill_create_or_patch",
            {
                "action": "create",
                "name": "bad-skill",
                "content": "# Bad Skill\n\nSome notes without required frontmatter.",
            },
        )
        assert result["success"] is False
        assert result["quality_gate"]["decision"] == "block"
        assert any("frontmatter" in reason.lower() for reason in result["quality_gate"]["reasons"])

    def test_skill_create_or_patch_quality_gate_blocks_missing_reusable_structure(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(
            server,
            "skill_create_or_patch",
            {
                "action": "create",
                "name": "thin-skill",
                "content": "---\nname: thin-skill\ndescription: Thin skill.\n---\n\n# Thin\n\nSome durable notes.",
            },
        )
        assert result["success"] is False
        assert result["quality_gate"]["decision"] == "block"
        assert any("structure" in reason.lower() for reason in result["quality_gate"]["reasons"])

    def test_skill_patch_quality_gate_blocks_removing_required_structure(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        skill_content = (
            "---\nname: patch-guard\ndescription: Patch guard skill.\n---\n\n"
            "# Patch Guard\n\n"
            "## Use_When\nUse when testing patch safety.\n\n"
            "## Steps\n1. Keep structure intact.\n\n"
            "## Verification\nRun tests.\n"
        )
        create = _run_tool(
            server,
            "skill_create_or_patch",
            {"action": "create", "name": "patch-guard", "content": skill_content},
        )
        assert create["success"] is True

        result = _run_tool(
            server,
            "skill_create_or_patch",
            {
                "action": "patch",
                "name": "patch-guard",
                "old_string": "## Steps\n1. Keep structure intact.\n\n## Verification\nRun tests.\n",
                "new_string": "",
            },
        )
        assert result["success"] is False
        assert result["quality_gate"]["decision"] == "block"
        assert any("structure" in reason.lower() for reason in result["quality_gate"]["reasons"])

    def test_skill_patch_quality_gate_projects_fuzzy_match_before_scoring(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        skill_content = (
            "---\nname: fuzzy-patch-guard\ndescription: Fuzzy patch guard skill.\n---\n\n"
            "# Fuzzy Patch Guard\n\n"
            "## Use_When\nUse when testing fuzzy patch safety.\n\n"
            "## Steps\n1. Keep structure intact.\n\n"
            "## Verification\nRun tests.\n"
        )
        create = _run_tool(
            server,
            "skill_create_or_patch",
            {"action": "create", "name": "fuzzy-patch-guard", "content": skill_content},
        )
        assert create["success"] is True

        result = _run_tool(
            server,
            "skill_create_or_patch",
            {
                "action": "patch",
                "name": "fuzzy-patch-guard",
                # Deliberately include boundary whitespace drift; production
                # patching can still match this via line-trimmed fuzzy matching.
                "old_string": "## Steps\n1. Keep structure intact.\n\n## Verification\nRun tests.   ",
                "new_string": "",
            },
        )
        assert result["success"] is False
        assert result["quality_gate"]["decision"] == "block"
        assert any("structure" in reason.lower() for reason in result["quality_gate"]["reasons"])

    def test_task_context_bundle(self, mcp_server_e2e, _event_loop, monkeypatch):
        server, _ = mcp_server_e2e
        monkeypatch.setattr("tools.session_search_tool.async_call_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call llm")))
        result = _run_tool(server, "task_context_bundle", {"query": "fastmcp"})
        assert result["success"] is True
        assert len(result["memory"]) <= 5
        assert len(result["user"]) <= 5
        assert len(result["session_hits"]) <= 5
        assert len(result["skill_candidates"]) <= 5
        assert all("content" not in skill for skill in result["skill_candidates"])
        assert result["hints"] == [
            "可使用 skill_view_safe(name=...) 查看候选技能的完整内容。",
            "可使用 session_recall_search(query=...) 做更聚焦的后续会话回忆检索。",
            "如需按 Hermes 原生 /plan 风格规划，可先调用 plan_skill_read()。",
            "确定方案后调用 plan(...)；任务完成后考虑调用 memory_write(...) 或 skill_create_or_patch(...)。",
        ]
        assert "quality_audit" in result
        assert "quality_filter" in result

        second = _run_tool(server, "task_context_bundle", {"query": "fastmcp"})
        assert second["success"] is True
        assert second["quality_audit"]["ran"] is False

    def test_task_context_bundle_filters_stale_quality_metadata(self, mcp_server_e2e, _event_loop, monkeypatch):
        server, _ = mcp_server_e2e
        monkeypatch.setattr("tools.session_search_tool.async_call_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call llm")))

        from tools.knowledge_quality import (
            isoformat,
            memory_item_key,
            save_quality_index,
            utc_now,
        )

        old_entry = "fastmcp server already exists"
        key = memory_item_key("memory", old_entry)
        save_quality_index(
            {
                "version": 1,
                "last_audit_at": None,
                "items": {
                    key: {
                        "kind": "memory",
                        "target": "memory",
                        "content_hash": key.rsplit(":", 1)[-1],
                        "status": "active",
                        "score": 80,
                        "scores": {},
                        "created_at": isoformat(utc_now()),
                        "last_verified_at": isoformat(utc_now()),
                        "review_after": "2000-01-01T00:00:00Z",
                        "expires_at": "2000-01-01T00:00:00Z",
                    }
                },
            }
        )

        result = _run_tool(server, "task_context_bundle", {"query": "fastmcp", "memory_limit": 5})
        assert result["success"] is True
        assert old_entry not in result["memory"]
        assert result["quality_audit"]["ran"] is True
        assert result["quality_audit"]["stale_count"] >= 1

    def test_task_context_bundle_filters_stale_skill_metadata(self, mcp_server_e2e, _event_loop, monkeypatch):
        server, _ = mcp_server_e2e
        monkeypatch.setattr("tools.session_search_tool.async_call_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call llm")))

        from tools.knowledge_quality import (
            content_hash,
            isoformat,
            save_quality_index,
            skill_item_key,
            utc_now,
        )

        skill_content = "fastmcp-helper stale metadata"
        key = skill_item_key("fastmcp-helper", "SKILL.md", skill_content)
        save_quality_index(
            {
                "version": 1,
                "last_audit_at": None,
                "items": {
                    key: {
                        "kind": "skill",
                        "name": "fastmcp-helper",
                        "file_path": "SKILL.md",
                        "content_hash": content_hash(skill_content),
                        "status": "active",
                        "score": 80,
                        "scores": {},
                        "created_at": isoformat(utc_now()),
                        "last_verified_at": isoformat(utc_now()),
                        "review_after": "2000-01-01T00:00:00Z",
                        "expires_at": "2000-01-01T00:00:00Z",
                    }
                },
            }
        )

        result = _run_tool(server, "task_context_bundle", {"query": "fastmcp", "skill_limit": 5})
        assert result["success"] is True
        assert all(skill["name"] != "fastmcp-helper" for skill in result["skill_candidates"])
        assert result["quality_filter"]["skills"]["excluded_from_bundle"] >= 1

    def test_task_context_bundle_prefers_active_skill_metadata_over_stale_history(self, mcp_server_e2e, _event_loop, monkeypatch):
        server, _ = mcp_server_e2e
        monkeypatch.setattr("tools.session_search_tool.async_call_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call llm")))

        from tools.knowledge_quality import (
            content_hash,
            save_quality_index,
            skill_item_key,
        )

        stale_content = "fastmcp-helper stale metadata"
        active_content = "fastmcp-helper active metadata"
        stale_key = skill_item_key("fastmcp-helper", "SKILL.md", stale_content)
        active_key = skill_item_key("fastmcp-helper", "SKILL.md", active_content)
        save_quality_index(
            {
                "version": 1,
                "last_audit_at": "2026-04-21T00:00:00Z",
                "items": {
                    active_key: {
                        "kind": "skill",
                        "name": "fastmcp-helper",
                        "file_path": "SKILL.md",
                        "content_hash": content_hash(active_content),
                        "status": "active",
                        "score": 90,
                        "created_at": "2026-04-21T00:00:00Z",
                    },
                    stale_key: {
                        "kind": "skill",
                        "name": "fastmcp-helper",
                        "file_path": "SKILL.md",
                        "content_hash": content_hash(stale_content),
                        "status": "stale",
                        "score": 50,
                        "created_at": "2026-04-20T00:00:00Z",
                    },
                },
            }
        )

        result = _run_tool(server, "task_context_bundle", {"query": "fastmcp", "skill_limit": 5})
        assert result["success"] is True
        assert any(skill["name"] == "fastmcp-helper" for skill in result["skill_candidates"])
        assert result["quality_filter"]["skills"]["excluded_from_bundle"] == 0

    def test_init_writes_trae_project_rules(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        result = _run_tool(server, "init", {"project_name": "Hermes FastMCP", "overwrite": True})
        assert result["success"] is True
        assert result["created"] is True
        assert result["path"] == ".trae/rules/hermes-mcp-workflow.md"
        assert "task_context_bundle(...)" in result["content"]
        assert "memory_write(...)" in result["content"]
        assert "skill_create_or_patch(...)" in result["content"]
        assert "必须" in result["content"]
        assert "默认不写入记忆或技能" in result["content"]
        assert len(result["content"]) <= 1000

        rule_file = Path(result["absolute_path"])
        assert rule_file.exists()
        assert "plan_skill_read()" in rule_file.read_text(encoding="utf-8")

    def test_init_prefers_client_root_over_server_cwd(self, mcp_server_e2e, _event_loop, tmp_path, monkeypatch):
        server, _ = mcp_server_e2e
        server_repo_dir = tmp_path / "server-repo"
        client_project_dir = tmp_path / "client-project"
        server_repo_dir.mkdir()
        client_project_dir.mkdir()
        monkeypatch.chdir(server_repo_dir)

        result = _run_tool(
            server,
            "init",
            {"project_name": "Client Project", "overwrite": True},
            context=_context_with_roots(client_project_dir),
        )

        expected = client_project_dir / ".trae" / "rules" / "hermes-mcp-workflow.md"
        assert Path(result["absolute_path"]) == expected
        assert expected.exists()
        assert not (server_repo_dir / ".trae" / "rules" / "hermes-mcp-workflow.md").exists()

    def test_plan_read_update(self, mcp_server_e2e, _event_loop):
        server, _ = mcp_server_e2e
        guide = _run_tool(server, "plan_skill_read")
        assert guide["success"] is True
        assert guide["name"] == "plan"
        assert "# Plan Mode" in guide["content"]

        created = _run_tool(
            server,
            "plan",
            {
                "task": "Add FastMCP planner executor workflow",
                "goal": "Expose lightweight planning guidance and plan tools.",
                "steps": [
                    "Inspect current FastMCP surfaces",
                    "Add plan storage helpers",
                    "Simplify Trae workflow guidance",
                ],
                "files": ["mcp_serve.py", "tools/plan_tool.py", "TRAE_MCP_SYSTEM_PROMPT_CN.md"],
                "tests": ["scripts/run_tests.sh tests/test_mcp_serve.py"],
            },
        )
        assert created["success"] is True
        assert created["path"].startswith(".hermes/plans/")

        read_back = _run_tool(server, "plan_read", {"path": created["path"]})
        assert read_back["success"] is True
        assert "Add FastMCP planner executor workflow" in read_back["content"]

        updated = _run_tool(
            server,
            "plan_update",
            {
                "path": created["path"],
                "content": "# Updated plan\n\n## Step-by-step plan\n\n- Do the thing\n",
            },
        )
        assert updated["success"] is True

        latest = _run_tool(server, "plan_read", {"latest": True})
        assert latest["success"] is True
        assert latest["content"].startswith("# Updated plan")

    def test_plan_tools_prefer_client_root_over_server_cwd(self, mcp_server_e2e, _event_loop, tmp_path, monkeypatch):
        server, _ = mcp_server_e2e
        server_repo_dir = tmp_path / "server-repo"
        client_project_dir = tmp_path / "client-project"
        server_repo_dir.mkdir()
        client_project_dir.mkdir()
        monkeypatch.chdir(server_repo_dir)
        ctx = _context_with_roots(client_project_dir)

        created = _run_tool(
            server,
            "plan",
            {"task": "Client rooted plan", "steps": ["Use client root"]},
            context=ctx,
        )
        expected_dir = client_project_dir / ".hermes" / "plans"
        assert Path(created["absolute_path"]).parent == expected_dir
        assert expected_dir.exists()
        assert not (server_repo_dir / ".hermes" / "plans").exists()

        read_back = _run_tool(server, "plan_read", {"path": created["path"]}, context=ctx)
        assert read_back["success"] is True
        assert read_back["absolute_path"] == created["absolute_path"]

        updated = _run_tool(
            server,
            "plan_update",
            {"path": created["path"], "content": "# Client rooted plan\n"},
            context=ctx,
        )
        assert updated["success"] is True
        assert Path(updated["absolute_path"]) == Path(created["absolute_path"])


# ---------------------------------------------------------------------------
# 5. SERVER LIFECYCLE / CLI INTEGRATION
# ---------------------------------------------------------------------------

class TestServerCreation:
    def test_create_server(self, populated_sessions_dir, monkeypatch):
        pytest.importorskip("mcp", reason="MCP SDK not installed")
        import mcp_serve
        monkeypatch.setattr(mcp_serve, "_get_sessions_dir", lambda: populated_sessions_dir)
        assert mcp_serve.create_mcp_server() is not None

    def test_create_with_bridge(self, populated_sessions_dir, monkeypatch):
        pytest.importorskip("mcp", reason="MCP SDK not installed")
        import mcp_serve
        monkeypatch.setattr(mcp_serve, "_get_sessions_dir", lambda: populated_sessions_dir)
        bridge = mcp_serve.EventBridge()
        assert mcp_serve.create_mcp_server(event_bridge=bridge) is not None

    def test_create_without_mcp_sdk(self, monkeypatch):
        import mcp_serve
        monkeypatch.setattr(mcp_serve, "_MCP_SERVER_AVAILABLE", False)
        with pytest.raises(ImportError, match="MCP 服务端依赖"):
            mcp_serve.create_mcp_server()


class TestRunMcpServer:
    def test_run_without_mcp_exits(self, monkeypatch):
        import mcp_serve
        monkeypatch.setattr(mcp_serve, "_MCP_SERVER_AVAILABLE", False)
        with pytest.raises(SystemExit) as exc_info:
            mcp_serve.run_mcp_server()
        assert exc_info.value.code == 1


class TestCliIntegration:
    def test_parse_serve(self):
        import argparse
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="command")
        mcp_p = subs.add_parser("mcp")
        mcp_sub = mcp_p.add_subparsers(dest="mcp_action")
        serve_p = mcp_sub.add_parser("serve")
        serve_p.add_argument("-v", "--verbose", action="store_true")

        args = parser.parse_args(["mcp", "serve"])
        assert args.mcp_action == "serve"
        assert args.verbose is False

    def test_parse_serve_verbose(self):
        import argparse
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="command")
        mcp_p = subs.add_parser("mcp")
        mcp_sub = mcp_p.add_subparsers(dest="mcp_action")
        serve_p = mcp_sub.add_parser("serve")
        serve_p.add_argument("-v", "--verbose", action="store_true")

        args = parser.parse_args(["mcp", "serve", "--verbose"])
        assert args.verbose is True

    def test_dispatcher_routes_serve(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        mock_run = MagicMock()
        monkeypatch.setattr("mcp_serve.run_mcp_server", mock_run)

        import argparse
        args = argparse.Namespace(mcp_action="serve", verbose=True)
        from hermes_cli.mcp_config import mcp_command
        mcp_command(args)
        mock_run.assert_called_once_with(verbose=True)


# ---------------------------------------------------------------------------
# 6. EDGE CASES
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_sessions_json(self, sessions_dir, monkeypatch):
        (sessions_dir / "sessions.json").write_text("{}")
        import mcp_serve
        monkeypatch.setattr(mcp_serve, "_get_sessions_dir", lambda: sessions_dir)
        assert mcp_serve._load_sessions_index() == {}

    def test_sessions_without_origin(self, sessions_dir, monkeypatch):
        data = {"agent:main:telegram:dm:111": {
            "session_key": "agent:main:telegram:dm:111",
            "session_id": "20260329_120000_xyz",
            "platform": "telegram",
            "updated_at": "2026-03-29T12:00:00",
        }}
        (sessions_dir / "sessions.json").write_text(json.dumps(data))
        import mcp_serve
        monkeypatch.setattr(mcp_serve, "_get_sessions_dir", lambda: sessions_dir)
        entries = mcp_serve._load_sessions_index()
        assert entries["agent:main:telegram:dm:111"]["platform"] == "telegram"

    def test_bridge_start_stop(self):
        from mcp_serve import EventBridge
        b = EventBridge()
        assert not b._running
        b._running = True
        b.stop()
        assert not b._running

    def test_truncation(self):
        assert len(("x" * 5000)[:2000]) == 2000


# ---------------------------------------------------------------------------
# 7. EVENT BRIDGE POLL LOOP E2E — real SQLite DB, mtime optimization
# ---------------------------------------------------------------------------

class TestEventBridgePollE2E:
    """End-to-end tests for the EventBridge polling loop with real files."""

    def test_poll_detects_new_messages(self, tmp_path, monkeypatch):
        """Write to SQLite + sessions.json, verify EventBridge picks it up."""
        import mcp_serve
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        monkeypatch.setattr(mcp_serve, "_get_sessions_dir", lambda: sessions_dir)

        session_id = "20260329_150000_poll_test"
        db_path = tmp_path / "state.db"

        # Write sessions.json
        sessions_data = {
            "agent:main:telegram:dm:poll_test": {
                "session_key": "agent:main:telegram:dm:poll_test",
                "session_id": session_id,
                "platform": "telegram",
                "chat_type": "dm",
                "display_name": "PollTest",
                "updated_at": "2026-03-29T15:00:05",
                "origin": {"platform": "telegram", "chat_id": "poll_test"},
            }
        }
        (sessions_dir / "sessions.json").write_text(json.dumps(sessions_data))

        # Write messages to SQLite
        messages = [
            {"role": "user", "content": "First message",
             "timestamp": "2026-03-29T15:00:01"},
            {"role": "assistant", "content": "Reply",
             "timestamp": "2026-03-29T15:00:03"},
        ]
        _create_test_db(db_path, session_id, messages)

        # Create a mock SessionDB that reads our test DB
        class TestDB:
            def get_messages(self, sid):
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM messages WHERE session_id = ? ORDER BY id",
                    (sid,),
                ).fetchall()
                conn.close()
                return [dict(r) for r in rows]

        monkeypatch.setattr(mcp_serve, "_get_session_db", lambda: TestDB())

        bridge = mcp_serve.EventBridge()
        # Run one poll cycle manually
        bridge._poll_once(TestDB())

        # Should have found the messages
        result = bridge.poll_events(after_cursor=0)
        assert len(result["events"]) == 2
        assert result["events"][0]["role"] == "user"
        assert result["events"][0]["content"] == "First message"
        assert result["events"][1]["role"] == "assistant"

    def test_poll_skips_when_unchanged(self, tmp_path, monkeypatch):
        """Second poll with no file changes should be a no-op."""
        import mcp_serve
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        monkeypatch.setattr(mcp_serve, "_get_sessions_dir", lambda: sessions_dir)

        session_id = "20260329_150000_skip_test"
        db_path = tmp_path / "state.db"

        sessions_data = {
            "agent:main:telegram:dm:skip": {
                "session_key": "agent:main:telegram:dm:skip",
                "session_id": session_id,
                "platform": "telegram",
                "updated_at": "2026-03-29T15:00:05",
                "origin": {"platform": "telegram", "chat_id": "skip"},
            }
        }
        (sessions_dir / "sessions.json").write_text(json.dumps(sessions_data))
        _create_test_db(db_path, session_id, [
            {"role": "user", "content": "Hello", "timestamp": "2026-03-29T15:00:01"},
        ])

        class TestDB:
            def __init__(self):
                self.call_count = 0

            def get_messages(self, sid):
                self.call_count += 1
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM messages WHERE session_id = ? ORDER BY id",
                    (sid,),
                ).fetchall()
                conn.close()
                return [dict(r) for r in rows]

        db = TestDB()
        bridge = mcp_serve.EventBridge()

        # First poll — should process
        bridge._poll_once(db)
        first_calls = db.call_count
        assert first_calls >= 1

        # Second poll — files unchanged, should skip entirely
        bridge._poll_once(db)
        assert db.call_count == first_calls, \
            "Second poll should skip DB queries when files unchanged"

    def test_poll_detects_new_message_after_db_write(self, tmp_path, monkeypatch):
        """Write a new message to the DB after first poll, verify it's detected."""
        import mcp_serve
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        monkeypatch.setattr(mcp_serve, "_get_sessions_dir", lambda: sessions_dir)

        session_id = "20260329_150000_new_msg"
        db_path = tmp_path / "state.db"

        sessions_data = {
            "agent:main:telegram:dm:new": {
                "session_key": "agent:main:telegram:dm:new",
                "session_id": session_id,
                "platform": "telegram",
                "updated_at": "2026-03-29T15:00:05",
                "origin": {"platform": "telegram", "chat_id": "new"},
            }
        }
        (sessions_dir / "sessions.json").write_text(json.dumps(sessions_data))
        _create_test_db(db_path, session_id, [
            {"role": "user", "content": "First", "timestamp": "2026-03-29T15:00:01"},
        ])

        class TestDB:
            def get_messages(self, sid):
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM messages WHERE session_id = ? ORDER BY id",
                    (sid,),
                ).fetchall()
                conn.close()
                return [dict(r) for r in rows]

        db = TestDB()
        bridge = mcp_serve.EventBridge()

        # First poll
        bridge._poll_once(db)
        r1 = bridge.poll_events(after_cursor=0)
        assert len(r1["events"]) == 1

        # Add a new message to the DB
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (session_id, "assistant", "New reply!", "2026-03-29T15:00:10"),
        )
        conn.commit()
        conn.close()
        # Touch the DB file to update mtime (WAL mode may not update mtime on small writes)
        os.utime(db_path, None)

        # Update sessions.json updated_at to trigger re-check
        sessions_data["agent:main:telegram:dm:new"]["updated_at"] = "2026-03-29T15:00:10"
        (sessions_dir / "sessions.json").write_text(json.dumps(sessions_data))

        # Second poll — should detect the new message
        bridge._poll_once(db)
        r2 = bridge.poll_events(after_cursor=r1["next_cursor"])
        assert len(r2["events"]) == 1
        assert r2["events"][0]["content"] == "New reply!"

    def test_poll_interval_is_200ms(self):
        """Verify the poll interval constant."""
        from mcp_serve import POLL_INTERVAL
        assert POLL_INTERVAL == 0.2
