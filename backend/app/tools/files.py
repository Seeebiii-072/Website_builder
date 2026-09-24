"""Validation for LLM-generated file payloads, and safe filesystem writes."""
from pathlib import Path
from typing import Any

from app.core.security import validate_generated_file_path, resolve_safe_path, UnsafePathError


class InvalidGenerationError(Exception):
    pass


def validate_files_payload(payload: Any) -> list[dict]:
    """Validate the {"files": [...]} shape returned by the LLM."""
    if not isinstance(payload, dict):
        raise InvalidGenerationError("Model response is not a JSON object")
    files = payload.get("files")
    if not isinstance(files, list) or len(files) == 0:
        raise InvalidGenerationError("Model response missing non-empty 'files' array")

    validated = []
    for i, f in enumerate(files):
        if not isinstance(f, dict):
            raise InvalidGenerationError(f"files[{i}] is not an object")
        path = f.get("path")
        content = f.get("content")
        if not path or not isinstance(path, str):
            raise InvalidGenerationError(f"files[{i}] is missing a valid 'path'")
        if content is None or not isinstance(content, str):
            raise InvalidGenerationError(f"files[{i}] ({path}) is missing string 'content'")
        try:
            validate_generated_file_path(path)
        except UnsafePathError as e:
            raise InvalidGenerationError(f"files[{i}] rejected: {e}")
        validated.append({"path": path.replace("\\", "/"), "content": content})
    return validated


def write_files(workspace_root: Path, files: list[dict]) -> list[str]:
    """Write validated files to the workspace. Returns list of written relative paths."""
    written = []
    for f in files:
        target = resolve_safe_path(workspace_root, f["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f["content"], encoding="utf-8")
        written.append(f["path"])
    return written


def list_file_tree(workspace_root: Path) -> list[dict]:
    """Return a flat list of {path, type} for the whole workspace, skipping heavy/hidden dirs."""
    skip_dirs = {"node_modules", ".git", ".next", "dist", "build"}
    results = []
    for p in sorted(workspace_root.rglob("*")):
        rel_parts = p.relative_to(workspace_root).parts
        if any(part in skip_dirs for part in rel_parts):
            continue
        if any(part.startswith(".") and part not in (".env.example",) for part in rel_parts):
            # allow dotfiles like .gitignore to be listed, but not .env etc (handled by security layer too)
            pass
        rel = p.relative_to(workspace_root).as_posix()
        from app.core.security import is_sensitive_path
        if is_sensitive_path(rel):
            continue
        results.append({"path": rel, "type": "dir" if p.is_dir() else "file"})
    return results


def read_file_content(workspace_root: Path, rel_path: str) -> str:
    target = resolve_safe_path(workspace_root, rel_path)
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(rel_path)
    return target.read_text(encoding="utf-8", errors="replace")


def read_all_files_for_context(workspace_root: Path, max_bytes: int = 60_000) -> list[dict]:
    """Read project files back for sending to the LLM as edit context, capped in size."""
    skip_dirs = {"node_modules", ".git", ".next", "dist", "build"}
    files = []
    total = 0
    for p in sorted(workspace_root.rglob("*")):
        if p.is_dir():
            continue
        rel_parts = p.relative_to(workspace_root).parts
        if any(part in skip_dirs for part in rel_parts):
            continue
        rel = p.relative_to(workspace_root).as_posix()
        from app.core.security import is_sensitive_path
        if is_sensitive_path(rel):
            continue
        try:
            content = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        total += len(content)
        if total > max_bytes:
            break
        files.append({"path": rel, "content": content})
    return files
