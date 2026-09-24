from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session

from app.core.db import get_session
from app.services import project as project_service
from app.services.export import create_project_zip

router = APIRouter(prefix="/api/projects", tags=["export"])


@router.get("/{project_id}/export")
def export_project(project_id: str, session: Session = Depends(get_session)):
    project = project_service.get_project(session, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    workspace = Path(project.workspace_path)
    if not workspace.exists():
        raise HTTPException(400, "Project has no generated files yet")

    out_dir = workspace.parent / "_exports"
    out_path = out_dir / f"{project.id}.zip"
    create_project_zip(workspace, out_path)
    return FileResponse(
        path=str(out_path),
        filename=f"{project.name.replace(' ', '_')}.zip",
        media_type="application/zip",
    )
