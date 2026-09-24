import logging
from pathlib import Path

from sqlmodel import Session, select

from app.core.config import settings
from app.core.events import event_bus
from app.models.models import Project, ProjectStatus
from app.services import llm
from app.tools.files import (
    InvalidGenerationError,
    validate_files_payload,
    write_files,
    read_all_files_for_context,
)

logger = logging.getLogger("project")

GENERATE_PROMPT_TEMPLATE = """Build a complete website for this request:

\"\"\"{prompt}\"\"\"

Respond with the full JSON file payload as instructed."""

EDIT_PROMPT_TEMPLATE = """Here is the CURRENT project source code (path: content pairs):

{files_context}

The user wants this change:
\"\"\"{message}\"\"\"

Return a JSON object with a "files" array containing ONLY the files that need
to be created or modified to satisfy the request (full new content for each,
not diffs). Do not include unchanged files."""

FIX_PROMPT_TEMPLATE = """The generated Next.js project failed to build.

Original user request:
\"\"\"{original_prompt}\"\"\"

Current project files:

{files_context}

Build error output:
{build_error}

Return a JSON object with a "files" array containing ONLY the corrected files
(full new content, not diffs) needed to fix this build error."""


def create_project(session: Session, name: str, prompt: str) -> Project:
    project = Project(
        name=name,
        prompt=prompt,
        status=ProjectStatus.CREATING,
        workspace_path="",
    )
    session.add(project)
    session.commit()
    session.refresh(project)

    workspace = settings.projects_path / project.id
    workspace.mkdir(parents=True, exist_ok=True)
    project.workspace_path = str(workspace)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def get_project(session: Session, project_id: str) -> Project | None:
    return session.get(Project, project_id)


def set_status(session: Session, project: Project, status: str, error_message: str | None = None):
    project.status = status
    project.error_message = error_message
    session.add(project)
    session.commit()
    session.refresh(project)


async def generate_website(session: Session, project: Project):
    """Core generation step: LLM -> validate -> write files."""
    set_status(session, project, ProjectStatus.GENERATING)
    await event_bus.publish(project.id, "generation_started", {})

    user_prompt = GENERATE_PROMPT_TEMPLATE.format(prompt=project.prompt)

    try:
        payload, provider = await llm.generate_json(user_prompt)
    except llm.LLMAllProvidersFailedError as e:
        msg = f"AI generation failed. {e}"
        set_status(session, project, ProjectStatus.FAILED, msg)
        await event_bus.publish(project.id, "generation_failed", {"error": msg})
        raise
    except ValueError as e:
        msg = f"AI generation failed: {e}"
        set_status(session, project, ProjectStatus.FAILED, msg)
        await event_bus.publish(project.id, "generation_failed", {"error": msg})
        raise

    try:
        files = validate_files_payload(payload)
    except InvalidGenerationError as e:
        msg = f"AI returned an invalid project structure: {e}"
        set_status(session, project, ProjectStatus.FAILED, msg)
        await event_bus.publish(project.id, "generation_failed", {"error": msg})
        raise

    workspace = Path(project.workspace_path)
    written = write_files(workspace, files)

    await event_bus.publish(
        project.id, "generation_completed", {"provider": provider, "files_written": written}
    )
    return written


async def edit_website(session: Session, project: Project, message: str):
    """AI edit workflow: load context, ask LLM for a diff-like file set, write changes."""
    await event_bus.publish(project.id, "ai_edit_started", {"message": message})

    workspace = Path(project.workspace_path)
    context_files = read_all_files_for_context(workspace)
    files_context = "\n\n".join(
        f"--- {f['path']} ---\n{f['content']}" for f in context_files
    )

    user_prompt = EDIT_PROMPT_TEMPLATE.format(files_context=files_context, message=message)

    try:
        payload, provider = await llm.generate_json(user_prompt)
    except (llm.LLMAllProvidersFailedError, ValueError) as e:
        msg = f"AI edit failed: {e}"
        await event_bus.publish(project.id, "ai_edit_failed", {"error": msg})
        raise

    try:
        files = validate_files_payload(payload)
    except InvalidGenerationError as e:
        msg = f"AI edit returned an invalid file set: {e}"
        await event_bus.publish(project.id, "ai_edit_failed", {"error": msg})
        raise

    written = write_files(workspace, files)
    await event_bus.publish(
        project.id, "ai_edit_completed", {"provider": provider, "files_changed": written}
    )
    return written


async def fix_build_error(session: Session, project: Project, build_error: str, attempt: int):
    """Ask the LLM to fix files given a build error, and write the corrections."""
    await event_bus.publish(project.id, "ai_fix_started", {"attempt": attempt})

    workspace = Path(project.workspace_path)
    context_files = read_all_files_for_context(workspace)
    files_context = "\n\n".join(
        f"--- {f['path']} ---\n{f['content']}" for f in context_files
    )

    user_prompt = FIX_PROMPT_TEMPLATE.format(
        original_prompt=project.prompt,
        files_context=files_context,
        build_error=build_error[-6000:],
    )

    payload, provider = await llm.generate_json(user_prompt)
    files = validate_files_payload(payload)
    written = write_files(workspace, files)

    await event_bus.publish(
        project.id, "ai_fix_completed", {"provider": provider, "files_changed": written, "attempt": attempt}
    )
    return written
