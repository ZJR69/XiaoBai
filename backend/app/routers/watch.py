"""dropzone 实时监听：watchdog 后台线程 + 变更计数端点（前端轮询提示）。

防污染契约：检测到变更只计数，绝不自动消化；提示后由用户决定。
"""
import threading
import time
from collections import deque

from fastapi import APIRouter

from app.routers.raw import DROPZONE

router = APIRouter(prefix="/api/raw", tags=["watch"])

# 最近变更事件（时间戳），用于前端提示「有新东西」
_recent_events: deque = deque(maxlen=100)
_lock = threading.Lock()

_started = False


def _on_event(event):
    from watchdog.events import FileSystemEventHandler

    if getattr(event, "is_synthetic", False):
        return
    src = getattr(event, "src_path", "")
    name = src.replace("\\", "/").rsplit("/", 1)[-1]
    if name.startswith("~$") or name.startswith("."):
        return
    with _lock:
        _recent_events.append(time.time())


def _watch_loop():
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    class Handler(FileSystemEventHandler):
        def on_created(self, event):
            if not event.is_directory:
                _on_event(event)

        def on_modified(self, event):
            if not event.is_directory:
                _on_event(event)

    observer = Observer()
    observer.schedule(Handler(), str(DROPZONE), recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(1)
    finally:
        observer.stop()
        observer.join()


@router.on_event("startup")
async def _start_watch():
    global _started
    if _started:
        return
    DROPZONE.mkdir(parents=True, exist_ok=True)
    t = threading.Thread(target=_watch_loop, daemon=True)
    t.start()
    _started = True


@router.get("/events")
def events(since: float = 0):
    """返回 since 之后的变更事件数（前端轮询， >0 则提示「投放区有新东西」）。"""
    with _lock:
        count = sum(1 for ts in _recent_events if ts > since)
    return {"count": count, "now": time.time()}
