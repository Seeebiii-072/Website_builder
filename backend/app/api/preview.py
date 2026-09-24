import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse
from sqlmodel import Session

from app.core.db import get_session
from app.core.events import event_bus
from app.services import project as project_service
from app.services import preview as preview_service

router = APIRouter(prefix="/api/projects", tags=["preview"])


@router.post("/{project_id}/preview/start")
async def start(project_id: str, session: Session = Depends(get_session)):
    project = project_service.get_project(session, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    row = await preview_service.start_preview(session, project)
    return row


@router.post("/{project_id}/preview/stop")
async def stop(project_id: str, session: Session = Depends(get_session)):
    project = project_service.get_project(session, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    await preview_service.stop_preview(session, project_id)
    return {"status": "stopped"}


@router.post("/{project_id}/preview/restart")
async def restart(project_id: str, session: Session = Depends(get_session)):
    project = project_service.get_project(session, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    row = await preview_service.restart_preview(session, project)
    return row


@router.get("/{project_id}/preview")
def get_preview(project_id: str, session: Session = Depends(get_session)):
    row = preview_service.get_preview(session, project_id)
    if row is None:
        raise HTTPException(404, "No preview for this project")
    return row


@router.get("/{project_id}/events")
async def events(project_id: str):
    """Server-Sent Events stream of status updates for a project."""
    queue = event_bus.subscribe(project_id)

    async def event_generator():
        try:
            while True:
                payload = await queue.get()
                data = json.loads(payload)
                yield {"event": data["type"], "data": json.dumps(data)}
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(project_id, queue)

    return EventSourceResponse(event_generator())
