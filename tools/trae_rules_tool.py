#!/usr/bin/env python3
"""
Generate project rules that make Trae follow the Hermes MCP workflow.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, Optional


def _workspace_root() -> Path:
    return Path.cwd()


def _default_rules_path() -> Path:
    return _workspace_root() / ".trae" / "rules" / "hermes-mcp-workflow.md"


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


def build_trae_project_rules(*, project_name: Optional[str] = None) -> str:
    project_label = (project_name or "").strip() or "当前项目"
    content = f"""#必须遵守的规则
    
1. 复杂任务开始前，必须先调用 `task_context_bundle(...)`。
2. 如上下文不足，再按需调用 `skill_view_safe(...)` 或 `session_recall_search(...)`。
3. 规划任务前先调用 `plan_skill_read()`。
4. 形成方案后，必须调用 `plan(...)` 落盘；需要修改时再用 `plan_read(...)` / `plan_update(...)`。
5. 默认不写入记忆或技能；只有“明显能在未来重复帮助”的内容才允许沉淀。
6. 仅当内容是稳定事实、长期约定、明确用户偏好，且大概率跨任务复用时，才调用 `memory_write(...)`。
7. 仅当本次形成了“已验证”的可复用流程、重复修复模式，或确认需要改进现有技能时，才调用 `skill_create_or_patch(...)`。
"""
    if len(content) > 1000:
        raise ValueError(f"Generated rules exceed 1000 characters: {len(content)}")
    return content


def init_trae_project_rules(
    *,
    project_name: Optional[str] = None,
    path: Optional[str] = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    target = Path(path) if path else _default_rules_path()
    if not target.is_absolute():
        target = _workspace_root() / target

    content = build_trae_project_rules(project_name=project_name)
    if target.exists() and not overwrite:
        existing = target.read_text(encoding="utf-8")
        try:
            rel = target.relative_to(_workspace_root())
        except ValueError:
            rel = target
        return {
            "success": True,
            "created": False,
            "path": _normalize_relpath(rel),
            "absolute_path": str(target),
            "content": existing,
            "message": "Rules file already exists. Set overwrite=true to replace it.",
        }

    _atomic_write(target, content if content.endswith("\n") else content + "\n")
    try:
        rel = target.relative_to(_workspace_root())
    except ValueError:
        rel = target
    return {
        "success": True,
        "created": True,
        "path": _normalize_relpath(rel),
        "absolute_path": str(target),
        "content": content if content.endswith("\n") else content + "\n",
        "message": "Trae project rules initialized.",
    }
