import asyncio
import logging
import shutil
import subprocess
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

    raise RuntimeError(
        "npm was not found on PATH. Install Node.js/npm to run builds."
    )


async def _run_subprocess(
    cmd: list[str],
    cwd: str,
    timeout: int = 300,
) -> tuple[int, str]:

    logger.info("Running command: %s", " ".join(cmd))
    logger.info("Working directory: %s", cwd)

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
                env={
                    **__import__("os").environ,
                    "CI": "true",
                    "NEXT_TELEMETRY_DISABLED": "1",
                },
            )

            return result.returncode, result.stdout

        except subprocess.TimeoutExpired as exc:
            output = exc.stdout or ""

            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")

            return -1, (
                f"Process timed out after {timeout} seconds.\n"
                f"{output}"
            )

        except Exception as exc:
            return -1, f"{type(exc).__name__}: {exc}"

    code, output = await asyncio.to_thread(run)

    logger.info(
        "Command finished: exit_code=%s output_tail=%s",
        code,
        output[-2000:],
    )

    return code, output


async def install_dependencies(workspace: Path) -> tuple[bool, str]:
    npm = _npm_executable()

    package_json = workspace / "package.json"

    if not package_json.exists():
        return False, "package.json not found in generated project"

    logger.info("Installing dependencies...")
    logger.info("Workspace: %s", workspace)

    code, output = await _run_subprocess(
        [
            npm,
            "install",
            "--no-audit",
            "--no-fund",
            "--prefer-offline",
        ],
        cwd=str(workspace),
        timeout=300,
    )

    return code == 0, output


async def run_build(workspace: Path) -> tuple[bool, str]:
    npm = _npm_executable()

    logger.info("Starting Next.js production build...")

    code, output = await _run_subprocess(
        [
            npm,
            "run",
            "build",
        ],
        cwd=str(workspace),
        timeout=300,
    )

    return code == 0, output


async def build_with_autofix(
    session: Session,
    project: Project,
) -> bool:

    workspace = Path(project.workspace_path)

    logger.info(
        "Starting build pipeline for project=%s workspace=%s",
        project.id,
        workspace,
    )

    project_service.set_status(
        session,
        project,
        ProjectStatus.BUILDING,
    )

    await event_bus.publish(
        project.id,
        "build_started",
        {},
    )

    build_row = Build(
        project_id=project.id,
        status=BuildStatus.RUNNING,
        attempt=0,
    )

    session.add(build_row)
    session.commit()

    # ---------------------------------------------------------
    # STEP 1: npm install
    # ---------------------------------------------------------

    logger.info("STEP 1/2: Installing dependencies")

    ok, install_log = await install_dependencies(workspace)

    if not ok:

        logger.error(
            "Dependency installation failed:\n%s",
            install_log[-8000:],
        )

        build_row.status = BuildStatus.FAILED
        build_row.logs = install_log[-8000:]

        session.add(build_row)
        session.commit()

        await event_bus.publish(
            project.id,
            "build_failed",
            {
                "stage": "install",
                "log": install_log[-4000:],
            },
        )

        project_service.set_status(
            session,
            project,
            ProjectStatus.FAILED,
            "Dependency installation failed",
        )

        return False

    logger.info("STEP 1/2: npm install completed successfully")

    # ---------------------------------------------------------
    # STEP 2: Build + auto-fix
    # ---------------------------------------------------------

    attempt = 0
    last_log = ""

    while attempt < settings.max_debug_attempts:

        attempt += 1

        logger.info(
            "STEP 2/2: Starting build attempt %s/%s",
            attempt,
            settings.max_debug_attempts,
        )

        build_row.attempt = attempt

        session.add(build_row)
        session.commit()

        ok, log = await run_build(workspace)

        last_log = log

        if ok:

            logger.info(
                "Build SUCCESS project=%s attempt=%s",
                project.id,
                attempt,
            )

            build_row.status = BuildStatus.SUCCESS
            build_row.logs = log[-8000:]

            from datetime import datetime

            build_row.completed_at = datetime.utcnow()

            session.add(build_row)
            session.commit()

            await event_bus.publish(
                project.id,
                "build_completed",
                {
                    "attempt": attempt,
                },
            )

            return True

        logger.error(
            "Build FAILED attempt=%s:\n%s",
            attempt,
            log[-8000:],
        )

        await event_bus.publish(
            project.id,
            "build_failed",
            {
                "stage": "build",
                "attempt": attempt,
                "max_attempts": settings.max_debug_attempts,
                "log": log[-4000:],
            },
        )

        if attempt >= settings.max_debug_attempts:
            break

        # -----------------------------------------------------
        # AI AUTO FIX
        # -----------------------------------------------------

        try:

            logger.info(
                "Running AI auto-fix attempt=%s",
                attempt,
            )

            await project_service.fix_build_error(
                session,
                project,
                log,
                attempt,
            )

        except Exception as e:

            logger.exception(
                "AI fix failed attempt=%s",
                attempt,
            )

            await event_bus.publish(
                project.id,
                "ai_fix_failed",
                {
                    "attempt": attempt,
                    "error": str(e),
                },
            )

            break

        # -----------------------------------------------------
        # Reinstall dependencies after AI modification
        # -----------------------------------------------------

        logger.info(
            "Reinstalling dependencies after AI fix..."
        )

        ok_install, install_log2 = await install_dependencies(
            workspace
        )

        if not ok_install:

            last_log = install_log2

            logger.error(
                "Dependency reinstall failed:\n%s",
                install_log2[-8000:],
            )

            break

    # ---------------------------------------------------------
    # FINAL FAILURE
    # ---------------------------------------------------------

    logger.error(
        "Build permanently failed after %s attempt(s)",
        attempt,
    )

    build_row.status = BuildStatus.FAILED
    build_row.logs = last_log[-8000:]

    session.add(build_row)
    session.commit()

    project_service.set_status(
        session,
        project,
        ProjectStatus.FAILED,
        f"Build failed after {attempt} attempt(s). "
        f"See build logs for details.",
    )

    return False
