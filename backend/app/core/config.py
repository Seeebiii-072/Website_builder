from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    # LLM - OpenRouter (primary)
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-3.5-sonnet"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # LLM - Gemini (fallback)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-1.5-pro"

    model_timeout_seconds: int = 60
    model_max_output_tokens: int = 16000
    model_temperature: float = 0.2

    # Which provider to try first: "gemini" or "openrouter". The other one is
    # used as the automatic fallback. Also falls back on invalid/truncated
    # JSON from the primary, not just network/HTTP failures.
    llm_primary_provider: str = "gemini"

    frontend_url: str = "http://localhost:3000"

    preview_start_port: int = 3001
    preview_max_port: int = 3100

    max_debug_attempts: int = 3

    database_url: str = "sqlite:///./data/app.db"
    projects_dir: str = "./data/projects"

    class Config:
        env_file = ".env"
        protected_namespaces = ("settings_",)

    @property
    def projects_path(self) -> Path:
        p = Path(self.projects_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
