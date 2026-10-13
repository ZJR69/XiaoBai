"""M4 适时提醒 + M6 提醒触发调度：后台 daemon 线程每 30 秒 tick。

防打扰原则（交互设计延续）：
- 每个触发只发一次（当日去重），不重复弹、不追着问
- 重要通知（截止临近）同步写入今日对话流，其余只进通知中心
- 生活提醒 once 类型触发后自动停用（active=0），不删记录
"""
import threading
import time
from datetime import date, datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel

from app.database import get_conn

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

_started = False
DUE_AHEAD_HOURS = 6  # 截止前 6 小时开始提醒


# ── 日程匹配（schedule.py 的 today 端点也复用） ──

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
DEFAULT_SEMESTER_START = date(2026, 9, 7)  # 用户实际开学日（settings.semester_start 可覆盖，换季手动改）


def _semester_start(conn) -> date:
    """学期第 1 周起点的周一：settings.semester_start（YYYY-MM-DD）优先，缺省用实际开学日。"""
    row = conn.execute("SELECT value FROM settings WHERE key = 'semester_start'").fetchone()
    if row:
        try:
            return date.fromisoformat(row["value"])
        except ValueError:
            pass
    return DEFAULT_SEMESTER_START


def _semester_week(d: date, semester_start: date | None = None) -> int:
    """教学周序号：从学期起点所在周的周一起算第 1 周。
    修复：旧版按 9 月 1 日所在周算，比学校实际校历（9 月 7 日开学）多算一周，单双周判定整体错位。"""
    start = semester_start or DEFAULT_SEMESTER_START
    monday = start - timedelta(days=start.weekday())
    return max(1, ((d - monday).days // 7) + 1)


def today_line() -> str:
    """对话上下文固定注入的第一行：今天日期 + 星期 + 教学周 + 本周范围（时间意识的锚点）。"""
    d = datetime.now().date()
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    with get_conn() as conn:
        wk = _semester_week(d, _semester_start(conn))
    return (f"今天是 {d.isoformat()} {WEEKDAY_NAMES[d.weekday()]}"
            f"（教学第 {wk} 周：{monday:%m.%d}–{sunday:%m.%d}）")


def _occurs_on(rrule_str: str, start_iso: str, d: date) -> bool:
    """重复系列（dtstart=系列首日 00:00）在日期 d 是否有实例。
    rrule 串坏了静默 False——建时（API/操作卡）已校验，这里是兜底；
    dateutil 缺失时同样静默降级（系列不展开，其余功能不受影响）。"""
    try:
        from dateutil.rrule import rrulestr
        rule = rrulestr(rrule_str.strip(), dtstart=datetime.fromisoformat(start_iso + "T00:00"))
    except Exception:
        return False
    day0 = datetime.combine(d, datetime.min.time())
    # 半开窗口 [day0, 次日0点)：右端点减 1 微秒——实例都在 00:00，闭区间会把次日 0 点的实例算进当天
    day_end = day0 + timedelta(days=1) - timedelta(microseconds=1)
    return bool(rule.between(day0, day_end, inc=True))


def slots_for_date(d: date | None = None) -> list[dict]:
    """某天的完整日程 = 周期性 slots（星期 + 单双周 + 有效范围）+ 当天一次性 events + RRULE 系列展开。
    返回条目带 source：'slot'（周期课程）/ 'event'（一次性或重复实例，实例另带 recurring=True）——
    周历据此区分渲染；其余消费端（今日/预告/简报/对话召回）无需关心来源，改此一处全部自动获益。"""
    d = d or datetime.now().date()
    dow = d.weekday()
    iso = d.isoformat()
    with get_conn() as conn:
        week_no = _semester_week(d, _semester_start(conn))
        rows = conn.execute(
            "SELECT * FROM schedule_slots WHERE day_of_week = ?", (dow,)
        ).fetchall()
        # 一次性日程（rrule 为空）；重复系列不在这查——它们的 date 是首次日期，靠下方展开命中任意天
        events = conn.execute(
            "SELECT * FROM schedule_events WHERE date = ? AND (rrule IS NULL OR rrule = '')", (iso,)
        ).fetchall()
        series = conn.execute(
            "SELECT * FROM schedule_events WHERE rrule IS NOT NULL AND rrule != ''"
        ).fetchall()
    out = []
    for r in rows:
        if r["week_pattern"] == "odd" and week_no % 2 == 0:
            continue
        if r["week_pattern"] == "even" and week_no % 2 == 1:
            continue
        if r["valid_from"] and iso < r["valid_from"]:
            continue
        if r["valid_to"] and iso > r["valid_to"]:
            continue
        out.append({**dict(r), "source": "slot"})
    for e in events:
        # 归一化出 slot 同形字段（day_of_week/week_pattern），消费端不用分辨结构
        out.append({**dict(e), "source": "event", "day_of_week": dow, "week_pattern": "all"})
    # RRULE 重复系列展开（阶段三）：查询时展开不落库——底层永远只有 1 个 TEXT 字段，新花式日程不改表结构
    for s in series:
        if _occurs_on(s["rrule"], s["date"], d):
            out.append({**dict(s), "source": "event", "recurring": True,
                        "day_of_week": dow, "week_pattern": "all"})
    return sorted(out, key=lambda s: s["start_time"] or "")


def _notify(kind: str, title: str, detail: str = "", important: bool = False):
    """写通知；important 同步写入今日对话流。当日同 kind+title 去重。"""
    now = datetime.now().isoformat(timespec="seconds")
    today = now[:10]
    with get_conn() as conn:
        dup = conn.execute(
            "SELECT 1 FROM notifications WHERE kind = ? AND title = ? AND created_at LIKE ?",
            (kind, title, today + "%"),
        ).fetchone()
        if dup:
            return
        conn.execute(
            "INSERT INTO notifications (kind, title, detail, important, created_at) VALUES (?, ?, ?, ?, ?)",
            (kind, title, detail, 1 if important else 0, now),
        )
        if important:
            # 重要通知进对话流：role=assistant，用户打开对话即可见
            conn.execute(
                "INSERT INTO chat_messages (session_date, role, content, created_at) VALUES (?, 'assistant', ?, ?)",
                (today, f"⏰ {title}\n{detail}".strip(), now),
            )


# ── 三类检查 ──

def _check_reminders():
    """生活提醒触发（M6 FR-6.1）：once/daily/weekly/interval"""
    now = datetime.now()
    today = now.date().isoformat()
    hm = now.strftime("%H:%M")
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM reminders WHERE active = 1").fetchall()
        for r in rows:
            tv = (r["trigger_value"] or "").strip()
            last = (r["last_fired_at"] or "")[:10]
            fired = False
            if r["trigger_type"] == "once":
                # ISO 日期/时间：到了就触发
                if tv and tv[:10] <= today and last != today:
                    fired = True
            elif r["trigger_type"] == "daily":
                # "HH:MM"：当天到点且今天没发过
                if tv and tv <= hm and last != today:
                    fired = True
            elif r["trigger_type"] == "weekly":
                # "W-HH:MM"：星期几 + 时刻，本周没发过
                if tv and len(tv) >= 6 and tv[1] == "-":
                    wd, t = tv[0], tv[2:]
                    if str(now.weekday()) == wd and t <= hm and (r["last_fired_at"] or "")[:10] < _week_start(now):
                        fired = True
            elif r["trigger_type"] == "interval":
                # 间隔小时数：距上次触发超过间隔
                try:
                    hours = float(tv)
                except ValueError:
                    continue
                if r["last_fired_at"]:
                    try:
                        last_dt = datetime.fromisoformat(r["last_fired_at"])
                        if now - last_dt >= timedelta(hours=hours):
                            fired = True
                    except ValueError:
                        fired = True
                else:
                    fired = True
            elif r["trigger_type"] == "relative":
                # 相对任务日期（2026-10-10）：触发时刻=任务[relative_to]+offset，每 tick 现算——
                # 任务改期/重复任务重排后 fire_time 跟着前移，last_fired_at 单调比较自动重新武装
                trow = conn.execute(
                    "SELECT due_at, start_at, end_at FROM tasks WHERE id = ?", (r["task_id"],)
                ).fetchone()
                rel_field = r["relative_to"] if r["relative_to"] in ("due_at", "start_at", "end_at") else None
                raw_base = trow[rel_field] if trow and rel_field else None
                if raw_base:
                    try:
                        base = datetime.fromisoformat(raw_base[:10] + "T00:00" if len(raw_base) == 10 else raw_base)
                    except ValueError:
                        base = None
                    if base:
                        fire_at = base + timedelta(minutes=r["offset_minutes"] or 0)
                        try:
                            last_dt = datetime.fromisoformat(r["last_fired_at"]) if r["last_fired_at"] else None
                        except ValueError:
                            last_dt = None
                        if fire_at <= now and (last_dt is None or last_dt < fire_at):
                            fired = True
            if fired:
                label = "相对任务日期" if r["trigger_type"] == "relative" else r["trigger_type"]
                _notify("reminder", r["title"], f"（{label} 提醒）")
                now_iso = now.isoformat(timespec="seconds")
                new_active = 0 if r["trigger_type"] == "once" else 1
                conn.execute(
                    "UPDATE reminders SET active = ?, last_fired_at = ? WHERE id = ?",
                    (new_active, now_iso, r["id"]),
                )


def _week_start(now: datetime) -> str:
    """本周一日期（weekly 去重用）"""
    monday = now.date() - timedelta(days=now.weekday())
    return monday.isoformat()


def _check_due_tasks():
    """任务截止临近（M4 FR-4.3）：截止前 6 小时一次性提醒"""
    now = datetime.now()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT title, due_at FROM tasks
               WHERE due_at IS NOT NULL AND status IN ('backlog', 'active')"""
        ).fetchall()
    for t in rows:
        raw_due = t["due_at"]
        try:
            if len(raw_due) == 10:  # 纯日期：按当天结束时刻算，白天也能触发截止提醒
                due = datetime.fromisoformat(raw_due + "T23:59")
            else:
                due = datetime.fromisoformat(raw_due)
        except ValueError:
            continue
        remaining = due - now
        if timedelta(0) <= remaining <= timedelta(hours=DUE_AHEAD_HOURS):
            hours_left = remaining.total_seconds() / 3600
            _notify(
                "due", f"「{t['title']}」快到期了",
                f"截止 {t['due_at'][:16].replace('T', ' ')}，还剩约 {hours_left:.1f} 小时",
                important=True,
            )


def _tick():
    # 阶段四摘除：日程预告（早报已有今日日程）、坟场通知、每周 lint（均随 wiki/投放区下线）
    for check in (_check_reminders, _check_due_tasks):
        try:
            check()
        except Exception:
            pass  # 单项失败不拖垮调度循环


def _scheduler_loop():
    while True:
        try:
            _tick()
        except Exception:
            pass
        time.sleep(30)


@router.on_event("startup")
async def _start_scheduler():
    global _started
    if _started:
        return
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()
    _started = True


# ── 通知端点（前端轮询 + 处理） ──

@router.get("")
def list_notifications(only_open: bool = True):
    """未处理通知列表（含数量），前端侧栏铃铛轮询"""
    with get_conn() as conn:
        if only_open:
            rows = conn.execute(
                "SELECT * FROM notifications WHERE dismissed_at IS NULL ORDER BY id DESC LIMIT 50"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM notifications ORDER BY id DESC LIMIT 50"
            ).fetchall()
    return [dict(r) for r in rows]


class DismissIn(BaseModel):
    ids: list[int]


@router.post("/dismiss")
def dismiss(body: DismissIn):
    """处理通知（可批量）"""
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        for nid in body.ids:
            conn.execute(
                "UPDATE notifications SET dismissed_at = ? WHERE id = ? AND dismissed_at IS NULL",
                (now, nid),
            )
    return {"ok": True}
