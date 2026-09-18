"""统一搜索（FR-2.5）：跨 wiki + 任务 + 项目 + 实体一次搜。

本地量级小（几十个 md 文件），全文遍历足够，不需要索引引擎。
"""
from pathlib import Path

from fastapi import APIRouter

from app.database import get_conn

router = APIRouter(prefix="/api/search", tags=["search"])

WIKI_DIR = Path(__file__).resolve().parents[3] / "storage" / "wiki"
EXCLUDE = {"schema.md", "log.md"}


@router.get("")
def search(q: str):
    q = (q or "").strip()
    if len(q) < 2:
        return {"q": q, "wiki": [], "tasks": [], "projects": []}

    # LIKE 通配符转义（% _ 当普通字符）
    like = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    # wiki 全文（含 core/entities/topics/sources）
    wiki_hits = []
    ql = q.lower()
    for f in sorted(WIKI_DIR.rglob("*.md")):
        rel = f.relative_to(WIKI_DIR).as_posix()
        if rel in EXCLUDE:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if ql in text.lower():
            title, snippet = rel, ""
            for i, line in enumerate(text.splitlines()):
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            # 命中行做片段
            for line in text.splitlines():
                if ql in line.lower():
                    snippet = line.strip()[:120]
                    break
            wiki_hits.append({"path": rel, "title": title, "snippet": snippet})

    with get_conn() as conn:
        tasks = conn.execute(
            "SELECT id, title, status, due_at FROM tasks WHERE title LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT 20",
            (f"%{like}%",),
        ).fetchall()
        projects = conn.execute(
            "SELECT id, name, category, status FROM projects WHERE name LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT 20",
            (f"%{like}%", f"%{like}%"),
        ).fetchall()

    return {
        "q": q,
        "wiki": wiki_hits[:20],
        "tasks": [dict(r) for r in tasks],
        "projects": [dict(r) for r in projects],
    }
