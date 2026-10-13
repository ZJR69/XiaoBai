"""对话流：小白的对话主界面后端。

核心价值（PRD P2 / 交互设计文档 1.1 / PRD 4.6 记忆分层）：
- 三层记忆注入：core.md 底座全文 + 进行中的事索引 + 实体索引（对标 Claude Code / ChatGPT）
- 关键词触发：消息提到某项目/人物名 → 自动附加其详情（进展日志/实体页）
- 「回顾对话」→ 批量提案（含去向 task/link/update/core/entity），所有入库经用户裁决（防污染铁律）
"""
import json
import re
import threading
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
    "personal": "个人", "club": "社团", "course": "课程", "other": "其他",
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
    """进行中的事索引（2026-09-26 树形）：一级一行 + 二级缩进行（子科目），封顶 30 条一级。
    每行露最近一条动态（类型+摘要）——如实现状，判断留给小白。"""
    rows = conn.execute(
        """SELECT p.id, p.name, p.category, p.parent_id,
                  (SELECT kind || '：' || substr(content, 1, 60) FROM progress_logs WHERE project_id = p.id AND done_at IS NULL ORDER BY id DESC LIMIT 1) AS last_activity
           FROM projects p WHERE p.status != 'closed' ORDER BY p.id LIMIT 60"""
    ).fetchall()
    tops = [r for r in rows if r["parent_id"] is None][:30]
    top_ids = {r["id"] for r in tops}
    lines = []
    for r in tops:
        lines.append(
            f"- {r['name']}（{CATEGORY_LABELS.get(r['category'], '其他')}）"
            + (f"｜最近：{r['last_activity']}" if r["last_activity"] else "")
        )
        for c in rows:
            if c["parent_id"] == r["id"]:
                lines.append(
                    f"　　- {c['name']}"
                    + (f"｜最近：{c['last_activity']}" if c["last_activity"] else "")
                )
    return lines


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
            """SELECT t.title, t.status, t.due_at, t.start_at, t.end_at, t.rrule, t.repeat_mode, p.name AS project
               FROM tasks t LEFT JOIN projects p ON t.project_id = p.id
               WHERE t.status IN ('backlog', 'active') ORDER BY t.priority LIMIT 30"""
        ).fetchall()
        inbox = conn.execute(
            "SELECT id, content FROM inbox_items WHERE status = 'pending' ORDER BY id DESC LIMIT 20"
        ).fetchall()
        reminders = conn.execute(
            """SELECT r.title, r.trigger_type, r.trigger_value, r.relative_to, r.offset_minutes, t.title AS task_title
               FROM reminders r LEFT JOIN tasks t ON r.task_id = t.id
               WHERE r.active = 1 ORDER BY r.id"""
        ).fetchall()
        events_once = conn.execute(
            """SELECT title, entry_type, date, start_time, end_time, location
               FROM schedule_events WHERE (rrule IS NULL OR rrule = '') AND date >= ?
               ORDER BY date LIMIT 15""",
            (datetime.now().date().isoformat(),),
        ).fetchall()
        events_series = conn.execute(
            """SELECT title, entry_type, date, start_time, end_time, location, rrule
               FROM schedule_events WHERE rrule IS NOT NULL AND rrule != '' ORDER BY id LIMIT 20"""
        ).fetchall()
        slots = conn.execute(
            """SELECT title, entry_type, day_of_week, start_time, end_time, location, week_pattern, valid_from, valid_to
               FROM schedule_slots ORDER BY day_of_week, start_time"""
        ).fetchall()
        filters = conn.execute(
            "SELECT title, filter_json FROM saved_filters ORDER BY position"
        ).fetchall()
        links = conn.execute(
            "SELECT id, title, kind, target, parent_id FROM links ORDER BY id LIMIT 50"
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

    # 任务（带截止与计划窗口；重复任务带规则——用户问「周期性待办」时据此回答）
    if tasks:
        lines.append("未完成任务：")
        for t in tasks:
            due = f"，截止 {t['due_at'][:10]}" if t["due_at"] else ""
            window = ""
            if t["start_at"] or t["end_at"]:
                window = f"，窗口 {t['start_at'][:10] if t['start_at'] else '?'}~{t['end_at'][:10] if t['end_at'] else '?'}"
            rep = f"，重复 {t['rrule']}（节奏 {t['repeat_mode'] or 'due'}）" if t["rrule"] else ""
            proj = f"（{t['project']}）" if t["project"] else ""
            lines.append(f"- {t['title']}{proj} [{t['status']}]{due}{window}{rep}")

    # 学期课表常驻（2026-10-09）：课表随时可见，用户说课表有错可直接改，不再依赖日期词触发
    if slots:
        from app.routers.scheduler import WEEKDAY_NAMES
        today_iso = datetime.now().date().isoformat()
        active_slots = [s for s in slots if not (s["valid_to"] and s["valid_to"] < today_iso)]
        ended_slots = [s for s in slots if s["valid_to"] and s["valid_to"] < today_iso]
        lines.append("学期课表（每周固定的课程/例会，长期生效；调时间/改地点/删课/改生效区间用 slot 操作）：")
        cur_day = None
        wp_map = {"odd": "·单周", "even": "·双周"}
        for s in active_slots:
            if s["day_of_week"] != cur_day:
                cur_day = s["day_of_week"]
                lines.append(WEEKDAY_NAMES[cur_day] + "：")
            wp = wp_map.get(s["week_pattern"] or "all", "")
            loc = f" @{s['location']}" if s["location"] else ""
            et = "" if (s["entry_type"] or "course") == "course" else f"[{s['entry_type']}]"
            # 生效区间可见（2026-10-12）：区间是「课程丢失」类问题的诊断锚点——
            # 模型能改 valid_to 就必须能看见 valid_to（注入面与操作面对齐）
            rng = f"（{s['valid_from'] or '?'} ~ {s['valid_to'] or '长期'}）"
            lines.append(f"- {s['start_time']}–{s['end_time']} {et}{s['title']}{loc}{wp}{rng}")
        if ended_slots:
            # 已结束槽位不隐藏（2026-10-12）：被误截断的课只存在于这里，
            # 藏起来模型对用户报障「课丢了」只能干瞪眼
            lines.append("已结束的课表槽位（用户报「课程丢失/后面几周没课」时先查这里——被误截断的用 update_slot 把 valid_to 延长到学期末即可修复）：")
            for s in ended_slots:
                loc = f" @{s['location']}" if s["location"] else ""
                lines.append(f"- {WEEKDAY_NAMES[s['day_of_week']]} {s['start_time']}–{s['end_time']} {s['title']}{loc}（{s['valid_from'] or '?'} ~ {s['valid_to']}，已结束）")

    # 日程表（schedule_events）：一次性日程 + 重复系列全量注入（2026-10-12）——
    # 系列必须带规则可见，否则模型会把「取消一次」误判成删整个系列
    if events_once:
        lines.append("近期一次性日程：")
        for e in events_once:
            loc = f" @{e['location']}" if e["location"] else ""
            lines.append(f"- {e['date']} {e['start_time']}–{e['end_time']} {e['title']}{loc}")
    if events_series:
        lines.append("重复日程系列（每条是一个系列，删/改都作用于整个系列；单次取消暂不支持）：")
        for e in events_series:
            loc = f" @{e['location']}" if e["location"] else ""
            lines.append(f"- {e['title']} {e['start_time']}–{e['end_time']}{loc}，首次 {e['date']}，规则 {e['rrule']}")

    # 第三层：实体索引
    entities = _entity_lines()
    if entities:
        lines.append("人物/机构（提到名字时小白知道去哪找详情）：")
        lines += entities

    if reminders:
        rel_map = {"due_at": "截止", "start_at": "开始", "end_at": "结束"}

        def _rem_desc(r):
            tt = r["trigger_type"]
            if tt == "relative":
                off = r["offset_minutes"] or 0
                mag = f"{abs(off)} 分钟" if abs(off) < 120 else (f"{abs(off)//60} 小时" if abs(off) < 1440 else f"{abs(off)//1440} 天")
                direction = "前" if off < 0 else "后"
                return f"《{r['task_title'] or '?'}》{rel_map.get(r['relative_to'] or 'due_at', '截止')}{direction} {mag}"
            if tt == "daily":
                return f"每天 {r['trigger_value']}"
            if tt == "weekly":
                from app.routers.scheduler import WEEKDAY_NAMES
                w, _, hm = (r["trigger_value"] or "0-09:00").partition("-")
                return f"每{WEEKDAY_NAMES[int(w)]} {hm}"
            if tt == "interval":
                return f"每 {r['trigger_value']} 小时"
            return str(r["trigger_value"])  # once：原值即完整时刻

        lines.append("生活提醒（改时间/改偏移用 update_reminder，删除用 delete_reminder）：")
        for r in reminders:
            lines.append(f"- {r['title']}（{_rem_desc(r)}）")
    if inbox:
        lines.append("闪记池（待归类的速记原文；用户说「处理草稿/整理闪记」时，逐条建议去向并用 resolve_inbox 出卡）：")
        for it in inbox:
            lines.append(f"- {it['content']}")
    if filters:
        lines.append("已存筛选（工作台任务池顶部的页签，对话里可按名引用）：")
        for f in filters:
            lines.append(f"- {f['title']}：{f['filter_json']}")

    # 资源索引（树形，2026-09-26 树化）：话题（group）层级缩进 + 叶子资源带地址；用户要地址时原文给出
    if links:
        kind_label = {"url": "网址", "file": "文件", "folder": "文件夹"}
        kids = {}
        for l in links:
            kids.setdefault(l["parent_id"], []).append(l)
        tree = []

        def _walk(pid, depth):
            for l in kids.get(pid, []):
                pad = "　" * depth  # 全角空格缩进，LLM 对层级更敏感
                if l["kind"] == "group":
                    tree.append(f"{pad}- 📂 {l['title']}")
                    _walk(l["id"], depth + 1)
                else:
                    tree.append(f"{pad}- [{kind_label.get(l['kind'], l['kind'])}] {l['title']}：{l['target']}")

        _walk(None, 0)
        lines.append("资源索引（用户登记的学习/项目链接与资料存放地址，按话题层级组织；提到对应主题时可引用）：")
        lines += tree
    return "\n".join(lines)


def _expand_mentions(message: str) -> str:
    """关键词触发（PRD 4.6 注入层第 5 条）：消息提到事项/任务/人物名 → 附加其完整上下文。
    TRAE 式引用语义：名字一出现，小白就拿到该条目的背景，不依赖用户复述。
    2026-09-26 升级：二级子事项名命中展开（带父归属）；一级命中附带子事项概览
    + 关联人物（实体页内容含事项名 → 人和事挂上）；日志取最近 10 条（叙述素材）。"""
    if not message:
        return ""
    extras = []
    with get_conn() as conn:
        projects = conn.execute(
            "SELECT id, name, description, parent_id FROM projects WHERE status != 'closed'"
        ).fetchall()
        names = {p["id"]: p["name"] for p in projects}
        for p in projects:
            if not (p["name"] and p["name"] in message):
                continue
            lineage = f"（属于《{names.get(p['parent_id'], '?')}》）" if p["parent_id"] is not None else ""
            parts = [f"### 事项《{p['name']}》{lineage}上下文"]
            if p["description"]:
                parts.append(f"描述：{p['description']}")
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
                "SELECT kind, content, created_at, done_at FROM progress_logs WHERE project_id = ? ORDER BY id DESC LIMIT 10",
                (p["id"],),
            ).fetchall()
            if logs:
                # 已落地（done_at）的进展标注出来——是真实历史但不再是「待做的下一步」
                parts.append("最近进展：\n" + "\n".join(
                    f"- [{lg['kind']}] {lg['content']}（{lg['created_at'][:10]}）"
                    + ("「已落地：随任务完成」" if lg["done_at"] else "")
                    for lg in reversed(logs)
                ))
            # 一级命中：附子事项概览（各自最近一条动态）
            if p["parent_id"] is None:
                kids = [c for c in projects if c["parent_id"] == p["id"]]
                kid_parts = []
                for c in kids:
                    klog = conn.execute(
                        "SELECT kind, substr(content, 1, 50) AS brief FROM progress_logs WHERE project_id = ? AND done_at IS NULL ORDER BY id DESC LIMIT 1",
                        (c["id"],),
                    ).fetchone()
                    kid_parts.append(f"- {c['name']}" + (f"：{klog['brief']}" if klog else ""))
                if kid_parts:
                    parts.append("子事项：\n" + "\n".join(kid_parts))
            # 人和事关联：实体页内容含本事项名 → 该人物与这件事有关，带上摘要
            related = []
            if ENTITIES_DIR.exists():
                for f in sorted(ENTITIES_DIR.glob("*.md")):
                    text = f.read_text(encoding="utf-8")
                    if p["name"] in text:
                        title = _page_title(f, f.stem)
                        related.append(f"- {title}：{text[:200]}")
                if related:
                    parts.append("相关人员/机构（实体页提到本事项）：\n" + "\n".join(related))
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
            # 来源标注（2026-10-12 修正）：重复系列必须亮明身份——误标「一次性」会诱导删整个系列
            if s.get("recurring"):
                tag = "（重复系列的一次，规则 " + str(s.get("rrule") or "") + "）"
            elif s.get("source") == "event":
                tag = "（一次性日程）"
            else:
                tag = ""
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
- 用户说「处理草稿 / 处理这几条闪记」时：逐条理解，看懂的就提议去向（建任务/登记资源/丢弃，给出建议的标题、日期、归属），看不懂或有歧义就先问清楚再动手；永远等用户确认

提问纪律（最重要，违反即失败）：
- 你没有任何执行能力，操作只能由用户点击操作卡执行。用户打字说「执行/确认/可以/好的」时，你要重新输出完整的操作块让用户点，绝不能说「已建好/已完成/都记下了」——你执行不了任何东西
- 一次对话涉及多件事（如用户一口气报了一堆安排）时，把所有事逐件过一遍，**当轮一次性问完全部待澄清问题**，禁止分批提问、禁止下一轮又冒出新问题
- 只问影响正确性的问题（截止日期、具体时间、指代不明的人/事）；标题措辞、同类拆分还是合并、要不要挂靠这类低风险细节，直接按合理默认出卡（用户可改可撤销），不要问
- 用户已明确说过的信息（如「作业和小测各一个」）不要拿来再问，仔细读原话
- 你上一轮问的问题用户还没回答（用户只说了「执行」或岔开了话题）时：明确指出哪些问题还悬着等回答，不要重复问全文，更不要擅自按自己的猜测出卡

主动识别（记录的缺口）：
- 用户的话里有「忘了会有损失」的信息（要做的事、安排、完成/取消、值得留存的事实、有用的网址或资料存放地址），即使不是明确指令，也用一句话主动提议记录或更新；用户同意再出操作卡，指代不清先问清，不猜
- 纯情绪宣泄不提议；同一话题没被理会，不重复提
- 用户在对话中陈述某件事的进展时，小白会自动把进展记到该事项名下（后台进行，无需用户操作）；所以直接自然地说就行，不用专门要求记录

对外整理（用户要向他人同步某件事时）：
- 用户说「帮我整理XX的进展」「我要跟老师/学长汇报XX」「给对方发个说明」时，基于【相关详情】里该事项的全部记录，整理成一段可直接发送的叙述稿：这件事在做什么、背后有谁、进行到什么阶段、下一步
- 视角和口径听用户指定（第一人称/全局视角、正式/随意）；没说就先问一句发给谁、什么场合
- 只用真实记录，不编造；记录不够时如实说还缺什么信息

操作指令（对话驱动操作，用户点确认后才会执行）：
- 用户让你做事（建任务、改日期、推截止、勾完成、加提醒、排日程）时，先用一两句自然的话回应，然后在回复最末尾输出一个操作块：
<<<ACTION>>>
{"op": "create_task", "target": null, "patch": {"title": "任务名", "due_at": "YYYY-MM-DD", "priority": 3}, "summary": "一句话说明这个操作做什么"}
<<<END>>>
- op 可选值：
  create_task（patch: title 必填，due_at/priority 可选，priority 1高-5低；start_at/end_at 可选=计划窗口（打算什么时候做，如「12号开始做」「12到15号之间做」；与死线 due_at 语义分离，用户只说了死线就不带），project 可选=挂靠的进行中的事名称，必须是【当前状态】里真实存在的名称，一字不差；不确定就不带。next_step 可选=这个待办就是该事务的「下一步」时带上一句事务视角的下一步说明（通常与任务名相近），会在事务里同步留一条关联日志——任务完成时这条日志自动落地不再展示，不用你再删；待办与事务下一步无关就不带）
  重复任务（用户说「每周X / 每隔N天 / 每两周 / 每天 / 每月…」的周期性待办）→ create_task 加 patch.rrule（自然语言→iCalendar RRULE：每周四=FREQ=WEEKLY;BYDAY=TH，隔周周四=FREQ=WEEKLY;INTERVAL=2;BYDAY=TH，周一三五=FREQ=WEEKLY;BYDAY=MO,WE,FR，每天=FREQ=DAILY，每隔3天=FREQ=DAILY;INTERVAL=3，每月1号=FREQ=MONTHLY;BYMONTHDAY=1；结束于某天加 UNTIL=20261231、限定次数加 COUNT=10）且必须带 due_at（首期截止）；patch.repeat_mode：due=按原截止节奏（默认，周报这类死线节奏），done=从完成时刻起算（用户说「从做完算起」「每隔N天做一次」的养护类）。用户勾选完成会自动推到下一期并留痕，规则到头才正常完成——不用你管
  update_task（target 填要改的任务名，patch 只放要改的字段：due_at/start_at/end_at/priority/title/status/rrule/repeat_mode）
  complete_task（target 要完成的任务名，不需要 patch）
  create_reminder（patch: title、trigger_type: once/daily/weekly/interval、trigger_value 如 2026-10-01T09:00 或 09:00 或 2-09:00 或 4）
  相对提醒（用户说「截止前一天提醒我 / 开始后 X 小时提醒我」这类跟着任务日期走的提醒）→ trigger_type 用 relative，不带 trigger_value；patch 带 task=任务名（必须是【当前状态】里真实存在的任务）、relative_to=due_at/start_at/end_at（默认 due_at）、offset_minutes=提前为负数（如截止前 1 天=-1440、前 2 小时=-120），延后为整数（结束后 30 分钟=30）。任务改期/重复重排后提醒自动跟随，不用你改
  update_reminder（改已有提醒。target=提醒名；patch 只放要改的：title/trigger_value（如 6 点改 7 点={"trigger_value":"19:00"} 或 once 改时刻）；相对提醒改偏移用 offset_minutes、改锚点用 relative_to。提醒只能改不能换类型，要换类型（如 once 改 daily）先 delete_reminder 再 create_reminder）
  create_event（建日程。patch: title 必填、date 必填 YYYY-MM-DD（重复系列则为首次日期）；start_time/end_time HH:MM、entry_type: course/meeting/outing/personal、location 可选；任意重复规律加 patch.rrule（iCalendar RRULE 原语，由你把自然语言翻译进去）：每周四=FREQ=WEEKLY;BYDAY=TH，隔周周四=FREQ=WEEKLY;INTERVAL=2;BYDAY=TH，周一三五=FREQ=WEEKLY;BYDAY=MO,WE,FR，每天=FREQ=DAILY，每月1号=FREQ=MONTHLY;BYMONTHDAY=1；结束于某天加 UNTIL=20261231、限定次数加 COUNT=10。学期课表（单双周教学周）不走这里，别用 create_event 建课程）
  delete_event（删除日程（重复系列=删整个系列）。target 填日程名，patch.date 可选=具体日期，用于区分同名日程。用户只想取消系列中的某一次时：说明暂不支持单次取消，可改为删整个系列或保持不动，让用户选）
  update_event（改一次性日程/重复系列（系列=改整个系列）。target=日程名，patch.date 可选=定位日期；要改的字段放 patch：title/start_time/end_time/location/note/entry_type/rrule；改日期用 patch.new_date）
  create_slot（建学期课表槽位：每周固定的课程/例会。patch: title 必填、day_of_week 必填 0–6（0=周一）、start_time/end_time 必填 HH:MM；entry_type（默认 course）、location、week_pattern: all/odd/even（单双周）、valid_from/valid_to 可选（YYYY-MM-DD；用户没提生效区间就不传——valid_to 会自动对齐在排课程的学期末共识；课程确实只上几周时才显式设，并在回复里向用户说明理由，禁止随意编短区间））
  update_slot（改课表槽位（调时间/换教室/换单双周/改生效区间）。target=课程名；一周多节同名课时 patch.day_of_week 指定哪一天，同一天仍有多节时再用 patch.start_time 填该节**当前**的开始时间定位（此时它不是改时间）；要改的字段放 patch：title/start_time/end_time/location/week_pattern/entry_type/valid_from/valid_to（YYYY-MM-DD；课程被误截断、要延长期效就改 valid_to）；改星期几用 patch.new_day）
  delete_slot（删课表槽位（这门课/这个例会从课表移除）。target=课程名，多节同名课时 patch.day_of_week 指定；删了可撤销）
  create_link（登记资源到资源索引。patch: title 必填、kind: url/file/folder 必填、target 必填（网址带 http(s):// 前缀；本地资源为绝对路径，如 D:\\资料\\x.pdf）；category（分组名，如 课程/工具/资料）、note、project 可选）
- 课表相关（调课/换教室/加课/删课/用户指出课表有错）一律用 slot 操作——学期课表是每周生效的权威记录，课表内容见【当前状态】的「学期课表」段；不要对课程用 create_event/delete_event（那是一次性日程）
- 用户报「课程丢失/后面几周没课/课表不全」时：先对照【当前状态】里「已结束的课表槽位」找被误截断的课（生效区间提前结束的就是病灶），确认后直接出 update_slot（patch.valid_to=与其他正常课程一致的学期末日期）修复，并向用户说明根因；不要反问用户「在哪看到的/丢了哪些」——数据你都有
- 日期一律 YYYY-MM-DD；target 必须是【当前状态】里真实存在的条目名
- 用户说「某天要上周X的课 / 要在某天加个安排」时：参照【你提到的日期对应的日程】（若已注入），为那一天逐门课/逐个安排各输出一个 create_event 操作块；日程里没有的课先问用户时间，不要编
- 任务明显属于某件进行中的事时（用户提到或【当前状态】里列出的），create_task 带上 project 挂靠；名称对不上就不带，不要问
- create_project（建「进行中的事」。patch: name 必填；description/category 可选，category ∈ research/collaboration/family/personal/club/course/other；parent 可选=挂靠的一级事务名）
  判断标准：一次性待办 → create_task；持续多日、有结构的事（有成员/有周期/有一串待办的组织、活动、课题，如「大训」「XX论坛筹备」）→ create_project。
  用户说「单独记一下 / 给XX单独建个档」而 XX 不在【当前状态】里 → 多半是要建事务，按合理默认出卡（类别不确定用 other）
- complete_project（完成「进行中的事」，target=事务名）：用户说「XX这事结束了/搞完了」且 XX 在【当前状态】里时提议；完成即整块收起（数据保留可恢复）
- delete_task / delete_reminder / delete_link（删任务/提醒/资源，target=条目名）：用户说「删掉/去掉/别要了」时提议；删了可撤销
- delete_log（删事务的备注/日志。target=日志内容关键词，patch.project 可选=限定事务名，匹配最近一条）：用户说「把XX那条备注删了/这条进展不作数」时提议；删了可撤销。注意与任务联动的「下一步」日志会随任务完成自动落地，不用删
- delete_project（删事务，仅限空事务——什么都没挂的误建）：非空事务（挂了任务/子事项/日志）不能删，引导用户用「完成」收起
- create_filter（存筛选——用户说「把 XX 存成筛选 / 以后我要常看这类」时提议。patch: title=筛选名（如「本周高优」）、filter=JSON 字符串）：契约为一层条件不嵌套，如 {"all":[{"field":"due_at","op":"<","value":"now+7d"},{"field":"priority","op":"<=","value":2}]}。all=全部满足（&&），可选 any=任一满足（||）；field ∈ done/status/priority/due_at/start_at/project_id/created_at；op ∈ =/!=/>/>=/</<=/in；日期字段的值可用相对表达式 now、now+7d、now-1w、now/d（今天 0 点）；done 用 true/false。存好后工作台任务池顶部会出现同名页签，一点即滤；不要主动存筛选，用户明确要「存/常用」才出卡
- delete_filter（删筛选，target=筛选名）：用户说「这个筛选不要了」时提议；删了可撤销
- resolve_inbox（处理一条闪记——用户说「处理草稿/整理闪记/这条记下来当任务」时，针对【当前状态】闪记池里的某一条出卡。target=该条闪记原文（可截取前段关键词）；patch.action 必填 ∈ task/life/idea/discard：task=归为任务（直接建任务，patch.title 可选=改写的任务名）、life=归为生活琐事留痕、idea=归为想法留痕、discard=丢弃。一条闪记一张卡，几条就出几张）
- 自我纠错：发现之前建的条目错了（重复/张冠李戴/用户纠正）→ 主动提议删除或修改卡，向用户说明删哪个、为什么；不要装作没看见
- 出卡与提问可并存：有等待澄清的问题时，把问题一次问全，**同时把已确定的事直接出卡**（如建事务的卡），不要等问题全部答完才出第一张卡；XX 已存在为任务但用户要「单独记一下/以后还有一堆相关的事」→ 直接出 create_project 卡（不删原任务，事务建好后可挂靠）
- 多件事就多输出几个操作块，一次出齐；拿不准的低风险字段直接省略（不带该字段），不要为零碎问题推迟出卡
- 只是聊天、回答问题、给建议时不输出操作块；只有日期/时间/指代这类影响正确性的信息缺失时才提问（且一次问全）"""


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


# ── 自动记忆（2026-09-26 铁律分层定案）──
# 对标 ChatGPT/Claude 的记忆哲学：模型自动记 + 全程透明 + 事后可管理。
# 分层：进展记录（progress_logs 五分类，source='agent'）自动入库，页面可见可删可改；
# 结构化操作（建任务/提醒/资源）仍走操作卡裁决。沉淀按钮退役，能力并入此处。

AUTO_MEMORY_LOG_KINDS = ("background", "decision", "status", "next_step", "note")


def _auto_memory(message: str, reply: str, actions: list | None = None) -> None:
    """对话结束后台提取进展 → 自动入库。命中门控：消息没提到任何进行中的事 → 零 LLM 开销直接返回。
    在后台线程跑（不阻塞对话的 done 事件），全程 agent_suggestions 留痕（user_action='auto'）。
    actions=本轮剥离出的操作卡：create_task 已带 next_step 的事务不再重复提取「下一步」（防双胞）。"""
    # 操作卡已覆盖的「下一步」事务名集合（卡未执行也无所谓——用户忽略卡=整条不要，语义一致）
    card_linked = {
        str(a.get("patch", {}).get("project") or "").strip()
        for a in (actions or [])
        if isinstance(a, dict) and a.get("op") == "create_task"
        and str(a.get("patch", {}).get("next_step") or "").strip()
    }
    card_linked.discard("")
    with get_conn() as conn:
        names = [p["name"] for p in conn.execute(
            "SELECT name FROM projects WHERE status != 'closed' AND name IS NOT NULL"
        ).fetchall() if p["name"] in (message or "")]
    if not names:
        return
    if not llm.llm_available():
        return

    prompt = f"""刚才用户和小白的这轮对话提到了进行中的事项：{ "、".join(names) }。

回顾对话，提取值得记录的进展信息。返回 JSON 数组，每项：
{{"dest": "log|core|entity",
  "project": "dest=log 必填：事项准确名称（从事项列表里选）",
  "log_kind": "dest=log 时必填：background/decision/status/next_step/note",
  "title": "dest=core 时为小节名；dest=entity 时为人物/机构名",
  "detail": "要记录的内容（一句话，用户视角的事实）"}}

去向判断：
- 用户陈述的进展、状态变化、做出的决定、明确的下一步 → log（挂到对应事项）
  log_kind 五分类：background=背景信息 / decision=做出的决定 / status=当前状态 / next_step=下一步 / note=其他备注
  decision 只用于用户明确拍板的事（「就这么定」「改成X」且这轮没有悬而未决）；
  用户只是提出想法、在讨论、小白还在追问细节、或事情尚未执行 → 一律用 note，detail 以「（待定）」开头
- 用户的底座信息（长期身份、方向、关键人物名单）→ core
- 具体某个人物/机构的细节（风格、诉求、关系）→ entity
- 纯提问、闲聊、情绪宣泄、小白自己的回复内容 → 不记录
宁缺毋滥，没有值得记的就返回 []

对话：
用户：{message}
小白：{reply}"""
    try:
        raw = llm.chat_completion(
            [{"role": "system", "content": "你是小白的记忆同步引擎，只输出 JSON。"},
             {"role": "user", "content": prompt}],
            temperature=0.2,
        )
        items = llm.extract_json(raw) or []
    except Exception:
        return  # 提取失败静默放弃——自动记忆是增益，不能影响主流程
    if not isinstance(items, list) or not items:
        return

    now = datetime.now().isoformat(timespec="seconds")
    today = now[:10]
    with get_conn() as conn:
        for it in items:
            if not isinstance(it, dict):
                continue
            dest = it.get("dest")
            detail = (it.get("detail") or "").strip()
            if not detail:
                continue
            if dest == "log":
                name = (it.get("project") or "").strip()
                row = conn.execute(
                    "SELECT id FROM projects WHERE name = ? AND status != 'closed'", (name,)
                ).fetchone()
                if not row:
                    continue
                kind = it.get("log_kind")
                if kind not in AUTO_MEMORY_LOG_KINDS:
                    kind = "note"
                # 未决意图降级（2026-10-12）：小白本轮回复里还带着追问 → 事情没拍板，
                # decision 强降为 note 并标注「（待定）」——说了没做的事不能进档案当决定
                if kind == "decision" and reply and ("？" in reply or "?" in reply):
                    kind = "note"
                    if not detail.startswith("（待定）"):
                        detail = "（待定）" + detail
                if kind == "next_step" and name in card_linked:
                    continue  # 操作卡的 create_task 已带 next_step 关联日志，跳过防双胞
                conn.execute(
                    "INSERT INTO progress_logs (project_id, kind, source, content, created_at) VALUES (?, ?, 'agent', ?, ?)",
                    (row["id"], kind, detail, now),
                )
            elif dest == "core":
                _write_core_section((it.get("title") or "杂项").strip(), detail)
            elif dest == "entity":
                _write_entity_page((it.get("title") or "").strip() or "未命名实体", detail, today)
            else:
                continue
            conn.execute(
                "INSERT INTO agent_suggestions (date, kind, content, user_action, created_at) VALUES (?, 'auto_memory', ?, 'auto', ?)",
                (today, str(it), now),
            )
        # 闪记闭环移植（FR-1.2）：本次提取内容覆盖到的待处理闪记自动确认
        corpus = "\n".join(
            f"{it.get('detail', '')}\n{it.get('title', '')}" for it in items if isinstance(it, dict)
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
        conn.commit()


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
    # 自动记忆（后台线程，不阻塞响应）：消息提到进行中的事 → 提取进展入库
    threading.Thread(target=_auto_memory, args=(body.message, reply, actions), daemon=True).start()
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
        # 资源索引锚点（阶段二；group 话题节点无地址，不做锚点）：命中可点击直达
        for l in conn.execute(
            "SELECT id, title, kind, target FROM links WHERE kind != 'group' ORDER BY id DESC LIMIT 100"
        ).fetchall():
            label = {"url": "网址", "file": "文件", "folder": "文件夹"}.get(l["kind"], "资源")
            out.append({"name": l["title"], "kind": "link", "hint": f"资源 · {label}",
                        "link_id": l["id"], "link_kind": l["kind"], "target": l["target"]})
    # 阶段四：wiki 锚点（entities/topics/sources 页）随知识库下线摘除——
    # 实体召回走 _expand_mentions 关键词展开（提名字即注入全文），不依赖锚点跳转
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
        # 自动记忆（后台线程，不阻塞 done 事件）
        threading.Thread(target=_auto_memory, args=(body.message, reply, actions), daemon=True).start()
        yield _sse({"done": True, "reply": reply, "actions": actions, "session_id": sid})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── 对话驱动操作（IDS 1.1/2.4：操作指令 → 确认卡 → 执行/撤销）──

TASK_STATUSES = ("backlog", "active", "done", "cancelled")
TASK_FIELDS = ("title", "status", "priority", "due_at", "project_id", "rrule", "repeat_mode", "start_at", "end_at")


class ActionIn(BaseModel):
    op: str  # create_task/update_task/complete_task/create_reminder/update_reminder/create_event/update_event/delete_event/create_slot/update_slot/delete_slot/create_link/create_project/complete_project/delete_task/delete_reminder/delete_link/delete_project/delete_log/create_filter/delete_filter/resolve_inbox
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


# 进行中的事类别（与 projects.py CATEGORIES 一致）
PROJECT_CATEGORIES = ("research", "collaboration", "family", "personal", "club", "course", "other")


def _find_project(conn, name: str):
    """按名称精确 → 模糊匹配进行中的事（不含已关闭）"""
    exact = conn.execute(
        "SELECT id, name, status, closed_at FROM projects WHERE name = ? AND status != 'closed' LIMIT 1",
        (name,),
    ).fetchone()
    if exact:
        return exact
    return conn.execute(
        "SELECT id, name, status, closed_at FROM projects WHERE name LIKE ? AND status != 'closed' ORDER BY id LIMIT 1",
        (f"%{name}%",),
    ).fetchone()


def _find_event(conn, name: str, dt: str | None = None):
    """定位一次性日程/重复系列：title+date 精确 → title 精确（最早一场）→ title 模糊"""
    if name and dt:
        row = conn.execute(
            "SELECT * FROM schedule_events WHERE title = ? AND date = ? LIMIT 1", (name, dt)
        ).fetchone()
        if row:
            return row
    if name:
        row = conn.execute(
            "SELECT * FROM schedule_events WHERE title = ? ORDER BY date LIMIT 1", (name,)
        ).fetchone()
        if row:
            return row
        return conn.execute(
            "SELECT * FROM schedule_events WHERE title LIKE ? ORDER BY date LIMIT 1", (f"%{name}%",)
        ).fetchone()
    return None


def _norm_time(v):
    """HH:MM 校验+补齐（8:00→08:00）；不合法返回 None"""
    s = str(v or "").strip()
    if len(s) == 4 and s[1] == ":":
        s = "0" + s
    if len(s) == 5 and s[2] == ":" and s[:2].isdigit() and s[3:].isdigit():
        return s
    return None


def _parse_dow(v):
    """day_of_week 归一化：0–6（0=周一），兼容「周一」…「周日」；缺省返回 None，无法解析返回 -1"""
    if v is None or v == "":
        return None
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    s = str(v).strip()
    if s in names:
        return names.index(s)
    try:
        n = int(s)
        return n if 0 <= n <= 6 else -1
    except ValueError:
        return -1


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
            # 防重复（2026-10-12）：同名未完成任务已存在多半是同一张卡被执行了两次
            dup = conn.execute(
                "SELECT id, due_at FROM tasks WHERE title = ? AND status IN ('backlog', 'active') LIMIT 1",
                (title,),
            ).fetchone()
            if dup:
                return {"ok": False, "error": f"已存在同名未完成任务「{title}」（如需调整请用 update_task），本次未重复创建"}
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
            rr = str(body.patch.get("rrule") or "").strip() or None
            if rr:
                from app.routers.tasks import valid_rrule
                if not valid_rrule(rr):
                    return {"ok": False, "error": f"rrule 无法解析（如 FREQ=WEEKLY;BYDAY=TH），收到 {rr}"}
            rmode = str(body.patch.get("repeat_mode") or "due").strip()
            if rmode not in ("due", "done"):
                return {"ok": False, "error": "repeat_mode 必须是 due/done 之一"}
            s_at = body.patch.get("start_at")
            e_at = body.patch.get("end_at")
            if s_at and e_at and str(e_at)[:10] < str(s_at)[:10]:
                return {"ok": False, "error": "结束日期不能早于开始日期"}
            cur = conn.execute(
                """INSERT INTO tasks (title, project_id, status, priority, due_at, rrule, repeat_mode, start_at, end_at, created_at)
                   VALUES (?, ?, 'backlog', ?, ?, ?, ?, ?, ?, ?)""",
                (title, project_id, body.patch.get("priority", 3), body.patch.get("due_at"),
                 rr, rmode if rr else "due", s_at, e_at, now),
            )
            target_id = cur.lastrowid
            # 智能关联（2026-10-09）：LLM 判断这个待办构成事务的下一步时带 patch.next_step，
            # 同步写一条关联日志（task_id 接线）——任务完成时这条日志随之落地（done_at）
            next_step = str(body.patch.get("next_step") or "").strip()
            if next_step and project_id:
                conn.execute(
                    """INSERT INTO progress_logs (project_id, task_id, kind, source, content, created_at)
                       VALUES (?, ?, 'next_step', 'agent', ?, ?)""",
                    (project_id, target_id, next_step, now),
                )
        elif body.op in ("update_task", "complete_task"):
            target = (body.target or "").strip()
            row = _find_task(conn, target) if target else None
            if not row:
                return {"ok": False, "error": f"没找到任务「{target or ''}」"}
            if body.op == "complete_task":
                # 重复任务（2026-10-10）：完成=同行重排到下一期，不置 done（与 REST 层同语义）；
                # 规则耗尽（UNTIL/COUNT）才落入下方正常完成。留痕用内部 op 名 reschedule_task
                full = conn.execute("SELECT * FROM tasks WHERE id = ?", (row["id"],)).fetchone()
                if full["rrule"]:
                    from app.routers.tasks import next_occurrence
                    nxt = next_occurrence(full["rrule"], full["repeat_mode"] or "due", full["due_at"], datetime.now())
                    if nxt:
                        conn.execute("UPDATE tasks SET due_at = ? WHERE id = ?", (nxt, row["id"]))
                        log_id = None
                        if full["project_id"]:
                            lc = conn.execute(
                                """INSERT INTO progress_logs (project_id, task_id, kind, source, content, created_at)
                                   VALUES (?, ?, 'status', 'agent', ?, ?)""",
                                (full["project_id"], row["id"], f"完成一期，下一期 {nxt}", now),
                            )
                            log_id = lc.lastrowid
                        uc = conn.execute(
                            "INSERT INTO agent_suggestions (date, kind, content, user_action, created_at) VALUES (?, 'chat_op', ?, 'executed', ?)",
                            (today, json.dumps(
                                {"action": {"op": "reschedule_task", "target": body.target, "patch": {}, "summary": body.summary},
                                 "pre_image": {"due_at": full["due_at"], "log_id": log_id},
                                 "target_id": row["id"]},
                                ensure_ascii=False), now),
                        )
                        return {"ok": True, "undo_id": uc.lastrowid, "summary": body.summary, "rescheduled": nxt}
            if body.op == "complete_task":
                patch = {"status": "done"}
            else:
                patch = {k: v for k, v in body.patch.items() if k in TASK_FIELDS}
                if "status" in patch and patch["status"] not in TASK_STATUSES:
                    return {"ok": False, "error": f"status 必须是 {TASK_STATUSES} 之一"}
                if patch.get("rrule"):
                    from app.routers.tasks import valid_rrule
                    if not valid_rrule(str(patch["rrule"])):
                        return {"ok": False, "error": f"rrule 无法解析（如 FREQ=WEEKLY;BYDAY=TH），收到 {patch['rrule']}"}
                if patch.get("repeat_mode") and patch["repeat_mode"] not in ("due", "done"):
                    return {"ok": False, "error": "repeat_mode 必须是 due/done 之一"}
                if not patch:
                    return {"ok": False, "error": "update_task 没有可改字段"}
            old = conn.execute("SELECT * FROM tasks WHERE id = ?", (row["id"],)).fetchone()
            # 窗口校验（三日期）：生效后最终值（patch 优先——显式 null 也是生效值，否则用现值）
            if body.op == "update_task":
                eff_s = patch["start_at"] if "start_at" in patch else old["start_at"]
                eff_e = patch["end_at"] if "end_at" in patch else old["end_at"]
                if eff_s and eff_e and str(eff_e)[:10] < str(eff_s)[:10]:
                    return {"ok": False, "error": "结束日期不能早于开始日期"}
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
            # 联动（2026-10-09）：任务完成 → 其关联的「下一步」日志落地（done_at），
            # 页面不再展示但数据保留留痕；撤销完成时由 undo 分支把 done_at 清回 NULL
            if body.op == "complete_task":
                conn.execute(
                    "UPDATE progress_logs SET done_at = ? WHERE task_id = ? AND done_at IS NULL",
                    (now, row["id"]),
                )
        elif body.op == "create_reminder":
            from app.routers.reminders import _normalize_trigger
            tt = body.patch.get("trigger_type", "once")
            r_title = str(body.patch.get("title") or body.summary or "提醒").strip()
            # 防重复（2026-10-12）：同名同类型提醒已存在且还激活，多半是同一张卡被执行了两次
            dup = conn.execute(
                "SELECT id FROM reminders WHERE title = ? AND trigger_type = ? AND active = 1 LIMIT 1",
                (r_title, tt),
            ).fetchone()
            if dup:
                return {"ok": False, "error": f"已存在同名「{tt}」提醒「{r_title}」（如需调整请用 update_reminder），本次未重复创建"}
            if tt == "relative":
                # 相对提醒（2026-10-10）：挂任务日期字段，改期自动跟随。task=任务名（精确→模糊）
                from app.routers.reminders import validate_relative
                tname = str(body.patch.get("task") or "").strip()
                trow = _find_task(conn, tname) if tname else None
                if not trow:
                    return {"ok": False, "error": f"相对提醒需要挂任务，没找到任务「{tname or ''}」"}
                rel_to = str(body.patch.get("relative_to") or "due_at").strip()
                try:
                    offset = validate_relative(conn, trow["id"], rel_to, body.patch.get("offset_minutes"))
                except Exception as e:
                    return {"ok": False, "error": getattr(e, "detail", str(e))}
                cur = conn.execute(
                    """INSERT INTO reminders (title, trigger_type, trigger_value, task_id, relative_to, offset_minutes)
                       VALUES (?, 'relative', '', ?, ?, ?)""",
                    (r_title, trow["id"], rel_to, offset),
                )
                target_id = cur.lastrowid
            else:
                tv = _normalize_trigger(tt, body.patch.get("trigger_value"))
                cur = conn.execute(
                    "INSERT INTO reminders (title, trigger_type, trigger_value) VALUES (?, ?, ?)",
                    (r_title, tt, tv),
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
            # 防重复（2026-10-12）：同名同首日的日程已存在，多半是同一张卡被执行了两次
            dup = conn.execute(
                "SELECT id FROM schedule_events WHERE title = ? AND date = ? LIMIT 1",
                (title, dt),
            ).fetchone()
            if dup:
                return {"ok": False, "error": f"已存在 {dt} 的同名日程「{title}」，本次未重复创建"}
            et = str(body.patch.get("entry_type") or "personal").strip()
            if et not in ("course", "meeting", "outing", "personal"):
                et = "personal"
            # 重复规律（阶段三）：LLM 负责自然语言→RRULE 翻译，这里只做可解析性校验
            rr = str(body.patch.get("rrule") or "").strip() or None
            if rr:
                try:
                    from dateutil.rrule import rrulestr
                    rrulestr(rr, dtstart=datetime.strptime(dt, "%Y-%m-%d"))
                except Exception:
                    return {"ok": False, "error": f"rrule 无法解析（如 FREQ=WEEKLY;BYDAY=TH），收到 {rr}"}
            cur = conn.execute(
                """INSERT INTO schedule_events (title, entry_type, date, start_time, end_time, location, rrule, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (title, et, dt,
                 str(body.patch.get("start_time") or "09:00"),
                 str(body.patch.get("end_time") or "10:00"),
                 body.patch.get("location"), rr, now),
            )
            target_id = cur.lastrowid
        elif body.op == "delete_event":
            target = (body.target or "").strip()
            dt = str(body.patch.get("date") or "").strip() or None
            row = _find_event(conn, target, dt)
            if not row:
                return {"ok": False, "error": f"没找到一次性日程「{target}」"}
            pre_image = dict(row)  # 撤销用整行快照（恢复插入）
            conn.execute("DELETE FROM schedule_events WHERE id = ?", (row["id"],))
            target_id = row["id"]
        elif body.op == "update_event":
            # 改一次性日程/重复系列（系列=改底层记录，全部实例随动）：
            # patch.date 仅作定位；改日期用 patch.new_date
            target = (body.target or "").strip()
            dt = str(body.patch.get("date") or "").strip() or None
            row = _find_event(conn, target, dt)
            if not row:
                return {"ok": False, "error": f"没找到日程「{target or ''}」"}
            patch = {}
            for k in ("title", "start_time", "end_time", "location", "note", "entry_type", "rrule"):
                if k in body.patch:
                    patch[k] = body.patch[k]
            new_date = str(body.patch.get("new_date") or "").strip()
            if new_date:
                try:
                    datetime.strptime(new_date, "%Y-%m-%d")
                except ValueError:
                    return {"ok": False, "error": f"new_date 格式应为 YYYY-MM-DD，收到 {new_date}"}
                patch["date"] = new_date
            if not patch:
                return {"ok": False, "error": "update_event 没有可改字段"}
            if "entry_type" in patch and patch["entry_type"] not in ("course", "meeting", "outing", "personal"):
                return {"ok": False, "error": "entry_type 必须是 course/meeting/outing/personal 之一"}
            for tk in ("start_time", "end_time"):
                if tk in patch:
                    t = _norm_time(patch[tk])
                    if not t:
                        return {"ok": False, "error": f"{tk} 格式应为 HH:MM，收到 {patch[tk]}"}
                    patch[tk] = t
            if patch.get("rrule"):
                try:
                    from dateutil.rrule import rrulestr
                    rrulestr(str(patch["rrule"]), dtstart=datetime.strptime(patch.get("date") or row["date"], "%Y-%m-%d"))
                except Exception:
                    return {"ok": False, "error": f"rrule 无法解析，收到 {patch['rrule']}"}
            pre_image = {k: row[k] for k in patch}  # 撤销用旧值快照
            sets = ", ".join(f"{k} = ?" for k in patch)
            conn.execute(f"UPDATE schedule_events SET {sets} WHERE id = ?", (*patch.values(), row["id"]))
            target_id = row["id"]
        elif body.op == "create_slot":
            # 建学期课表槽位（每周固定课程/例会，长期生效；单双周用 week_pattern）
            title = str(body.patch.get("title") or "").strip()
            if not title:
                return {"ok": False, "error": "create_slot 需要 title"}
            dow = _parse_dow(body.patch.get("day_of_week"))
            if dow is None or dow < 0:
                return {"ok": False, "error": "create_slot 需要 day_of_week（0–6，0=周一）"}
            st = _norm_time(body.patch.get("start_time"))
            en = _norm_time(body.patch.get("end_time"))
            if not st or not en:
                return {"ok": False, "error": "create_slot 需要 start_time/end_time（HH:MM）"}
            wp = str(body.patch.get("week_pattern") or "all").strip()
            if wp not in ("all", "odd", "even"):
                return {"ok": False, "error": "week_pattern 必须是 all/odd/even 之一"}
            etype = str(body.patch.get("entry_type") or "course").strip()
            if etype not in ("course", "meeting", "outing", "personal"):
                etype = "course"
            # 生效区间校验 + 学期共识默认（2026-10-12）：历史上 LLM 随意编 valid_to
            # （9-13/10-04 这类短区间）导致课程中途从周视图「消失」。 now：
            # 格式非法拒、valid_to<valid_from 拒、用户没提则自动对齐在排课程的多数结束日
            from datetime import date as _dcls
            vf, vt = body.patch.get("valid_from"), body.patch.get("valid_to")
            for lbl, dv in (("valid_from", vf), ("valid_to", vt)):
                if dv is not None and str(dv).strip():
                    try:
                        _dcls.fromisoformat(str(dv).strip())
                    except ValueError:
                        return {"ok": False, "error": f"{lbl} 格式应为 YYYY-MM-DD，收到 {dv}"}
            vf = str(vf).strip() if vf and str(vf).strip() else None
            vt = str(vt).strip() if vt and str(vt).strip() else None
            if vf and vt and vt < vf:
                return {"ok": False, "error": f"valid_to（{vt}）早于 valid_from（{vf}）"}
            if not vt:
                cons = conn.execute(
                    """SELECT valid_to, COUNT(*) AS n FROM schedule_slots
                       WHERE valid_to IS NOT NULL AND valid_to >= date('now')
                       GROUP BY valid_to ORDER BY n DESC, valid_to DESC LIMIT 1"""
                ).fetchone()
                if cons:
                    vt = cons["valid_to"]
            cur = conn.execute(
                """INSERT INTO schedule_slots (title, entry_type, day_of_week, start_time, end_time, location, week_pattern, valid_from, valid_to)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (title, etype, dow, st, en, body.patch.get("location"), wp, vf, vt),
            )
            target_id = cur.lastrowid
        elif body.op in ("update_slot", "delete_slot"):
            # 定位课表槽位：title 精确 → 模糊；一周多节同名课时用 patch.day_of_week 消歧
            from app.routers.scheduler import WEEKDAY_NAMES
            target = (body.target or "").strip()
            rows = []
            if target:
                rows = conn.execute(
                    "SELECT * FROM schedule_slots WHERE title = ? ORDER BY day_of_week, start_time", (target,)
                ).fetchall()
            if not rows and target:
                rows = conn.execute(
                    "SELECT * FROM schedule_slots WHERE title LIKE ? ORDER BY day_of_week, start_time", (f"%{target}%",)
                ).fetchall()
            if not rows:
                return {"ok": False, "error": f"没找到课表槽位「{target or ''}」"}
            if len(rows) > 1:
                dow = _parse_dow(body.patch.get("day_of_week"))
                narrowed = [r for r in rows if dow is not None and dow >= 0 and r["day_of_week"] == dow]
                if len(narrowed) == 1:
                    rows = narrowed
                else:
                    cand = narrowed if narrowed else rows
                    # 同名同天多节的二级定位（2026-10-12）：patch.start_time 与某一节
                    # 现值一致时视为定位符（此时不是改时间；是改时间的话值必不同、
                    # 匹配不上会走下面的消歧报错，安全）
                    pst = body.patch.get("start_time")
                    if pst is not None:
                        pst_n = _norm_time(pst)
                        by_st = [r for r in cand if pst_n and r["start_time"] == pst_n]
                        if len(by_st) == 1:
                            rows = by_st
            if len(rows) > 1:
                desc = "、".join(
                    f"{WEEKDAY_NAMES[r['day_of_week']]} {r['start_time']}（{r['valid_from'] or '?'}~{r['valid_to'] or '长期'}）"
                    for r in rows
                )
                return {"ok": False, "error": f"「{target}」在课表里有 {len(rows)} 节：{desc}。请用 day_of_week 指定改哪一天（0=周一）；同一天有多节时，再用 start_time 填该节当前的开始时间来定位"}
            row = rows[0]
            if body.op == "delete_slot":
                pre_image = dict(row)  # 撤销用整行快照（恢复插入）
                conn.execute("DELETE FROM schedule_slots WHERE id = ?", (row["id"],))
                target_id = row["id"]
            else:
                patch = {}
                for k in ("title", "start_time", "end_time", "location", "week_pattern", "entry_type", "valid_from", "valid_to"):
                    if k in body.patch:
                        patch[k] = body.patch[k]
                # 改星期几：多节定位时 day_of_week 是定位符；单节时与现值不同视为要改（new_day 优先）
                new_day = body.patch.get("new_day")
                if new_day is None and len(rows) == 1 and "day_of_week" in body.patch:
                    nd = _parse_dow(body.patch.get("day_of_week"))
                    if nd is not None and nd >= 0 and nd != row["day_of_week"]:
                        new_day = nd
                if new_day is not None:
                    nd = _parse_dow(new_day)
                    if nd is None or nd < 0:
                        return {"ok": False, "error": "new_day 必须是 0–6（0=周一）"}
                    patch["day_of_week"] = nd
                if not patch:
                    return {"ok": False, "error": "update_slot 没有可改字段"}
                if "week_pattern" in patch and patch["week_pattern"] not in ("all", "odd", "even"):
                    return {"ok": False, "error": "week_pattern 必须是 all/odd/even 之一"}
                if "entry_type" in patch and patch["entry_type"] not in ("course", "meeting", "outing", "personal"):
                    return {"ok": False, "error": "entry_type 必须是 course/meeting/outing/personal 之一"}
                for tk in ("start_time", "end_time"):
                    if tk in patch:
                        t = _norm_time(patch[tk])
                        if not t:
                            return {"ok": False, "error": f"{tk} 格式应为 HH:MM，收到 {patch[tk]}"}
                        patch[tk] = t
                # 生效区间格式校验（2026-10-12）：原样入库时代结束——LLM 编的非法/矛盾区间不再能落库
                from datetime import date as _dcls2
                for dk in ("valid_from", "valid_to"):
                    if dk in patch:
                        try:
                            _dcls2.fromisoformat(str(patch[dk]).strip())
                        except ValueError:
                            return {"ok": False, "error": f"{dk} 格式应为 YYYY-MM-DD，收到 {patch[dk]}"}
                        patch[dk] = str(patch[dk]).strip()
                if "valid_to" in patch and row["valid_from"] and patch["valid_to"] < row["valid_from"]:
                    return {"ok": False, "error": f"valid_to（{patch['valid_to']}）早于该槽位的 valid_from（{row['valid_from']}）"}
                pre_image = {k: row[k] for k in patch}  # 撤销用旧值快照
                sets = ", ".join(f"{k} = ?" for k in patch)
                conn.execute(f"UPDATE schedule_slots SET {sets} WHERE id = ?", (*patch.values(), row["id"]))
                target_id = row["id"]
        elif body.op == "create_link":
            title = str(body.patch.get("title") or "").strip()
            kind = str(body.patch.get("kind") or "").strip()
            target = str(body.patch.get("target") or "").strip()
            if not title or not target:
                return {"ok": False, "error": "create_link 需要 title 和 target"}
            if kind not in ("url", "file", "folder"):
                return {"ok": False, "error": "kind 必须是 url/file/folder 之一"}
            if kind == "url" and not target.startswith(("http://", "https://")):
                return {"ok": False, "error": "网址需以 http(s):// 开头"}
            # 挂靠进行中的事（同 create_task：名称必须精确命中真实条目，防幻觉）
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
                """INSERT INTO links (title, kind, target, category, note, project_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (title, kind, target,
                 str(body.patch.get("category") or "其他").strip() or "其他",
                 body.patch.get("note"), project_id, now),
            )
            target_id = cur.lastrowid
        elif body.op == "create_project":
            # 建「进行中的事」（2026-09-26）：大训这类持续多日、有结构的事务
            name = str(body.patch.get("name") or "").strip()
            if not name:
                return {"ok": False, "error": "create_project 需要 name"}
            category = str(body.patch.get("category") or "other").strip()
            if category not in PROJECT_CATEGORIES:
                return {"ok": False, "error": f"category 必须是 {PROJECT_CATEGORIES} 之一"}
            # 挂靠一级事务（可选，同 create_task：名称精确匹配防幻觉）
            parent_id = None
            parent_name = str(body.patch.get("parent") or "").strip()
            if parent_name:
                prow = conn.execute(
                    "SELECT id FROM projects WHERE name = ? AND status != 'closed' AND parent_id IS NULL",
                    (parent_name,),
                ).fetchone()
                if not prow:
                    return {"ok": False, "error": f"没找到一级事务「{parent_name}」可挂靠（父必须是一级，最多两级）"}
                parent_id = prow["id"]
            cur = conn.execute(
                "INSERT INTO projects (name, description, category, parent_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (name, body.patch.get("description"), category, parent_id, now),
            )
            target_id = cur.lastrowid
        elif body.op == "complete_project":
            # 完成事务（对称能力）：整块收起，数据保留可恢复
            target = (body.target or "").strip()
            prow = _find_project(conn, target) if target else None
            if not prow:
                return {"ok": False, "error": f"没找到进行中的事「{target or ''}」"}
            pre_image = {"status": prow["status"], "closed_at": prow["closed_at"]}
            conn.execute(
                "UPDATE projects SET status = 'closed', closed_at = ? WHERE id = ?",
                (now, prow["id"]),
            )
            target_id = prow["id"]
        elif body.op == "delete_task":
            # 删任务（破坏性，pre-image 整行快照供撤销）：挂靠日志脱离，同页面手动删
            target = (body.target or "").strip()
            row = _find_task(conn, target) if target else None
            if not row:
                return {"ok": False, "error": f"没找到任务「{target or ''}」"}
            old = conn.execute("SELECT * FROM tasks WHERE id = ?", (row["id"],)).fetchone()
            pre_image = dict(old)
            conn.execute("UPDATE progress_logs SET task_id = NULL WHERE task_id = ?", (row["id"],))
            conn.execute("UPDATE reminders SET task_id = NULL WHERE task_id = ?", (row["id"],))
            conn.execute("DELETE FROM tasks WHERE id = ?", (row["id"],))
            target_id = row["id"]
        elif body.op == "update_reminder":
            # 改提醒（2026-10-12，堵幻觉 op）：只改字段不换类型；改动后重新武装（last_fired_at 清空）
            target = (body.target or "").strip()
            row = None
            if target:
                row = conn.execute(
                    "SELECT * FROM reminders WHERE title = ? ORDER BY id LIMIT 1", (target,)
                ).fetchone()
            if not row and target:
                row = conn.execute(
                    "SELECT * FROM reminders WHERE title LIKE ? ORDER BY id LIMIT 1", (f"%{target}%",)
                ).fetchone()
            if not row:
                return {"ok": False, "error": f"没找到提醒「{target or ''}」"}
            if "trigger_type" in body.patch and str(body.patch["trigger_type"]).strip() != row["trigger_type"]:
                return {"ok": False, "error": "提醒不能换类型；要换类型请先 delete_reminder 再 create_reminder"}
            patch = {}
            if "title" in body.patch:
                nt = str(body.patch["title"]).strip()
                if not nt:
                    return {"ok": False, "error": "title 不能为空"}
                patch["title"] = nt
            if row["trigger_type"] == "relative":
                # 相对提醒：可改锚点 relative_to / 偏移 offset_minutes（需重校验）
                if "relative_to" in body.patch or "offset_minutes" in body.patch:
                    from app.routers.reminders import validate_relative
                    rel_to = str(body.patch.get("relative_to") or row["relative_to"] or "due_at").strip()
                    off_in = body.patch.get("offset_minutes", row["offset_minutes"])
                    try:
                        patch["offset_minutes"] = validate_relative(conn, row["task_id"], rel_to, off_in)
                        patch["relative_to"] = rel_to
                    except Exception as e:
                        return {"ok": False, "error": getattr(e, "detail", str(e))}
            else:
                if "trigger_value" in body.patch:
                    from app.routers.reminders import _normalize_trigger
                    tv_in = str(body.patch["trigger_value"]).strip()
                    # 宽容归一（2026-10-12）：once 提醒只给 HH:MM 时，沿用原日期拼成完整时刻——
                    # 模型/用户说「改到 21 点半」不该被要求重述整个日期
                    if row["trigger_type"] == "once" and re.fullmatch(r"\d{1,2}:\d{2}", tv_in):
                        tv_in = f"{(row['trigger_value'] or '')[:10]}T{tv_in}"
                    try:
                        patch["trigger_value"] = _normalize_trigger(row["trigger_type"], tv_in)
                    except Exception as e:
                        return {"ok": False, "error": getattr(e, "detail", str(e))}
            if not patch:
                return {"ok": False, "error": "update_reminder 没有可改字段（title/trigger_value，相对提醒为 relative_to/offset_minutes）"}
            pre_image = {k: row[k] for k in patch}
            pre_image["last_fired_at"] = row["last_fired_at"]
            patch["last_fired_at"] = None  # 重新武装：改过的提醒按新参数重新具备触发资格
            sets = ", ".join(f"{k} = ?" for k in patch)
            conn.execute(f"UPDATE reminders SET {sets} WHERE id = ?", (*patch.values(), row["id"]))
            target_id = row["id"]
        elif body.op == "delete_reminder":
            # 删提醒（破坏性，整行快照供撤销）
            target = (body.target or "").strip()
            row = None
            if target:
                row = conn.execute(
                    "SELECT * FROM reminders WHERE title = ? ORDER BY id LIMIT 1", (target,)
                ).fetchone()
            if not row and target:
                row = conn.execute(
                    "SELECT * FROM reminders WHERE title LIKE ? ORDER BY id LIMIT 1", (f"%{target}%",)
                ).fetchone()
            if not row:
                return {"ok": False, "error": f"没找到提醒「{target or ''}」"}
            pre_image = dict(row)
            conn.execute("DELETE FROM reminders WHERE id = ?", (row["id"],))
            target_id = row["id"]
        elif body.op == "delete_link":
            # 删资源（破坏性，整行快照供撤销）
            target = (body.target or "").strip()
            row = None
            if target:
                row = conn.execute(
                    "SELECT * FROM links WHERE title = ? ORDER BY id LIMIT 1", (target,)
                ).fetchone()
            if not row and target:
                row = conn.execute(
                    "SELECT * FROM links WHERE title LIKE ? ORDER BY id LIMIT 1", (f"%{target}%",)
                ).fetchone()
            if not row:
                return {"ok": False, "error": f"没找到资源「{target or ''}」"}
            pre_image = dict(row)
            conn.execute("DELETE FROM links WHERE id = ?", (row["id"],))
            target_id = row["id"]
        elif body.op == "delete_project":
            # 删事务——仅限空事务（误建场景）：挂了任务/子事项/日志的非空事务必须走 complete_project
            target = (body.target or "").strip()
            prow = _find_project(conn, target) if target else None
            if not prow:
                return {"ok": False, "error": f"没找到进行中的事「{target or ''}」"}
            pid = prow["id"]
            has_content = (
                conn.execute("SELECT 1 FROM tasks WHERE project_id = ? LIMIT 1", (pid,)).fetchone()
                or conn.execute("SELECT 1 FROM projects WHERE parent_id = ? LIMIT 1", (pid,)).fetchone()
                or conn.execute("SELECT 1 FROM progress_logs WHERE project_id = ? LIMIT 1", (pid,)).fetchone()
            )
            if has_content:
                return {"ok": False, "error": f"「{prow['name']}」下面已经挂了任务/子事项/日志，不能删；如果这事结束了，用「完成」收起（数据保留）"}
            pre_image = dict(prow)
            conn.execute("DELETE FROM projects WHERE id = ?", (pid,))
            target_id = pid
        elif body.op == "delete_log":
            # 删事务日志（破坏性，整行快照供撤销）：target=日志内容关键词，
            # patch.project 可选=限定某件进行中的事（精确名）；取最近一条匹配
            kw = (body.target or "").strip()
            if not kw:
                return {"ok": False, "error": "delete_log 需要 target（日志内容关键词）"}
            scope_sql, scope_params = "", []
            project_name = str(body.patch.get("project") or "").strip()
            if project_name:
                prow = conn.execute(
                    "SELECT id FROM projects WHERE name = ? AND status != 'closed'", (project_name,)
                ).fetchone()
                if not prow:
                    return {"ok": False, "error": f"没找到进行中的事「{project_name}」，请确认名称"}
                scope_sql, scope_params = " AND project_id = ?", [prow["id"]]
            row = conn.execute(
                f"SELECT * FROM progress_logs WHERE content = ?{scope_sql} ORDER BY created_at DESC, id DESC LIMIT 1",
                (kw, *scope_params),
            ).fetchone()
            if not row:
                row = conn.execute(
                    f"SELECT * FROM progress_logs WHERE content LIKE ?{scope_sql} ORDER BY created_at DESC, id DESC LIMIT 1",
                    (f"%{kw}%", *scope_params),
                ).fetchone()
            if not row:
                return {"ok": False, "error": f"没找到内容含「{kw}」的日志"}
            pre_image = dict(row)
            conn.execute("DELETE FROM progress_logs WHERE id = ?", (row["id"],))
            target_id = row["id"]
        elif body.op == "create_filter":
            # 存筛选（2026-10-10，Vikunja 借鉴）：结构化 JSON 定义，白名单校验后入库；前端本地求值
            from app.routers.filters import validate_filter
            ftitle = str(body.patch.get("title") or "").strip()
            fjson = str(body.patch.get("filter") or "").strip()
            if not ftitle or not fjson:
                return {"ok": False, "error": "create_filter 需要 patch.title 和 patch.filter（JSON 字符串）"}
            try:
                validate_filter(fjson)
            except Exception as e:
                return {"ok": False, "error": getattr(e, "detail", str(e))}
            if conn.execute("SELECT 1 FROM saved_filters WHERE title = ?", (ftitle,)).fetchone():
                return {"ok": False, "error": f"已存在同名筛选「{ftitle}」，换个名字或先删掉旧的"}
            nxt = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM saved_filters").fetchone()[0]
            cur = conn.execute(
                "INSERT INTO saved_filters (title, filter_json, position, created_at) VALUES (?, ?, ?, ?)",
                (ftitle, fjson, nxt, now),
            )
            target_id = cur.lastrowid
        elif body.op == "delete_filter":
            # 删筛选（整行快照供撤销；页签随之消失）
            target = (body.target or "").strip()
            row = None
            if target:
                row = conn.execute(
                    "SELECT * FROM saved_filters WHERE title = ? ORDER BY id LIMIT 1", (target,)
                ).fetchone()
            if not row and target:
                row = conn.execute(
                    "SELECT * FROM saved_filters WHERE title LIKE ? ORDER BY id LIMIT 1", (f"%{target}%",)
                ).fetchone()
            if not row:
                return {"ok": False, "error": f"没找到筛选「{target or ''}」"}
            pre_image = dict(row)
            conn.execute("DELETE FROM saved_filters WHERE id = ?", (row["id"],))
            target_id = row["id"]
        elif body.op == "resolve_inbox":
            # 处理闪记（2026-10-12）：对话里完成归类闭环。action: task/life/idea/discard
            # task 额外建任务（记入 pre_image 供撤销连带删除）；其余仅改状态留痕，与 inbox.py 语义一致
            kw = (body.target or "").strip()
            if not kw:
                return {"ok": False, "error": "resolve_inbox 需要 target（闪记原文或前段关键词）"}
            row = conn.execute(
                "SELECT * FROM inbox_items WHERE content = ? AND status = 'pending' ORDER BY id LIMIT 1", (kw,)
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT * FROM inbox_items WHERE content LIKE ? AND status = 'pending' ORDER BY id LIMIT 1",
                    (f"%{kw}%",),
                ).fetchone()
            if not row:
                return {"ok": False, "error": f"闪记池里没找到含「{kw}」的待处理条目（可能已被处理）"}
            act = str(body.patch.get("action") or "").strip()
            if act not in ("task", "life", "idea", "discard"):
                return {"ok": False, "error": "patch.action 必须是 task/life/idea/discard 之一"}
            new_status = "discarded" if act == "discard" else "confirmed"
            created_task_id = None
            if act == "task":
                t_title = str(body.patch.get("title") or row["content"]).strip()
                dup = conn.execute(
                    "SELECT id FROM tasks WHERE title = ? AND status IN ('backlog', 'active') LIMIT 1",
                    (t_title,),
                ).fetchone()
                if dup:
                    return {"ok": False, "error": f"已存在同名未完成任务「{t_title}」，闪记保持待处理未动"}
                cur = conn.execute(
                    "INSERT INTO tasks (title, status, created_at) VALUES (?, 'backlog', ?)",
                    (t_title, now),
                )
                created_task_id = cur.lastrowid
            conn.execute(
                "UPDATE inbox_items SET status = ?, resolved_at = ? WHERE id = ?",
                (new_status, now, row["id"]),
            )
            pre_image = {"id": row["id"], "created_task_id": created_task_id}
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
            if op == "complete_task":
                # 联动恢复（2026-10-09）：撤销完成 → 关联日志 done_at 清回 NULL，重新出现在页面
                conn.execute("UPDATE progress_logs SET done_at = NULL WHERE task_id = ?", (tid,))
        elif op == "create_reminder" and tid:
            conn.execute("DELETE FROM reminders WHERE id = ?", (tid,))
        elif op == "update_reminder" and tid and pre_image:
            sets = ", ".join(f"{k} = ?" for k in pre_image)
            conn.execute(f"UPDATE reminders SET {sets} WHERE id = ?", (*pre_image.values(), tid))
        elif op == "resolve_inbox" and pre_image:
            # 处理了又撤：闪记回待处理；连带建的任务一并删除（其关联日志解挂）
            conn.execute(
                "UPDATE inbox_items SET status = 'pending', resolved_at = NULL WHERE id = ?",
                (pre_image.get("id"),),
            )
            ct = pre_image.get("created_task_id")
            if ct:
                conn.execute("UPDATE progress_logs SET task_id = NULL WHERE task_id = ?", (ct,))
                conn.execute("UPDATE reminders SET task_id = NULL WHERE task_id = ?", (ct,))
                conn.execute("DELETE FROM tasks WHERE id = ?", (ct,))
        elif op == "reschedule_task" and tid and pre_image:
            # 重复任务重排又撤（2026-10-10）：due_at 回滚 + 删掉「完成一期」留痕日志
            conn.execute("UPDATE tasks SET due_at = ? WHERE id = ?", (pre_image.get("due_at"), tid))
            if pre_image.get("log_id"):
                conn.execute("DELETE FROM progress_logs WHERE id = ?", (pre_image["log_id"],))
        elif op == "create_event" and tid:
            conn.execute("DELETE FROM schedule_events WHERE id = ?", (tid,))
        elif op == "create_slot" and tid:
            conn.execute("DELETE FROM schedule_slots WHERE id = ?", (tid,))
        elif op == "update_event" and tid and pre_image:
            sets = ", ".join(f"{k} = ?" for k in pre_image)
            conn.execute(f"UPDATE schedule_events SET {sets} WHERE id = ?", (*pre_image.values(), tid))
        elif op == "update_slot" and tid and pre_image:
            sets = ", ".join(f"{k} = ?" for k in pre_image)
            conn.execute(f"UPDATE schedule_slots SET {sets} WHERE id = ?", (*pre_image.values(), tid))
        elif op == "create_link" and tid:
            conn.execute("DELETE FROM links WHERE id = ?", (tid,))
        elif op == "create_project" and tid:
            # 建了又撤：删事务行；其下任务解挂（project_id 置空，任务保留）；
            # 其二级子事项升回一级（不随葬）；日志随行删除（无事务可挂）
            conn.execute("UPDATE tasks SET project_id = NULL WHERE project_id = ?", (tid,))
            conn.execute("UPDATE projects SET parent_id = NULL WHERE parent_id = ?", (tid,))
            conn.execute("DELETE FROM progress_logs WHERE project_id = ?", (tid,))
            conn.execute("DELETE FROM projects WHERE id = ?", (tid,))
        elif op == "complete_project" and tid and pre_image:
            # 完成又撤：按快照恢复状态
            conn.execute(
                "UPDATE projects SET status = ?, closed_at = ? WHERE id = ?",
                (pre_image.get("status"), pre_image.get("closed_at"), tid),
            )
        elif op == "delete_event" and pre_image:
            # 删了又撤：按快照整行恢复（含原 id）
            conn.execute(
                """INSERT INTO schedule_events (id, title, entry_type, date, start_time, end_time, location, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (pre_image.get("id"), pre_image.get("title"), pre_image.get("entry_type"),
                 pre_image.get("date"), pre_image.get("start_time"), pre_image.get("end_time"),
                 pre_image.get("location"), pre_image.get("note"), pre_image.get("created_at")),
            )
        elif op == "delete_slot" and pre_image:
            conn.execute(
                """INSERT INTO schedule_slots (id, title, entry_type, day_of_week, start_time, end_time, location, week_pattern, valid_from, valid_to)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (pre_image.get("id"), pre_image.get("title"), pre_image.get("entry_type"),
                 pre_image.get("day_of_week"), pre_image.get("start_time"), pre_image.get("end_time"),
                 pre_image.get("location"), pre_image.get("week_pattern"),
                 pre_image.get("valid_from"), pre_image.get("valid_to")),
            )
        elif op == "delete_task" and pre_image:
            # 新列（rrule/repeat_mode/start_at/end_at，2026-10-10）老快照没有就置 NULL（.get 兜底）
            conn.execute(
                """INSERT INTO tasks (id, title, project_id, status, priority, due_at, estimate, created_at, completed_at, rrule, repeat_mode, start_at, end_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (pre_image.get("id"), pre_image.get("title"), pre_image.get("project_id"),
                 pre_image.get("status"), pre_image.get("priority"), pre_image.get("due_at"),
                 pre_image.get("estimate"), pre_image.get("created_at"), pre_image.get("completed_at"),
                 pre_image.get("rrule"), pre_image.get("repeat_mode"),
                 pre_image.get("start_at"), pre_image.get("end_at")),
            )
        elif op == "delete_reminder" and pre_image:
            conn.execute(
                """INSERT INTO reminders (id, title, trigger_type, trigger_value, scope, active, last_fired_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (pre_image.get("id"), pre_image.get("title"), pre_image.get("trigger_type"),
                 pre_image.get("trigger_value"), pre_image.get("scope"),
                 pre_image.get("active"), pre_image.get("last_fired_at")),
            )
        elif op == "delete_link" and pre_image:
            conn.execute(
                """INSERT INTO links (id, title, kind, target, category, note, project_id, entity, parent_id, position, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (pre_image.get("id"), pre_image.get("title"), pre_image.get("kind"), pre_image.get("target"),
                 pre_image.get("category"), pre_image.get("note"), pre_image.get("project_id"),
                 pre_image.get("entity"), pre_image.get("parent_id"), pre_image.get("position"),
                 pre_image.get("created_at")),
            )
        elif op == "delete_project" and pre_image:
            conn.execute(
                """INSERT INTO projects (id, name, description, category, parent_id, status, closed_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (pre_image.get("id"), pre_image.get("name"), pre_image.get("description"),
                 pre_image.get("category"), pre_image.get("parent_id"), pre_image.get("status"),
                 pre_image.get("closed_at"), pre_image.get("created_at")),
            )
        elif op == "delete_log" and pre_image:
            # 删日志又撤：按快照整行恢复（done_at 列 2026-10-09 迁移才有，老快照没有就不写）
            cols = ["id", "project_id", "task_id", "kind", "content", "source", "created_at"]
            if "done_at" in pre_image:
                cols.append("done_at")
            conn.execute(
                f"INSERT INTO progress_logs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                tuple(pre_image.get(c) for c in cols),
            )
        elif op == "create_filter" and tid:
            conn.execute("DELETE FROM saved_filters WHERE id = ?", (tid,))
        elif op == "delete_filter" and pre_image:
            conn.execute(
                "INSERT INTO saved_filters (id, title, filter_json, position, created_at) VALUES (?, ?, ?, ?, ?)",
                (pre_image.get("id"), pre_image.get("title"), pre_image.get("filter_json"),
                 pre_image.get("position"), pre_image.get("created_at")),
            )
        else:
            return {"ok": False, "error": "该操作不支持撤销"}
        conn.execute(
            "UPDATE agent_suggestions SET user_action = 'undone' WHERE id = ?", (body.undo_id,)
        )
    return {"ok": True}


# ── 沉淀退役（2026-09-26 铁律分层定案）──
# 原 /api/review + /api/proposals/apply（回顾对话→批量提案→逐条裁决）整体下线：
# 进展记录改由 _auto_memory 对话后自动入库（透明可管理），core/entity 能力并入；
# 结构化操作（task/link）由操作卡承担（提示词里的主动识别仍会提议）。
# _write_core_section / _write_entity_page 保留——自动记忆的写盘器。


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

