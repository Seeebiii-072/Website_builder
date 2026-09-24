import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.core.db import get_session
from app.models.models import Project, ProjectStatus
from app.services import project as project_service
from app.services import build as build_service
from app.services import preview as preview_service
from app.services.llm import LLMAllProvidersFailedError

logger = logging.getLogger("api.projects")
router = APIRouter(prefix="/api/projects", tags=["projects"])


class CreateProjectRequest(BaseModel):
    name: str
    prompt: str


class ProjectResponse(BaseModel):
    id: str
    name: str
    prompt: str
    status: str
    error_message: str | None = None
    preview_url: str | None = None
    created_at: str
    updated_at: str


class EditRequest(BaseModel):
    message: str


def _serialize(session: Session, project: Project) -> ProjectResponse:
    preview = preview_service.get_preview(session, project.id)
    return ProjectResponse(
        id=project.id,
        name=project.name,
        prompt=project.prompt,
        status=project.status,
        error_message=project.error_message,
        preview_url=preview.url if preview else None,
        created_at=project.created_at.isoformat(),
        updated_at=project.updated_at.isoformat(),
    )


async def _run_full_pipeline(project_id: str):
    """Generate -> build (with autofix) -> start preview. Runs as a background task."""
    from app.core.db import engine
    from app.core.events import event_bus
    from sqlmodel import Session as SQLSession

    with SQLSession(engine) as session:
        project = project_service.get_project(session, project_id)
        if project is None:
            return
        try:
            try:
                await project_service.generate_website(session, project)
            except (LLMAllProvidersFailedError, ValueError):
                # already logged, status set, and an event published inside
                # generate_website itself - nothing more to do here.
                return

            session.refresh(project)
            ok = await build_service.build_with_autofix(session, project)
            if not ok:
                return

            session.refresh(project)
            await preview_service.start_preview(session, project)
        except Exception as e:
            # Catch-all so an unexpected bug (e.g. in the build/preview
            # services) can never leave a project stuck in GENERATING /
            # BUILDING forever with no terminal event fired.
            logger.exception("Unexpected error in generation pipeline for %s", project_id)
            project_service.set_status(
                session, project, ProjectStatus.FAILED, f"Unexpected error: {e}"
            )
            await event_bus.publish(project_id, "generation_failed", {"error": f"Unexpected error: {e}"})


@router.post("", response_model=ProjectResponse)
def create_project(req: CreateProjectRequest, session: Session = Depends(get_session)):
    if not req.name.strip() or not req.prompt.strip():
        raise HTTPException(400, "name and prompt are required")
    project = project_service.create_project(session, req.name.strip(), req.prompt.strip())
    return _serialize(session, project)


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, session: Session = Depends(get_session)):
    project = project_service.get_project(session, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return _serialize(session, project)


@router.post("/{project_id}/generate", response_model=ProjectResponse)
async def generate(project_id: str, session: Session = Depends(get_session)):
    project = project_service.get_project(session, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    asyncio.create_task(_run_full_pipeline(project_id))
    project_service.set_status(session, project, ProjectStatus.GENERATING)
    return _serialize(session, project)


@router.post("/{project_id}/edit", response_model=ProjectResponse)
async def edit(project_id: str, req: EditRequest, session: Session = Depends(get_session)):
    project = project_service.get_project(session, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    if not req.message.strip():
        raise HTTPException(400, "message is required")

    async def _edit_pipeline():
        from app.core.db import engine
        from app.core.events import event_bus
        from sqlmodel import Session as SQLSession
        with SQLSession(engine) as s:
            p = project_service.get_project(s, project_id)
            if p is None:
                return
            try:
                try:
                    await project_service.edit_website(s, p, req.message.strip())
                except Exception:
                    # ai_edit_failed already published inside edit_website
                    return
                s.refresh(p)
                ok = await build_service.build_with_autofix(s, p)
                if not ok:
                    return
                s.refresh(p)
                await preview_service.start_preview(s, p)
            except Exception as e:
                logger.exception("Unexpected error in edit pipeline for %s", project_id)
                await event_bus.publish(project_id, "ai_edit_failed", {"error": f"Unexpected error: {e}"})

    asyncio.create_task(_edit_pipeline())
    return _serialize(session, project)


@router.get("", response_model=list[ProjectResponse])
def list_projects(session: Session = Depends(get_session)):
    from sqlmodel import select
    projects = session.exec(select(Project).order_by(Project.created_at.desc())).all()
    return [_serialize(session, p) for p in projects]
