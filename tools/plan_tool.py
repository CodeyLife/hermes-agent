#!/usr/bin/env python3
"""
Deterministic plan artifacts for MCP clients.

These helpers intentionally do not invoke Hermes's agent runtime or bundled
``/plan`` skill. They provide a thin, stateless file-backed contract that lets
external MCP clients such as Trae create, read, and update markdown plans in
the active workspace.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.skill_commands import build_plan_path


_HEADING_RE = re.compile(r"^#+\s+")


def _workspace_root() -> Path:
    return Path.cwd()


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _plans_dir() -> Path:
    path = _workspace_root() / ".hermes" / "plans"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _normalize_relpath(path: Path) -> str:
    return path.as_posix()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        dir=str(path.parent),
        prefix=f".{path.name}.tmp-",
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _coerce_lines(values: Optional[List[Any]]) -> List[str]:
    lines: List[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text:
            lines.append(text)
    return lines


def _section(title: str, values: List[str], *, bullets: bool = True) -> List[str]:
    if not values:
        return []
    lines = [f"## {title}", ""]
    if bullets:
        lines.extend(f"- {value}" for value in values)
    else:
        lines.extend(values)
    lines.append("")
    return lines


def _build_markdown_plan(
    *,
    task: str,
    goal: Optional[str] = None,
    context: Optional[List[Any]] = None,
    approach: Optional[List[Any]] = None,
    steps: Optional[List[Any]] = None,
    files: Optional[List[Any]] = None,
    tests: Optional[List[Any]] = None,
    risks: Optional[List[Any]] = None,
) -> str:
    task = str(task or "").strip()
    goal = str(goal or "").strip()
    if not task:
        raise ValueError("task is required")

    lines: List[str] = [f"# Plan — {task}", ""]
    if goal:
        lines.extend(["## Goal", "", goal, ""])

    lines.extend(_section("Current context / assumptions", _coerce_lines(context)))
    lines.extend(_section("Proposed approach", _coerce_lines(approach), bullets=False))
    lines.extend(_section("Step-by-step plan", _coerce_lines(steps)))
    lines.extend(_section("Files likely to change", _coerce_lines(files)))
    lines.extend(_section("Tests / validation", _coerce_lines(tests)))
    lines.extend(_section("Risks / tradeoffs", _coerce_lines(risks)))

    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + "\n"


def create_plan(
    *,
    task: str,
    goal: Optional[str] = None,
    context: Optional[List[Any]] = None,
    approach: Optional[List[Any]] = None,
    steps: Optional[List[Any]] = None,
    files: Optional[List[Any]] = None,
    tests: Optional[List[Any]] = None,
    risks: Optional[List[Any]] = None,
    content: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a markdown plan in ``.hermes/plans`` under the current workspace."""
    body = str(content or "").strip()
    if body:
        markdown = body + ("\n" if not body.endswith("\n") else "")
    else:
        markdown = _build_markdown_plan(
            task=task,
            goal=goal,
            context=context,
            approach=approach,
            steps=steps,
            files=files,
            tests=tests,
            risks=risks,
        )

    relative_path = build_plan_path(task)
    absolute_path = _workspace_root() / relative_path
    _atomic_write(absolute_path, markdown)
    return {
        "success": True,
        "task": task,
        "path": _normalize_relpath(relative_path),
        "absolute_path": str(absolute_path),
        "content": markdown,
    }


def _resolve_plan_path(path: Optional[str], latest: bool = False) -> tuple[Optional[Path], Optional[str]]:
    if path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = _workspace_root() / candidate
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        plans_dir = _plans_dir().resolve()
        try:
            resolved.relative_to(plans_dir)
        except ValueError:
            return None, "Plan path must stay within the workspace .hermes/plans directory."
        if not resolved.exists() or not resolved.is_file():
            return None, f"Plan not found: {path}"
        return resolved, None

    if latest:
        plans = sorted(_plans_dir().glob("*.md"))
        if not plans:
            return None, "No plans exist in .hermes/plans."
        return plans[-1], None

    return None, "Either path or latest=true is required."


def read_plan(*, path: Optional[str] = None, latest: bool = False) -> Dict[str, Any]:
    """Read a plan from ``.hermes/plans``."""
    resolved, error = _resolve_plan_path(path, latest=latest)
    if error:
        return {"success": False, "error": error}

    content = resolved.read_text(encoding="utf-8")
    try:
        rel = resolved.relative_to(_workspace_root())
    except ValueError:
        rel = resolved

    title = ""
    for line in content.splitlines():
        if _HEADING_RE.match(line.strip()):
            title = line.strip()
            break

    return {
        "success": True,
        "path": _normalize_relpath(rel),
        "absolute_path": str(resolved),
        "title": title,
        "content": content,
    }


def update_plan(*, path: str, content: str) -> Dict[str, Any]:
    """Replace the contents of an existing plan."""
    resolved, error = _resolve_plan_path(path, latest=False)
    if error:
        return {"success": False, "error": error}

    new_content = str(content or "").strip()
    if not new_content:
        return {"success": False, "error": "content is required"}

    normalized = new_content + ("\n" if not new_content.endswith("\n") else "")
    _atomic_write(resolved, normalized)
    try:
        rel = resolved.relative_to(_workspace_root())
    except ValueError:
        rel = resolved
    return {
        "success": True,
        "path": _normalize_relpath(rel),
        "absolute_path": str(resolved),
        "content": normalized,
    }


def read_bundled_plan_skill() -> Dict[str, Any]:
    """Return the repo-bundled Hermes ``/plan`` skill for Trae guidance."""
    skill_path = _repo_root() / "skills" / "software-development" / "plan" / "SKILL.md"
    if not skill_path.exists():
        return {"success": False, "error": "Bundled Hermes /plan skill not found."}
    content = skill_path.read_text(encoding="utf-8")
    return {
        "success": True,
        "name": "plan",
        "path": _normalize_relpath(skill_path.relative_to(_repo_root())),
        "content": content,
    }
