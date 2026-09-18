"""小白 · Personal Context OS — 后端入口"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.routers import (
    briefing, capture, chat, inbox, projects, raw, reminders, schedule, scheduler, tasks, watch, wiki,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # 启动时确保库表就绪
    yield


app = FastAPI(title="小白 · Personal Context OS", version="0.1.0", lifespan=lifespan)

# 本地前端开发端口
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(capture.router)
app.include_router(inbox.router)
app.include_router(tasks.router)
app.include_router(projects.router)
app.include_router(wiki.router)
app.include_router(reminders.router)
app.include_router(chat.router)
app.include_router(raw.router)
app.include_router(watch.router)
app.include_router(schedule.router)
app.include_router(scheduler.router)
app.include_router(briefing.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "xiaobai"}
