# from sqlmodel import SQLModel, create_engine, Session
# from app.core.config import settings

# connect_args = {"check_same_thread": False}
# engine = create_engine(settings.database_url, echo=False, connect_args=connect_args)


# def init_db():
#     from app.models import models  # noqa: F401  (ensure models are registered)
#     SQLModel.metadata.create_all(engine)


# def get_session():
#     with Session(engine) as session:
#         yield session

from pathlib import Path

from sqlmodel import SQLModel, create_engine, Session
from app.core.config import settings


# Resolve database path safely for local + Railway environments
BASE_DIR = Path(__file__).resolve().parents[2]  # backend/
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_PATH = DATA_DIR / "app.db"

# SQLite URL using absolute path
DATABASE_URL = f"sqlite:///{DATABASE_PATH.as_posix()}"

connect_args = {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args=connect_args,
)


def init_db():
    from app.models import models  # noqa: F401
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session