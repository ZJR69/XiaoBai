"""任务 CRUD（M1：FR-1.3；完成态任务保留可见，符合用户既有习惯）"""
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_conn

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

STATUSES = ("backlog", "active", "done", "cancelled")
REPEAT_MODES = ("due", "done")


def valid_rrule(rrule_str: str) -> bool:
    """RRULE 可解析性校验（LLM 负责自然语言翻译，这里只兜底——同 create_event 的契约）"""
    try:
        from dateutil.rrule import rrulestr
        rrulestr(rrule_str.strip(), dtstart=datetime(2000, 1, 1))
        return True
    except Exception:
        return False


def next_occurrence(rrule_str: str, mode: str, due_at: str | None, now: datetime) -> str | None:
    """重复任务的下一期 due_at（YYYY-MM-DD）；规则耗尽（UNTIL/COUNT）或解析失败返回 None（→ 正常完成）。
    mode='due'：节奏锚定原截止（dtstart=原截止 0 点），取 max(原截止, now) 之后的下一格——
        提前完成跳下一期，逾期完成跳未来最近一期，不会因补做旧账而立刻又逾期；
    mode='done'：从完成时刻起算（每 3 天浇花，哪天做哪天重置）。"""
    try:
        from dateutil.rrule import rrulestr
        if mode == "due" and due_at:
            base = datetime.fromisoformat(due_at[:10] + "T00:00")
            rule = rrulestr(rrule_str.strip(), dtstart=base)
            ref = max(base, now)
        else:
            rule = rrulestr(rrule_str.strip(), dtstart=now)
            ref = now
        nxt = rule.after(ref, inc=False)
    except Exception:
        return None
    return nxt.date().isoformat() if nxt else None


class TaskIn(BaseModel):
    title: str
    priority: int = 3  # 1(高)–5(低)
    due_at: str | None = None
    project_id: int | None = None
    rrule: str | None = None  # 重复规则（iCalendar RRULE）；NULL=一次性任务
    repeat_mode: str = "due"  # due=按原截止节奏 / done=从完成时刻起算
    start_at: str | None = None  # 计划窗口起（打算什么时候做；与死线 due_at 语义分离）
    end_at: str | None = None    # 计划窗口止


class TaskPatch(BaseModel):
    title: str | None = None
    status: str | None = None
    priority: int | None = None
    due_at: str | None = None
    project_id: int | None = None
    rrule: str | None = None  # 显式传 null = 取消重复
    repeat_mode: str | None = None
    start_at: str | None = None  # 显式传 null = 清除窗口
    end_at: str | None = None


@router.get("")
def list_tasks():
    # 未完成任务在前（按优先级+截止时间），已完成保留可见排在后
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM tasks
               WHERE status NOT IN ('cancelled')
               ORDER BY CASE status WHEN 'done' THEN 1 ELSE 0 END,
                        priority ASC,
                        COALESCE(due_at, '9999-12-31') ASC,
                        id DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("", status_code=201)
def create_task(t: TaskIn):
    now = datetime.now().isoformat(timespec="seconds")
    rrule = (t.rrule or "").strip() or None
    if rrule and not valid_rrule(rrule):
        raise HTTPException(400, f"rrule 无法解析（如 FREQ=WEEKLY;BYDAY=TH），收到 {rrule}")
    if t.repeat_mode not in REPEAT_MODES:
        raise HTTPException(400, f"repeat_mode 必须是 {REPEAT_MODES} 之一")
    if t.start_at and t.end_at and t.end_at[:10] < t.start_at[:10]:
        raise HTTPException(400, "结束日期不能早于开始日期")
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO tasks (title, project_id, status, priority, due_at, rrule, repeat_mode, start_at, end_at, created_at)
               VALUES (?, ?, 'backlog', ?, ?, ?, ?, ?, ?, ?)""",
            (t.title.strip(), t.project_id, t.priority, t.due_at, rrule,
             t.repeat_mode if rrule else "due", t.start_at, t.end_at, now),
        )
        task_id = cur.lastrowid
    return {"id": task_id, "title": t.title, "status": "backlog"}


@router.patch("/{task_id}")
def update_task(task_id: int, patch: TaskPatch):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        # 重复任务勾选完成（2026-10-10）：不置 done，due_at 同行重排到下一期（Vikunja 式）。
        # 规则耗尽（UNTIL/COUNT 到头）才落入下方正常完成逻辑
        if patch.status == "done" and row["rrule"]:
            nxt = next_occurrence(row["rrule"], row["repeat_mode"] or "due", row["due_at"], datetime.now())
            if nxt:
                conn.execute("UPDATE tasks SET due_at = ? WHERE id = ?", (nxt, task_id))
                if row["project_id"]:
                    # 留痕：完成一期的事实记入事务日志（页面最新动态可见）
                    stamp = datetime.now().isoformat(timespec="seconds")
                    conn.execute(
                        """INSERT INTO progress_logs (project_id, task_id, kind, source, content, created_at)
                           VALUES (?, ?, 'status', 'agent', ?, ?)""",
                        (row["project_id"], task_id, f"完成一期，下一期 {nxt}", stamp),
                    )
                return {"ok": True, "rescheduled": nxt}
        updates, params = [], []
        for field in ("title", "status", "priority", "due_at", "project_id", "rrule", "repeat_mode", "start_at", "end_at"):
            val = getattr(patch, field)
            if field in ("project_id", "due_at", "rrule", "start_at", "end_at") and field in patch.model_fields_set:
                # 显式传入（含 null=取消挂靠/清除日期/取消重复/清除窗口）就生效
                if field == "rrule" and val and not valid_rrule(str(val)):
                    raise HTTPException(400, f"rrule 无法解析（如 FREQ=WEEKLY;BYDAY=TH），收到 {val}")
                updates.append(f"{field} = ?")
                params.append(val)
            elif val is not None:
                if field == "status" and val not in STATUSES:
                    raise HTTPException(400, f"status 必须是 {STATUSES} 之一")
                if field == "repeat_mode" and val not in REPEAT_MODES:
                    raise HTTPException(400, f"repeat_mode 必须是 {REPEAT_MODES} 之一")
                if field == "rrule" and not valid_rrule(str(val)):
                    raise HTTPException(400, f"rrule 无法解析（如 FREQ=WEEKLY;BYDAY=TH），收到 {val}")
                updates.append(f"{field} = ?")
                params.append(val)
        # 窗口校验：取生效后的最终值（patch 优先，否则用现值）——只改一端也要对着另一端验
        eff_start = patch.start_at if "start_at" in patch.model_fields_set else row["start_at"]
        eff_end = patch.end_at if "end_at" in patch.model_fields_set else row["end_at"]
        if eff_start and eff_end and str(eff_end)[:10] < str(eff_start)[:10]:
            raise HTTPException(400, "结束日期不能早于开始日期")
        if patch.status == "done":
            updates.append("completed_at = ?")
            params.append(datetime.now().isoformat(timespec="seconds"))
        elif patch.status in ("backlog", "active"):
            # 重新打开：清掉完成时间残留
            updates.append("completed_at = NULL")
        if not updates:
            raise HTTPException(400, "没有需要更新的字段")
        params.append(task_id)
        conn.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", params)
    return {"ok": True}


@router.delete("/{task_id}")
def delete_task(task_id: int):
    with get_conn() as conn:
        # 先脱离挂靠的进展日志（FK 约束会阻断直接删除；日志保留，不随任务蒸发）
        conn.execute("UPDATE progress_logs SET task_id = NULL WHERE task_id = ?", (task_id,))
        # 相对提醒同样脱离（2026-10-10 冒烟发现：FK 阻断致 500；提醒留下变惰性，用户可自行删）
        conn.execute("UPDATE reminders SET task_id = NULL WHERE task_id = ?", (task_id,))
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "任务不存在")
    return {"ok": True}
