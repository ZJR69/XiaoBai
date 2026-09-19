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
SCHEDULE_AHEAD_MIN = 15  # 日程开始前 15 分钟预告


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


def slots_for_date(d: date | None = None) -> list[dict]:
    """某天的完整日程 = 周期性 slots（星期 + 单双周 + 有效范围）+ 当天一次性 events。
    返回条目带 source：'slot'（周期）/ 'event'（一次性）——周历据此区分渲染；
    其余消费端（今日/预告/简报/对话召回）无需关心来源，改此一处全部自动获益。"""
    d = d or datetime.now().date()
    dow = d.weekday()
    iso = d.isoformat()
    with get_conn() as conn:
        week_no = _semester_week(d, _semester_start(conn))
        rows = conn.execute(
            "SELECT * FROM schedule_slots WHERE day_of_week = ?", (dow,)
        ).fetchall()
        events = conn.execute(
            "SELECT * FROM schedule_events WHERE date = ?", (iso,)
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
            if fired:
                _notify("reminder", r["title"], f"（{r['trigger_type']} 提醒）")
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


def _check_schedule_upcoming():
    """日程预告（M4 FR-4.3）：下一项开始前 15 分钟"""
    now = datetime.now()
    now_hm = now.strftime("%H:%M")
    for s in slots_for_date(now.date()):
        if s["start_time"] and s["start_time"] > now_hm:
            # 分钟差计算
            sh, sm = map(int, s["start_time"].split(":"))
            nh, nm = now.hour, now.minute
            diff = (sh * 60 + sm) - (nh * 60 + nm)
            if 0 < diff <= SCHEDULE_AHEAD_MIN:
                where = f"@{s['location']} " if s["location"] else ""
                _notify(
                    "schedule", f"即将开始：{s['title']}",
                    f"{where}{s['start_time']}–{s['end_time']}",
                )
                break


def _check_graveyard():
    """坟场清理（M5 FR-5.2）：投放区文件超 14 天未消化，建议处理或放弃。
    只查投放区文件——闪记池不催（交互定案 IDS 2.5：小白永不主动催闪记）。
    当日去重由 _notify 兜底，每 tick 检查成本可忽略。"""
    cutoff = (datetime.now() - timedelta(days=14)).isoformat(timespec="seconds")
    with get_conn() as conn:
        old_files = conn.execute(
            "SELECT COUNT(*) AS n FROM raw_files WHERE status = 'untracked' AND first_seen_at < ?",
            (cutoff,),
        ).fetchone()["n"]
    if old_files >= 3:
        _notify("graveyard", "投放区有文件躺了两周以上",
                f"{old_files} 个文件超过 14 天未消化。消化它们，或移出投放区。")


def _check_weekly_lint():
    """每周一体检（PRD 4.5 每日节律）：wiki 有问题时发一条通知，每周至多一次。
    只通知不修复——修复需用户在知识库视图确认（半自动契约）。"""
    now = datetime.now()
    if now.weekday() != 0 or now.hour < 8:
        return
    week_id = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]}"
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'last_lint_week'").fetchone()
        if row and row["value"] == week_id:
            return
        conn.execute(
            """INSERT INTO settings (key, value) VALUES ('last_lint_week', ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (week_id,),
        )
    from app.routers.wiki import collect_lint
    try:
        result = collect_lint()
    except Exception:
        return
    if not result["ok"]:
        _notify("lint", "知识库体检发现问题",
                f"{len(result['issues'])} 个问题（孤儿页/core 膨胀/重复实体等），去知识库视图点「体检」看详情。")


def _tick():
    for check in (_check_reminders, _check_due_tasks, _check_schedule_upcoming, _check_graveyard,
                  _check_weekly_lint):
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
