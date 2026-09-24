from sqlmodel import SQLModel, create_engine, Session
from app.core.config import settings

connect_args = {"check_same_thread": False}
engine = create_engine(settings.database_url, echo=False, connect_args=connect_args)


def init_db():
    from app.models import models  # noqa: F401  (ensure models are registered)
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
