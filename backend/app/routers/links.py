"""资源索引（重构蓝图阶段二）：学习/项目链接、资料存放地址的登记与一键直达。

定位：旧 wiki 知识库（内容消化入库）整体下线，本模块接替其「什么值得存」的入口职能——
登记的不再是内容本身，而是内容的地址（网址 / 本地文件 / 文件夹）。
打开行为（蓝图定案）：url 由前端新开标签；file/folder 由 /open 调资源管理器，
文件用 explorer /select 打开所在文件夹并「选中不打开」（打开方式留给用户选择）。

树化（2026-09-26）：用户裁决平铺改层级——kind='group' 为话题索引节点（无 target，可无限嵌套），
url/file/folder 为叶子资源；parent_id 指向父 group（NULL=根级）。删除非空 group 拒绝（先移空）。
拖拽（同日）：position 字段支撑同级自由排序；/move 端点换父+排序一次完成（前端拖拽唯一入口）。
"""
import os
import subprocess
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_conn

router = APIRouter(prefix="/api/links", tags=["links"])

KINDS = ("url", "file", "folder", "group")
EDITABLE = ("title", "kind", "target", "category", "note", "project_id", "entity", "parent_id")


class LinkIn(BaseModel):
    title: str
    kind: str
    target: str | None = None  # group 无地址
    category: str | None = None
    note: str | None = None
    project_id: int | None = None
    entity: str | None = None
    parent_id: int | None = None


def _validate(kind: str, target: str) -> str | None:
    """kind/target 合法性，返回错误文案（None = 通过）"""
    if kind not in KINDS:
        return f"kind 必须是 {'/'.join(KINDS)} 之一"
    if kind == "group":
        return None  # 话题节点没有地址
    if not target:
        return "资源地址必填"
    if kind == "url" and not target.startswith(("http://", "https://")):
        return "网址需以 http(s):// 开头"
    return None


def _check_parent(conn, parent_id: int | None, self_id: int | None = None) -> str | None:
    """父节点合法性：必须指向已存在的 group；不能挂到自己或自己的后代下（防环）。"""
    if parent_id is None:
        return None
    row = conn.execute("SELECT kind FROM links WHERE id = ?", (parent_id,)).fetchone()
    if not row:
        return "父话题不存在"
    if row["kind"] != "group":
        return "父节点必须是话题（group）"
    if self_id is not None:
        # 沿 parent 链上溯，撞见自己即成环
        cur, depth = parent_id, 0
        while cur is not None and depth < 100:
            if cur == self_id:
                return "不能挂到自己或自己的子话题下"
            cur = conn.execute("SELECT parent_id FROM links WHERE id = ?", (cur,)).fetchone()
            cur = cur["parent_id"] if cur else None
            depth += 1
    return None


@router.get("")
def list_links():
    """全量列出（前端按 parent_id 建树渲染；同层按 position 升序；项目名 join 出供展示）"""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT l.*, p.name AS project_name
               FROM links l LEFT JOIN projects p ON l.project_id = p.id
               ORDER BY l.position, l.id"""
        ).fetchall()
    return {"links": [dict(r) for r in rows]}


@router.post("", status_code=201)
def create_link(body: LinkIn):
    title, kind = body.title.strip(), body.kind.strip()
    target = (body.target or "").strip()
    if not title:
        raise HTTPException(400, "title 必填")
    err = _validate(kind, target)
    if err:
        raise HTTPException(400, err)
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        err = _check_parent(conn, body.parent_id)
        if err:
            raise HTTPException(400, err)
        # 新条目落在目标层末尾（同层 position 最大值 +1）
        pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM links WHERE parent_id IS ?",
            (body.parent_id,),
        ).fetchone()["p"]
        cur = conn.execute(
            """INSERT INTO links (title, kind, target, category, note, project_id, entity, parent_id, position, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, kind, target, (body.category or "").strip() or "其他",
             body.note, body.project_id, body.entity, body.parent_id, pos, now),
        )
        lid = cur.lastrowid
    return {"ok": True, "id": lid}


@router.put("/{lid}")
def update_link(lid: int, patch: dict):
    sets = {k: v for k, v in patch.items() if k in EDITABLE}
    if not sets:
        raise HTTPException(400, "没有可改字段")
    with get_conn() as conn:
        old = conn.execute("SELECT kind, target FROM links WHERE id = ?", (lid,)).fetchone()
        if not old:
            raise HTTPException(404, "资源不存在")
        new_kind = sets.get("kind") or old["kind"]
        new_target = sets.get("target", old["target"]) or ""
        err = _validate(new_kind, new_target.strip())
        if err:
            raise HTTPException(400, err)
        if new_kind == "group" and "target" not in sets:
            sets["target"] = ""  # 改成话题时清空地址
        if "parent_id" in sets:
            pid = sets["parent_id"] or None
            err = _check_parent(conn, pid, self_id=lid)
            if err:
                raise HTTPException(400, err)
            sets["parent_id"] = pid
        cols = ", ".join(f"{k} = ?" for k in sets)
        cur = conn.execute(f"UPDATE links SET {cols} WHERE id = ?", (*sets.values(), lid))
        if cur.rowcount == 0:
            raise HTTPException(404, "资源不存在")
    return {"ok": True}


@router.delete("/{lid}")
def delete_link(lid: int):
    with get_conn() as conn:
        row = conn.execute("SELECT kind FROM links WHERE id = ?", (lid,)).fetchone()
        if not row:
            raise HTTPException(404, "资源不存在")
        if row["kind"] == "group":
            n = conn.execute("SELECT COUNT(*) AS n FROM links WHERE parent_id = ?", (lid,)).fetchone()["n"]
            if n:
                raise HTTPException(400, f"话题下还有 {n} 个条目，先移走或删掉它们")
        conn.execute("DELETE FROM links WHERE id = ?", (lid,))
    return {"ok": True}


@router.post("/{lid}/move")
def move_link(lid: int, body: dict):
    """拖拽移动：换父 + 同级排序一次完成。body: { parent_id, position }，
    position 是目标下标（前端算好；越界钳到末尾），整个目标层重写 position 保持连续。"""
    parent_id = (body or {}).get("parent_id") or None
    position = (body or {}).get("position", 0)
    try:
        position = int(position)
    except (TypeError, ValueError):
        raise HTTPException(400, "position 必须是整数")
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM links WHERE id = ?", (lid,)).fetchone()
        if not row:
            raise HTTPException(404, "资源不存在")
        err = _check_parent(conn, parent_id, self_id=lid)
        if err:
            raise HTTPException(400, err)
        # 目标层的兄弟（排除自己）按 position 排序，插到目标下标，整层重写保持连续
        siblings = conn.execute(
            "SELECT id FROM links WHERE parent_id IS ? AND id != ? ORDER BY position, id",
            (parent_id, lid),
        ).fetchall()
        ids = [s["id"] for s in siblings]
        pos = max(0, min(position, len(ids)))
        ids.insert(pos, lid)
        conn.execute("UPDATE links SET parent_id = ? WHERE id = ?", (parent_id, lid))
        for i, sid in enumerate(ids):
            conn.execute("UPDATE links SET position = ? WHERE id = ?", (i, sid))
    return {"ok": True}


@router.post("/pick")
def pick_path(body: dict):
    """弹系统文件/文件夹选择框，返回所选完整路径（只取路径，不上传不打开）。
    浏览器安全策略让前端拿不到本地完整路径（只有 fakepath），所以由本地后端代弹
    （tkinter 是 Python 标准库，Windows 自带）。请求会阻塞到用户选完或取消——
    本地单用户应用，可接受。"""
    kind = (body or {}).get("kind", "file")
    if kind not in ("file", "folder"):
        raise HTTPException(400, "kind 必须是 file/folder")
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        raise HTTPException(500, "本机 Python 缺少 tkinter，无法弹系统选择框")
    root = tk.Tk()
    root.withdraw()  # 只留对话框，不出主窗口
    root.attributes("-topmost", True)  # 顶到浏览器前面，免得以为没反应
    try:
        if kind == "folder":
            path = filedialog.askdirectory(parent=root, title="选择文件夹（只记录路径）")
        else:
            path = filedialog.askopenfilename(parent=root, title="选择文件（只记录路径）")
    finally:
        root.destroy()
    if not path:  # 用户点了取消
        return {"ok": True, "path": None}
    return {"ok": True, "path": os.path.normpath(path)}


@router.post("/{lid}/open")
def open_link(lid: int):
    """本地资源一键直达：文件 → 资源管理器定位并选中（不打开）；文件夹 → 直接打开。
    url 不走这里（前端 window.open 新开标签页）。"""
    with get_conn() as conn:
        row = conn.execute("SELECT kind, target FROM links WHERE id = ?", (lid,)).fetchone()
    if not row:
        raise HTTPException(404, "资源不存在")
    if row["kind"] == "url":
        raise HTTPException(400, "网址由前端直接打开")
    path = os.path.normpath(row["target"])
    if not os.path.exists(path):
        raise HTTPException(404, "路径不存在，文件可能已移动或改名")
    if row["kind"] == "file":
        subprocess.Popen(["explorer.exe", "/select,", path])
    else:
        subprocess.Popen(["explorer.exe", path])
    return {"ok": True}
