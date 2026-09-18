"""内置日程表（M4 FR-4.1）：课程/会议/外出/个人安排，课程只是日程的一类。

复用 PRD 第 6 节的 schedule_slots 表。week_pattern 支持单双周（odd/even），
valid_from/valid_to 圈定学期等有效范围。
"""
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_conn

router = APIRouter(prefix="/api/schedule", tags=["schedule"])

ENTRY_TYPES = ("course", "meeting", "outing", "personal")
ENTRY_LABELS = {"course": "课程", "meeting": "会议", "outing": "外出", "personal": "个人"}
WEEK_PATTERN = ("all", "odd", "even")
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class SlotIn(BaseModel):
    title: str
    entry_type: str = "course"
    day_of_week: int  # 0=周一 … 6=周日
    start_time: str  # "08:00"
    end_time: str  # "09:40"
    location: str | None = None
    week_pattern: str = "all"  # all/odd/even
    valid_from: str | None = None  # "2026-09-01"
    valid_to: str | None = None


class SlotPatch(BaseModel):
    title: str | None = None
    entry_type: str | None = None
    day_of_week: int | None = None
    start_time: str | None = None
    end_time: str | None = None
    location: str | None = None
    week_pattern: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None


def _validate(entry_type=None, day_of_week=None, week_pattern=None,
              start_time=None, end_time=None):
    if entry_type is not None and entry_type not in ENTRY_TYPES:
        raise HTTPException(400, f"entry_type 必须是 {ENTRY_TYPES} 之一")
    if day_of_week is not None and not 0 <= day_of_week <= 6:
        raise HTTPException(400, "day_of_week 必须是 0–6（0=周一）")
    if week_pattern is not None and week_pattern not in WEEK_PATTERN:
        raise HTTPException(400, f"week_pattern 必须是 {WEEK_PATTERN} 之一")
    for t in (start_time, end_time):
        if t is not None and not (len(t) == 5 and ":" == t[2]):
            raise HTTPException(400, f"时间格式应为 HH:MM，收到 {t}")


@router.get("")
def list_slots():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM schedule_slots ORDER BY day_of_week, start_time"
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/today")
def today_slots():
    """今天的日程（考虑星期、单双周、有效范围），按开始时间排序"""
    from app.routers.scheduler import slots_for_date

    return {"date": datetime.now().date().isoformat(), "slots": slots_for_date()}


@router.post("", status_code=201)
def create_slot(s: SlotIn):
    _validate(s.entry_type, s.day_of_week, s.week_pattern, s.start_time, s.end_time)
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO schedule_slots
               (title, entry_type, day_of_week, start_time, end_time, location, week_pattern, valid_from, valid_to)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (s.title.strip(), s.entry_type, s.day_of_week, s.start_time, s.end_time,
             s.location, s.week_pattern, s.valid_from, s.valid_to),
        )
        return {"id": cur.lastrowid, "title": s.title}


@router.patch("/{slot_id}")
def update_slot(slot_id: int, patch: SlotPatch):
    _validate(patch.entry_type, patch.day_of_week, patch.week_pattern,
              patch.start_time, patch.end_time)
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM schedule_slots WHERE id = ?", (slot_id,)).fetchone():
            raise HTTPException(404, "日程不存在")
        updates, params = [], []
        for field in ("title", "entry_type", "day_of_week", "start_time", "end_time",
                      "location", "week_pattern", "valid_from", "valid_to"):
            val = getattr(patch, field)
            if val is not None:
                updates.append(f"{field} = ?")
                params.append(val)
        if not updates:
            raise HTTPException(400, "没有需要更新的字段")
        params.append(slot_id)
        conn.execute(f"UPDATE schedule_slots SET {', '.join(updates)} WHERE id = ?", params)
    return {"ok": True}


@router.delete("/{slot_id}")
def delete_slot(slot_id: int):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM schedule_slots WHERE id = ?", (slot_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "日程不存在")
    return {"ok": True}
