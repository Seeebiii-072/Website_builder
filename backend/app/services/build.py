import os
import subprocess
import asyncio
import json
import logging
import shutil
from pathlib import Path

from sqlmodel import Session

from app.core.config import settings
from app.core.events import event_bus
from app.models.models import Project, Build, BuildStatus, ProjectStatus
from app.services import project as project_service

logger = logging.getLogger("build")


def _npm_executable() -> str:
    npm = shutil.which("npm")
    if npm:
        return npm
    npm_cmd = shutil.which("npm.cmd")
    if npm_cmd:
        return npm_cmd
    raise RuntimeError("npm was not found on PATH. Install Node.js/npm to run builds.")


async def _run_subprocess(
    cmd: list[str],
    cwd: str,
    timeout: int = 600,
) -> tuple[int, str]:
    def run():
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
            )
            return result.returncode, result.stdout
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout or ""
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            return -1, f"Process timed out after {timeout} seconds.\n{output}"
        except Exception as exc:
            return -1, f"{type(exc).__name__}: {exc}"

    return await asyncio.to_thread(run)


async def install_dependencies(workspace: Path) -> tuple[bool, str]:
    npm = _npm_executable()
    if not (workspace / "package.json").exists():
        return False, "package.json not found in generated project"
    code, output = await _run_subprocess([npm, "install"], cwd=workspace, timeout=600)
    return code == 0, output


async def run_build(workspace: Path) -> tuple[bool, str]:
    npm = _npm_executable()
    code, output = await _run_subprocess([npm, "run", "build"], cwd=workspace, timeout=600)
    return code == 0, output


async def build_with_autofix(session: Session, project: Project) -> bool:
    """
    Install deps, build, and on failure ask the LLM to fix files and retry,
    up to settings.max_debug_attempts times. Returns True if build succeeded.
    """
    workspace = Path(project.workspace_path)
    project_service.set_status(session, project, ProjectStatus.BUILDING)
    await event_bus.publish(project.id, "build_started", {})

    build_row = Build(project_id=project.id, status=BuildStatus.RUNNING, attempt=0)
    session.add(build_row)
    session.commit()

    ok, install_log = await install_dependencies(workspace)
    if not ok:
        build_row.status = BuildStatus.FAILED
        build_row.logs = install_log[-8000:]
        session.add(build_row)
        session.commit()
        await event_bus.publish(project.id, "build_failed", {"stage": "install", "log": install_log[-4000:]})
        project_service.set_status(session, project, ProjectStatus.FAILED, "Dependency installation failed")
        return False

    attempt = 0
    last_log = ""
    while attempt < settings.max_debug_attempts:
        attempt += 1
        build_row.attempt = attempt
        session.add(build_row)
        session.commit()

        ok, log = await run_build(workspace)
        last_log = log
        if ok:
            build_row.status = BuildStatus.SUCCESS
            build_row.logs = log[-8000:]
            from datetime import datetime
            build_row.completed_at = datetime.utcnow()
            session.add(build_row)
            session.commit()
            await event_bus.publish(project.id, "build_completed", {"attempt": attempt})
            return True

        await event_bus.publish(
            project.id,
            "build_failed",
            {"stage": "build", "attempt": attempt, "max_attempts": settings.max_debug_attempts, "log": log[-4000:]},
        )

        if attempt >= settings.max_debug_attempts:
            break

        # Ask the AI to fix the code and retry
        try:
            await project_service.fix_build_error(session, project, log, attempt)
        except Exception as e:
            logger.error("Auto-fix attempt %s failed: %s", attempt, e)
            await event_bus.publish(project.id, "ai_fix_failed", {"attempt": attempt, "error": str(e)})
            break

        # dependencies may have changed after a fix
        ok_install, install_log2 = await install_dependencies(workspace)
        if not ok_install:
            last_log = install_log2
            break

    build_row.status = BuildStatus.FAILED
    build_row.logs = last_log[-8000:]
    session.add(build_row)
    session.commit()
    project_service.set_status(
        session, project, ProjectStatus.FAILED,
        f"Build failed after {attempt} attempt(s). See build logs for details.",
    )
    return False
