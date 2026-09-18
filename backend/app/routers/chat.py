"""对话流：小白的对话主界面后端。

核心价值（PRD P2 / 交互设计文档 1.1 / PRD 4.6 记忆分层）：
- 三层记忆注入：core.md 底座全文 + 进行中的事索引 + 实体索引（对标 Claude Code / ChatGPT）
- 关键词触发：消息提到某项目/人物名 → 自动附加其详情（进展日志/实体页）
- 「回顾对话」→ 批量提案（含去向 task/update/knowledge/core/entity），所有入库经用户裁决（防污染铁律）
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
CORE_MD = WIKI_DIR / "core.md"
ENTITIES_DIR = WIKI_DIR / "entities"

CATEGORY_LABELS = {
    "research": "科研", "collaboration": "合作", "family": "家庭",
    "personal": "个人", "club": "社团", "other": "其他",
}


def _page_title(path: Path, default: str) -> str:
    """读 markdown 首个 # 标题，失败用默认名"""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    except OSError:
        pass
    return default


def _ongoing_lines(conn) -> list[str]:
    """进行中的事索引：每件一行（名称 + 类别 + 下一步 + 最近动态），封顶 30 条（PRD 4.6）"""
    rows = conn.execute(
        """SELECT p.id, p.name, p.category,
                  (SELECT content FROM progress_logs WHERE project_id = p.id AND kind = 'next_step' ORDER BY id DESC LIMIT 1) AS next_step,
                  (SELECT kind || '：' || substr(content, 1, 60) FROM progress_logs WHERE project_id = p.id ORDER BY id DESC LIMIT 1) AS last_activity
           FROM projects p WHERE p.status != 'closed' ORDER BY p.id DESC LIMIT 30"""
    ).fetchall()
    return [
        f"- {r['name']}（{CATEGORY_LABELS.get(r['category'], '其他')}）"
        + (f" 下一步：{r['next_step'][:60]}" if r["next_step"] else "")
        + (f"｜最近：{r['last_activity']}" if r["last_activity"] else "")
        for r in rows
    ]


def _entity_lines() -> list[str]:
    """实体索引：每条一行（名字 + 一句话描述），Claude Code MEMORY.md 同款模式"""
    lines = []
    if ENTITIES_DIR.exists():
        for f in sorted(ENTITIES_DIR.glob("*.md")):
            title = _page_title(f, f.stem)
            # 正文第一句作为描述
            desc = ""
            for line in f.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if s and not s.startswith(("#", "-", "---", ">")) and not s.startswith("type:") and ":" not in s[:12]:
                    desc = s.lstrip("- ").strip()
                    break
            lines.append(f"- {title}：{desc[:80]}" if desc else f"- {title}")
    return lines


def _build_context() -> str:
    """三层记忆注入（PRD 4.6）：core 全文 + 事务索引 + 实体索引 + 知识库目录"""
    with get_conn() as conn:
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
        ongoing = _ongoing_lines(conn)

    lines = ["【用户的当前状态（小白的后台记忆，真实数据）】"]

    # 第一层：core.md 底座全文
    if CORE_MD.exists():
        core = CORE_MD.read_text(encoding="utf-8").strip()
        if core:
            lines.append("【核心档案（底座记忆，core.md 全文）】")
            lines.append(core)

    # 第二层：进行中的事索引
    if ongoing:
        lines.append("进行中的事（详情可查，需要时问小白）：")
        lines += ongoing

    # 任务（带截止）
    if tasks:
        lines.append("未完成任务：")
        for t in tasks:
            due = f"，截止 {t['due_at'][:10]}" if t["due_at"] else ""
            proj = f"（{t['project']}）" if t["project"] else ""
            lines.append(f"- {t['title']}{proj} [{t['status']}]{due}")

    # 第三层：实体索引
    entities = _entity_lines()
    if entities:
        lines.append("人物/机构（提到名字时小白知道去哪找详情）：")
        lines += entities

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


def _expand_mentions(message: str) -> str:
    """关键词触发（PRD 4.6 注入层第 5 条）：消息提到项目/人物名 → 附加其详情。
    Claude Code 同款精确关键词匹配，无需向量库。"""
    if not message:
        return ""
    extras = []
    with get_conn() as conn:
        projects = conn.execute(
            "SELECT id, name FROM projects WHERE status != 'closed'"
        ).fetchall()
        for p in projects:
            if p["name"] and p["name"] in message:
                logs = conn.execute(
                    "SELECT kind, content, created_at FROM progress_logs WHERE project_id = ? ORDER BY id DESC LIMIT 5",
                    (p["id"],),
                ).fetchall()
                if logs:
                    body = "\n".join(
                        f"- [{lg['kind']}] {lg['content']}（{lg['created_at'][:10]}）"
                        for lg in reversed(logs)
                    )
                    extras.append(f"### 项目《{p['name']}》最近进展\n{body}")
    if ENTITIES_DIR.exists():
        for f in sorted(ENTITIES_DIR.glob("*.md")):
            title = _page_title(f, f.stem)
            if title and title in message:
                extras.append(f"### 实体页《{title}》\n{f.read_text(encoding='utf-8')[:3000]}")
    return "\n\n".join(extras)


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
        context = _build_context()
        extra = _expand_mentions(body.message)
        if extra:
            context += "\n\n【相关详情（因为你提到了它，自动展开）】\n" + extra
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + context},
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
{{"kind": "task|knowledge|update|core|entity", "title": "简短标题（core 为小节名，entity 为人物/机构名）",
  "detail": "具体内容",
  "target": "仅 update 需要：要更新的任务或项目的准确名称（从对话中找）",
  "evidence": "来源对话摘录",
  "action": "新增任务|新增知识|更新现有条目|写入核心档案|新增人物/机构页"}}

去向判断：
- 用户的底座信息（组织性质、战略定位、关键人物名单、长期方向、我是谁）→ core
- 具体某个人物/机构的细节（风格、诉求、关系）→ entity
- 一次性待办 → task
- 可消化的知识点 → knowledge
- 对既有任务/项目的进展更新 → update（target 填名称）

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
    proposals: list[dict]  # 用户裁决通过的提案（含 kind/title/detail/target）


def _write_core_section(title: str, detail: str) -> tuple[bool, str]:
    """写 core.md 底座记忆：同名小节覆盖（更新即覆盖，历史在 log），新小节追加。"""
    CORE_MD.parent.mkdir(parents=True, exist_ok=True)
    if not CORE_MD.exists():
        CORE_MD.write_text(
            "# 核心档案\n\n> 底座记忆：保持精炼，详见 schema.md。\n", encoding="utf-8"
        )
    content = CORE_MD.read_text(encoding="utf-8")
    section = f"## {title}\n\n{detail.strip()}\n"
    pattern = re.compile(rf"^## {re.escape(title)}[ \t]*\n(?:(?!^## ).*\n?)*", re.MULTILINE)
    if pattern.search(content):
        content = pattern.sub(section, content)
        note = "小节已存在，已覆盖更新"
    else:
        content = content.rstrip() + "\n\n" + section
        note = "新增小节"
    CORE_MD.write_text(content, encoding="utf-8")
    return True, note


def _register_review(page: str, title: str):
    """入库知识登记间隔重现（M5 FR-5.1：1 天后首次重现）"""
    from datetime import date, timedelta

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO spaced_reviews (page, title, introduced_at, next_review_at, interval_days)
               VALUES (?, ?, ?, ?, 1)""",
            (page, title, datetime.now().isoformat(timespec="seconds"),
             (date.today() + timedelta(days=1)).isoformat()),
        )


def _write_entity_page(title: str, detail: str, today: str) -> tuple[bool, str]:
    """写 entities/ 实体页：已存在则追加更新记录，不存在则新建。"""
    slug = re.sub(r'[\\/:*?"<>|\s]+', "-", title)[:60]
    page_rel = f"entities/{slug}.md"
    page = WIKI_DIR / page_rel
    page.parent.mkdir(parents=True, exist_ok=True)
    if page.exists():
        old = page.read_text(encoding="utf-8")
        # 更新 frontmatter 的 updated + 追加更新记录
        old = re.sub(r"^updated: .*$", f"updated: {today}", old, count=1, flags=re.MULTILINE)
        content = old.rstrip() + f"\n\n## 更新 {today}\n\n{detail.strip()}\n"
        note = "实体页已存在，追加更新"
    else:
        front = f"---\ntype: entity\ncreated: {today}\nupdated: {today}\n---\n\n"
        content = front + f"# {title}\n\n{detail.strip()}\n"
        note = "新建实体页"
    page.write_text(content, encoding="utf-8")
    return True, note


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
                _register_review(page_rel, title)  # 间隔重现登记
            elif p.get("kind") == "core":
                title = (p.get("title") or "杂项").strip()
                ok, note = _write_core_section(title, p.get("detail", ""))
                _append_log(f"## [{today}] core | {title}")
                result = {"kind": "core", "title": title, "ok": ok, "note": note}
            elif p.get("kind") == "entity":
                title = (p.get("title") or "未命名实体").strip()
                ok, note = _write_entity_page(title, p.get("detail", ""), today)
                slug = re.sub(r'[\\/:*?"<>|\s]+', "-", title)[:60]
                page_rel = f"entities/{slug}.md"
                _update_index(page_rel, title, section="entities")
                _append_log(f"## [{today}] entity | {title}")
                result = {"kind": "entity", "title": title, "ok": ok, "note": note, "page": page_rel}
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
