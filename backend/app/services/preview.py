import asyncio
import logging
import shutil
import socket
import sys
from pathlib import Path
from typing import Optional
import os
import subprocess
import httpx
from sqlmodel import Session

from app.core.config import settings
from app.core.events import event_bus
from app.models.models import Project, Preview, PreviewStatus, ProjectStatus
from app.services import project as project_service

logger = logging.getLogger("preview")

# In-memory registry of live subprocess handles, keyed by project_id.
# (Process objects can't be stored in SQLite; only pid/port/status/url go there.)
_RUNNING_PROCESSES: dict[str, asyncio.subprocess.Process] = {}


def _npm_executable() -> str:
    npm = shutil.which("npm")
    if npm:
        return npm
    npm_cmd = shutil.which("npm.cmd")
    if npm_cmd:
        return npm_cmd
    raise RuntimeError("npm was not found on PATH. Install Node.js/npm to run previews.")


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def find_available_port() -> int:
    for port in range(settings.preview_start_port, settings.preview_max_port + 1):
        if _port_is_free(port):
            return port
    raise RuntimeError(
        f"No available port between {settings.preview_start_port} and {settings.preview_max_port}"
    )


def _get_or_create_preview_row(session: Session, project_id: str) -> Preview:
    row = session.get(Preview, project_id)
    if row is None:
        row = Preview(project_id=project_id, status=PreviewStatus.STOPPED)
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


async def _wait_until_responsive(url: str, timeout_seconds: int = 60) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    async with httpx.AsyncClient(timeout=2) as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await client.get(url)
                if resp.status_code < 500:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
    return False


# async def start_preview(session: Session, project: Project) -> Preview:
#     workspace = Path(project.workspace_path)
#     row = _get_or_create_preview_row(session, project.id)

#     # Stop any existing process for this project first (restart semantics)
#     await stop_preview(session, project.id)

#     port = find_available_port()
#     row.status = PreviewStatus.STARTING
#     row.port = port
#     row.pid = None
#     row.url = None
#     row.error_message = None
#     session.add(row)
#     session.commit()
#     await event_bus.publish(project.id, "preview_starting", {"port": port})

#     npm = _npm_executable()
#     cmd = [npm, "run", "dev", "--", "--port", str(port), "--hostname", "127.0.0.1"]

#     try:
#         proc = await asyncio.create_subprocess_exec(
#             *cmd,
#             cwd=str(workspace),
#             stdout=asyncio.subprocess.PIPE,
#             stderr=asyncio.subprocess.STDOUT,
#         )
#     except OSError as e:
#         row.status = PreviewStatus.FAILED
#         row.error_message = f"Failed to start dev server: {e}"
#         session.add(row)
#         session.commit()
#         await event_bus.publish(project.id, "preview_failed", {"error": row.error_message})
#         return row

#     _RUNNING_PROCESSES[project.id] = proc
#     row.pid = proc.pid
#     session.add(row)
#     session.commit()

#     url = f"http://127.0.0.1:{port}"
#     responsive = await _wait_until_responsive(url, timeout_seconds=90)

#     if not responsive or proc.returncode is not None:
#         row.status = PreviewStatus.FAILED
#         row.error_message = "Preview server did not become responsive in time"
#         session.add(row)
#         session.commit()
#         await event_bus.publish(project.id, "preview_failed", {"error": row.error_message})
#         return row

#     row.status = PreviewStatus.RUNNING
#     row.url = url
#     session.add(row)
#     session.commit()
#     await event_bus.publish(project.id, "preview_ready", {"url": url})

#     # project is only "READY" once a live preview is actually confirmed
#     project_service.set_status(session, project, ProjectStatus.READY)
#     return row
async def start_preview(session: Session, project: Project) -> Preview:
    workspace = Path(project.workspace_path)
    row = _get_or_create_preview_row(session, project.id)

    # Stop any existing process for this project first (restart semantics)
    await stop_preview(session, project.id)

    port = find_available_port()

    row.status = PreviewStatus.STARTING
    row.port = port
    row.pid = None
    row.url = None
    row.error_message = None

    session.add(row)
    session.commit()

    await event_bus.publish(
        project.id,
        "preview_starting",
        {"port": port},
    )

    npm = _npm_executable()

    cmd = [
        npm,
        "run",
        "dev",
        "--",
        "--port",
        str(port),
        "--hostname",
        "127.0.0.1",
    ]

    try:
        # Windows-safe subprocess startup.
        # asyncio.create_subprocess_exec() can raise
        # NotImplementedError under the current Windows event loop.
        def start_process():
            return subprocess.Popen(
                cmd,
                cwd=str(workspace),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )

        proc = await asyncio.to_thread(start_process)

    except OSError as e:
        row.status = PreviewStatus.FAILED
        row.error_message = f"Failed to start dev server: {e}"

        session.add(row)
        session.commit()

        await event_bus.publish(
            project.id,
            "preview_failed",
            {"error": row.error_message},
        )

        return row

    except Exception as e:
        row.status = PreviewStatus.FAILED
        row.error_message = (
            f"Unexpected error while starting preview: "
            f"{type(e).__name__}: {e}"
        )

        session.add(row)
        session.commit()

        await event_bus.publish(
            project.id,
            "preview_failed",
            {"error": row.error_message},
        )

        return row

    _RUNNING_PROCESSES[project.id] = proc

    row.pid = proc.pid

    session.add(row)
    session.commit()

    url = f"http://127.0.0.1:{port}"

    responsive = await _wait_until_responsive(
        url,
        timeout_seconds=90,
    )

    if not responsive:
        row.status = PreviewStatus.FAILED
        row.error_message = (
            "Preview server did not become responsive in time"
        )

        # Clean up the process if it failed to become responsive.
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass

        _RUNNING_PROCESSES.pop(project.id, None)

        session.add(row)
        session.commit()

        await event_bus.publish(
            project.id,
            "preview_failed",
            {"error": row.error_message},
        )

        return row

    # Check process status AFTER responsiveness check.
    if proc.poll() is not None:
        row.status = PreviewStatus.FAILED
        row.error_message = (
            f"Preview server exited unexpectedly "
            f"with code {proc.returncode}"
        )

        _RUNNING_PROCESSES.pop(project.id, None)

        session.add(row)
        session.commit()

        await event_bus.publish(
            project.id,
            "preview_failed",
            {"error": row.error_message},
        )

        return row

    row.status = PreviewStatus.RUNNING
    row.url = url

    session.add(row)
    session.commit()

    await event_bus.publish(
        project.id,
        "preview_ready",
        {"url": url},
    )

    # Project becomes READY only after the live preview is confirmed.
    project_service.set_status(
        session,
        project,
        ProjectStatus.READY,
    )

    return row


# async def stop_preview(session: Session, project_id: str) -> None:
#     proc = _RUNNING_PROCESSES.pop(project_id, None)
#     if proc is not None and proc.returncode is None:
#         proc.terminate()
#         try:
#             await asyncio.wait_for(proc.wait(), timeout=10)
#         except asyncio.TimeoutError:
#             proc.kill()
#             await proc.wait()

#     row = session.get(Preview, project_id)
#     if row is not None:
#         row.status = PreviewStatus.STOPPED
#         row.pid = None
#         session.add(row)
#         session.commit()
#         await event_bus.publish(project_id, "preview_stopped", {})
async def stop_preview(session: Session, project_id: str) -> None:
    proc = _RUNNING_PROCESSES.pop(project_id, None)

    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()

            # Popen.wait() is blocking, so run it in a worker thread.
            await asyncio.wait_for(
                asyncio.to_thread(proc.wait),
                timeout=10,
            )

        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass

            await asyncio.to_thread(proc.wait)

        except Exception:
            try:
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass

    row = session.get(Preview, project_id)

    if row is not None:
        row.status = PreviewStatus.STOPPED
        row.pid = None

        session.add(row)
        session.commit()

        await event_bus.publish(
            project_id,
            "preview_stopped",
            {},
        )

async def restart_preview(session: Session, project: Project) -> Preview:
    return await start_preview(session, project)


def get_preview(session: Session, project_id: str) -> Optional[Preview]:
    return session.get(Preview, project_id)


def is_preview_running(project_id: str) -> bool:
    proc = _RUNNING_PROCESSES.get(project_id)
    return proc is not None and proc.returncode is None
