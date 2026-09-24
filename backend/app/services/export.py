import zipfile
from pathlib import Path

EXCLUDED_DIRS = {"node_modules", ".git", ".next", "dist", "build"}
EXCLUDED_NAMES = {".env"}
EXCLUDED_PREFIXES = (".env.",)


def _should_exclude(rel_parts: tuple[str, ...]) -> bool:
    for part in rel_parts:
        if part in EXCLUDED_DIRS or part in EXCLUDED_NAMES:
            return True
        if any(part.startswith(p) for p in EXCLUDED_PREFIXES):
            return True
    return False


def create_project_zip(workspace: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(workspace.rglob("*")):
            if p.is_dir():
                continue
            rel = p.relative_to(workspace)
            if _should_exclude(rel.parts):
                continue
            zf.write(p, arcname=str(rel))
    return output_path
