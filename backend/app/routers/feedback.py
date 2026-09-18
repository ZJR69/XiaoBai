"""M4 FR-4.4 反馈对话 + FR-4.5 温和收束自校准。

晚间复盘流程（对话重，不弹表单）：
1. 20 点后当天首次打开 → should-start 返回 pending → 前端自动发起
2. start：小白在对话流发开场白（注入今日完成情况），建 feedback_session
3. 用户自然语言聊（就在普通对话流里）
4. 结束复盘：extract → LLM 提取信号（实际完成/负荷感受/修正意见）入库
5. 自校准：负荷过载 → 并行度阈值 -1；轻松 → +1（简报的收束预警用这个阈值）
"""
import json
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app import llm
from app.database import get_conn

router = APIRouter(prefix="/api/feedback", tags=["feedback"])

FEEDBACK_HOUR = 20  # 20 点后发起
DEFAULT_PARALLEL_THRESHOLD = 8
THRESHOLD_MIN, THRESHOLD_MAX = 3, 15


def get_parallel_threshold() -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'parallel_threshold'").fetchone()
    try:
        return int(row["value"]) if row else DEFAULT_PARALLEL_THRESHOLD
    except (ValueError, TypeError):
        return DEFAULT_PARALLEL_THRESHOLD


def _adjust_threshold(load: str) -> int | None:
    """负荷信号 → 阈值微调。返回新阈值（无变化返回 None）。"""
    old = get_parallel_threshold()
    new = old
    if load == "overloaded":
        new = max(THRESHOLD_MIN, old - 1)
    elif load == "easy":
        new = min(THRESHOLD_MAX, old + 1)
    if new != old:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO settings (key, value) VALUES ('parallel_threshold', ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (str(new),),
            )
    return new if new != old else None


def _today() -> str:
    return datetime.now().date().isoformat()


@router.get("/should-start")
def should_start():
    """晚间复盘状态：pending（该开始了）/ in_progress / done / not_yet（还没到点）。
    先查当日 session（进行中的复盘跨天也不丢按钮），再判时段。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, signals FROM feedback_sessions WHERE date = ?", (_today(),)
        ).fetchone()
    if row:
        return {"status": "done" if row["signals"] else "in_progress", "session_id": row["id"]}
    if datetime.now().hour < FEEDBACK_HOUR:
        return {"status": "not_yet"}
    return {"status": "pending"}


@router.post("/start")
def start():
    """发起复盘：开场白写入对话流（LLM 生成，注入今日完成情况）"""
    status = should_start()
    if status["status"] != "pending":
        return {"ok": False, "status": status["status"]}

    # 今日上下文：完成/未完成任务、今日日程
    with get_conn() as conn:
        done_tasks = conn.execute(
            "SELECT title FROM tasks WHERE status = 'done' AND completed_at LIKE ?",
            (_today() + "%",),
        ).fetchall()
        open_tasks = conn.execute(
            "SELECT title FROM tasks WHERE status IN ('backlog', 'active') LIMIT 10"
        ).fetchall()
    from app.routers.scheduler import slots_for_date
    slots = slots_for_date()

    data = (
        (f"今日已完成：{'、'.join(t['title'] for t in done_tasks) or '（无记录）'}\n"
         f"仍开放的任务：{'、'.join(t['title'] for t in open_tasks) or '（无）'}\n"
         f"今日日程：{'、'.join(s['title'] for s in slots) or '（无）'}")
    )

    if llm.llm_available():
        try:
            opening = llm.chat_completion(
                [{"role": "system",
                  "content": ("你是小白。现在是晚上，发起今日复盘对话。基于数据自然地问两三个问题："
                              "今天实际做了什么、感觉负荷如何（轻松/刚好/过载）、"
                              "对今天的安排有什么想调整的。语气像朋友间的收尾聊天，"
                              "不超过 100 字，不要列 bullet，就说话。")},
                 {"role": "user", "content": data}],
                temperature=0.6,
            )
        except Exception:
            opening = None
    else:
        opening = None
    if not opening:
        opening = "今天过得怎么样？实际做了什么、感觉如何（轻松/刚好/过载），随便聊聊，我记下来帮你校准后面的安排。"

    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO feedback_sessions (date, transcript, created_at) VALUES (?, '', ?)",
            (_today(), now),
        )
        conn.execute(
            "INSERT INTO chat_messages (session_date, role, content, created_at) VALUES (?, 'assistant', ?, ?)",
            (_today(), f"🌙 {opening}", now),
        )
    return {"ok": True, "opening": opening}


class ExtractIn(BaseModel):
    messages: list[dict]  # 复盘对话消息


@router.post("/extract")
def extract(body: ExtractIn):
    """结束复盘：提取结构化信号入库 + 阈值自校准（幂等：当天已提取过则拒绝）"""
    # 幂等保护：没有进行中的 session（未发起/已提取）直接拒绝，防止重复调整阈值
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM feedback_sessions WHERE date = ? AND signals IS NULL", (_today(),)
        ).fetchone()
    if not row:
        return {"ok": False, "error": "今天没有进行中的复盘，或已经结束过了"}

    transcript = "\n".join(
        f"{'用户' if m['role'] == 'user' else '小白'}：{m['content']}" for m in body.messages
    )
    signals = {"completed": [], "load": "ok", "notes": ""}
    if llm.llm_available():
        try:
            raw = llm.chat_completion(
                [{"role": "system",
                  "content": ('从复盘对话提取信号，返回 JSON：'
                              '{"completed": ["实际完成的事"], "load": "easy|ok|overloaded", "notes": "对安排的修正意见一句话"}'
                              '。没有的项留空，不要编造。')},
                 {"role": "user", "content": transcript}],
                temperature=0.2,
            )
            parsed = llm.extract_json(raw)
            # extract_json 可能返回 list——只要 dict
            if isinstance(parsed, dict):
                signals = parsed
        except Exception:
            pass

    new_threshold = _adjust_threshold(signals.get("load", "ok"))
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE feedback_sessions SET transcript = ?, signals = ?
               WHERE date = ? AND signals IS NULL""",
            (transcript, json.dumps(signals, ensure_ascii=False), _today()),
        )
        if cur.rowcount == 0:
            # 并发兜底：另一请求刚刚完成提取——不重复调阈值（已在上面调过则回滚不了，
            # 但 UPDATE rowcount=0 说明此刻才撞上，阈值调整以先到者为准）
            return {"ok": False, "error": "复盘刚被结束过"}
    return {
        "ok": True,
        "signals": signals,
        "threshold_changed": new_threshold,
        "threshold": new_threshold or get_parallel_threshold(),
    }
