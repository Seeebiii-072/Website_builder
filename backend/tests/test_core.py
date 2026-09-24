"""
Automated tests covering: LLM provider fallback, JSON parsing/validation,
filesystem security (path traversal), and ZIP export exclusion rules.

Run with: pytest tests/ -v
"""
import json
import zipfile
from pathlib import Path
from unittest.mock import patch, AsyncMock

import httpx
import pytest

from app.services import llm
from app.tools.files import validate_files_payload, write_files, InvalidGenerationError
from app.core.security import resolve_safe_path, UnsafePathError
from app.services.export import create_project_zip
from app.core.events import EventBus


# ---------- LLM provider tests ----------

def test_extract_json_strips_markdown_fences():
    raw = '```json\n{"files": [{"path": "a.txt", "content": "hi"}]}\n```'
    parsed = llm._extract_json(raw)
    assert parsed["files"][0]["path"] == "a.txt"


@pytest.mark.asyncio
async def test_openrouter_success(monkeypatch):
    monkeypatch.setattr(llm.settings, "openrouter_api_key", "fake-key")
    monkeypatch.setattr(llm.settings, "gemini_api_key", "")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": '{"files":[{"path":"x.txt","content":"y"}]}'}}]}

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=FakeResp())):
        payload, provider = await llm.generate_json("build me a site")
        assert provider == "openrouter"
        assert payload["files"][0]["path"] == "x.txt"


@pytest.mark.asyncio
async def test_openrouter_timeout_falls_back_to_gemini(monkeypatch):
    monkeypatch.setattr(llm.settings, "openrouter_api_key", "fake-key")
    monkeypatch.setattr(llm.settings, "gemini_api_key", "fake-gemini-key")

    class FakeGeminiResp:
        status_code = 200
        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": '{"files":[{"path":"g.txt","content":"z"}]}'}]}}]}

    async def fake_post(self, url, **kwargs):
        if "openrouter" in url:
            raise httpx.TimeoutException("simulated timeout")
        return FakeGeminiResp()

    with patch("httpx.AsyncClient.post", new=fake_post):
        payload, provider = await llm.generate_json("build me a site")
        assert provider == "gemini"
        assert payload["files"][0]["path"] == "g.txt"


@pytest.mark.asyncio
async def test_both_providers_fail_raises(monkeypatch):
    monkeypatch.setattr(llm.settings, "openrouter_api_key", "fake-key")
    monkeypatch.setattr(llm.settings, "gemini_api_key", "fake-gemini-key")

    async def fake_post(self, url, **kwargs):
        raise httpx.ConnectError("simulated connection error")

    with patch("httpx.AsyncClient.post", new=fake_post):
        with pytest.raises(llm.LLMAllProvidersFailedError) as exc_info:
            await llm.generate_json("build me a site")
        assert len(exc_info.value.errors) == 2


@pytest.mark.asyncio
async def test_gemini_is_tried_first_when_configured_as_primary(monkeypatch):
    monkeypatch.setattr(llm.settings, "openrouter_api_key", "fake-key")
    monkeypatch.setattr(llm.settings, "gemini_api_key", "fake-gemini-key")
    monkeypatch.setattr(llm.settings, "llm_primary_provider", "gemini")

    calls = []

    async def fake_post(self, url, **kwargs):
        calls.append(url)
        if "generativelanguage" in url:
            class FakeResp:
                status_code = 200
                def json(self):
                    return {"candidates": [{"content": {"parts": [{"text": '{"files":[{"path":"g.txt","content":"z"}]}'}]}}]}
            return FakeResp()
        raise AssertionError("OpenRouter should not be called when Gemini succeeds as primary")

    with patch("httpx.AsyncClient.post", new=fake_post):
        payload, provider = await llm.generate_json("build me a site")
        assert provider == "gemini"
        assert len(calls) == 1  # OpenRouter was never even attempted


@pytest.mark.asyncio
async def test_falls_back_on_truncated_json_not_just_network_errors(monkeypatch):
    """
    Reproduces the real-world bug: a provider returns HTTP 200 with output
    that got cut off mid-string (e.g. it hit its own max-output-token cap).
    That must be treated as a failure and trigger fallback to the next
    provider, not silently propagate as a generation failure with no retry.
    """
    monkeypatch.setattr(llm.settings, "openrouter_api_key", "fake-key")
    monkeypatch.setattr(llm.settings, "gemini_api_key", "fake-gemini-key")
    monkeypatch.setattr(llm.settings, "llm_primary_provider", "openrouter")

    truncated = '{"files": [{"path": "app/page.tsx", "content": "export default function Page() { return <div>Hello'  # cut off mid-string, no closing

    async def fake_post(self, url, **kwargs):
        if "openrouter" in url:
            class FakeResp:
                status_code = 200
                def json(self):
                    return {"choices": [{"message": {"content": truncated}}]}
            return FakeResp()
        class FakeGeminiResp:
            status_code = 200
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": '{"files":[{"path":"g.txt","content":"z"}]}'}]}}]}
        return FakeGeminiResp()

    with patch("httpx.AsyncClient.post", new=fake_post):
        payload, provider = await llm.generate_json("build me a site")
        assert provider == "gemini"
        assert payload["files"][0]["path"] == "g.txt"


@pytest.mark.asyncio
async def test_both_providers_truncated_json_raises_all_failed(monkeypatch):
    monkeypatch.setattr(llm.settings, "openrouter_api_key", "fake-key")
    monkeypatch.setattr(llm.settings, "gemini_api_key", "fake-gemini-key")

    truncated = '{"files": [{"path": "a.txt", "content": "unterminated'

    async def fake_post(self, url, **kwargs):
        class FakeResp:
            status_code = 200
            def json(self):
                if "openrouter" in url:
                    return {"choices": [{"message": {"content": truncated}}]}
                return {"candidates": [{"content": {"parts": [{"text": truncated}]}}]}
        return FakeResp()

    with patch("httpx.AsyncClient.post", new=fake_post):
        with pytest.raises(llm.LLMAllProvidersFailedError) as exc_info:
            await llm.generate_json("build me a site")
        assert len(exc_info.value.errors) == 2
        assert all(e.exc_type == "InvalidJSON" for e in exc_info.value.errors)


# ---------- Filesystem security tests ----------

def test_path_traversal_rejected():
    with pytest.raises(InvalidGenerationError):
        validate_files_payload({"files": [{"path": "../../etc/passwd", "content": "pwned"}]})


def test_sensitive_file_rejected():
    with pytest.raises(InvalidGenerationError):
        validate_files_payload({"files": [{"path": ".env", "content": "SECRET=1"}]})


def test_absolute_path_rejected():
    with pytest.raises(InvalidGenerationError):
        validate_files_payload({"files": [{"path": "/etc/passwd", "content": "pwned"}]})


def test_valid_files_write_correctly(tmp_path):
    files = validate_files_payload({"files": [
        {"path": "package.json", "content": '{"name":"test"}'},
        {"path": "app/page.tsx", "content": "export default function Page(){return null}"},
    ]})
    written = write_files(tmp_path, files)
    assert written == ["package.json", "app/page.tsx"]
    assert (tmp_path / "app/page.tsx").exists()


def test_resolve_safe_path_blocks_escape(tmp_path):
    with pytest.raises(UnsafePathError):
        resolve_safe_path(tmp_path, "../outside.txt")


def test_missing_path_or_content_rejected():
    with pytest.raises(InvalidGenerationError):
        validate_files_payload({"files": [{"path": "a.txt"}]})
    with pytest.raises(InvalidGenerationError):
        validate_files_payload({"files": [{"content": "hi"}]})


def test_invalid_top_level_shape_rejected():
    with pytest.raises(InvalidGenerationError):
        validate_files_payload({"not_files": []})
    with pytest.raises(InvalidGenerationError):
        validate_files_payload({"files": []})


# ---------- ZIP export tests ----------

def test_zip_export_excludes_sensitive_and_heavy_dirs(tmp_path):
    workspace = tmp_path / "proj"
    (workspace / "node_modules" / "pkg").mkdir(parents=True)
    (workspace / "node_modules" / "pkg" / "index.js").write_text("noop")
    (workspace / "app").mkdir(parents=True)
    (workspace / "app" / "page.tsx").write_text("export default function Page(){return null}")
    (workspace / "package.json").write_text('{"name":"test"}')
    (workspace / ".env").write_text("SECRET=1")

    out = tmp_path / "out.zip"
    create_project_zip(workspace, out)

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "package.json" in names
        assert "app/page.tsx" in names
        assert not any("node_modules" in n for n in names)
        assert ".env" not in names


# ---------- SSE event bus tests ----------

@pytest.mark.asyncio
async def test_late_subscriber_receives_replayed_events():
    """
    A browser that finishes navigating *after* events already fired (e.g. AI
    generation fails almost instantly due to a missing API key) must still
    see those events instead of the UI hanging forever.
    """
    bus = EventBus()
    await bus.publish("proj1", "generation_started", {})
    await bus.publish("proj1", "generation_failed", {"error": "no api key"})

    q = bus.subscribe("proj1")
    assert q.qsize() == 2

    first = json.loads(await q.get())
    second = json.loads(await q.get())
    assert first["type"] == "generation_started"
    assert second["type"] == "generation_failed"


@pytest.mark.asyncio
async def test_events_for_different_projects_dont_leak():
    bus = EventBus()
    await bus.publish("proj1", "generation_started", {})
    await bus.publish("proj2", "generation_started", {})

    q = bus.subscribe("proj1")
    assert q.qsize() == 1
