import asyncio
import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sse_starlette.sse import EventSourceResponse
from sqlmodel import Session

from app.core.db import get_session
from app.core.events import event_bus
from app.services import project as project_service
from app.services import preview as preview_service


router = APIRouter(
    prefix="/api/projects",
    tags=["preview"],
)


@router.post("/{project_id}/preview/start")
async def start(
    project_id: str,
    session: Session = Depends(get_session),
):
    project = project_service.get_project(session, project_id)

    if project is None:
        raise HTTPException(
            status_code=404,
            detail="Project not found",
        )

    row = await preview_service.start_preview(
        session,
        project,
    )

    return row


@router.post("/{project_id}/preview/stop")
async def stop(
    project_id: str,
    session: Session = Depends(get_session),
):
    project = project_service.get_project(session, project_id)

    if project is None:
        raise HTTPException(
            status_code=404,
            detail="Project not found",
        )

    await preview_service.stop_preview(
        session,
        project_id,
    )

    return {"status": "stopped"}


@router.post("/{project_id}/preview/restart")
async def restart(
    project_id: str,
    session: Session = Depends(get_session),
):
    project = project_service.get_project(session, project_id)

    if project is None:
        raise HTTPException(
            status_code=404,
            detail="Project not found",
        )

    row = await preview_service.restart_preview(
        session,
        project,
    )

    return row


@router.get("/{project_id}/preview")
def get_preview(
    project_id: str,
    session: Session = Depends(get_session),
):
    row = preview_service.get_preview(
        session,
        project_id,
    )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="No preview for this project",
        )

    return row


# ============================================================
# PREVIEW PROXY
# ============================================================

async def _proxy_preview(
    project_id: str,
    request: Request,
    session: Session,
    path: str = "",
):
    """
    Proxy requests from the browser to the generated
    Next.js preview server running internally on Railway.

    Browser:
        /api/projects/{id}/preview/view

    Backend:
        http://127.0.0.1:{preview_port}
    """

    row = preview_service.get_preview(
        session,
        project_id,
    )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="No preview found for this project",
        )

    if not row.port:
        raise HTTPException(
            status_code=409,
            detail="Preview does not have a port",
        )

    if not preview_service.is_preview_running(project_id):
        raise HTTPException(
            status_code=409,
            detail="Preview server is not running",
        )

    # Internal Railway URL.
    target_url = f"http://127.0.0.1:{row.port}"

    if path:
        target_url += f"/{path}"

    # Preserve query parameters.
    query_string = request.url.query

    if query_string:
        target_url += f"?{query_string}"

    # Forward safe request headers.
    headers = {}

    for header_name in (
        "accept",
        "accept-language",
        "cache-control",
        "user-agent",
    ):
        value = request.headers.get(header_name)

        if value:
            headers[header_name] = value

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=5.0,
                read=60.0,
                write=30.0,
                pool=5.0,
            ),
            follow_redirects=False,
        ) as client:

            upstream = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
            )

    except httpx.ConnectError:
        raise HTTPException(
            status_code=502,
            detail="Preview server is not reachable",
        )

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Preview server timed out",
        )

    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Preview proxy error: {exc}",
        )

    # Don't forward hop-by-hop headers.
    excluded_headers = {
        "content-length",
        "content-encoding",
        "transfer-encoding",
        "connection",
        "keep-alive",
    }

    response_headers = {}

    for key, value in upstream.headers.items():
        if key.lower() not in excluded_headers:
            response_headers[key] = value

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )


# Main preview page
@router.get("/{project_id}/preview/view")
async def preview_view(
    project_id: str,
    request: Request,
    session: Session = Depends(get_session),
):
    return await _proxy_preview(
        project_id=project_id,
        request=request,
        session=session,
        path="",
    )


# Next.js assets / files
@router.get("/{project_id}/preview/view/{path:path}")
async def preview_view_path(
    project_id: str,
    path: str,
    request: Request,
    session: Session = Depends(get_session),
):
    return await _proxy_preview(
        project_id=project_id,
        request=request,
        session=session,
        path=path,
    )


@router.get("/{project_id}/events")
async def events(project_id: str):
    """Server-Sent Events stream of status updates for a project."""

    queue = event_bus.subscribe(project_id)

    async def event_generator():
        try:
            while True:
                payload = await queue.get()
                data = json.loads(payload)

                yield {
                    "event": data["type"],
                    "data": json.dumps(data),
                }

        except asyncio.CancelledError:
            pass

        finally:
            event_bus.unsubscribe(
                project_id,
                queue,
            )

    return EventSourceResponse(event_generator())
