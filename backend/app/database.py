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
  created_at TEXT
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
        conn.commit()
    finally:
        conn.close()
