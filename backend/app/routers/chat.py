"""对话流：小白的对话主界面后端。

核心价值（PRD P2 / 交互设计文档 1.1 / PRD 4.6 记忆分层）：
- 三层记忆注入：core.md 底座全文 + 进行中的事索引 + 实体索引（对标 Claude Code / ChatGPT）
- 关键词触发：消息提到某项目/人物名 → 自动附加其详情（进展日志/实体页）
- 「回顾对话」→ 批量提案（含去向 task/update/knowledge/core/entity），所有入库经用户裁决（防污染铁律）
"""
import json
import re
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
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
    """三层记忆注入（PRD 4.6）：今天日期行（时间意识锚点）+ core 全文 + 事务索引 + 实体索引 + 知识库目录"""
    from app.routers.scheduler import today_line

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

    lines = [today_line(), "", "【用户的当前状态（小白的后台记忆，真实数据）】"]

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
    """关键词触发（PRD 4.6 注入层第 5 条）：消息提到项目/任务/人物名 → 附加其完整上下文。
    TRAE 式引用语义：名字一出现，小白就拿到该条目的背景，不依赖用户复述。"""
    if not message:
        return ""
    extras = []
    with get_conn() as conn:
        projects = conn.execute(
            "SELECT id, name, description FROM projects WHERE status != 'closed'"
        ).fetchall()
        for p in projects:
            if p["name"] and p["name"] in message:
                # 完整背景：描述 + 下一步 + 挂靠的未完成任务 + 最近日志
                parts = [f"### 项目《{p['name']}》上下文"]
                if p["description"]:
                    parts.append(f"描述：{p['description']}")
                nxt = conn.execute(
                    "SELECT content FROM progress_logs WHERE project_id = ? AND kind = 'next_step' ORDER BY id DESC LIMIT 1",
                    (p["id"],),
                ).fetchone()
                if nxt:
                    parts.append(f"下一步：{nxt['content']}")
                open_tasks = conn.execute(
                    "SELECT title, status, due_at FROM tasks WHERE project_id = ? AND status IN ('backlog', 'active') ORDER BY priority",
                    (p["id"],),
                ).fetchall()
                if open_tasks:
                    task_lines = []
                    for t in open_tasks:
                        due = f"，截止 {t['due_at'][:10]}" if t["due_at"] else ""
                        task_lines.append(f"- {t['title']} [{t['status']}]{due}")
                    parts.append("挂靠任务：\n" + "\n".join(task_lines))
                logs = conn.execute(
                    "SELECT kind, content, created_at FROM progress_logs WHERE project_id = ? ORDER BY id DESC LIMIT 5",
                    (p["id"],),
                ).fetchall()
                if logs:
                    parts.append("最近进展：\n" + "\n".join(
                        f"- [{lg['kind']}] {lg['content']}（{lg['created_at'][:10]}）"
                        for lg in reversed(logs)
                    ))
                extras.append("\n".join(parts))
        # 任务名命中 → 展开任务详情（含所属项目）。独立于项目命中——
        # 旧版被 mentioned_ids 门控，只提任务名不提项目名时不展开（bug）
        tasks = conn.execute(
            """SELECT t.title, t.status, t.due_at, t.priority, p.name AS project
               FROM tasks t LEFT JOIN projects p ON t.project_id = p.id
               WHERE t.status IN ('backlog', 'active')"""
        ).fetchall()
        for t in tasks:
            if t["title"] and t["title"] in message:
                proj = f"，属于《{t['project']}》" if t["project"] else ""
                due = f"，截止 {t['due_at'][:10]}" if t["due_at"] else ""
                extras.append(f"### 任务《{t['title']}》\n状态 {t['status']}，优先级 P{t['priority']}{proj}{due}")
    if ENTITIES_DIR.exists():
        for f in sorted(ENTITIES_DIR.glob("*.md")):
            title = _page_title(f, f.stem)
            if title and title in message:
                extras.append(f"### 实体页《{title}》\n{f.read_text(encoding='utf-8')[:3000]}")
    return "\n\n".join(extras)


# ── 日期触发的日程召回（查询式注入，不是全塞）──
# 消息提到日期词 → LLM 提取出具体日期 → 只展开这些日期的真实日程。没提日期的消息零开销。

DATE_HINT = re.compile(
    r"(今天|明天|后天|大后天|昨天|前天|周[一二三四五六日天]|星期[一二三四五六日天]|礼拜[一二三四五六日天]"
    r"|下{1,2}周|本周|这周|\d{1,2}\s*月\s*\d{1,2}\s*[日号]?|\d{1,2}\s*[日号]|\d{4}-\d{1,2}-\d{1,2})"
)

DATE_EXTRACT_PROMPT = """从用户消息中提取其明确提到的所有日期，输出 JSON 数组（元素为 YYYY-MM-DD 字符串），没有就输出 []。
今天是 {today}（{weekday}），以此为基准：
- 今天/明天/后天/大后天/昨天/前天 直接推算
- 「周X/星期X」不带修饰：未来最近的那个周X（含今天）；「这周/本周周X」：本周（周一起算）；「下周周X」：下一周
- 「X月X日」：今年；「X号/X日」：本月
- 一句话里可能提到多个日期，必须全部提取，一个不漏；作为参照的星期也算（例：「周日要上周二的课」→ 周日和周二两个日期都要输出）
- 不要推测消息里没有的日期
只输出 JSON 数组本身，不要任何解释。"""


def _rule_dates(message: str, today: date) -> list[date]:
    """确定性日期解析（parser 主体，覆盖高频表达，保证这些表达 100% 不漏不错）：
    相对日（今天/明天/…）、(下下周/下周/下/这周/本周/这)?(周/星期/礼拜)X、X月X日、YYYY-MM-DD。"""
    from datetime import timedelta

    wd_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    out = set()
    for w, off in (("大后天", 3), ("后天", 2), ("明天", 1), ("今天", 0), ("昨天", -1), ("前天", -2)):
        if w in message:
            out.add(today + timedelta(days=off))
    monday = today - timedelta(days=today.weekday())
    for m in re.finditer(r"(下下周|下周|下|这周|本周|这)?[周星期礼拜]([一二三四五六日天])", message):
        prefix, wd = m.group(1), wd_map[m.group(2)]
        if prefix == "下下周":
            out.add(monday + timedelta(days=14 + wd))
        elif prefix in ("下周", "下"):
            out.add(monday + timedelta(days=7 + wd))
        elif prefix in ("这周", "本周", "这"):
            out.add(monday + timedelta(days=wd))
        else:
            out.add(today + timedelta(days=(wd - today.weekday()) % 7))  # 裸 周X：未来最近（含今天）
    for m in re.finditer(r"(\d{4})-(\d{1,2})-(\d{1,2})", message):
        try:
            out.add(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    for m in re.finditer(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", message):
        try:
            out.add(date(today.year, int(m.group(1)), int(m.group(2))))
        except ValueError:
            pass
    return sorted(out)


def _schedule_recall(message: str) -> str:
    """日期词触发的日程召回（查询式注入，不是全塞）：规则解析保证高频表达不漏 +
    LLM 提取补长尾（如「圣诞」「3号」），取并集后逐日展开真实日程。
    预筛无日期痕迹的消息直接返回，不为闲聊多付 LLM 调用。"""
    if not message or not DATE_HINT.search(message):
        return ""
    from app.routers.scheduler import WEEKDAY_NAMES, slots_for_date

    today = datetime.now().date()
    found = set(_rule_dates(message, today))
    if llm.llm_available():
        prompt = DATE_EXTRACT_PROMPT.format(today=today.isoformat(), weekday=WEEKDAY_NAMES[today.weekday()])
        try:
            raw = llm.chat_completion(
                [{"role": "system", "content": "你是日期提取器，只输出 JSON。"},
                 {"role": "user", "content": prompt + "\n\n消息：" + message}],
                temperature=0,
            )
            extracted = llm.extract_json(raw)
            if isinstance(extracted, list):
                for s in extracted:
                    try:
                        found.add(date.fromisoformat(str(s).strip()))
                    except ValueError:
                        continue
        except Exception:
            pass  # LLM 失败不挡主流程，规则解析结果仍可用
    if not found:
        return ""
    lines = []
    for d in sorted(found)[:5]:  # 封顶 5 天防上下文膨胀
        head = f"{d.isoformat()} {WEEKDAY_NAMES[d.weekday()]}" + ("（今天）" if d == today else "")
        slots = slots_for_date(d)
        if not slots:
            # 明确告知「空」，避免 LLM 误以为数据缺失而编日程
            lines.append(f"{head}：当天没有日程")
            continue
        lines.append(head + "：")
        for s in slots:
            loc = f" @{s['location']}" if s.get("location") else ""
            tag = "（一次性日程）" if s.get("source") == "event" else ""
            lines.append(f"- {s['start_time']}–{s['end_time']} {s['title']}{loc}{tag}")
    return "\n".join(lines)


SYSTEM_PROMPT = """你是「小白」，用户的个人管理智能体，常驻在用户电脑上。

你的角色：
- 你管理用户的任务、项目、知识、日程和生活琐事，是唯一权威记录处的维护者
- 你聪明、直接、克制，正常大模型语气，不刻意角色扮演
- 用户多任务并行容易焦虑，你帮用户做减法：砍任务、排优先级，而不是堆更多
- 涉及修改用户数据（改日期、建任务、入库知识）时，你提出建议并等用户确认，绝不擅自执行

约束：
- 上下文第一行是今天的真实日期和教学周；所有相对日期（周日、下周二）都以它为基准推算，绝不自己猜日期
- 只引用下方【当前状态】里真实存在的条目，不要编造不存在的任务或项目
- 回复简洁，说重点，必要时用短列表
- 用户说「处理这几条闪记」时：逐条理解，看懂的就提议去向（建任务/知识/丢弃，给出建议的标题、日期、归属），看不懂或有歧义就先问清楚再动手；永远等用户确认

操作指令（对话驱动操作，用户点确认后才会执行）：
- 用户让你做事（建任务、改日期、推截止、勾完成、加提醒、排日程）时，先用一两句自然的话回应，然后在回复最末尾输出一个操作块：
<<<ACTION>>>
{"op": "create_task", "target": null, "patch": {"title": "任务名", "due_at": "YYYY-MM-DD", "priority": 3}, "summary": "一句话说明这个操作做什么"}
<<<END>>>
- op 可选值：
  create_task（patch: title 必填，due_at/priority 可选，priority 1高-5低，project 可选=挂靠的进行中的事名称，必须是【当前状态】里真实存在的名称，一字不差；不确定就不带）
  update_task（target 填要改的任务名，patch 只放要改的字段：due_at/priority/title/status）
  complete_task（target 要完成的任务名，不需要 patch）
  create_reminder（patch: title、trigger_type: once/daily/weekly/interval、trigger_value 如 2026-10-01T09:00 或 09:00 或 2-09:00 或 4）
  create_event（一次性日程。patch: title 必填、date 必填 YYYY-MM-DD；start_time/end_time HH:MM、entry_type: course/meeting/outing/personal、location 可选）
  delete_event（删除一次性日程。target 填日程名，patch.date 可选=具体日期，用于区分同名日程）
- 日期一律 YYYY-MM-DD；target 必须是【当前状态】里真实存在的条目名
- 用户说「某天要上周X的课 / 要在某天加个安排」时：参照【你提到的日期对应的日程】（若已注入），为那一天逐门课/逐个安排各输出一个 create_event 操作块；日程里没有的课先问用户时间，不要编
- 任务明显属于某件进行中的事时（用户提到或【当前状态】里列出的），create_task 带上 project 挂靠；名称对不上就先问，不要猜
- 只是聊天、回答问题、给建议时不输出操作块；信息不全先问清楚，别猜着填"""


ACTION_BLOCK = re.compile(r"<<<ACTION>>>(.*?)<<<END>>>", re.DOTALL)


def _extract_actions(reply: str) -> tuple[str, list[dict]]:
    """从回复中剥离操作块：返回 (纯文本, 操作列表)。解析失败的块丢弃不执行。"""
    actions = []

    def _repl(m):
        try:
            a = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            return ""
        if isinstance(a, dict) and a.get("op"):
            actions.append(a)
        return ""

    text = ACTION_BLOCK.sub(_repl, reply).strip()
    return text, actions


class ChatIn(BaseModel):
    message: str
    history: list[dict] = []  # [{role, content}]，前端传最近若干轮
    session_id: int | None = None  # 所属会话；缺省则新建（标题取本条消息）


def _ensure_session(session_id: int | None, first_message: str, now: str) -> int:
    """会话处理：session_id 有效则沿用（并刷新活跃时间），否则新建。返回 session_id。"""
    with get_conn() as conn:
        if session_id:
            row = conn.execute("SELECT id FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
            if row:
                conn.execute("UPDATE chat_sessions SET updated_at = ? WHERE id = ?", (now, session_id))
                return session_id
        title = first_message.strip().replace("\n", " ")[:30] or "新对话"
        cur = conn.execute(
            "INSERT INTO chat_sessions (title, created_at, updated_at) VALUES (?, ?, ?)",
            (title, now, now),
        )
        return cur.lastrowid


@router.post("/chat")
def chat(body: ChatIn):
    now = datetime.now().isoformat(timespec="seconds")
    today = now[:10]
    sid = _ensure_session(body.session_id, body.message, now)

    # 持久化用户消息（记忆同步的数据源）
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chat_messages (session_date, role, content, created_at, session_id) VALUES (?, 'user', ?, ?, ?)",
            (today, body.message, now, sid),
        )

    if not llm.llm_available():
        reply = "我还没有接入大模型。请在 backend/.env 里配置 DEEPSEEK_API_KEY（参考 .env.example），配置后重启后端，我就能带着你的项目记忆和你对话了。"
    else:
        context = _build_context()
        extra = _expand_mentions(body.message)
        if extra:
            context += "\n\n【相关详情（因为你提到了它，自动展开）】\n" + extra
        sched = _schedule_recall(body.message)
        if sched:
            context += "\n\n【你提到的日期对应的日程（真实数据，来自日程表）】\n" + sched
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + context},
            *body.history[-20:],  # 最近 20 条防超长
            {"role": "user", "content": body.message},
        ]
        try:
            reply = llm.chat_completion(messages)
        except Exception as e:  # LLM 调用失败不能让对话崩掉
            reply = f"（调用大模型失败：{e}。请检查 API key 和网络。）"

    # 剥离操作块：正文持久化纯文本，操作以结构化卡片返回前端（用户确认后才执行）
    reply, actions = _extract_actions(reply)

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chat_messages (session_date, role, content, created_at, session_id) VALUES (?, 'assistant', ?, ?, ?)",
            (today, reply, now, sid),
        )
    return {"reply": reply, "actions": actions, "session_id": sid}


@router.get("/anchors")
def anchors():
    """实体锚点名单（IDS 2.1 防幻觉）：对话中这些真实条目名可点击跳转。
    锚点数据来自真实库——小白不能引用不存在的条目。"""
    out = []
    with get_conn() as conn:
        for p in conn.execute(
            "SELECT name, category FROM projects WHERE status != 'closed'"
        ).fetchall():
            out.append({"name": p["name"], "kind": "project", "hint": "进行中的事"})
        for t in conn.execute(
            "SELECT title, status, due_at FROM tasks WHERE status IN ('backlog', 'active')"
        ).fetchall():
            due = f"，截止 {t['due_at'][:10]}" if t["due_at"] else ""
            out.append({"name": t["title"], "kind": "task", "hint": f"任务 [{t['status']}]{due}"})
    for sub in ("entities", "topics", "sources"):
        d = WIKI_DIR / sub
        if d.exists():
            for f in sorted(d.glob("*.md")):
                title = _page_title(f, f.stem)
                if title and len(title) >= 2:
                    out.append({"name": title, "kind": "wiki", "hint": "知识库页"})
    return {"anchors": out}


# ── 会话管理（多会话 + 分支，参考常见大模型对话应用）──


@router.get("/chat/sessions")
def list_sessions():
    """会话列表，按最近活跃排序。parent_id 非空 = 分支会话。"""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT s.id, s.title, s.parent_id, s.created_at, s.updated_at,
                      (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id = s.id) AS n
               FROM chat_sessions s ORDER BY s.updated_at DESC, s.id DESC"""
        ).fetchall()
    return {"sessions": [dict(r) for r in rows]}


@router.delete("/chat/sessions/{sid}")
def delete_session(sid: int):
    """删除会话及其消息（分支会话是独立复制体，不受影响，仅解除父引用）。"""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM chat_sessions WHERE id = ?", (sid,))
        if cur.rowcount == 0:
            raise HTTPException(404, "会话不存在")
        conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (sid,))
        # 分支的 parent_id 指向本会话 → 置空，避免悬空引用
        conn.execute("UPDATE chat_sessions SET parent_id = NULL WHERE parent_id = ?", (sid,))
    return {"ok": True}


@router.get("/chat/sessions/{sid}/messages")
def session_messages(sid: int):
    """单个会话的消息（含 id，供分支定位）。若会话今天活跃，把今天的早报等
    天级消息（session_id 为 NULL）按时间序合并进来——它们属于今天，不属于某个会话。"""
    with get_conn() as conn:
        sess = conn.execute("SELECT * FROM chat_sessions WHERE id = ?", (sid,)).fetchone()
        if not sess:
            raise HTTPException(404, "会话不存在")
        rows = conn.execute(
            "SELECT id, role, content, created_at FROM chat_messages WHERE session_id = ? ORDER BY id",
            (sid,),
        ).fetchall()
        msgs = [dict(r) for r in rows]
        today = datetime.now().isoformat()[:10]
        if (sess["updated_at"] or "")[:10] == today:
            day_rows = conn.execute(
                """SELECT id, role, content, created_at FROM chat_messages
                   WHERE session_id IS NULL AND session_date = ? ORDER BY id""",
                (today,),
            ).fetchall()
            msgs = sorted([*msgs, *[dict(r) for r in day_rows]], key=lambda m: m["id"])
    return {"session": dict(sess), "messages": msgs}


class BranchIn(BaseModel):
    message_id: int  # 从哪条消息开始分叉（该消息及之前的全部历史被复制）


@router.post("/chat/sessions/{sid}/branch", status_code=201)
def branch_session(sid: int, body: BranchIn):
    """开对话分支：复制该会话截至 message_id 的全部消息为新会话，之后各走各路。"""
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        sess = conn.execute("SELECT * FROM chat_sessions WHERE id = ?", (sid,)).fetchone()
        if not sess:
            raise HTTPException(404, "会话不存在")
        pivot = conn.execute(
            "SELECT id FROM chat_messages WHERE id = ? AND session_id = ?", (body.message_id, sid)
        ).fetchone()
        if not pivot:
            raise HTTPException(404, "分支点消息不存在或不属于该会话")
        rows = conn.execute(
            "SELECT session_date, role, content, created_at FROM chat_messages WHERE session_id = ? AND id <= ? ORDER BY id",
            (sid, body.message_id),
        ).fetchall()
        title = (sess["title"] or "对话") + " · 分支"
        cur = conn.execute(
            "INSERT INTO chat_sessions (title, parent_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (title, sid, now, now),
        )
        new_sid = cur.lastrowid
        for r in rows:
            conn.execute(
                "INSERT INTO chat_messages (session_date, role, content, created_at, session_id) VALUES (?, ?, ?, ?, ?)",
                (r["session_date"], r["role"], r["content"], r["created_at"], new_sid),
            )
    return {"id": new_sid, "title": title, "parent_id": sid}


@router.post("/chat/stream")
def chat_stream(body: ChatIn):
    """流式对话（SSE）：增量推 delta，结束时推 {done, reply, actions, session_id}。
    持久化与操作块剥离逻辑与 /api/chat 完全一致。"""
    from fastapi.responses import StreamingResponse

    now = datetime.now().isoformat(timespec="seconds")
    today = now[:10]
    sid = _ensure_session(body.session_id, body.message, now)

    def _sse(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def gen():
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO chat_messages (session_date, role, content, created_at, session_id) VALUES (?, 'user', ?, ?, ?)",
                (today, body.message, now, sid),
            )
        full = ""
        if not llm.llm_available():
            full = "我还没有接入大模型。请在 backend/.env 里配置 DEEPSEEK_API_KEY（参考 .env.example），配置后重启后端，我就能带着你的项目记忆和你对话了。"
            yield _sse({"delta": full})
        else:
            context = _build_context()
            extra = _expand_mentions(body.message)
            if extra:
                context += "\n\n【相关详情（因为你提到了它，自动展开）】\n" + extra
            sched = _schedule_recall(body.message)
            if sched:
                context += "\n\n【你提到的日期对应的日程（真实数据，来自日程表）】\n" + sched
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + context},
                *body.history[-20:],
                {"role": "user", "content": body.message},
            ]
            try:
                for delta in llm.chat_completion_stream(messages):
                    full += delta
                    yield _sse({"delta": delta})
            except Exception as e:  # 中途失败：已流出的部分保留，补上错误说明
                full += f"\n（调用大模型失败：{e}。请检查 API key 和网络。）"
                yield _sse({"delta": f"\n（调用大模型失败：{e}。请检查 API key 和网络。）"})

        reply, actions = _extract_actions(full)
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO chat_messages (session_date, role, content, created_at, session_id) VALUES (?, 'assistant', ?, ?, ?)",
                (today, reply, now, sid),
            )
        yield _sse({"done": True, "reply": reply, "actions": actions, "session_id": sid})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── 对话驱动操作（IDS 1.1/2.4：操作指令 → 确认卡 → 执行/撤销）──

TASK_STATUSES = ("backlog", "active", "done", "cancelled")
TASK_FIELDS = ("title", "status", "priority", "due_at", "project_id")


class ActionIn(BaseModel):
    op: str  # create_task / update_task / complete_task / create_reminder / create_event / delete_event
    target: str | None = None
    patch: dict = {}
    summary: str = ""


def _find_task(conn, name: str):
    """按名称精确 → 模糊匹配任务"""
    exact = conn.execute(
        "SELECT id, title FROM tasks WHERE title = ? LIMIT 1", (name,)
    ).fetchone()
    if exact:
        return exact
    return conn.execute(
        "SELECT id, title FROM tasks WHERE title LIKE ? ORDER BY id LIMIT 1", (f"%{name}%",)
    ).fetchone()


@router.post("/chat/execute")
def execute_action(body: ActionIn):
    """执行操作卡（用户点「执行」后才调用——半自动契约）。
    留痕 + 存 pre-image 供撤销。"""
    now = datetime.now().isoformat(timespec="seconds")
    today = now[:10]
    with get_conn() as conn:
        pre_image = None
        target_id = None
        if body.op == "create_task":
            title = str(body.patch.get("title") or "").strip()
            if not title:
                return {"ok": False, "error": "create_task 需要 title"}
            # 挂靠进行中的事（实体索引防幻觉）：project 名必须精确匹配真实条目
            project_id = None
            project_name = str(body.patch.get("project") or "").strip()
            if project_name:
                row = conn.execute(
                    "SELECT id FROM projects WHERE name = ? AND status != 'closed'", (project_name,)
                ).fetchone()
                if not row:
                    return {"ok": False, "error": f"没找到进行中的事「{project_name}」，请确认名称或先创建"}
                project_id = row["id"]
            cur = conn.execute(
                """INSERT INTO tasks (title, project_id, status, priority, due_at, created_at)
                   VALUES (?, ?, 'backlog', ?, ?, ?)""",
                (title, project_id, body.patch.get("priority", 3), body.patch.get("due_at"), now),
            )
            target_id = cur.lastrowid
        elif body.op in ("update_task", "complete_task"):
            target = (body.target or "").strip()
            row = _find_task(conn, target) if target else None
            if not row:
                return {"ok": False, "error": f"没找到任务「{target or ''}」"}
            if body.op == "complete_task":
                patch = {"status": "done"}
            else:
                patch = {k: v for k, v in body.patch.items() if k in TASK_FIELDS}
                if "status" in patch and patch["status"] not in TASK_STATUSES:
                    return {"ok": False, "error": f"status 必须是 {TASK_STATUSES} 之一"}
                if not patch:
                    return {"ok": False, "error": "update_task 没有可改字段"}
            old = conn.execute("SELECT * FROM tasks WHERE id = ?", (row["id"],)).fetchone()
            pre_image = {k: old[k] for k in patch}  # 撤销用旧值快照
            sets = ", ".join(f"{k} = ?" for k in patch)
            params = list(patch.values())
            if patch.get("status") == "done":
                sets += ", completed_at = ?"
                params.append(now)
            elif patch.get("status") in ("backlog", "active"):
                sets += ", completed_at = NULL"
            params.append(row["id"])
            conn.execute(f"UPDATE tasks SET {sets} WHERE id = ?", params)
            target_id = row["id"]
        elif body.op == "create_reminder":
            from app.routers.reminders import _normalize_trigger
            tt = body.patch.get("trigger_type", "once")
            tv = _normalize_trigger(tt, body.patch.get("trigger_value"))
            cur = conn.execute(
                "INSERT INTO reminders (title, trigger_type, trigger_value) VALUES (?, ?, ?)",
                (str(body.patch.get("title") or body.summary or "提醒").strip(), tt, tv),
            )
            target_id = cur.lastrowid
        elif body.op == "create_event":
            title = str(body.patch.get("title") or "").strip()
            dt = str(body.patch.get("date") or "").strip()
            if not title or not dt:
                return {"ok": False, "error": "create_event 需要 title 和 date"}
            try:
                datetime.strptime(dt, "%Y-%m-%d")
            except ValueError:
                return {"ok": False, "error": f"date 格式应为 YYYY-MM-DD，收到 {dt}"}
            et = str(body.patch.get("entry_type") or "personal").strip()
            if et not in ("course", "meeting", "outing", "personal"):
                et = "personal"
            cur = conn.execute(
                """INSERT INTO schedule_events (title, entry_type, date, start_time, end_time, location, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (title, et, dt,
                 str(body.patch.get("start_time") or "09:00"),
                 str(body.patch.get("end_time") or "10:00"),
                 body.patch.get("location"), now),
            )
            target_id = cur.lastrowid
        elif body.op == "delete_event":
            target = (body.target or "").strip()
            dt = str(body.patch.get("date") or "").strip()
            row = None
            if target and dt:
                row = conn.execute(
                    "SELECT * FROM schedule_events WHERE title = ? AND date = ? LIMIT 1", (target, dt)
                ).fetchone()
            if not row and target:
                row = conn.execute(
                    "SELECT * FROM schedule_events WHERE title = ? ORDER BY date LIMIT 1", (target,)
                ).fetchone()
            if not row and target:
                row = conn.execute(
                    "SELECT * FROM schedule_events WHERE title LIKE ? ORDER BY date LIMIT 1", (f"%{target}%",)
                ).fetchone()
            if not row:
                return {"ok": False, "error": f"没找到一次性日程「{target}」"}
            pre_image = dict(row)  # 撤销用整行快照（恢复插入）
            conn.execute("DELETE FROM schedule_events WHERE id = ?", (row["id"],))
            target_id = row["id"]
        else:
            return {"ok": False, "error": f"未知操作 {body.op}"}

        cur = conn.execute(
            "INSERT INTO agent_suggestions (date, kind, content, user_action, created_at) VALUES (?, 'chat_op', ?, 'executed', ?)",
            (today, json.dumps(
                {"action": body.model_dump(), "pre_image": pre_image, "target_id": target_id},
                ensure_ascii=False), now),
        )
        undo_id = cur.lastrowid
    return {"ok": True, "undo_id": undo_id, "summary": body.summary}


class UndoIn(BaseModel):
    undo_id: int


@router.post("/chat/undo")
def undo_action(body: UndoIn):
    """撤销已执行的操作卡（IDS 2.2：即时小事确认卡带撤销按钮）"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT content, user_action FROM agent_suggestions WHERE id = ?", (body.undo_id,)
        ).fetchone()
        if not row or row["user_action"] != "executed":
            return {"ok": False, "error": "找不到可撤销的记录"}
        try:
            rec = json.loads(row["content"])
        except json.JSONDecodeError:
            return {"ok": False, "error": "记录损坏，无法撤销"}
        action, pre_image, tid = rec.get("action", {}), rec.get("pre_image"), rec.get("target_id")
        op = action.get("op")
        if op == "create_task" and tid:
            # 建了又撤：删掉（挂靠日志脱离，同 tasks 删除逻辑）
            conn.execute("UPDATE progress_logs SET task_id = NULL WHERE task_id = ?", (tid,))
            conn.execute("DELETE FROM tasks WHERE id = ?", (tid,))
        elif op in ("update_task", "complete_task") and tid and pre_image:
            sets = ", ".join(f"{k} = ?" for k in pre_image)
            conn.execute(f"UPDATE tasks SET {sets} WHERE id = ?", (*pre_image.values(), tid))
            if pre_image.get("status") in ("backlog", "active"):
                conn.execute("UPDATE tasks SET completed_at = NULL WHERE id = ?", (tid,))
        elif op == "create_reminder" and tid:
            conn.execute("DELETE FROM reminders WHERE id = ?", (tid,))
        elif op == "create_event" and tid:
            conn.execute("DELETE FROM schedule_events WHERE id = ?", (tid,))
        elif op == "delete_event" and pre_image:
            # 删了又撤：按快照整行恢复（含原 id）
            conn.execute(
                """INSERT INTO schedule_events (id, title, entry_type, date, start_time, end_time, location, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (pre_image.get("id"), pre_image.get("title"), pre_image.get("entry_type"),
                 pre_image.get("date"), pre_image.get("start_time"), pre_image.get("end_time"),
                 pre_image.get("location"), pre_image.get("note"), pre_image.get("created_at")),
            )
        else:
            return {"ok": False, "error": "该操作不支持撤销"}
        conn.execute(
            "UPDATE agent_suggestions SET user_action = 'undone' WHERE id = ?", (body.undo_id,)
        )
    return {"ok": True}


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
    rejected: list[dict] = []  # 用户拒绝的提案（PRD 4.4：全量留痕可审计）


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
                if page.is_file():
                    # 同名知识二次入库：追加为带日期的更新节，不静默覆盖旧内容
                    old = page.read_text(encoding="utf-8")
                    update_section = f"\n\n## 更新 {today}\n\n{p.get('detail', '')}\n"
                    if p.get("evidence"):
                        update_section += f"\n> 来源对话：{p['evidence']}\n"
                    page.write_text(old.rstrip() + update_section, encoding="utf-8")
                    note = "同名页已存在，追加为更新节"
                else:
                    page.write_text(front + body_text, encoding="utf-8")
                    note = "新建知识页"
                    _register_review(page_rel, title)  # 间隔重现登记（仅新建时）
                _update_index(page_rel, title)
                _append_log(f"## [{today}] ingest | 对话沉淀：{title}")
                result = {"kind": "knowledge", "title": title, "page": page_rel, "ok": True, "note": note}
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
        # 被拒提案留痕（PRD 4.4 / 风险对策：agent_suggestions 全量留痕，前端可审计）
        for p in body.rejected:
            conn.execute(
                "INSERT INTO agent_suggestions (date, kind, content, user_action, created_at) VALUES (?, ?, ?, ?, ?)",
                (today, "memory_sync", str(p), "rejected", now),
            )

        # 闪记闭环（FR-1.2）：本次入库提案覆盖到的闪记自动确认——处理完不用回闪记池手动丢
        flash_closed = 0
        corpus = "\n".join(
            f"{p.get('evidence') or ''}\n{p.get('detail') or ''}\n{p.get('title') or ''}"
            for p in body.proposals
        )
        if corpus.strip():
            for it in conn.execute(
                "SELECT id, content FROM inbox_items WHERE status = 'pending'"
            ).fetchall():
                key = it["content"].strip()
                if len(key) > 40:
                    key = key[:40]
                if key and key in corpus:
                    conn.execute(
                        "UPDATE inbox_items SET status = 'confirmed', resolved_at = ? WHERE id = ?",
                        (now, it["id"]),
                    )
                    flash_closed += 1
    return {"applied": applied, "flash_closed": flash_closed}
