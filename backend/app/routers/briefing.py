"""M4 FR-4.2 每日简报：今日三件事 + 杂务提醒 + 本周回顾 + 并行度预警。

- 当天生成一次缓存（briefings 表），LLM 不可用时降级为模板版
- 注入三层记忆（chat._build_context）+ 今日日程 + 临近截止，小白「带着全部记忆」做简报
- shown_at：当天首次在对话流展示后置位，之后不再自动出现（侧栏可手动重看）
"""
from datetime import datetime, timedelta

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import llm
from app.database import get_conn

router = APIRouter(prefix="/api/briefing", tags=["briefing"])


def _today() -> str:
    return datetime.now().date().isoformat()


def _build_briefing_data() -> str:
    """组装简报原料：三层记忆 + 今日日程 + 截止临近 + 本周动态"""
    from app.routers.chat import _build_context
    from app.routers.scheduler import slots_for_date

    parts = [_build_context()]

    # 今日日程
    slots = slots_for_date()
    if slots:
        lines = ["今日日程："]
        for s in slots:
            loc = f"@{s['location']}" if s["location"] else ""
            lines.append(f"- {s['start_time']}–{s['end_time']} {s['title']} {loc}")
        parts.append("\n".join(lines))

    # 临近截止（未来 3 天）
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT title, due_at FROM tasks
               WHERE due_at IS NOT NULL AND status IN ('backlog', 'active')
                 AND due_at >= ? AND due_at <= ? ORDER BY due_at""",
            (_today(), (datetime.now() + timedelta(days=3)).date().isoformat()),
        ).fetchall()
    if rows:
        parts.append("临近截止（3 天内）：\n" + "\n".join(
            f"- {r['title']}（{r['due_at'][:10]}）" for r in rows
        ))

    # 本周项目动态
    week_ago = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
    with get_conn() as conn:
        logs = conn.execute(
            """SELECT p.name, l.kind, l.content, l.created_at FROM progress_logs l
               JOIN projects p ON l.project_id = p.id
               WHERE l.created_at >= ? ORDER BY l.id DESC LIMIT 10""",
            (week_ago,),
        ).fetchall()
    if logs:
        parts.append("本周事务动态：\n" + "\n".join(
            f"- {r['name']}：{r['content'][:50]}（{r['created_at'][:10]}）" for r in logs
        ))

    return "\n\n".join(parts)


BRIEFING_PROMPT = """你是小白，用户的个人管理智能体。基于以下真实数据生成今日早间简报。

要求：
- 开头一句自然的话（不要「好的，以下是简报」这种废话）
- 「今日三件事」：从任务和事务里挑出今天最值得推进的 3 件，说明为什么是它们
- 「杂务提醒」：生活提醒类，没有就不写这节
- 「本周回顾」：本周事务动态的一句话总结，没有就略过
- 「并行度预警」：未完成任务 + 进行中的事总数超过 8 件时，提醒该收束（砍/延/委派），没超就不写
- 语气克制直接，全文不超过 250 字，markdown 列表
- 只引用数据里真实存在的条目，不要编造"""


def _generate() -> str:
    data = _build_briefing_data()
    if llm.llm_available():
        try:
            return llm.chat_completion(
                [{"role": "system", "content": BRIEFING_PROMPT},
                 {"role": "user", "content": data}],
                temperature=0.4,
            )
        except Exception as e:
            # LLM 失败降级：模板版简报（保证每天早上有东西看）
            return _template(data, f"（LLM 暂不可用：{e}）")
    return _template(data, "")


def _template(data: str, note: str) -> str:
    """无 LLM 时的降级模板：直接抽数据里的行"""
    lines = ["早上好。", ""]
    for section in ("今日日程：", "临近截止（3 天内）：", "本周事务动态：", "未完成任务："):
        idx = data.find(section)
        if idx >= 0:
            block = data[idx:].split("\n\n")[0]
            lines.append(block)
            lines.append("")
    if note:
        lines.append(note)
    return "\n".join(lines).strip()


def _get_or_create(today: str) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM briefings WHERE date = ?", (today,)).fetchone()
        if row:
            return dict(row)
    content = _generate()
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO briefings (date, content, created_at) VALUES (?, ?, ?)",
            (today, content, now),
        )
    return {"date": today, "content": content, "created_at": now, "shown_at": None}


@router.get("")
def get_briefing(refresh: bool = False):
    """获取今日简报（默认当天缓存）。refresh=true 重新生成。"""
    today = _today()
    if refresh:
        now = datetime.now().isoformat(timespec="seconds")
        content = _generate()
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO briefings (date, content, created_at) VALUES (?, ?, ?)
                   ON CONFLICT(date) DO UPDATE SET content = excluded.content, created_at = excluded.created_at""",
                (today, content, now),
            )
    return _get_or_create(today)


@router.get("/should-show")
def should_show():
    """当天简报是否尚未在对话流展示过（首次打开应用时前端轮询）"""
    with get_conn() as conn:
        row = conn.execute("SELECT shown_at FROM briefings WHERE date = ?", (_today(),)).fetchone()
    if row and row["shown_at"]:
        return {"show": False}
    # 当天尚未生成也不强制——让前端拿到 briefing 内容后再标记
    return {"show": True}


@router.post("/mark-shown")
def mark_shown():
    with get_conn() as conn:
        conn.execute(
            "UPDATE briefings SET shown_at = ? WHERE date = ?",
            (datetime.now().isoformat(timespec="seconds"), _today()),
        )
    return {"ok": True}
