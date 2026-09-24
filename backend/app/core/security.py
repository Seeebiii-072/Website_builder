"""Filesystem safety helpers: prevent path traversal and protect sensitive files."""
from pathlib import Path

BLOCKED_NAMES = {
    ".env",
    ".git",
}
BLOCKED_PREFIXES = (
    ".env.",
)
BLOCKED_SUBSTRINGS = (
    "credentials",
    "private_key",
    "id_rsa",
    ".pem",
    "secret",
)


class UnsafePathError(Exception):
    pass


def is_sensitive_path(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    lower_full = rel_path.lower()
    for part in parts:
        if part in BLOCKED_NAMES:
            return True
        if any(part.startswith(p) for p in BLOCKED_PREFIXES):
            return True
    if any(s in lower_full for s in BLOCKED_SUBSTRINGS):
        return True
    return False


def resolve_safe_path(workspace_root: Path, rel_path: str) -> Path:
    """
    Resolve rel_path against workspace_root, guaranteeing the result stays
    inside workspace_root. Raises UnsafePathError otherwise.
    """
    if rel_path is None or rel_path == "":
        raise UnsafePathError("Empty path is not allowed")

    # Reject absolute paths and drive letters outright
    if rel_path.startswith("/") or rel_path.startswith("\\"):
        raise UnsafePathError(f"Absolute paths are not allowed: {rel_path}")
    if len(rel_path) > 1 and rel_path[1] == ":":
        raise UnsafePathError(f"Absolute paths are not allowed: {rel_path}")

    if is_sensitive_path(rel_path):
        raise UnsafePathError(f"Access to sensitive path is blocked: {rel_path}")

    workspace_root = workspace_root.resolve()
    candidate = (workspace_root / rel_path).resolve()

    try:
        candidate.relative_to(workspace_root)
    except ValueError:
        raise UnsafePathError(f"Path escapes project workspace: {rel_path}")

    return candidate


def validate_generated_file_path(rel_path: str) -> None:
    """Validate a path coming from the LLM before it is ever written to disk."""
    if not rel_path or not isinstance(rel_path, str):
        raise UnsafePathError("File path must be a non-empty string")
    if ".." in Path(rel_path).parts:
        raise UnsafePathError(f"Path traversal detected: {rel_path}")
    if rel_path.startswith("/") or rel_path.startswith("\\"):
        raise UnsafePathError(f"Absolute paths are not allowed: {rel_path}")
    if len(rel_path) > 1 and rel_path[1] == ":":
        raise UnsafePathError(f"Absolute paths are not allowed: {rel_path}")
    if is_sensitive_path(rel_path):
        raise UnsafePathError(f"Refusing to write sensitive path: {rel_path}")
