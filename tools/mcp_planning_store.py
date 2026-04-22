"""Profile-scoped storage helpers for MCP planning workflows."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from hermes_constants import display_hermes_home, get_hermes_home


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def slugify_task(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    text = text.strip("-")
    return text or "plan"


def make_plan_id(instruction: str) -> str:
    slug = slugify_task(instruction)[:80]
    stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    return f"{slug}-{stamp}"


@dataclass(frozen=True)
class PlanPaths:
    plan_id: str
    root: Path
    session_dir: Path
    plan_file: Path
    metadata_file: Path
    reviews_dir: Path
    contexts_dir: Path


def get_plan_paths(plan_id: str) -> PlanPaths:
    root = get_hermes_home() / "plan"
    session_dir = root / "sessions" / plan_id
    return PlanPaths(
        plan_id=plan_id,
        root=root,
        session_dir=session_dir,
        plan_file=session_dir / "plan.md",
        metadata_file=session_dir / "metadata.json",
        reviews_dir=session_dir / "reviews",
        contexts_dir=session_dir / "contexts",
    )


def ensure_plan_session(
    instruction: str,
    *,
    plan_id: Optional[str] = None,
    mode: str = "ralplan",
    interactive: bool = False,
    deliberate: bool = False,
) -> Dict[str, Any]:
    resolved_plan_id = plan_id or make_plan_id(instruction)
    paths = get_plan_paths(resolved_plan_id)
    paths.reviews_dir.mkdir(parents=True, exist_ok=True)
    paths.contexts_dir.mkdir(parents=True, exist_ok=True)
    now = _utc_now()
    metadata = {
        "plan_id": resolved_plan_id,
        "task_slug": slugify_task(instruction),
        "instruction": instruction,
        "mode": mode,
        "interactive": bool(interactive),
        "deliberate": bool(deliberate),
        "status": "draft",
        "iteration": 0,
        "latest_verdict": None,
        "created_at": _iso(now),
        "updated_at": _iso(now),
    }
    if paths.metadata_file.exists():
        metadata = read_metadata(resolved_plan_id)
    else:
        write_metadata(resolved_plan_id, metadata)
    return metadata


def read_metadata(plan_id: str) -> Dict[str, Any]:
    paths = get_plan_paths(plan_id)
    if not paths.metadata_file.exists():
        raise FileNotFoundError(f"Plan metadata not found: {plan_id}")
    return json.loads(paths.metadata_file.read_text(encoding="utf-8"))


def write_metadata(plan_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    paths = get_plan_paths(plan_id)
    paths.session_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(metadata)
    payload["plan_id"] = plan_id
    payload["updated_at"] = _iso(_utc_now())
    if "created_at" not in payload:
        payload["created_at"] = payload["updated_at"]
    paths.metadata_file.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return payload


def save_plan_markdown(
    plan_id: str,
    markdown: str,
    *,
    status: Optional[str] = None,
    iteration: Optional[int] = None,
) -> Dict[str, Any]:
    paths = get_plan_paths(plan_id)
    paths.session_dir.mkdir(parents=True, exist_ok=True)
    paths.plan_file.write_text(str(markdown or ""), encoding="utf-8")
    metadata = read_metadata(plan_id) if paths.metadata_file.exists() else {"plan_id": plan_id}
    if status is not None:
        metadata["status"] = status
    if iteration is not None:
        metadata["iteration"] = int(iteration)
    write_metadata(plan_id, metadata)
    return metadata


def read_plan_markdown(plan_id: str) -> str:
    paths = get_plan_paths(plan_id)
    if not paths.plan_file.exists():
        raise FileNotFoundError(f"Plan markdown not found: {plan_id}")
    return paths.plan_file.read_text(encoding="utf-8")


def save_review(
    plan_id: str,
    *,
    role: str,
    iteration: int,
    markdown: str,
    verdict: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_role = str(role or "").strip().lower()
    if normalized_role not in {"architect", "critic"}:
        raise ValueError("role must be 'architect' or 'critic'")
    paths = get_plan_paths(plan_id)
    paths.reviews_dir.mkdir(parents=True, exist_ok=True)
    review_path = paths.reviews_dir / f"{int(iteration)}-{normalized_role}.md"
    review_path.write_text(str(markdown or ""), encoding="utf-8")
    metadata = read_metadata(plan_id) if paths.metadata_file.exists() else {"plan_id": plan_id}
    metadata["iteration"] = int(iteration)
    if verdict:
        metadata["latest_verdict"] = verdict
        upper = verdict.upper()
        if upper == "APPROVE":
            metadata["status"] = "approved"
        elif upper in {"REVISE", "REJECT"}:
            metadata["status"] = "review"
    write_metadata(plan_id, metadata)
    return metadata


def read_review(plan_id: str, iteration: int, role: str) -> str:
    normalized_role = str(role or "").strip().lower()
    paths = get_plan_paths(plan_id)
    review_path = paths.reviews_dir / f"{int(iteration)}-{normalized_role}.md"
    if not review_path.exists():
        raise FileNotFoundError(
            f"Plan review not found: {plan_id} iteration={iteration} role={normalized_role}"
        )
    return review_path.read_text(encoding="utf-8")


def save_context_snapshot(
    plan_id: str,
    *,
    content: str,
    snapshot_id: Optional[str] = None,
) -> str:
    paths = get_plan_paths(plan_id)
    paths.contexts_dir.mkdir(parents=True, exist_ok=True)
    resolved_snapshot = snapshot_id or f"context-{_utc_now().strftime('%Y%m%dT%H%M%SZ')}"
    context_path = paths.contexts_dir / f"{resolved_snapshot}.md"
    context_path.write_text(str(content or ""), encoding="utf-8")
    return resolved_snapshot


def read_context_snapshot(plan_id: str, snapshot_id: str) -> str:
    paths = get_plan_paths(plan_id)
    context_path = paths.contexts_dir / f"{snapshot_id}.md"
    if not context_path.exists():
        raise FileNotFoundError(f"Plan context not found: {plan_id} snapshot={snapshot_id}")
    return context_path.read_text(encoding="utf-8")


def plan_storage_path_display(plan_id: str) -> str:
    return f"{display_hermes_home()}/plan/sessions/{plan_id}/"
