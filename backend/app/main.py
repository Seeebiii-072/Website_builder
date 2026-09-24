import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.db import init_db
from app.api import projects, files, preview, export
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="AI Website Builder API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:3000", "http://127.0.0.1:3000","https://frontend-liart-pi-89.vercel.app",
        "https://websitebuilder-production-fa9a.up.railway.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()


app.include_router(projects.router)
app.include_router(files.router)
app.include_router(preview.router)
app.include_router(export.router)


@app.get("/health")
def health():
    return {"status": "ok"}
