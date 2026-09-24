# AI Website Builder

Describe a website in plain language; the system generates a real Next.js
codebase with an LLM, writes it to disk, installs dependencies, builds it,
starts a real dev server on a dynamic port, and shows it in a live iframe.
You can then ask the AI for changes, watch it edit the actual files, rebuild,
and refresh the preview — and export the whole project as a ZIP.

## What's real here vs. what needs your setup

Every piece of this — the path-traversal/secret-file protection, the
OpenRouter→Gemini fallback logic, the JSON validation, the build pipeline,
and the live preview server — was written and then **actually executed** in
a sandbox while building this: a real minimal Next.js project was installed,
built, and served on a dynamically-found port through this exact code, and a
12-test pytest suite (provider fallback, path traversal, sensitive-file
blocking, ZIP exclusions) passes.

What was **not** tested end-to-end is the full "type a prompt → AI writes a
whole site" flow, because that requires a real `OPENROUTER_API_KEY` or
`GEMINI_API_KEY`, which I don't have. Once you add a key, that flow should
work as designed, but you're the first to run it against a live model —
watch the chat panel and backend logs for anything unexpected the first
few times.

## Prerequisites

- Python 3.10+
- Node.js 18+ and npm (needed both to run the frontend, and because the
  backend shells out to `npm install` / `npm run build` / `npm run dev`
  *inside each generated project*)
- An OpenRouter API key (https://openrouter.ai/keys) and/or a Gemini API key
  (https://aistudio.google.com/apikey). At least one is required for AI
  generation to work.

## 1. Backend setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and fill in at least one of:

```
OPENROUTER_API_KEY=sk-or-...
GEMINI_API_KEY=...
```

`LLM_PRIMARY_PROVIDER` controls which one is tried first (default: `gemini`,
falling back to OpenRouter). Whichever provider fails — for a network error,
an HTTP error, *or* because it returned truncated/invalid JSON — the other
one is tried automatically before generation is reported as failed.

Run the backend:

```bash
python -m uvicorn app.main:app --reload --port 8000
```

The API is now at `http://localhost:8000` (docs at `/docs`).

### Run the backend test suite

```bash
pytest tests/ -v
```

This covers: OpenRouter success, OpenRouter failure → Gemini fallback, both
providers failing, JSON parsing/validation, path-traversal protection,
sensitive-file blocking, and ZIP export exclusions — all with mocked HTTP
calls, so no API key is needed to run these.

## 2. Frontend setup

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

Open `http://localhost:3000`. `.env.local` points the frontend at the
backend (`NEXT_PUBLIC_API_URL=http://localhost:8000`) — change this if your
backend runs elsewhere.

## 3. How to test AI generation

1. With both servers running, open `http://localhost:3000`.
2. Type: `Create a modern coffee shop website called Brew Haven.`
3. Click **Create Website**. You'll be routed to `/projects/{id}`.
4. Watch the chat panel: it streams `generation_started` →
   `generation_completed` → `build_started` → `build_completed` →
   `preview_starting` → `preview_ready` via Server-Sent Events
   (`GET /api/projects/{id}/events`).
5. If `OPENROUTER_API_KEY` is invalid/unset, you should see it fall back to
   Gemini automatically (check backend logs for
   `LLM provider failed | provider=openrouter | ...`); if both are
   unset/invalid you'll get a clear "AI generation failed" message with the
   reason from each provider — never a silent failure.

## 4. How to test the live preview

Once `preview_ready` fires, the center panel loads
`http://127.0.0.1:<dynamic-port>` in a real iframe — open the browser dev
tools Network tab and you'll see requests going to that port, and you can
also open it directly in a new tab. This is a real `next dev` server
started by the backend as a subprocess (`app/services/preview.py`), not a
static mock — you can verify by editing a generated file yourself on disk
and refreshing.

## 5. How to test AI editing

In the chat input, type something like:

```
Add a pricing section below the hero.
```

The backend loads the current project's files, sends them plus your request
to the LLM (`POST /api/projects/{id}/edit`), writes only the changed files,
rebuilds, and restarts the preview. The chat panel shows
`ai_edit_started` → `ai_edit_completed` → `build_*` → `preview_ready`, and
the iframe refreshes automatically.

## 6. How to test ZIP export

Click **Export ZIP** in the top bar, or hit
`GET /api/projects/{id}/export` directly. The ZIP excludes `node_modules`,
`.git`, `.next`, `dist`/`build`, and any `.env*` file, and includes
everything needed to run the project independently:

```bash
unzip Brew_Haven.zip -d brew-haven
cd brew-haven
npm install
npm run dev
```

## 7. Build-failure auto-fix

If `npm run build` fails inside a generated project, the backend sends the
build error, the current files, and the original prompt back to the LLM and
asks for corrected files (`MAX_DEBUG_ATTEMPTS`, default 3, in `.env`). The
chat panel shows each attempt; if all attempts fail, the project is marked
`FAILED` with the last build log available via the `Build` table / logs.

## Known limitations

- **Free-tier models can still truncate mid-response.** Many free OpenRouter
  models cap output at a fixed size (e.g. 4K-8K tokens) regardless of what
  `MODEL_MAX_OUTPUT_TOKENS` requests, especially for a large multi-file
  Next.js payload. When that happens the JSON comes back cut off mid-string,
  which now correctly triggers fallback to the other provider (see
  `test_falls_back_on_truncated_json_not_just_network_errors` in
  `tests/test_core.py`) instead of silently failing — but if *both*
  configured providers are small free models, both can still truncate on a
  large enough site and generation will fail with "invalid/truncated JSON"
  from each. If you hit this consistently: ask for a smaller site first
  (fewer sections), raise `MODEL_MAX_OUTPUT_TOKENS`, or use a model with a
  larger output cap for at least one of the two providers.
- **No live-server persistence across restarts**: preview process handles
  are held in memory (`_RUNNING_PROCESSES` in `preview.py`); if you restart
  the backend, previously running preview servers become orphaned (their
  DB row will still say `RUNNING` until you call `/preview/restart`). A
  production version would want a reconciliation step on startup.
- **No file editing in the UI yet**: the code viewer is read-only, per the
  original spec ("Do not allow the user to edit files directly unless this
  feature is intentionally implemented later").
- **Single global `MAX_DEBUG_ATTEMPTS`**: applies per full build cycle, not
  configurable per project from the UI.
- **No auth**: this is a local single-user tool as specified; there's no
  login, and anyone who can reach the backend port can create/edit/export
  any project.
- **End-to-end AI generation is not something I could run in the sandbox
  I built this in** — I don't have an OpenRouter or Gemini key. I verified
  every stage of the pipeline works (including a real Next.js build and a
  real live dev-server preview using a hand-written test project), and the
  provider-fallback code path against mocked failures, but the actual
  "LLM writes a full site" call is untested against a live model. Please
  watch closely the first time you run it for real.
- **Windows note**: the spec calls out `npm.cmd` on Windows; both
  `build.py` and `preview.py` already look for `npm.cmd` if `npm` isn't
  found on PATH, but this hasn't been tested on Windows itself (built and
  tested on Linux).

## Project structure

```
backend/
  app/
    main.py              # FastAPI app, CORS, router registration
    core/
      config.py           # env-driven settings
      security.py          # path traversal / secret-file protection
      db.py                 # SQLModel engine/session
      events.py             # SSE pub/sub
    models/models.py     # Project, Build, Preview tables + status enums
    services/
      llm.py                 # OpenRouter -> Gemini provider abstraction
      project.py            # generate/edit/fix orchestration
      build.py               # npm install/build + AI auto-fix retry loop
      preview.py            # dynamic port, subprocess mgmt, health check
      export.py              # ZIP creation with exclusions
    tools/files.py        # validate/write/read generated files safely
    api/
      projects.py           # POST /api/projects, /generate, /edit
      files.py                 # file tree + file content
      preview.py             # start/stop/restart + SSE endpoint
      export.py               # ZIP download
  tests/test_core.py     # 12 tests: provider fallback, security, export
  requirements.txt
  .env.example

frontend/
  app/
    page.tsx                       # project creation screen
    projects/[id]/page.tsx   # three-panel workspace + SSE wiring
  components/
    ChatPanel.tsx, PreviewPanel.tsx, FileExplorer.tsx, TopBar.tsx, StatusBadge.tsx
  lib/api.ts                       # typed API client
  package.json
  .env.local.example
```
