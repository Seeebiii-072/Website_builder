from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.core.db import get_session
from app.core.security import UnsafePathError
from app.services import project as project_service
from app.tools.files import list_file_tree, read_file_content

router = APIRouter(prefix="/api/projects", tags=["files"])


@router.get("/{project_id}/files")
def get_file_tree(project_id: str, session: Session = Depends(get_session)):
    project = project_service.get_project(session, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    workspace = Path(project.workspace_path)
    if not workspace.exists():
        return []
    return list_file_tree(workspace)


@router.get("/{project_id}/files/{path:path}")
def get_file(project_id: str, path: str, session: Session = Depends(get_session)):
    project = project_service.get_project(session, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    workspace = Path(project.workspace_path)
    try:
        content = read_file_content(workspace, path)
    except UnsafePathError as e:
        raise HTTPException(403, str(e))
    except FileNotFoundError:
        raise HTTPException(404, "File not found")
    return {"path": path, "content": content}
