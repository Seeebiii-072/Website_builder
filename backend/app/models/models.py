import uuid
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


def gen_id() -> str:
    return uuid.uuid4().hex[:12]


class ProjectStatus:
    CREATING = "CREATING"
    GENERATING = "GENERATING"
    BUILDING = "BUILDING"
    READY = "READY"
    FAILED = "FAILED"


class PreviewStatus:
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    FAILED = "FAILED"


class BuildStatus:
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class Project(SQLModel, table=True):
    id: str = Field(default_factory=gen_id, primary_key=True)
    name: str
    prompt: str
    status: str = Field(default=ProjectStatus.CREATING)
    workspace_path: str
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Build(SQLModel, table=True):
    id: str = Field(default_factory=gen_id, primary_key=True)
    project_id: str = Field(index=True)
    status: str = Field(default=BuildStatus.PENDING)
    attempt: int = 0
    logs: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None


class Preview(SQLModel, table=True):
    project_id: str = Field(primary_key=True)
    port: Optional[int] = None
    pid: Optional[int] = None
    status: str = Field(default=PreviewStatus.STOPPED)
    url: Optional[str] = None
    error_message: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
