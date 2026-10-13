"""数据库层：SQLite 连接与建表（表结构定义见 docs/小白PRD.md 第 6 节）"""
import sqlite3
from datetime import datetime
from pathlib import Path

# backend/app/database.py → 仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "storage" / "xiaobai.db"

# 与 PRD 第 6 节数据模型保持一致
DDL = """
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  category TEXT DEFAULT 'other',
  status TEXT DEFAULT 'active',
  parent_id INTEGER REFERENCES projects(id),  -- 二级事项（2026-09-26：父=一级事务，封顶二级，子不能再有子）
  created_at TEXT,
  closed_at TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  project_id INTEGER REFERENCES projects(id),
  status TEXT DEFAULT 'backlog',
  priority INTEGER DEFAULT 3,
  due_at TEXT,
  estimate TEXT,
  created_at TEXT,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS progress_logs (
  id INTEGER PRIMARY KEY,
  project_id INTEGER REFERENCES projects(id),
  task_id INTEGER REFERENCES tasks(id),
  kind TEXT,
  content TEXT NOT NULL,
  source TEXT DEFAULT 'user',
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS inbox_items (
  id INTEGER PRIMARY KEY,
  content TEXT NOT NULL,
  source TEXT DEFAULT 'quick_note',
  status TEXT DEFAULT 'pending',
  ai_suggestion TEXT,
  captured_at TEXT,
  resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS schedule_slots (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  entry_type TEXT DEFAULT 'course',
  day_of_week INTEGER,
  start_time TEXT,
  end_time TEXT,
  location TEXT,
  week_pattern TEXT DEFAULT 'all',
  valid_from TEXT,
  valid_to TEXT
);

-- 一次性日程（日程的默认形态：调课/牙医/临时会议；周期性课程与固定组会才在 schedule_slots）
CREATE TABLE IF NOT EXISTS schedule_events (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  entry_type TEXT DEFAULT 'personal',
  date TEXT NOT NULL,              -- YYYY-MM-DD（一次性=发生日；重复系列=首次日期 dtstart）
  start_time TEXT,
  end_time TEXT,
  location TEXT,
  note TEXT,
  rrule TEXT,                      -- iCalendar RRULE（阶段三；NULL=一次性。重复系列查询时展开不落库）
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_date ON schedule_events(date);

CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  trigger_type TEXT,
  trigger_value TEXT,
  scope TEXT DEFAULT 'life',
  active INTEGER DEFAULT 1,
  last_fired_at TEXT
);

CREATE TABLE IF NOT EXISTS feedback_sessions (
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  transcript TEXT,
  signals TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS agent_suggestions (
  id INTEGER PRIMARY KEY,
  date TEXT,
  kind TEXT,
  content TEXT,
  user_action TEXT,
  created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_inbox_status ON inbox_items(status);

-- wiki 归档状态（Git 化：归档时记录哈希，之后对比判断 modified）
-- archived_content 为归档时内容快照（modified 页 diff 用，2026-09-19 定案），存量行可能为 NULL
CREATE TABLE IF NOT EXISTS wiki_pages (
  path TEXT PRIMARY KEY,
  archived_hash TEXT,
  archived_at TEXT
);

-- wiki 事件流（知识库视图「最近动态」feed 与统计的机读数据源，IDS 2.6）
-- 写路径三件套：任何 wiki 变更 = 写文件 + 记 log.md + 插本表；归档例外（基线元操作，不写）
CREATE TABLE IF NOT EXISTS wiki_events (
  id INTEGER PRIMARY KEY,
  kind TEXT,          -- 来源分类（feed 着色维度）：ingest 消化/settle 对话沉淀/edit 手动编辑/raw_modified 原料变动/lint 体检
  summary TEXT,       -- 人读摘要（含 CRUD 信息：新增/更新…）
  paths TEXT,         -- JSON 数组：相关 wiki 页相对路径（feed 渲染为可点链接）
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_wiki_events_time ON wiki_events(created_at);

-- 知识页被引用计数（FR-2.9：坟场清理与「什么值得存」的反馈闭环）
CREATE TABLE IF NOT EXISTS page_refs (
  id INTEGER PRIMARY KEY,
  page TEXT NOT NULL,  -- wiki 页相对路径
  kind TEXT,           -- anchor_click（对话锚点点击）/pack（context 打包引用）/briefing（间隔重现展示）
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_page_refs_page ON page_refs(page);

-- 对话消息（对话流持久化，记忆同步的数据源）
CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY,
  session_date TEXT,
  role TEXT,
  content TEXT NOT NULL,
  created_at TEXT,
  session_id INTEGER               -- 所属会话（NULL = 天级消息：早报等）
);

-- 对话会话（多会话 + 分支：parent_id 指向被分支的会话）
CREATE TABLE IF NOT EXISTS chat_sessions (
  id INTEGER PRIMARY KEY,
  title TEXT,
  parent_id INTEGER,
  created_at TEXT,
  updated_at TEXT
);

-- 投放区文件（dropzone 扫描登记 + 消化状态）
CREATE TABLE IF NOT EXISTS raw_files (
  path TEXT PRIMARY KEY,             -- 相对 dropzone 的路径
  size INTEGER,
  archived_hash TEXT,                -- 消化时的哈希（对比判断 modified）
  status TEXT DEFAULT 'untracked',   -- untracked/digested
  source_page TEXT,                  -- 消化后生成的 wiki 摘要页路径
  first_seen_at TEXT,
  digested_at TEXT
);

-- 调度器通知（M4/M6：提醒触发、截止临近、日程预告、简报就绪）
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY,
  kind TEXT,                         -- reminder/due/schedule
  title TEXT NOT NULL,
  detail TEXT,
  important INTEGER DEFAULT 0,       -- 重要通知同步写入对话流
  created_at TEXT,
  dismissed_at TEXT                  -- 非空 = 已处理
);
CREATE INDEX IF NOT EXISTS idx_notifications_open ON notifications(dismissed_at);

-- 早间简报（M4 FR-4.2：当天生成一次缓存；shown_at 非空 = 已在对话流展示过）
CREATE TABLE IF NOT EXISTS briefings (
  date TEXT PRIMARY KEY,
  content TEXT NOT NULL,
  created_at TEXT,
  shown_at TEXT
);

-- 键值设置（M4 FR-4.5：并行度阈值等，随反馈对话自校准）
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);

-- 间隔重现队列（M5 FR-5.1：入库知识按倍增间隔 1/3/6/12/24…30 封顶，在简报中重现）
CREATE TABLE IF NOT EXISTS spaced_reviews (
  id INTEGER PRIMARY KEY,
  page TEXT NOT NULL,                 -- wiki 页相对路径
  title TEXT NOT NULL,
  introduced_at TEXT,
  next_review_at TEXT,                -- 到期日（含当天）
  interval_days INTEGER DEFAULT 1,
  review_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_reviews_due ON spaced_reviews(next_review_at);

-- 资源索引（重构蓝图阶段二，知识库新定位：学习/项目链接与资料存放地址的登记页）
-- 登记的不再是内容本身而是内容的地址；点击行为：url 前端新开标签，file/folder 后端 explorer 定位
CREATE TABLE IF NOT EXISTS links (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  kind TEXT NOT NULL,              -- url / file / folder / group（group=话题索引节点，2026-09-26 树化）
  target TEXT NOT NULL,            -- 网址（带 http 前缀）或本地绝对路径（group 为空串）
  category TEXT DEFAULT '其他',    -- 旧平铺分组名（树化后仅遗留兼容，页面不再按此聚类）
  note TEXT,
  project_id INTEGER REFERENCES projects(id),
  entity TEXT,                     -- 挂靠实体名（可选；实体是 wiki entities/ 页，无表）
  parent_id INTEGER REFERENCES links(id),  -- 树形父节点（NULL=根级；父必须是 group）
  position INTEGER,                   -- 同层排序（拖拽排序用；旧库 ALTER 补列后由 init_db 存量赋值）
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_links_category ON links(category);
-- 注：idx_links_parent 索引在 init_db 的 ALTER 补列后创建（旧库跑 DDL 时列尚不存在，放这里会崩）

-- 保存筛选（2026-10-10，Vikunja saved filters 借鉴）：对话出结构化 JSON 定义，用户裁决入库；
-- 后端只做 CRUD，求值在前端本地（任务量小，零查询引擎）
CREATE TABLE IF NOT EXISTS saved_filters (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  filter_json TEXT NOT NULL,       -- 结构化筛选定义（白名单校验过的 JSON）
  position INTEGER,                -- 页签排序
  created_at TEXT
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    try:
        conn.executescript(DDL)
        # 轻量迁移：给已存在的库补列（列已存在时静默跳过）
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN category TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE chat_messages ADD COLUMN session_id INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE wiki_pages ADD COLUMN archived_content TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE schedule_events ADD COLUMN rrule TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE links ADD COLUMN parent_id INTEGER REFERENCES links(id)")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE links ADD COLUMN position INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN parent_id INTEGER REFERENCES projects(id)")
        except sqlite3.OperationalError:
            pass
        # 2026-10-09 日志落地标记：任务完成 → 关联「下一步」日志置 done_at（页面不再展示，数据保留留痕）
        try:
            conn.execute("ALTER TABLE progress_logs ADD COLUMN done_at TEXT")
        except sqlite3.OperationalError:
            pass
        # 2026-10-10 重复任务（借鉴 Vikunja）：rrule=iCalendar 原语（与 schedule_events 同底层），
        # repeat_mode=due（按原截止节奏锚定）/done（从完成时刻起算）；勾选完成=同行重排到下一期
        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN rrule TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN repeat_mode TEXT DEFAULT 'due'")
        except sqlite3.OperationalError:
            pass
        # 2026-10-10 三日期（借鉴 Vikunja）：start_at/end_at=计划窗口（打算什么时候做），due_at=死线，语义分离
        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN start_at TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN end_at TEXT")
        except sqlite3.OperationalError:
            pass
        # 2026-10-10 相对提醒（借鉴 Vikunja）：提醒挂任务的日期字段（trigger_type='relative'），
        # 触发时刻=任务[relative_to]+offset_minutes，调度器每 tick 现算——任务改期提醒自动跟随
        try:
            conn.execute("ALTER TABLE reminders ADD COLUMN task_id INTEGER REFERENCES tasks(id)")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE reminders ADD COLUMN relative_to TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE reminders ADD COLUMN offset_minutes INTEGER")
        except sqlite3.OperationalError:
            pass
        # 索引须在列就绪后建（旧库 executescript 时列尚未存在，放 DDL 会崩）
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_links_parent ON links(parent_id)")
        # 存量迁移（2026-09-26 二级事项，幂等由 settings 标记）：
        # 「课学习」类课程事务的描述里解析科目名单 → 每门课建二级子事项挂到父级下。
        # 描述格式约定：「…共 9 门课：科目一、科目二、…。学期…」——取「门课：」到下一个「。」之间、按「、」切分
        split_done = conn.execute(
            "SELECT value FROM settings WHERE key = 'projects_course_split'"
        ).fetchone()
        if not split_done:
            import re as _re
            for p in conn.execute(
                "SELECT id, name, description, category FROM projects WHERE category = 'course' AND parent_id IS NULL"
            ).fetchall():
                m = p["description"] and _re.search(r"门课[：:]\s*([^。]+)", p["description"])
                if not m:
                    continue
                stamp = datetime.now().isoformat(timespec="seconds")
                for name in [s.strip() for s in m.group(1).split("、") if s.strip()]:
                    conn.execute(
                        """INSERT INTO projects (name, description, category, status, parent_id, created_at)
                           VALUES (?, ?, 'course', 'active', ?, ?)""",
                        (name, "", p["id"], stamp),
                    )
            conn.execute(
                """INSERT INTO settings (key, value) VALUES ('projects_course_split', '1')
                   ON CONFLICT(key) DO UPDATE SET value = '1'"""
            )
        # 存量迁移（2026-09-26 树化，幂等由 settings 标记）：旧平铺 category → 一级 group 节点，
        # 原链接挂进对应话题下；「其他」/空分组留在根级（不值得为它们建组）
        done = conn.execute("SELECT value FROM settings WHERE key = 'links_tree_migrated'").fetchone()
        if not done:
            cats = conn.execute(
                """SELECT DISTINCT category FROM links
                   WHERE (parent_id IS NULL) AND kind != 'group'
                     AND category IS NOT NULL AND category != '' AND category != '其他'"""
            ).fetchall()
            stamp = datetime.now().isoformat(timespec="seconds")
            for r in cats:
                cat = r["category"]
                cur = conn.execute(
                    "INSERT INTO links (title, kind, target, category, parent_id, created_at) VALUES (?, 'group', '', '其他', NULL, ?)",
                    (cat, stamp),
                )
                conn.execute(
                    "UPDATE links SET parent_id = ? WHERE category = ? AND kind != 'group' AND parent_id IS NULL",
                    (cur.lastrowid, cat),
                )
            conn.execute(
                """INSERT INTO settings (key, value) VALUES ('links_tree_migrated', '1')
                   ON CONFLICT(key) DO UPDATE SET value = '1'"""
            )
        # 存量赋 position（幂等：只补 NULL 行——含上面 category 迁移新插的 group，按 parent 分组 id 升序 0..n；
        # 放在 category 迁移之后，新行由 create 端点填）
        null_pos = conn.execute(
            "SELECT id, parent_id FROM links WHERE position IS NULL ORDER BY id"
        ).fetchall()
        if null_pos:
            counters = {}
            for r in null_pos:
                key = r["parent_id"]
                counters[key] = counters.get(key, -1) + 1
                conn.execute("UPDATE links SET position = ? WHERE id = ?", (counters[key], r["id"]))
        # 存量迁移：多会话改造前，按日期把旧对话归入每日期一个会话（幂等：二跑无 NULL 普通消息）
        # 天级消息（☀ 早报 / 🌙 复盘 / ⏰ 通知）不归会话——它们属于某一天，不复制进每个会话
        old_dates = conn.execute(
            """SELECT DISTINCT session_date FROM chat_messages
               WHERE session_id IS NULL AND role IN ('user','assistant')
                 AND content NOT LIKE '☀%' AND content NOT LIKE '🌙%' AND content NOT LIKE '⏰%'"""
        ).fetchall()
        for r in old_dates:
            day = r["session_date"]
            first = conn.execute(
                """SELECT content FROM chat_messages WHERE session_date=? AND session_id IS NULL
                   AND role='user' ORDER BY id LIMIT 1""", (day,)
            ).fetchone()
            title = (first["content"][:30] if first else day) or day
            cur = conn.execute(
                "INSERT INTO chat_sessions (title, created_at, updated_at) VALUES (?, ?, ?)",
                (title, day, day),
            )
            conn.execute(
                """UPDATE chat_messages SET session_id=?
                   WHERE session_date=? AND session_id IS NULL
                     AND role IN ('user','assistant')
                     AND content NOT LIKE '☀%' AND content NOT LIKE '🌙%' AND content NOT LIKE '⏰%'""",
                (cur.lastrowid, day),
            )
        conn.commit()
    finally:
        conn.close()
