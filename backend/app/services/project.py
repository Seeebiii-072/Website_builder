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
2. The application will be built inside a Linux container with approximately
   1 GB RAM.
3. The generated project MUST be optimized for low-memory builds.
4. Keep package.json dependencies minimal.
5. ONLY add a dependency when the generated code actually imports and uses it.
6. Prefer built-in React, Next.js, semantic HTML, CSS and Tailwind utilities.
7. Avoid unnecessary third-party libraries.
8. Do NOT add heavy libraries such as:
   - framer-motion
   - large charting libraries
   - carousel/slider libraries
   - large state-management libraries
   - unnecessary UI component libraries
   - unnecessary icon packages
   - unnecessary animation libraries
   unless the user's request explicitly requires them.
9. Do not add unnecessary API routes, databases, SDKs or server-side services
   for a static website.
10. Prefer static content and server-renderable components.
11. Avoid large generated datasets.
12. Avoid huge inline SVG files.
13. Avoid duplicated content.
14. Keep pages and components reasonably small.
15. Do not generate unnecessary files.
16. Use a simple Next.js App Router architecture.
17. Do not add custom webpack configuration.
18. Do not add unnecessary build plugins.
19. Keep package versions mutually compatible.
20. Do NOT intentionally use known vulnerable/deprecated Next.js versions.
21. Do NOT use Next.js 14.1.0.
22. Use a patched stable Next.js version compatible with the selected React
    version.
23. Make sure all imports point to files that actually exist.
24. Make sure referenced assets exist or use safe remote URLs.
25. Make sure the application can successfully run:

    npm install
    npm run build

    inside a Linux container with approximately 1 GB RAM.

DESIGN REQUIREMENTS:

- Create a polished modern responsive website.
- Use clean reusable components.
- Use accessible semantic HTML.
- Use CSS/Tailwind instead of heavy UI libraries whenever possible.
- Make the website mobile responsive.
- Keep the design visually rich while keeping implementation lightweight.

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
- Do NOT introduce heavy libraries unless the requested feature genuinely
  requires one.
- Prefer existing dependencies and components.
- Prefer React, Next.js, CSS and Tailwind solutions.
- Do not add unnecessary API routes, databases, SDKs or build plugins.
- Do not introduce vulnerable or deprecated framework versions.
- Do not downgrade the existing Next.js version.
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

1. Identify the actual cause of the build failure first.
2. Fix ONLY the files responsible for the reported problem.
3. Do not rewrite the entire project unnecessarily.
4. Do not introduce new dependencies unless absolutely required.
5. Prefer fixing existing code using existing dependencies.
6. Keep the project lightweight.
7. Do not add heavy libraries to solve simple issues.
8. Do not modify unrelated files.
9. Do not downgrade Next.js.
10. Do not introduce known vulnerable/deprecated Next.js versions.
11. Make sure every import resolves correctly.
12. Ensure the final project can run:

    npm install
    npm run build

RESOURCE LIMIT RULE:

If the build output contains:

- "Killed"
- exit code 137
- "out of memory"
- "JavaScript heap out of memory"
- "heap out of memory"
- "ENOMEM"
- "cannot allocate memory"

treat it primarily as a resource/memory limitation.

In that situation:

- Do NOT randomly rewrite application code.
- Do NOT add dependencies.
- Do NOT add heavy libraries.
- Do NOT generate large files.
- Do NOT make speculative application changes.

Only make a change if there is a clear, minimal configuration/code change
that directly reduces build resource usage.

Return a JSON object with a "files" array containing ONLY the corrected files
(full new content, not diffs) needed to fix this build error.
"""


def create_project(
    session: Session,
    name: str,
    prompt: str,
) -> Project:

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
    workspace.mkdir(
        parents=True,
        exist_ok=True,
    )

    project.workspace_path = str(workspace)

    session.add(project)
    session.commit()
    session.refresh(project)

    return project


def get_project(
    session: Session,
    project_id: str,
) -> Project | None:

    return session.get(
        Project,
        project_id,
    )


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
        prompt=project.prompt,
    )

    try:

        payload, provider = await llm.generate_json(
            user_prompt,
        )

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
            {
                "error": msg,
            },
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
            {
                "error": msg,
            },
        )

        raise

    try:

        files = validate_files_payload(
            payload,
        )

    except InvalidGenerationError as e:

        msg = (
            f"AI returned an invalid project structure: {e}"
        )

        set_status(
            session,
            project,
            ProjectStatus.FAILED,
            msg,
        )

        await event_bus.publish(
            project.id,
            "generation_failed",
            {
                "error": msg,
            },
        )

        raise

    workspace = Path(
        project.workspace_path,
    )

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
    """AI edit workflow."""

    await event_bus.publish(
        project.id,
        "ai_edit_started",
        {
            "message": message,
        },
    )

    workspace = Path(
        project.workspace_path,
    )

    context_files = read_all_files_for_context(
        workspace,
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
            user_prompt,
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
            payload,
        )

    except InvalidGenerationError as e:

        msg = (
            f"AI edit returned an invalid file set: {e}"
        )

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

    workspace = Path(
        project.workspace_path,
    )

    context_files = read_all_files_for_context(
        workspace,
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
        user_prompt,
    )

    files = validate_files_payload(
        payload,
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
