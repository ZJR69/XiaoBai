"""生活角：提醒（M2 先供视图，触发调度 M6 接入）"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_conn

router = APIRouter(prefix="/api/reminders", tags=["reminders"])


class ReminderIn(BaseModel):
    title: str
    trigger_type: str = "once"  # once/daily/weekly/interval
    trigger_value: str | None = None  # ISO 时间或简单描述


@router.get("")
def list_reminders():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM reminders ORDER BY active DESC, id DESC").fetchall()
    return [dict(r) for r in rows]


@router.post("", status_code=201)
def create_reminder(r: ReminderIn):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (title, trigger_type, trigger_value) VALUES (?, ?, ?)",
            (r.title.strip(), r.trigger_type, r.trigger_value),
        )
        return {"id": cur.lastrowid, "title": r.title}


@router.post("/{reminder_id}/toggle")
def toggle(reminder_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT active FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        if not row:
            raise HTTPException(404, "提醒不存在")
        conn.execute(
            "UPDATE reminders SET active = ? WHERE id = ?",
            (0 if row["active"] else 1, reminder_id),
        )
    return {"ok": True}


@router.delete("/{reminder_id}")
def delete_reminder(reminder_id: int):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "提醒不存在")
    return {"ok": True}
