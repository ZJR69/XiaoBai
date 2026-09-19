"""数据库层：SQLite 连接与建表（表结构定义见 docs/小白PRD.md 第 6 节）"""
import sqlite3
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
  date TEXT NOT NULL,              -- YYYY-MM-DD（唯一定位维度，一次性）
  start_time TEXT,
  end_time TEXT,
  location TEXT,
  note TEXT,
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
CREATE TABLE IF NOT EXISTS wiki_pages (
  path TEXT PRIMARY KEY,
  archived_hash TEXT,
  archived_at TEXT
);

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
        # 索引须在列就绪后建（旧库 executescript 时列尚未存在，放 DDL 会崩）
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id)")
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
