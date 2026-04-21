#!/usr/bin/env python3
"""Deterministic knowledge quality gates for MCP memory and skill writes.

The helpers in this module are intentionally dependency-free and profile-scoped.
They provide a lightweight first-pass quality gate before durable agent knowledge
is written, plus a bounded freshness audit for task context bundles.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from hermes_constants import get_hermes_home

try:
    import fcntl  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:
    import msvcrt  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None

INDEX_VERSION = 1
AUDIT_INTERVAL_HOURS = 24
DEFAULT_REVIEW_DAYS = 30
WARN_REVIEW_DAYS = 14
EXTERNAL_REVIEW_DAYS = 14

QUALITY_DIRNAME = "knowledge_quality"
INDEX_FILENAME = "index.json"
AUDIT_LOG_FILENAME = "audit-log.jsonl"

TEMPORARY_PATTERNS = (
    "temporary", "temporarily", "for now", "today only", "this task", "current task",
    "right now", "just now", "当前正在", "本次任务", "这次任务", "临时", "暂时", "刚刚",
)
SPECULATIVE_PATTERNS = (
    "maybe", "probably", "possibly", "guess", "suspect", "might be", "could be",
    "可能", "猜测", "也许", "大概", "疑似", "推测",
)
VERIFIED_PATTERNS = (
    "verified", "tested", "confirmed", "observed", "from agents.md", "from file",
    "source:", "evidence:", "verified by", "test:", "tests:", "通过", "验证", "测试",
    "来自", "依据", "AGENTS.md",
)
REUSABLE_PATTERNS = (
    "always", "never", "must", "prefer", "use ", "do not", "when ", "before ",
    "after ", "workflow", "procedure", "rule", "policy", "pitfall", "preference",
    "必须", "不要", "总是", "优先", "流程", "规则", "偏好", "踩坑", "适用于",
)
COMPLETENESS_PATTERNS = (
    "problem", "cause", "solution", "applies to", "verified by", "source", "evidence",
    "问题", "原因", "方案", "解决", "适用", "位置", "引用", "验证", "来源",
)
CONFLICT_NEGATORS = ("do not", "don't", "must not", "not use", "never", "avoid", "禁止", "不要", "不能", "禁用")
CONFLICT_POSITIVES = ("must", "always", "use", "uses", "prefer", "required", "必须", "总是", "使用", "优先")
SKILL_STRUCTURE_PATTERNS = (
    "use_when", "steps", "verification", "pitfalls", "examples", "purpose",
    "## use", "## steps", "## verification", "<use_when>", "<steps>",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        normalized = value.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def content_hash(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def memory_item_key(target: str, content: str) -> str:
    return f"memory:{target}:{content_hash(content)}"


def skill_item_key(name: str, file_path: Optional[str], content: str) -> str:
    rel = file_path or "SKILL.md"
    return f"skill:{name}:{rel}:{content_hash(content)}"


def quality_dir() -> Path:
    return get_hermes_home() / QUALITY_DIRNAME


def index_path() -> Path:
    return quality_dir() / INDEX_FILENAME


def audit_log_path() -> Path:
    return quality_dir() / AUDIT_LOG_FILENAME


@contextmanager
def quality_index_lock():
    """Serialize quality index read-modify-write updates across MCP clients."""
    lock_path = quality_dir() / f"{INDEX_FILENAME}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if fcntl is None and msvcrt is None:
        yield
        return

    if msvcrt and (not lock_path.exists() or lock_path.stat().st_size == 0):
        lock_path.write_text(" ", encoding="utf-8")

    fd = open(lock_path, "r+" if msvcrt else "a+")
    try:
        if fcntl:
            fcntl.flock(fd, fcntl.LOCK_EX)
        else:
            fd.seek(0)
            msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        if fcntl:
            fcntl.flock(fd, fcntl.LOCK_UN)
        elif msvcrt:
            try:
                fd.seek(0)
                msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
            except (OSError, IOError):
                pass
        fd.close()


def _empty_index() -> Dict[str, Any]:
    return {"version": INDEX_VERSION, "last_audit_at": None, "items": {}}


def load_quality_index() -> Dict[str, Any]:
    path = index_path()
    if not path.exists():
        return _empty_index()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_index()
    if not isinstance(data, dict):
        return _empty_index()
    data.setdefault("version", INDEX_VERSION)
    data.setdefault("last_audit_at", None)
    if not isinstance(data.get("items"), dict):
        data["items"] = {}
    return data


def save_quality_index(index: Dict[str, Any]) -> None:
    qdir = quality_dir()
    qdir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(qdir), prefix=".index_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, index_path())
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def append_audit_log(event: Dict[str, Any]) -> None:
    qdir = quality_dir()
    qdir.mkdir(parents=True, exist_ok=True)
    event = {"at": isoformat(utc_now()), **event}
    with audit_log_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _contains_any(text: str, patterns: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(pattern.lower() in lowered for pattern in patterns)


def _score_component(base: int, maximum: int) -> int:
    return max(0, min(maximum, base))


def _token_set(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-zA-Z0-9_一-鿿]{2,}", text.lower()) if len(token) >= 2}


def _similarity(a: str, b: str) -> float:
    ta = _token_set(a)
    tb = _token_set(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _has_negation(text: str) -> bool:
    return _contains_any(text, CONFLICT_NEGATORS)


def _has_positive_directive(text: str) -> bool:
    return _contains_any(text, CONFLICT_POSITIVES)


def _detect_memory_conflict(content: str, existing_entries: Iterable[str]) -> Tuple[str, List[str], List[str]]:
    reasons: List[str] = []
    suggestions: List[str] = []
    content_norm = content.strip().lower()
    content_neg = _has_negation(content)
    content_pos = _has_positive_directive(content)

    for entry in existing_entries:
        entry_norm = str(entry).strip().lower()
        if not entry_norm:
            continue
        if content_norm == entry_norm:
            reasons.append("Duplicate durable memory entry already exists.")
            return "duplicate", reasons, ["Skip this write; the entry already exists."]

        sim = _similarity(content, entry)
        if sim >= 0.72:
            reasons.append("Candidate is highly similar to an existing memory entry.")
            suggestions.append("Use action='replace' with old_text targeting the existing entry if this is an update.")
            return "similar", reasons, suggestions

        entry_neg = _has_negation(entry)
        entry_pos = _has_positive_directive(entry)
        if sim >= 0.25 and ((content_neg and entry_pos) or (content_pos and entry_neg)):
            reasons.append("Candidate appears to conflict with an existing directive-like memory entry.")
            suggestions.append("Use replace to supersede the older entry instead of adding a conflicting one.")
            return "conflict", reasons, suggestions

    return "none", reasons, suggestions


def _source_type_for_content(content: str) -> str:
    lowered = content.lower()
    if "agents.md" in lowered or "system" in lowered or "developer instruction" in lowered:
        return "project_instruction"
    if "http://" in lowered or "https://" in lowered or "api" in lowered or "model" in lowered or "version" in lowered:
        return "external_or_versioned"
    if "test" in lowered or "verified" in lowered or "file" in lowered:
        return "verified_observation"
    return "agent_observation"


def _review_after_for(decision: str, source_type: str, now: datetime) -> str:
    days = DEFAULT_REVIEW_DAYS
    if decision == "warn":
        days = WARN_REVIEW_DAYS
    if source_type == "external_or_versioned":
        days = min(days, EXTERNAL_REVIEW_DAYS)
    return isoformat(now + timedelta(days=days))


def _finalize_gate(kind: str, scores: Dict[str, int], reasons: List[str], suggestions: List[str], *, source_type: str, hard_decision: Optional[str] = None) -> Dict[str, Any]:
    total = sum(scores.values())
    decision = hard_decision
    if decision is None:
        if total >= 75:
            decision = "pass"
        elif total >= 60:
            decision = "warn"
        elif total >= 45:
            decision = "pending_review"
        else:
            decision = "block"
    now = utc_now()
    return {
        "kind": kind,
        "decision": decision,
        "score": total,
        "scores": scores,
        "reasons": reasons,
        "suggestions": suggestions,
        "source_type": source_type,
        "status": "active" if decision in ("pass", "warn") else decision,
        "review_after": _review_after_for(decision, source_type, now) if decision in ("pass", "warn", "pending_review") else None,
        "expires_at": None,
    }


def evaluate_memory_write(action: str, target: str, content: str, old_text: Optional[str], existing_entries: Iterable[str]) -> Dict[str, Any]:
    text = content or ""
    reasons: List[str] = []
    suggestions: List[str] = []
    source_type = _source_type_for_content(text)

    stability = 12
    if _contains_any(text, VERIFIED_PATTERNS):
        stability += 14
    if "durable" in text.lower() or "long-term" in text.lower() or "长期" in text:
        stability += 6
    if target == "user":
        stability += 4
    if _contains_any(text, TEMPORARY_PATTERNS):
        stability -= 18
        reasons.append("Content looks temporary or task-local rather than durable knowledge.")
    if _contains_any(text, SPECULATIVE_PATTERNS):
        stability -= 14
        reasons.append("Content looks speculative; durable memory should be verified.")
    stability = _score_component(stability, 30)

    reuse = 8
    if _contains_any(text, REUSABLE_PATTERNS):
        reuse += 12
    if "durable" in text.lower() or "long-term" in text.lower() or "长期" in text:
        reuse += 8
    if target == "user":
        reuse += 8
    if len(text.strip()) >= 25:
        reuse += 5
    if _contains_any(text, TEMPORARY_PATTERNS):
        reuse -= 12
    reuse = _score_component(reuse, 25)

    completeness = 8
    if _contains_any(text, COMPLETENESS_PATTERNS):
        completeness += 10
    if "durable" in text.lower() or "long-term" in text.lower() or "长期" in text:
        completeness += 4
    if ":" in text or "-" in text or "；" in text or ";" in text:
        completeness += 4
    if len(text.strip()) >= 20:
        completeness += 5
    if len(text.strip()) >= 80:
        completeness += 3
    completeness = _score_component(completeness, 25)

    conflict = 20
    conflict_type = "none"
    if action == "add":
        conflict_type, conflict_reasons, conflict_suggestions = _detect_memory_conflict(text, existing_entries)
        reasons.extend(conflict_reasons)
        suggestions.extend(conflict_suggestions)
        if conflict_type == "duplicate":
            conflict = 0
        elif conflict_type == "similar":
            conflict = 8
        elif conflict_type == "conflict":
            conflict = 0
    elif action == "replace":
        conflict = 18
        if not old_text:
            conflict = 0
            reasons.append("Replace operations need old_text to identify the superseded knowledge.")
    conflict = _score_component(conflict, 20)

    hard_decision = None
    if conflict_type == "duplicate":
        hard_decision = "block"
    elif conflict_type in ("similar", "conflict"):
        hard_decision = "suggest_replace"
    elif _contains_any(text, TEMPORARY_PATTERNS) and _contains_any(text, SPECULATIVE_PATTERNS):
        hard_decision = "block"

    if not reasons and hard_decision is None:
        reasons.append("Candidate passed deterministic durability, reuse, completeness, and conflict checks.")

    gate = _finalize_gate(
        "memory",
        {"stability": stability, "reuse": reuse, "completeness": completeness, "conflict": conflict},
        reasons,
        suggestions,
        source_type=source_type,
        hard_decision=hard_decision,
    )
    gate["conflict_type"] = conflict_type
    return gate


def _has_frontmatter(content: str) -> bool:
    return bool(content and content.startswith("---") and re.search(r"\n---\s*\n", content[3:]))


def _has_skill_structure(content: str) -> bool:
    lowered = (content or "").lower()
    has_trigger = any(token in lowered for token in ("use_when", "## use", "<use_when>", "purpose", "when "))
    has_procedure = any(token in lowered for token in ("steps", "## steps", "<steps>", "verification", "pitfalls", "examples"))
    return has_trigger and has_procedure


def evaluate_skill_change(
    action: str,
    name: str,
    content: Optional[str] = None,
    old_string: Optional[str] = None,
    new_string: Optional[str] = None,
    file_path: Optional[str] = None,
    file_content: Optional[str] = None,
) -> Dict[str, Any]:
    candidate = content if content is not None else (file_content if file_content is not None else new_string or "")
    reasons: List[str] = []
    suggestions: List[str] = []
    source_type = "procedural_skill"

    # Non-mutating/destructive policy stays with the existing tool. Quality gate records only.
    if action in ("delete", "remove_file"):
        return _finalize_gate(
            "skill",
            {"stability": 20, "reuse": 20, "completeness": 20, "conflict": 20},
            ["Destructive skill actions bypass quality scoring and rely on existing action policy."],
            [],
            source_type=source_type,
            hard_decision="pass",
        )

    stability = 14
    if _contains_any(candidate, VERIFIED_PATTERNS):
        stability += 10
    if _contains_any(candidate, TEMPORARY_PATTERNS) or _contains_any(candidate, SPECULATIVE_PATTERNS):
        stability -= 12
        reasons.append("Skill change appears temporary or speculative.")
    stability = _score_component(stability, 30)

    reuse = 12
    if _contains_any(candidate, REUSABLE_PATTERNS) or action in ("patch", "edit"):
        reuse += 10
    if "skill" in candidate.lower() or "workflow" in candidate.lower() or "steps" in candidate.lower():
        reuse += 3
    reuse = _score_component(reuse, 25)

    completeness = 8
    if action in ("create", "edit"):
        if _has_frontmatter(candidate):
            completeness += 7
        else:
            reasons.append("SKILL.md content is missing YAML frontmatter.")
        if _has_skill_structure(candidate):
            completeness += 8
        else:
            reasons.append("Skill content is missing reusable structure such as Use_When, Steps, or Verification guidance.")
            suggestions.append("Add trigger conditions, steps, and verification guidance to make the skill reusable.")
        if len(candidate.strip()) >= 120:
            completeness += 5
    elif action == "patch":
        if old_string and new_string is not None:
            completeness += 12
        if _contains_any(candidate, SKILL_STRUCTURE_PATTERNS + VERIFIED_PATTERNS):
            completeness += 5
    elif action == "write_file":
        if file_path and candidate.strip():
            completeness += 12
        if len(candidate.strip()) >= 40:
            completeness += 4
    completeness = _score_component(completeness, 25)

    conflict = 20
    if action == "create" and not name:
        conflict = 0
        reasons.append("Skill name is required for create.")
    conflict = _score_component(conflict, 20)

    hard_decision = None
    if action in ("create", "edit") and not _has_frontmatter(candidate):
        hard_decision = "block"
    elif action in ("create", "edit") and not _has_skill_structure(candidate):
        hard_decision = "block"
    elif action in ("create", "edit") and len(candidate.strip()) < 40:
        hard_decision = "block"
    elif _contains_any(candidate, TEMPORARY_PATTERNS) and _contains_any(candidate, SPECULATIVE_PATTERNS):
        hard_decision = "block"

    if not reasons and hard_decision is None:
        reasons.append("Skill change passed deterministic quality checks.")

    return _finalize_gate(
        "skill",
        {"stability": stability, "reuse": reuse, "completeness": completeness, "conflict": conflict},
        reasons,
        suggestions,
        source_type=source_type,
        hard_decision=hard_decision,
    )


def should_allow_write(gate: Dict[str, Any]) -> bool:
    return gate.get("decision") in ("pass", "warn")


def blocked_result(gate: Dict[str, Any], message: Optional[str] = None) -> Dict[str, Any]:
    decision = gate.get("decision", "block")
    return {
        "success": False,
        "error": message or f"Knowledge quality gate rejected this write ({decision}).",
        "quality_gate": gate,
    }


def record_quality_metadata(kind: str, identity: Dict[str, Any], content: str, gate: Dict[str, Any]) -> None:
    if gate.get("decision") not in ("pass", "warn"):
        return
    now = utc_now()
    with quality_index_lock():
        index = load_quality_index()
        items = index.setdefault("items", {})
        if kind == "memory":
            key = memory_item_key(str(identity.get("target", "memory")), content)
        else:
            key = skill_item_key(str(identity.get("name", "")), identity.get("file_path"), content)
            for old_key, old_item in items.items():
                if old_key == key or not isinstance(old_item, dict):
                    continue
                if (
                    old_item.get("kind") == "skill"
                    and old_item.get("name") == identity.get("name")
                    and old_item.get("file_path") == identity.get("file_path")
                    and old_item.get("status") not in ("deprecated", "superseded")
                ):
                    old_item["status"] = "superseded"
                    old_item["superseded_by"] = key
        items[key] = {
            "kind": kind,
            **identity,
            "content_hash": content_hash(content),
            "status": "active",
            "score": gate.get("score"),
            "scores": gate.get("scores", {}),
            "decision": gate.get("decision"),
            "source_type": gate.get("source_type"),
            "created_at": isoformat(now),
            "last_verified_at": isoformat(now),
            "review_after": gate.get("review_after"),
            "expires_at": gate.get("expires_at"),
            "reasons": gate.get("reasons", []),
            "supersedes": [],
            "superseded_by": None,
        }
        save_quality_index(index)
    append_audit_log({"event": "record", "key": key, "decision": gate.get("decision"), "score": gate.get("score")})


def _refresh_item_status(item: Dict[str, Any], now: datetime) -> bool:
    status = item.get("status", "active")
    if status in ("deprecated", "superseded", "pending_review", "block", "suggest_replace"):
        return False
    expires_at = parse_iso(item.get("expires_at"))
    if expires_at and expires_at <= now:
        item["status"] = "stale"
        return True
    review_after = parse_iso(item.get("review_after"))
    if review_after and review_after <= now:
        item["status"] = "needs_review"
        return True
    return False


def audit_due_knowledge(*, force: bool = False) -> Dict[str, Any]:
    now = utc_now()
    index = load_quality_index()
    last = parse_iso(index.get("last_audit_at"))
    due = force or last is None or (now - last) >= timedelta(hours=AUDIT_INTERVAL_HOURS)
    summary = {
        "ran": False,
        "last_audit_at": index.get("last_audit_at"),
        "stale_count": 0,
        "needs_review_count": 0,
        "deprecated_count": 0,
        "legacy_untracked_count": 0,
        "notes": [],
    }
    if not due:
        return summary

    with quality_index_lock():
        index = load_quality_index()
        for item in index.get("items", {}).values():
            if not isinstance(item, dict):
                continue
            before = item.get("status", "active")
            _refresh_item_status(item, now)
            after = item.get("status", "active")
            if after == "stale":
                summary["stale_count"] += 1
            elif after == "needs_review":
                summary["needs_review_count"] += 1
            elif after in ("deprecated", "superseded"):
                summary["deprecated_count"] += 1
            elif before in ("deprecated", "superseded"):
                summary["deprecated_count"] += 1

        index["last_audit_at"] = isoformat(now)
        save_quality_index(index)
    summary["ran"] = True
    summary["last_audit_at"] = index["last_audit_at"]
    if summary["stale_count"]:
        summary["notes"].append(f"{summary['stale_count']} stale knowledge entries were excluded from default context.")
    if summary["needs_review_count"]:
        summary["notes"].append(f"{summary['needs_review_count']} knowledge entries need review and were marked in context.")
    append_audit_log({"event": "audit", **summary})
    return summary


def filter_memory_entries(entries: List[str], target: str, audit_summary: Optional[Dict[str, Any]] = None) -> Tuple[List[Any], Dict[str, Any]]:
    index = load_quality_index()
    items = index.get("items", {})
    filtered: List[Any] = []
    excluded = 0
    marked = 0
    legacy = 0
    for entry in entries:
        key = memory_item_key(target, entry)
        meta = items.get(key)
        if not meta:
            legacy += 1
            filtered.append(entry)
            continue
        status = meta.get("status", "active")
        if status in ("stale", "deprecated", "superseded", "pending_review", "block", "suggest_replace"):
            excluded += 1
            continue
        if status == "needs_review":
            marked += 1
            filtered.append({"content": entry, "quality_status": "needs_review", "review_after": meta.get("review_after")})
        else:
            filtered.append(entry)
    summary = {
        "excluded_from_bundle": excluded,
        "marked_needs_review": marked,
        "legacy_untracked_count": legacy,
    }
    if audit_summary is not None:
        audit_summary["legacy_untracked_count"] = audit_summary.get("legacy_untracked_count", 0) + legacy
        if excluded:
            audit_summary.setdefault("notes", []).append(f"{excluded} {target} entries were excluded by quality status.")
        if legacy:
            audit_summary.setdefault("notes", []).append(f"{legacy} {target} entries have no quality metadata yet.")
    return filtered, summary


def filter_skill_candidates(candidates: List[Dict[str, Any]], audit_summary: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Filter or annotate skill candidates using skill quality metadata by name."""
    index = load_quality_index()
    skill_statuses: Dict[str, List[Dict[str, Any]]] = {}
    for item in index.get("items", {}).values():
        if not isinstance(item, dict) or item.get("kind") != "skill":
            continue
        name = str(item.get("name") or "")
        if not name:
            continue
        skill_statuses.setdefault(name, []).append(item)

    filtered: List[Dict[str, Any]] = []
    excluded = 0
    marked = 0
    legacy = 0
    for candidate in candidates:
        name = str(candidate.get("name") or "")
        metas = skill_statuses.get(name, [])
        if not metas:
            legacy += 1
            filtered.append(candidate)
            continue
        meta = _select_current_skill_metadata(metas)
        status = meta.get("status", "active")
        if status in ("stale", "deprecated", "superseded", "pending_review", "block", "suggest_replace"):
            excluded += 1
            continue
        if status == "needs_review":
            marked += 1
            annotated = dict(candidate)
            annotated["quality_status"] = "needs_review"
            annotated["review_after"] = meta.get("review_after")
            filtered.append(annotated)
        else:
            filtered.append(candidate)

    summary = {
        "excluded_from_bundle": excluded,
        "marked_needs_review": marked,
        "legacy_untracked_count": legacy,
    }
    if audit_summary is not None:
        audit_summary["legacy_untracked_count"] = audit_summary.get("legacy_untracked_count", 0) + legacy
        if excluded:
            audit_summary.setdefault("notes", []).append(f"{excluded} skill candidates were excluded by quality status.")
        if legacy:
            audit_summary.setdefault("notes", []).append(f"{legacy} skill candidates have no quality metadata yet.")
    return filtered, summary


def _select_current_skill_metadata(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pick the metadata row that best represents the current skill candidate."""
    status_rank = {
        "active": 0,
        "needs_review": 1,
        "warn": 1,
        "stale": 2,
        "pending_review": 3,
        "suggest_replace": 3,
        "block": 3,
        "deprecated": 4,
        "superseded": 5,
    }
    best = items[0]
    best_rank = status_rank.get(str(best.get("status") or "active"), 2)
    best_created = parse_iso(best.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)
    for item in items[1:]:
        rank = status_rank.get(str(item.get("status") or "active"), 2)
        created = parse_iso(item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)
        if rank < best_rank or (rank == best_rank and created > best_created):
            best = item
            best_rank = rank
            best_created = created
    return best
