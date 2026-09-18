"""对话流：小白的对话主界面后端。

核心价值（PRD P2 / 交互设计文档 1.1）：
- 小白拥有用户全部项目记忆，对话零同步成本
- 对话中实体锚定（防幻觉）：回复中项目名等关联真实库条目
- 「回顾对话」→ 批量提案，所有入库经用户裁决（防污染铁律）
"""
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app import llm
from app.database import get_conn

router = APIRouter(prefix="/api", tags=["chat"])


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

    lines = ["【用户的当前状态（小白的后台记忆，真实数据）】"]
    if projects:
        lines.append("进行中的项目：" + "、".join(p["name"] for p in projects))
    if tasks:
        lines.append("未完成任务：")
        for t in tasks:
            due = f"，截止 {t['due_at'][:10]}" if t["due_at"] else ""
            proj = f"（{t['project']}）" if t["project"] else ""
            lines.append(f"- {t['title']}{proj} [{t['status']}]{due}")
    if inbox["n"]:
        lines.append(f"闪记池有 {inbox['n']} 条待处理")
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
    """裁决通过的提案执行入库。task 建任务；knowledge 写入 wiki（sources 下对话沉淀页）。"""
    import re
    from pathlib import Path

    from app.routers.raw import _append_log, _update_index

    applied = []
    now = datetime.now().isoformat(timespec="seconds")
    today = now[:10]
    wiki_dir = Path(__file__).resolve().parents[3] / "storage" / "wiki"

    with get_conn() as conn:
        for p in body.proposals:
            if p.get("kind") == "task":
                conn.execute(
                    "INSERT INTO tasks (title, status, created_at) VALUES (?, 'backlog', ?)",
                    (p.get("title") or p.get("detail", "")[:100], now),
                )
                applied.append({"kind": "task", "title": p.get("title")})
            elif p.get("kind") == "knowledge":
                title = (p.get("title") or "未命名知识条目").strip()
                slug = re.sub(r'[\\/:*?"<>|\s]+', "-", title)[:60]
                page_rel = f"sources/{slug}.md"
                page = wiki_dir / page_rel
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
                applied.append({"kind": "knowledge", "title": title, "page": page_rel})
            conn.execute(
                "INSERT INTO agent_suggestions (date, kind, content, user_action, created_at) VALUES (?, ?, ?, 'accepted', ?)",
                (today, "memory_sync", str(p), now),
            )
    return {"applied": applied}
