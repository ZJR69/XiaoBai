"""对话流：小白的对话主界面后端。

核心价值（PRD P2 / 交互设计文档 1.1）：
- 小白拥有用户全部项目记忆，对话零同步成本
- 对话中实体锚定（防幻觉）：回复中项目名等关联真实库条目
- 「回顾对话」→ 批量提案，所有入库经用户裁决（防污染铁律）
"""
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from app import llm
from app.database import get_conn

router = APIRouter(prefix="/api", tags=["chat"])

# backend/app/routers/chat.py → storage/wiki
WIKI_DIR = Path(__file__).resolve().parents[3] / "storage" / "wiki"


def _build_context() -> str:
    """从库中提取用户当前全局状态，注入 system prompt（小白的记忆）"""
    with get_conn() as conn:
        projects = conn.execute(
            "SELECT id, name, status FROM projects WHERE status = 'active'"
        ).fetchall()
        tasks = conn.execute(
            """SELECT t.title, t.status, t.due_at, p.name AS project
               FROM tasks t LEFT JOIN projects p ON t.project_id = p.id
               WHERE t.status IN ('backlog', 'active') ORDER BY t.priority LIMIT 30"""
        ).fetchall()
        inbox = conn.execute(
            "SELECT COUNT(*) AS n FROM inbox_items WHERE status = 'pending'"
        ).fetchone()
        reminders = conn.execute(
            "SELECT title FROM reminders WHERE active = 1 ORDER BY id"
        ).fetchall()

    lines = ["【用户的当前状态（小白的后台记忆，真实数据）】"]
    if projects:
        lines.append("进行中的项目：" + "、".join(p["name"] for p in projects))
    if tasks:
        lines.append("未完成任务：")
        for t in tasks:
            due = f"，截止 {t['due_at'][:10]}" if t["due_at"] else ""
            proj = f"（{t['project']}）" if t["project"] else ""
            lines.append(f"- {t['title']}{proj} [{t['status']}]{due}")
    if reminders:
        lines.append("生活提醒：" + "、".join(r["title"] for r in reminders))
    if inbox["n"]:
        lines.append(f"闪记池有 {inbox['n']} 条待处理")

    # 知识库目录（Karpathy Query 模式：问答先走 index）
    wiki_index = WIKI_DIR / "index.md"
    if wiki_index.exists():
        entries = [
            l for l in wiki_index.read_text(encoding="utf-8").splitlines()
            if l.startswith("- ") and "（暂无）" not in l
        ]
        if entries:
            lines.append("知识库目录（用户已消化的知识，涉及相关知识时优先引用这些条目）：")
            lines += entries[:50]
    return "\n".join(lines)


SYSTEM_PROMPT = """你是「小白」，用户的个人管理智能体，常驻在用户电脑上。

你的角色：
- 你管理用户的任务、项目、知识、日程和生活琐事，是唯一权威记录处的维护者
- 你聪明、直接、克制，正常大模型语气，不刻意角色扮演
- 用户多任务并行容易焦虑，你帮用户做减法：砍任务、排优先级，而不是堆更多
- 涉及修改用户数据（改日期、建任务、入库知识）时，你提出建议并等用户确认，绝不擅自执行

约束：
- 只引用下方【当前状态】里真实存在的条目，不要编造不存在的任务或项目
- 回复简洁，说重点，必要时用短列表
- 用户说「处理这几条闪记」时：逐条理解，看懂的就提议去向（建任务/知识/丢弃，给出建议的标题、日期、归属），看不懂或有歧义就先问清楚再动手；永远等用户确认"""


class ChatIn(BaseModel):
    message: str
    history: list[dict] = []  # [{role, content}]，前端传最近若干轮


@router.post("/chat")
def chat(body: ChatIn):
    now = datetime.now().isoformat(timespec="seconds")
    today = now[:10]

    # 持久化用户消息（记忆同步的数据源）
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chat_messages (session_date, role, content, created_at) VALUES (?, 'user', ?, ?)",
            (today, body.message, now),
        )

    if not llm.llm_available():
        reply = "我还没有接入大模型。请在 backend/.env 里配置 DEEPSEEK_API_KEY（参考 .env.example），配置后重启后端，我就能带着你的项目记忆和你对话了。"
    else:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + _build_context()},
            *body.history[-20:],  # 最近 20 条防超长
            {"role": "user", "content": body.message},
        ]
        try:
            reply = llm.chat_completion(messages)
        except Exception as e:  # LLM 调用失败不能让对话崩掉
            reply = f"（调用大模型失败：{e}。请检查 API key 和网络。）"

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chat_messages (session_date, role, content, created_at) VALUES (?, 'assistant', ?, ?)",
            (today, reply, now),
        )
    return {"reply": reply}


@router.get("/chat/history")
def chat_history(date: str | None = None):
    """拉取某天的对话记录（默认今天）。前端刷新后恢复对话流，不做只写不读的假持久化。"""
    day = date or datetime.now().isoformat()[:10]
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content, created_at FROM chat_messages WHERE session_date = ? ORDER BY id",
            (day,),
        ).fetchall()
    return {"date": day, "messages": [dict(r) for r in rows]}


class ReviewIn(BaseModel):
    messages: list[dict]  # 本次要回顾的对话（含 role/content）


@router.post("/review")
def review(body: ReviewIn):
    """「回顾对话」：批量生成提案清单（防污染：入库前必须经用户逐条裁决）"""
    if not llm.llm_available():
        return {"proposals": [], "note": "未配置 DEEPSEEK_API_KEY，回顾功能暂不可用。"}

    transcript = "\n".join(
        f"{'用户' if m['role'] == 'user' else '小白'}：{m['content']}" for m in body.messages
    )
    prompt = f"""回顾以下对话，提取值得入库的共识。返回 JSON 数组，每项：
{{"kind": "task|knowledge|update", "title": "简短标题", "detail": "具体内容",
  "target": "仅 update 需要：要更新的任务或项目的准确名称（从对话或当前状态中找）",
  "evidence": "来源对话摘录", "action": "新增任务|新增知识条目|更新现有条目"}}
只提取真实形成共识的内容，宁缺毋滥。没有就返回 []。

对话：
{transcript}"""
    try:
        raw = llm.chat_completion(
            [{"role": "system", "content": "你是小白的记忆同步引擎，只输出 JSON。"},
             {"role": "user", "content": prompt}],
            temperature=0.2,
        )
        proposals = llm.extract_json(raw) or []
    except Exception as e:
        return {"proposals": [], "note": f"回顾失败：{e}"}
    return {"proposals": proposals}


class ProposalDecision(BaseModel):
    proposals: list[dict]  # 用户裁决通过的提案（含 kind/title/detail）


@router.post("/proposals/apply")
def apply_proposals(body: ProposalDecision):
    """裁决通过的提案执行入库。task 建任务；knowledge 写入 wiki（sources 下对话沉淀页）；
    update 挂靠目标条目的进展日志（PRD FR-3.1：对话中的信息可挂靠）。
    留痕如实：执行失败的提案记 user_action='failed'，不谎报 accepted。"""
    from app.routers.raw import _append_log, _update_index

    applied = []
    now = datetime.now().isoformat(timespec="seconds")
    today = now[:10]

    def _find(conn, table: str, name: str):
        """按名称精确 → 模糊匹配任务/项目（tasks 列为 title，projects 列为 name）"""
        col = "title" if table == "tasks" else "name"
        exact = conn.execute(
            f"SELECT id, {col} AS name FROM {table} WHERE {col} = ? LIMIT 1", (name,)
        ).fetchone()
        if exact:
            return exact
        return conn.execute(
            f"SELECT id, {col} AS name FROM {table} WHERE {col} LIKE ? ORDER BY id LIMIT 1",
            (f"%{name}%",),
        ).fetchone()

    with get_conn() as conn:
        for p in body.proposals:
            result = None
            if p.get("kind") == "task":
                conn.execute(
                    "INSERT INTO tasks (title, status, created_at) VALUES (?, 'backlog', ?)",
                    (p.get("title") or p.get("detail", "")[:100], now),
                )
                result = {"kind": "task", "title": p.get("title"), "ok": True}
            elif p.get("kind") == "knowledge":
                title = (p.get("title") or "未命名知识条目").strip()
                slug = re.sub(r'[\\/:*?"<>|\s]+', "-", title)[:60]
                page_rel = f"sources/{slug}.md"
                page = WIKI_DIR / page_rel
                front = (
                    f"---\ntype: source\ncreated: {today}\nupdated: {today}\n"
                    f"source: conversation\n---\n\n"
                )
                body_text = f"# {title}\n\n{p.get('detail', '')}\n"
                if p.get("evidence"):
                    body_text += f"\n> 来源对话：{p['evidence']}\n"
                page.parent.mkdir(parents=True, exist_ok=True)
                page.write_text(front + body_text, encoding="utf-8")
                _update_index(page_rel, title)
                _append_log(f"## [{today}] ingest | 对话沉淀：{title}")
                result = {"kind": "knowledge", "title": title, "page": page_rel, "ok": True}
            elif p.get("kind") == "update":
                target = (p.get("target") or p.get("title") or "").strip()
                detail = p.get("detail", "")
                row = _find(conn, "tasks", target) if target else None
                if row:
                    conn.execute(
                        "INSERT INTO progress_logs (task_id, kind, source, content, created_at) VALUES (?, 'note', 'agent', ?, ?)",
                        (row["id"], detail, now),
                    )
                    result = {"kind": "update", "title": row["name"], "ok": True, "note": "已挂靠任务进展日志"}
                else:
                    proj = _find(conn, "projects", target) if target else None
                    if proj:
                        conn.execute(
                            "INSERT INTO progress_logs (project_id, kind, source, content, created_at) VALUES (?, 'note', 'agent', ?, ?)",
                            (proj["id"], detail, now),
                        )
                        result = {"kind": "update", "title": proj["name"], "ok": True, "note": "已挂靠项目进展日志"}
                    else:
                        result = {
                            "kind": "update", "title": target or "(无目标)",
                            "ok": False, "error": "未找到目标任务/项目，未执行",
                        }
            else:
                result = {"kind": p.get("kind"), "title": p.get("title"), "ok": False, "error": "未知提案类型"}

            applied.append(result)
            conn.execute(
                "INSERT INTO agent_suggestions (date, kind, content, user_action, created_at) VALUES (?, ?, ?, ?, ?)",
                (today, "memory_sync", str(p), "accepted" if result.get("ok") else "failed", now),
            )
    return {"applied": applied}
