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


@router.get("/week")
def week_slots(start: str | None = None):
    """某周 7 天的日程（start=该周任意一天，取所在周的周一；默认本周）。
    周历视图数据源：单双周/有效期判定复用 slots_for_date，不在前端重复实现。"""
    from datetime import timedelta

    from app.routers.scheduler import slots_for_date

    try:
        d = datetime.strptime(start, "%Y-%m-%d").date() if start else datetime.now().date()
    except ValueError:
        raise HTTPException(400, "start 格式应为 YYYY-MM-DD")
    monday = d - timedelta(days=d.weekday())
    days = []
    for i in range(7):
        day = monday + timedelta(days=i)
        days.append({"date": day.isoformat(), "day_of_week": i, "slots": slots_for_date(day)})
    return {"start": monday.isoformat(), "days": days}


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


# ── 一次性日程（schedule_events）：日程的默认形态——调课/牙医/临时会议 ──
# 周期性课程与固定组会才用上面的 slots。查询不走这里的列表，统一走 slots_for_date 合并。

class EventIn(BaseModel):
    title: str
    entry_type: str = "personal"
    date: str  # YYYY-MM-DD
    start_time: str = "09:00"
    end_time: str = "10:00"
    location: str | None = None
    note: str | None = None


class EventPatch(BaseModel):
    title: str | None = None
    entry_type: str | None = None
    date: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    location: str | None = None
    note: str | None = None


def _validate_event(entry_type=None, date_s=None, start_time=None, end_time=None):
    if entry_type is not None and entry_type not in ENTRY_TYPES:
        raise HTTPException(400, f"entry_type 必须是 {ENTRY_TYPES} 之一")
    if date_s is not None:
        try:
            datetime.strptime(date_s, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, f"date 格式应为 YYYY-MM-DD，收到 {date_s}")
    for t in (start_time, end_time):
        if t is not None and not (len(t) == 5 and ":" == t[2]):
            raise HTTPException(400, f"时间格式应为 HH:MM，收到 {t}")


@router.get("/events")
def list_events(start: str | None = None, end: str | None = None):
    """一次性日程列表（可按日期范围过滤）"""
    sql = "SELECT * FROM schedule_events"
    cond, params = [], []
    if start:
        cond.append("date >= ?")
        params.append(start)
    if end:
        cond.append("date <= ?")
        params.append(end)
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY date, start_time"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


@router.post("/events", status_code=201)
def create_event(e: EventIn):
    _validate_event(e.entry_type, e.date, e.start_time, e.end_time)
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO schedule_events (title, entry_type, date, start_time, end_time, location, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (e.title.strip(), e.entry_type, e.date, e.start_time, e.end_time, e.location, e.note, now),
        )
        return {"id": cur.lastrowid, "title": e.title}


@router.patch("/events/{event_id}")
def update_event(event_id: int, patch: EventPatch):
    _validate_event(patch.entry_type, patch.date, patch.start_time, patch.end_time)
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM schedule_events WHERE id = ?", (event_id,)).fetchone():
            raise HTTPException(404, "日程不存在")
        updates, params = [], []
        for field in ("title", "entry_type", "date", "start_time", "end_time", "location", "note"):
            val = getattr(patch, field)
            if val is not None:
                updates.append(f"{field} = ?")
                params.append(val)
        if not updates:
            raise HTTPException(400, "没有需要更新的字段")
        params.append(event_id)
        conn.execute(f"UPDATE schedule_events SET {', '.join(updates)} WHERE id = ?", params)
    return {"ok": True}


@router.delete("/events/{event_id}")
def delete_event(event_id: int):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM schedule_events WHERE id = ?", (event_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "日程不存在")
    return {"ok": True}
