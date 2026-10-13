"""项目与进展日志 + Context 打包器（M3 提前打通数据层，M2 先供全景视图）"""
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_conn

router = APIRouter(prefix="/api/projects", tags=["projects"])

LOG_KINDS = ("background", "decision", "status", "next_step", "note")

# PRD 4.6：projects 语义升级为「进行中的事」；course 是存量数据（旧版校验漏了它，2026-09-26 补上）
CATEGORIES = ("research", "collaboration", "family", "personal", "club", "course", "other")

CATEGORY_LABELS = {
    "research": "科研",
    "collaboration": "协作",
    "family": "家庭",
    "personal": "个人",
    "club": "社团",
    "course": "课程",
    "other": "其他",
}


class ProjectIn(BaseModel):
    name: str
    description: str | None = None
    category: str = "other"  # research/collaboration/family/personal/club/course/other
    parent_id: int | None = None  # 二级事项：挂到某一级事务下（封顶二级，父自身不得再有父）


class LogIn(BaseModel):
    kind: str  # background/decision/status/next_step/note
    content: str


class LogPatch(BaseModel):
    kind: str | None = None
    content: str | None = None


class ProjectPatch(BaseModel):
    status: str | None = None  # active/closed（closed = 完成，数据保留可恢复）
    name: str | None = None
    description: str | None = None
    category: str | None = None
    parent_id: int | None = None  # 挂靠/取消挂靠（null=升为一级）


def _check_parent(conn, parent_id: int | None, self_id: int | None = None) -> None:
    """父校验（二级封顶）：父必须存在、自身是一级（parent_id 为空）、不能是自己（防自环）。
    二级封顶意味着不存在多级链，天然无环。"""
    if parent_id is None:
        return
    if parent_id == self_id:
        raise HTTPException(400, "不能挂到自己下面")
    row = conn.execute("SELECT id, parent_id FROM projects WHERE id = ?", (parent_id,)).fetchone()
    if not row:
        raise HTTPException(400, "父事项不存在")
    if row["parent_id"] is not None:
        raise HTTPException(400, "最多两级：所选父已是二级事项")


@router.get("")
def list_projects(include_closed: bool = False):
    """树形列表（2026-09-26 二级事项）：一级事务带 children 数组；
    一级的聚合状态 = 自身与全部子事项的最近一条日志 + 未完成任务数。"""
    where = "" if include_closed else "WHERE status != 'closed'"
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM projects {where} ORDER BY id DESC"
        ).fetchall()
        result = []
        children_map: dict[int, list] = {}
        for p in rows:
            tasks = conn.execute(
                "SELECT id, title, status, due_at, priority FROM tasks WHERE project_id = ? ORDER BY priority",
                (p["id"],),
            ).fetchall()
            last_log = conn.execute(
                """SELECT kind, content, created_at FROM progress_logs
                   WHERE project_id = ? AND done_at IS NULL
                   ORDER BY id DESC LIMIT 1""",
                (p["id"],),
            ).fetchone()
            item = {
                **dict(p),
                "tasks": [dict(t) for t in tasks],
                "last_activity": dict(last_log) if last_log else None,
            }
            if p["parent_id"] is None:
                result.append(item)
            else:
                children_map.setdefault(p["parent_id"], []).append(item)
        # 一级挂 children + 聚合（子事项的最近动态/待办数一并算入父级态势）
        for top in result:
            kids = children_map.get(top["id"], [])
            top["children"] = kids
            all_logs = [top["last_activity"]] + [k["last_activity"] for k in kids]
            all_logs = [l for l in all_logs if l]
            if all_logs:
                top["last_activity"] = max(all_logs, key=lambda l: l["created_at"])
            top["open_tasks"] = sum(
                1 for t in top["tasks"] if t["status"] in ("backlog", "active")
            ) + sum(
                1 for k in kids for t in k["tasks"] if t["status"] in ("backlog", "active")
            )
        return result


@router.post("", status_code=201)
def create_project(p: ProjectIn):
    if p.category not in CATEGORIES:
        raise HTTPException(400, f"category 必须是 {CATEGORIES} 之一")
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        _check_parent(conn, p.parent_id)
        cur = conn.execute(
            "INSERT INTO projects (name, description, category, parent_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (p.name.strip(), p.description, p.category, p.parent_id, now),
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
    """某事的全部进展日志（进展管理弹层数据源）。
    已落地（done_at，随任务完成联动）的排底，页面灰化展示——数据保留留痕。"""
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
            raise HTTPException(404, "项目不存在")
        rows = conn.execute(
            """SELECT id, kind, content, created_at, done_at FROM progress_logs
               WHERE project_id = ? ORDER BY (done_at IS NULL) DESC, id DESC""",
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
    """字段编辑 + 状态闭环：active ⇄ closed。完成时记 closed_at，恢复时清空。数据永不删除。"""
    if patch.status not in (None, "active", "closed"):
        raise HTTPException(400, "status 必须是 active/closed 之一")
    if patch.category is not None and patch.category not in CATEGORIES:
        raise HTTPException(400, f"category 必须是 {CATEGORIES} 之一")
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
            raise HTTPException(404, "项目不存在")
        # 挂靠校验：新父必须合法；且自己若有子事项，则不能再挂人（封顶二级）
        if "parent_id" in patch.model_fields_set:
            _check_parent(conn, patch.parent_id, self_id=project_id)
            if patch.parent_id is not None:
                has_kids = conn.execute(
                    "SELECT 1 FROM projects WHERE parent_id = ? LIMIT 1", (project_id,)
                ).fetchone()
                if has_kids:
                    raise HTTPException(400, "该事项下已有二级事项，不能再挂到别的事务下（最多两级）")
        updates, params = [], []
        if patch.status is not None:
            updates.append("status = ?"); params.append(patch.status)
            if patch.status == "closed":
                updates.append("closed_at = ?"); params.append(now)
            else:
                updates.append("closed_at = NULL")
        for field in ("name", "description", "category", "parent_id"):
            if field in patch.model_fields_set:
                val = getattr(patch, field)
                updates.append(f"{field} = ?")
                params.append(val.strip() if field == "name" and val else val)
        if updates:
            params.append(project_id)
            conn.execute(f"UPDATE projects SET {', '.join(updates)} WHERE id = ?", params)
    return {"ok": True}


@router.get("/{project_id}/context-pack")
def context_pack(project_id: int):
    """Context 打包器：结构化进展日志 → 完整背景摘要（给老师/学长/其他 AI 同步用）。
    一级事务的包含全部二级子事项的日志（整块事务全貌）。"""
    with get_conn() as conn:
        p = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not p:
            raise HTTPException(404, "项目不存在")
        kids = conn.execute(
            "SELECT id, name FROM projects WHERE parent_id = ? ORDER BY id", (project_id,)
        ).fetchall()
        log_ids = [project_id] + [k["id"] for k in kids]
        placeholders = ",".join("?" * len(log_ids))
        logs = conn.execute(
            f"SELECT project_id, kind, content, created_at FROM progress_logs WHERE project_id IN ({placeholders}) ORDER BY id",
            log_ids,
        ).fetchall()
        tasks = conn.execute(
            f"SELECT title, status, due_at FROM tasks WHERE project_id IN ({placeholders})",
            log_ids,
        ).fetchall()

    kid_names = {k["id"]: k["name"] for k in kids}
    sections = {"background": [], "decision": [], "status": [], "next_step": [], "note": []}
    for lg in logs:
        prefix = f"〔{kid_names[lg['project_id']]}〕" if lg["project_id"] in kid_names else ""
        sections[lg["kind"]].append(f"{prefix}{lg['content']}（{lg['created_at'][:10]}）")

    lines = [f"# {p['name']} · 背景同步包", ""]
    if p["description"]:
        lines += [p["description"], ""]
    if kids:
        lines.append("包含：" + "、".join(k["name"] for k in kids))
        lines.append("")
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
