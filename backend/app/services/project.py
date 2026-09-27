import logging
from pathlib import Path

from sqlmodel import Session

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

IMPORTANT BUILD AND RESOURCE CONSTRAINTS:

1. Generate a lightweight, production-ready Next.js application.
2. The application will be built inside a Linux container with only 1 GB RAM.
3. Keep the project simple and efficient so `npm install` and `npm run build`
   can complete within a low-memory environment.
4. Keep package.json dependencies minimal.
5. ONLY add a dependency if the generated code actually imports and uses it.
6. Prefer built-in React, Next.js, semantic HTML, CSS, and existing Tailwind
   utilities over external libraries.
7. DO NOT add heavy libraries such as:
   - framer-motion
   - large charting libraries
   - carousel/slider libraries
   - large state-management libraries
   - unnecessary UI component libraries
   - unnecessary icon packages
   - animation libraries
   - large utility packages
   unless the user's request explicitly requires them.
8. Do not add unnecessary API routes, databases, SDKs, server-side data
   fetching, or external services for a static website.
9. Prefer static content and server-renderable components whenever possible.
10. Avoid generating large datasets, huge JSON files, large inline SVGs,
    unnecessary images, or duplicated content.
11. Keep the number of pages and components reasonable for the user's request.
12. Do not generate unnecessary files.
13. Use a simple and valid Next.js App Router structure.
14. Keep package versions mutually compatible.
15. Do not add custom webpack configuration unless absolutely necessary.
16. Do not add build plugins unless they are required by the application.
17. Avoid unnecessary TypeScript complexity.
18. Make sure all imports point to files that actually exist.
19. Make sure all referenced assets exist or use safe remote/image URLs.
20. The final project must be able to run:

    npm install
    npm run build

    inside a Linux environment with approximately 1 GB RAM.

DESIGN REQUIREMENTS:

- Create a polished, modern, responsive website.
- Use clean component structure.
- Use accessible semantic HTML.
- Make the UI visually appealing without relying on heavy dependencies.
- Use CSS/Tailwind for styling rather than external UI libraries whenever possible.
- Ensure mobile responsiveness.
- Do not sacrifice the requested design, but implement it using lightweight
  technologies.

OUTPUT REQUIREMENT:

Respond ONLY with the complete JSON file payload in the required format.
The payload must contain the complete contents of all required project files.
"""


EDIT_PROMPT_TEMPLATE = """Here is the CURRENT project source code
(path: content pairs):

{files_context}

The user wants this change:

\"\"\"{message}\"\"\"

Modify the existing project to satisfy the user's request.

IMPORTANT:

- Preserve the existing architecture unless a change is required.
- Do not rewrite unrelated files.
- Return ONLY files that need to be created or modified.
- Do not return unchanged files.
- Keep dependencies minimal.
- Do NOT introduce heavy libraries unless the user's requested feature
  genuinely requires one.
- Prefer existing dependencies and existing components.
- Prefer React/Next.js/CSS/Tailwind solutions over adding packages.
- Do not add unnecessary API routes, databases, SDKs, or build plugins.
- Make sure every new import resolves correctly.
- Make sure the project remains compatible with:
    npm install
    npm run build
- Keep the project suitable for a Linux container with approximately 1 GB RAM.

Return a JSON object with a "files" array containing ONLY the files that need
to be created or modified to satisfy the request (full new content for each,
not diffs). Do not include unchanged files.
"""


FIX_PROMPT_TEMPLATE = """The generated Next.js project failed to build.

Original user request:

\"\"\"{original_prompt}\"\"\"

Current project files:

{files_context}

Build error output:

{build_error}

IMPORTANT BUILD-FIX RULES:

1. First identify the actual cause of the build failure.
2. Fix ONLY the files responsible for the reported problem.
3. Do not rewrite the entire project unnecessarily.
4. Do not introduce new dependencies unless absolutely required.
5. Prefer fixing the existing code using the current dependencies.
6. Keep the project lightweight and compatible with a 1 GB RAM build
   environment.
7. Do not add heavy libraries just to solve a simple issue.
8. Do not modify unrelated files.
9. Ensure every import resolves correctly.
10. Ensure the final project can run:
       npm install
       npm run build

RESOURCE LIMIT RULE:

If the build output contains any of the following:

- "Killed"
- exit code 137
- "out of memory"
- "JavaScript heap out of memory"
- "heap out of memory"
- "ENOMEM"

treat this primarily as a resource/memory limitation rather than an
application-code error.

In that situation:

- Do NOT randomly rewrite application code.
- Do NOT add dependencies.
- Do NOT add heavy libraries.
- Do NOT generate large files.
- Only make a change if there is a clear, minimal configuration/code reason
  that can directly reduce build memory usage.

Return a JSON object with a "files" array containing ONLY the corrected files
(full new content, not diffs) needed to fix this build error.
"""


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


def set_status(
    session: Session,
    project: Project,
    status: str,
    error_message: str | None = None,
):
    project.status = status
    project.error_message = error_message

    session.add(project)
    session.commit()
    session.refresh(project)


async def generate_website(
    session: Session,
    project: Project,
):
    """Core generation step: LLM -> validate -> write files."""

    set_status(
        session,
        project,
        ProjectStatus.GENERATING,
    )

    await event_bus.publish(
        project.id,
        "generation_started",
        {},
    )

    user_prompt = GENERATE_PROMPT_TEMPLATE.format(
        prompt=project.prompt
    )

    try:
        payload, provider = await llm.generate_json(user_prompt)

    except llm.LLMAllProvidersFailedError as e:
        msg = f"AI generation failed. {e}"

        set_status(
            session,
            project,
            ProjectStatus.FAILED,
            msg,
        )

        await event_bus.publish(
            project.id,
            "generation_failed",
            {"error": msg},
        )

        raise

    except ValueError as e:
        msg = f"AI generation failed: {e}"

        set_status(
            session,
            project,
            ProjectStatus.FAILED,
            msg,
        )

        await event_bus.publish(
            project.id,
            "generation_failed",
            {"error": msg},
        )

        raise

    try:
        files = validate_files_payload(payload)

    except InvalidGenerationError as e:
        msg = f"AI returned an invalid project structure: {e}"

        set_status(
            session,
            project,
            ProjectStatus.FAILED,
            msg,
        )

        await event_bus.publish(
            project.id,
            "generation_failed",
            {"error": msg},
        )

        raise

    workspace = Path(project.workspace_path)

    written = write_files(
        workspace,
        files,
    )

    await event_bus.publish(
        project.id,
        "generation_completed",
        {
            "provider": provider,
            "files_written": written,
        },
    )

    return written


async def edit_website(
    session: Session,
    project: Project,
    message: str,
):
    """AI edit workflow: load context, ask LLM for changed files."""

    await event_bus.publish(
        project.id,
        "ai_edit_started",
        {
            "message": message,
        },
    )

    workspace = Path(project.workspace_path)

    context_files = read_all_files_for_context(
        workspace
    )

    files_context = "\n\n".join(
        f"--- {f['path']} ---\n{f['content']}"
        for f in context_files
    )

    user_prompt = EDIT_PROMPT_TEMPLATE.format(
        files_context=files_context,
        message=message,
    )

    try:
        payload, provider = await llm.generate_json(
            user_prompt
        )

    except (
        llm.LLMAllProvidersFailedError,
        ValueError,
    ) as e:

        msg = f"AI edit failed: {e}"

        await event_bus.publish(
            project.id,
            "ai_edit_failed",
            {
                "error": msg,
            },
        )

        raise

    try:
        files = validate_files_payload(
            payload
        )

    except InvalidGenerationError as e:

        msg = f"AI edit returned an invalid file set: {e}"

        await event_bus.publish(
            project.id,
            "ai_edit_failed",
            {
                "error": msg,
            },
        )

        raise

    written = write_files(
        workspace,
        files,
    )

    await event_bus.publish(
        project.id,
        "ai_edit_completed",
        {
            "provider": provider,
            "files_changed": written,
        },
    )

    return written


async def fix_build_error(
    session: Session,
    project: Project,
    build_error: str,
    attempt: int,
):
    """Ask the LLM to fix files given a build error."""

    await event_bus.publish(
        project.id,
        "ai_fix_started",
        {
            "attempt": attempt,
        },
    )

    workspace = Path(project.workspace_path)

    context_files = read_all_files_for_context(
        workspace
    )

    files_context = "\n\n".join(
        f"--- {f['path']} ---\n{f['content']}"
        for f in context_files
    )

    user_prompt = FIX_PROMPT_TEMPLATE.format(
        original_prompt=project.prompt,
        files_context=files_context,
        build_error=build_error[-6000:],
    )

    payload, provider = await llm.generate_json(
        user_prompt
    )

    files = validate_files_payload(
        payload
    )

    written = write_files(
        workspace,
        files,
    )

    await event_bus.publish(
        project.id,
        "ai_fix_completed",
        {
            "provider": provider,
            "files_changed": written,
            "attempt": attempt,
        },
    )

    return written
