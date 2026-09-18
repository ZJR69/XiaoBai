"""全局速记：任何内容 5 秒内进收件箱（设计哲学：先记下，再分类）"""
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.database import get_conn

router = APIRouter(prefix="/api", tags=["capture"])


class CaptureIn(BaseModel):
    content: str
    source: str = "quick_note"  # quick_note / bookmark / manual


@router.post("/capture", status_code=201)
def capture(item: CaptureIn):
    content = item.content.strip()
    if not content:
        from fastapi import HTTPException
        raise HTTPException(400, "内容不能为空")
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO inbox_items (content, source, status, captured_at) VALUES (?, ?, 'pending', ?)",
            (content, item.source, now),
        )
        item_id = cur.lastrowid
    return {"id": item_id, "content": content, "status": "pending"}
