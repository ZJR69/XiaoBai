"""项目与进展日志 + Context 打包器（M3 提前打通数据层，M2 先供全景视图）"""
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_conn

router = APIRouter(prefix="/api/projects", tags=["projects"])

LOG_KINDS = ("background", "decision", "status", "next_step", "note")

# PRD 4.6：projects 语义升级为「进行中的事」
CATEGORIES = ("research", "collaboration", "family", "personal", "club", "other")


class ProjectIn(BaseModel):
    name: str
    description: str | None = None
    category: str = "other"  # research/collaboration/family/personal/club/other


class LogIn(BaseModel):
    kind: str  # background/decision/status/next_step/note
    content: str


class LogPatch(BaseModel):
    kind: str | None = None
    content: str | None = None


class ProjectPatch(BaseModel):
    status: str | None = None  # active/closed（closed = 完成，数据保留可恢复）


@router.get("")
def list_projects(include_closed: bool = False):
    where = "" if include_closed else "WHERE status != 'closed'"
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM projects {where} ORDER BY id DESC"
        ).fetchall()
        result = []
        for p in rows:
            tasks = conn.execute(
                "SELECT id, title, status, due_at, priority FROM tasks WHERE project_id = ? ORDER BY priority",
                (p["id"],),
            ).fetchall()
            next_step = conn.execute(
                "SELECT content, created_at FROM progress_logs WHERE project_id = ? AND kind = 'next_step' ORDER BY id DESC LIMIT 1",
                (p["id"],),
            ).fetchone()
            last_log = conn.execute(
                "SELECT kind, content, created_at FROM progress_logs WHERE project_id = ? ORDER BY id DESC LIMIT 1",
                (p["id"],),
            ).fetchone()
            result.append({
                **dict(p),
                "tasks": [dict(t) for t in tasks],
                "next_step": dict(next_step) if next_step else None,
                "last_activity": dict(last_log) if last_log else None,
            })
    return result


@router.post("", status_code=201)
def create_project(p: ProjectIn):
    if p.category not in CATEGORIES:
        raise HTTPException(400, f"category 必须是 {CATEGORIES} 之一")
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO projects (name, description, category, created_at) VALUES (?, ?, ?, ?)",
            (p.name.strip(), p.description, p.category, now),
        )
        return {"id": cur.lastrowid, "name": p.name, "category": p.category}


@router.post("/{project_id}/logs", status_code=201)
def add_log(project_id: int, log: LogIn):
    if log.kind not in LOG_KINDS:
        raise HTTPException(400, f"kind 必须是 {LOG_KINDS} 之一")
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
            raise HTTPException(404, "项目不存在")
        cur = conn.execute(
            "INSERT INTO progress_logs (project_id, kind, content, created_at) VALUES (?, ?, ?, ?)",
            (project_id, log.kind, log.content.strip(), now),
        )
        return {"id": cur.lastrowid}


@router.get("/{project_id}/logs")
def list_logs(project_id: int):
    """某事的全部进展日志（进展管理弹层数据源）"""
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
            raise HTTPException(404, "项目不存在")
        rows = conn.execute(
            "SELECT id, kind, content, created_at FROM progress_logs WHERE project_id = ? ORDER BY id DESC",
            (project_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.patch("/{project_id}/logs/{log_id}")
def update_log(project_id: int, log_id: int, patch: LogPatch):
    """编辑日志（背景包是实时聚合，改完即生效）"""
    if patch.kind is not None and patch.kind not in LOG_KINDS:
        raise HTTPException(400, f"kind 必须是 {LOG_KINDS} 之一")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM progress_logs WHERE id = ? AND project_id = ?", (log_id, project_id)
        ).fetchone()
        if not row:
            raise HTTPException(404, "日志不存在")
        updates, params = [], []
        for field in ("kind", "content"):
            val = getattr(patch, field)
            if val is not None:
                updates.append(f"{field} = ?")
                params.append(val.strip() if field == "content" else val)
        if not updates:
            raise HTTPException(400, "没有需要更新的字段")
        params.append(log_id)
        conn.execute(f"UPDATE progress_logs SET {', '.join(updates)} WHERE id = ?", params)
    return {"ok": True}


@router.delete("/{project_id}/logs/{log_id}")
def delete_log(project_id: int, log_id: int):
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM progress_logs WHERE id = ? AND project_id = ?", (log_id, project_id)
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "日志不存在")
    return {"ok": True}


@router.patch("/{project_id}")
def update_project(project_id: int, patch: ProjectPatch):
    """状态闭环：active ⇄ closed。完成时记 closed_at，恢复时清空。数据永不删除。"""
    if patch.status not in (None, "active", "closed"):
        raise HTTPException(400, "status 必须是 active/closed 之一")
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
            raise HTTPException(404, "项目不存在")
        if patch.status == "closed":
            conn.execute("UPDATE projects SET status='closed', closed_at=? WHERE id=?", (now, project_id))
        elif patch.status == "active":
            conn.execute("UPDATE projects SET status='active', closed_at=NULL WHERE id=?", (project_id,))
    return {"ok": True}


@router.get("/{project_id}/context-pack")
def context_pack(project_id: int):
    """Context 打包器：结构化进展日志 → 完整背景摘要（给老师/学长/其他 AI 同步用）"""
    with get_conn() as conn:
        p = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not p:
            raise HTTPException(404, "项目不存在")
        logs = conn.execute(
            "SELECT kind, content, created_at FROM progress_logs WHERE project_id = ? ORDER BY id",
            (project_id,),
        ).fetchall()
        tasks = conn.execute(
            "SELECT title, status, due_at FROM tasks WHERE project_id = ?", (project_id,)
        ).fetchall()

    sections = {"background": [], "decision": [], "status": [], "next_step": [], "note": []}
    for lg in logs:
        sections[lg["kind"]].append(f"{lg['content']}（{lg['created_at'][:10]}）")

    lines = [f"# {p['name']} · 背景同步包", ""]
    if p["description"]:
        lines += [p["description"], ""]
    titles = {"background": "背景", "decision": "关键决策", "status": "当前状态", "next_step": "下一步", "note": "备注"}
    for kind, title in titles.items():
        if sections[kind]:
            lines.append(f"## {title}")
            lines += [f"- {c}" for c in sections[kind]]
            lines.append("")
    if tasks:
        lines.append("## 相关任务")
        lines += [f"- {t['title']}（{t['status']}）" for t in tasks]
    return {"project": p["name"], "pack": "\n".join(lines).strip()}
