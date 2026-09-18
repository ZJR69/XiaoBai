"""收件箱：M1 为手动归类（AI 分类建议在 M2 接入）"""
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_conn

router = APIRouter(prefix="/api/inbox", tags=["inbox"])

ALLOWED_TYPES = ("task", "knowledge", "life", "idea")


@router.get("")
def list_inbox(status: str = "pending"):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM inbox_items WHERE status = ? ORDER BY id DESC", (status,)
        ).fetchall()
    return [dict(r) for r in rows]


class ConfirmIn(BaseModel):
    type: str  # task / knowledge / life / idea
    title: str | None = None  # 修正后的标题，默认用原文


@router.post("/{item_id}/confirm")
def confirm(item_id: int, body: ConfirmIn):
    if body.type not in ALLOWED_TYPES:
        raise HTTPException(400, f"type 必须是 {ALLOWED_TYPES} 之一")
    now = datetime.now().isoformat(timespec="seconds")
    today = now[:10]
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM inbox_items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "收件箱条目不存在")
        # 归为任务 → 直接建任务；归为知识 → 写 wiki sources 页（PRD FR-2.1 消化通路）
        if body.type == "task":
            conn.execute(
                "INSERT INTO tasks (title, status, created_at) VALUES (?, 'backlog', ?)",
                (body.title or row["content"], now),
            )
        conn.execute(
            "UPDATE inbox_items SET status = 'confirmed', resolved_at = ? WHERE id = ?",
            (now, item_id),
        )
    if body.type == "knowledge":
        _write_knowledge_page(body.title or row["content"][:60], row["content"], today)
    return {"ok": True, "type": body.type}


def _write_knowledge_page(title: str, detail: str, today: str):
    """闪记确认的知识 → wiki sources 页 + index/log + 间隔重现登记"""
    import re as _re
    from pathlib import Path

    from app.routers.raw import _append_log, _update_index

    wiki_dir = Path(__file__).resolve().parents[3] / "storage" / "wiki"
    slug = _re.sub(r'[\\/:*?"<>|\s]+', "-", title)[:60]
    page_rel = f"sources/{slug}.md"
    page = wiki_dir / page_rel
    front = (
        f"---\ntype: source\ncreated: {today}\nupdated: {today}\n"
        f"source: inbox\n---\n\n"
    )
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        front + f"# {title}\n\n{detail}\n\n> 来源：闪记确认\n", encoding="utf-8"
    )
    _update_index(page_rel, title)
    _append_log(f"## [{today}] ingest | 闪记沉淀：{title}")
    # 间隔重现登记（M5 FR-5.1）
    from datetime import date, timedelta

    from app.database import get_conn
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO spaced_reviews (page, title, introduced_at, next_review_at, interval_days)
               VALUES (?, ?, ?, ?, 1)""",
            (page_rel, title, datetime.now().isoformat(timespec="seconds"),
             (date.today() + timedelta(days=1)).isoformat()),
        )


@router.post("/{item_id}/discard")
def discard(item_id: int):
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE inbox_items SET status = 'discarded', resolved_at = ? WHERE id = ?",
            (now, item_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "收件箱条目不存在")
    return {"ok": True}
