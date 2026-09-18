"""收件箱：M1 为手动归类（AI 分类建议在 M2 接入）"""
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_conn

router = APIRouter(prefix="/api/inbox", tags=["inbox"])

ALLOWED_TYPES = ("task", "knowledge", "life", "idea")


@router.get("")
def list_inbox(status: str = "pending"):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM inbox_items WHERE status = ? ORDER BY id DESC", (status,)
        ).fetchall()
    return [dict(r) for r in rows]


class ConfirmIn(BaseModel):
    type: str  # task / knowledge / life / idea
    title: str | None = None  # 修正后的标题，默认用原文


@router.post("/{item_id}/confirm")
def confirm(item_id: int, body: ConfirmIn):
    if body.type not in ALLOWED_TYPES:
        raise HTTPException(400, f"type 必须是 {ALLOWED_TYPES} 之一")
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM inbox_items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "收件箱条目不存在")
        # 归为任务 → 直接建任务；其余类型 M1 仅标记确认（M2 起接入消化/提醒流程）
        if body.type == "task":
            conn.execute(
                "INSERT INTO tasks (title, status, created_at) VALUES (?, 'backlog', ?)",
                (body.title or row["content"], now),
            )
        conn.execute(
            "UPDATE inbox_items SET status = 'confirmed', resolved_at = ? WHERE id = ?",
            (now, item_id),
        )
    return {"ok": True, "type": body.type}


@router.post("/{item_id}/discard")
def discard(item_id: int):
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE inbox_items SET status = 'discarded', resolved_at = ? WHERE id = ?",
            (now, item_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "收件箱条目不存在")
    return {"ok": True}
