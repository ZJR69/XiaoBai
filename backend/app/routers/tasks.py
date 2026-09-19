"""任务 CRUD（M1：FR-1.3；完成态任务保留可见，符合用户既有习惯）"""
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_conn

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

STATUSES = ("backlog", "active", "done", "cancelled")


class TaskIn(BaseModel):
    title: str
    priority: int = 3  # 1(高)–5(低)
    due_at: str | None = None
    project_id: int | None = None


class TaskPatch(BaseModel):
    title: str | None = None
    status: str | None = None
    priority: int | None = None
    due_at: str | None = None
    project_id: int | None = None


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
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO tasks (title, project_id, status, priority, due_at, created_at)
               VALUES (?, ?, 'backlog', ?, ?, ?)""",
            (t.title.strip(), t.project_id, t.priority, t.due_at, now),
        )
        task_id = cur.lastrowid
    return {"id": task_id, "title": t.title, "status": "backlog"}


@router.patch("/{task_id}")
def update_task(task_id: int, patch: TaskPatch):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        updates, params = [], []
        for field in ("title", "status", "priority", "due_at", "project_id"):
            val = getattr(patch, field)
            if field == "project_id" and "project_id" in patch.model_fields_set:
                # 显式传入（含 null=取消挂靠）就生效
                updates.append("project_id = ?")
                params.append(val)
            elif val is not None:
                if field == "status" and val not in STATUSES:
                    raise HTTPException(400, f"status 必须是 {STATUSES} 之一")
                updates.append(f"{field} = ?")
                params.append(val)
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
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "任务不存在")
    return {"ok": True}
