"""知识库 wiki：浏览 + Git 化状态（已归档/未归档/有修改）"""
import hashlib
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_conn

router = APIRouter(prefix="/api/wiki", tags=["wiki"])

# backend/app/routers/wiki.py → storage/wiki
WIKI_DIR = Path(__file__).resolve().parents[3] / "storage" / "wiki"
EXCLUDE = {"schema.md"}  # 维护手册不参与归档状态


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_path(rel: str) -> Path:
    p = (WIKI_DIR / rel).resolve()
    if not p.is_relative_to(WIKI_DIR):
        raise HTTPException(400, "非法路径")
    return p


def _status(rel: str, current_hash: str) -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT archived_hash FROM wiki_pages WHERE path = ?", (rel,)).fetchone()
    if not row:
        return "untracked"  # 未归档
    return "clean" if row["archived_hash"] == current_hash else "modified"  # 已归档 / 有修改


@router.get("/pages")
def list_pages():
    pages = []
    with get_conn() as conn:
        for f in sorted(WIKI_DIR.rglob("*.md")):
            rel = f.relative_to(WIKI_DIR).as_posix()
            if rel in EXCLUDE:
                continue
            # 首个 # 标题作为显示名
            title = rel
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            h = _file_hash(f)
            pages.append({
                "path": rel,
                "title": title,
                "status": _status(rel, h),
                "size": f.stat().st_size,
            })
    return pages


@router.get("/page")
def read_page(path: str):
    p = _safe_path(path)
    if not p.is_file():
        raise HTTPException(404, "页面不存在")
    return {"path": path, "content": p.read_text(encoding="utf-8")}


class ArchiveIn(BaseModel):
    path: str


@router.post("/archive")
def archive(body: ArchiveIn):
    """归档动作：记录当前哈希（工作流式，用户说整理/点归档时触发）"""
    p = _safe_path(body.path)
    if not p.is_file():
        raise HTTPException(404, "页面不存在")
    rel = p.relative_to(WIKI_DIR).as_posix()
    h = _file_hash(p)
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO wiki_pages (path, archived_hash, archived_at) VALUES (?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET archived_hash = excluded.archived_hash, archived_at = excluded.archived_at""",
            (rel, h, now),
        )
    return {"ok": True, "path": rel, "status": "clean"}
