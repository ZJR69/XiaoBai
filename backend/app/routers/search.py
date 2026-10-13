"""统一搜索（FR-2.5）：跨 wiki + 任务 + 项目 + 资源 + 日程 + 提醒一次搜。

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
        return {"q": q, "wiki": [], "tasks": [], "projects": [], "links": [], "events": [], "reminders": []}

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
        # 资源（链接/文件/文件夹；话题分组无地址，title 命中即可）
        links = conn.execute(
            "SELECT id, title, kind, target FROM links WHERE title LIKE ? ESCAPE '\\' OR target LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT 10",
            (f"%{like}%", f"%{like}%"),
        ).fetchall()
        # 日程（一次性 + 重复系列，标题/地点命中）
        events = conn.execute(
            "SELECT id, title, date, start_time, location, rrule FROM schedule_events WHERE title LIKE ? ESCAPE '\\' OR location LIKE ? ESCAPE '\\' ORDER BY date DESC LIMIT 10",
            (f"%{like}%", f"%{like}%"),
        ).fetchall()
        # 提醒（含相对提醒挂的任务名也参与命中）
        reminders = conn.execute(
            """SELECT r.id, r.title, r.trigger_type, r.trigger_value
               FROM reminders r WHERE r.title LIKE ? ESCAPE '\\' ORDER BY r.id DESC LIMIT 10""",
            (f"%{like}%",),
        ).fetchall()

    return {
        "q": q,
        "wiki": wiki_hits[:20],
        "tasks": [dict(r) for r in tasks],
        "projects": [dict(r) for r in projects],
        "links": [dict(r) for r in links],
        "events": [dict(r) for r in events],
        "reminders": [dict(r) for r in reminders],
    }
