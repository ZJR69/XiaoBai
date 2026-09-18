"""生活角：提醒 CRUD（触发调度在 scheduler.py，30s tick）"""
import re
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_conn

router = APIRouter(prefix="/api/reminders", tags=["reminders"])

TRIGGER_TYPES = ("once", "daily", "weekly", "interval")


def _normalize_trigger(trigger_type: str, trigger_value: str | None) -> str:
    """校验 + 归一化触发值（调度器按严格格式消费，脏值会永不触发）。
    once: YYYY-MM-DD[THH:MM]；daily: HH:MM；weekly: W-HH:MM（W=0-6 周一至周日）；interval: 小时数"""
    v = (trigger_value or "").strip()
    if trigger_type == "once":
        if not v:
            raise HTTPException(400, "一次性提醒需要日期（YYYY-MM-DD 或 YYYY-MM-DD HH:MM）")
        v = v.replace(" ", "T")
        try:
            datetime.fromisoformat(v[:10] if len(v) == 10 else v)
        except ValueError:
            raise HTTPException(400, "日期格式应为 YYYY-MM-DD（可带 HH:MM）")
        return v
    if trigger_type == "daily":
        if not v:
            return "09:00"  # 默认早上 9 点
        if not re.fullmatch(r"\d{1,2}:\d{2}", v):
            raise HTTPException(400, "每天提醒的格式应为 HH:MM")
        h, m = v.split(":")
        if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
            raise HTTPException(400, "时间超出范围")
        return f"{int(h):02d}:{m}"
    if trigger_type == "weekly":
        # 前端传 "W-HH:MM"（下拉选星期）；兼容裸 HH:MM（默认周一）
        if re.fullmatch(r"\d{1,2}:\d{2}", v):
            v = f"0-{v}"
        if not re.fullmatch(r"[0-6]-\d{1,2}:\d{2}", v):
            raise HTTPException(400, "每周提醒的格式应为 星期-HH:MM（星期 0-6，0=周一）")
        w, hm = v.split("-", 1)
        h, m = hm.split(":")
        if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
            raise HTTPException(400, "时间超出范围")
        return f"{w}-{int(h):02d}:{m}"
    if trigger_type == "interval":
        if not v:
            raise HTTPException(400, "间隔提醒需要小时数（如 4 表示每 4 小时）")
        try:
            hours = float(v)
        except ValueError:
            raise HTTPException(400, "间隔应为小时数（数字）")
        if hours <= 0:
            raise HTTPException(400, "间隔必须大于 0")
        return v
    raise HTTPException(400, f"trigger_type 必须是 {TRIGGER_TYPES} 之一")


class ReminderIn(BaseModel):
    title: str
    trigger_type: str = "once"  # once/daily/weekly/interval
    trigger_value: str | None = None


@router.get("")
def list_reminders():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM reminders ORDER BY active DESC, id DESC").fetchall()
    return [dict(r) for r in rows]


@router.post("", status_code=201)
def create_reminder(r: ReminderIn):
    trigger_value = _normalize_trigger(r.trigger_type, r.trigger_value)
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (title, trigger_type, trigger_value) VALUES (?, ?, ?)",
            (r.title.strip(), r.trigger_type, trigger_value),
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
