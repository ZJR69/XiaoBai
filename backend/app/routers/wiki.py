"""知识库 wiki：浏览 + Git 化状态（已归档/未归档/有修改）"""
import hashlib
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_conn

router = APIRouter(prefix="/api/wiki", tags=["wiki"])

# backend/app/routers/wiki.py → storage/wiki
WIKI_DIR = Path(__file__).resolve().parents[3] / "storage" / "wiki"
EXCLUDE = {"schema.md"}  # 维护手册不参与归档状态


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_path(rel: str) -> Path:
    p = (WIKI_DIR / rel).resolve()
    if not p.is_relative_to(WIKI_DIR):
        raise HTTPException(400, "非法路径")
    return p


def _status(rel: str, current_hash: str) -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT archived_hash FROM wiki_pages WHERE path = ?", (rel,)).fetchone()
    if not row:
        return "untracked"  # 未归档
    return "clean" if row["archived_hash"] == current_hash else "modified"  # 已归档 / 有修改


@router.get("/pages")
def list_pages():
    pages = []
    with get_conn() as conn:
        for f in sorted(WIKI_DIR.rglob("*.md")):
            rel = f.relative_to(WIKI_DIR).as_posix()
            if rel in EXCLUDE:
                continue
            # 首个 # 标题作为显示名
            title = rel
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            h = _file_hash(f)
            pages.append({
                "path": rel,
                "title": title,
                "status": _status(rel, h),
                "size": f.stat().st_size,
            })
    return pages


@router.get("/page")
def read_page(path: str):
    p = _safe_path(path)
    if not p.is_file():
        raise HTTPException(404, "页面不存在")
    return {"path": path, "content": p.read_text(encoding="utf-8")}


class SavePageIn(BaseModel):
    path: str
    content: str


@router.put("/page")
def save_page(body: SavePageIn):
    """直接编辑保存（FR-2.4）：用户手动编辑记 log，不自动改内容之外的东西"""
    p = _safe_path(body.path)
    if not p.is_file():
        raise HTTPException(404, "页面不存在")
    p.write_text(body.content, encoding="utf-8")
    rel = p.relative_to(WIKI_DIR).as_posix()
    from app.routers.raw import _append_log
    _append_log(f"## [{datetime.now().date().isoformat()}] edit | {rel}（用户手动编辑）")
    return {"ok": True, "path": rel}


def collect_lint() -> dict:
    """体检逻辑（路由与每周调度共用，M5 记忆整理）。只报告，修复需用户确认。
    检查项：core.md 精炼度 / index 与实际文件一致性 / 孤儿页 / 重复实体 / 死链重现。"""
    issues = []

    # 1. core.md 精炼度（注意力预算，PRD 4.6）
    core = WIKI_DIR / "core.md"
    if core.is_file():
        lines = [l for l in core.read_text(encoding="utf-8").splitlines() if l.strip()]
        if len(lines) > 80:
            issues.append({"kind": "core_bloat", "detail": f"core.md 有 {len(lines)} 行非空内容，"
                         "超过 80 行会稀释注意力，建议精简（判据：删掉一条会不会导致具体错误，不会就删）"})

    # 2. index 与实际文件一致性 + 孤儿页
    idx = WIKI_DIR / "index.md"
    idx_text = idx.read_text(encoding="utf-8") if idx.exists() else ""
    actual = []
    for f in WIKI_DIR.rglob("*.md"):
        rel = f.relative_to(WIKI_DIR).as_posix()
        if rel in EXCLUDE or rel in ("index.md", "core.md", "log.md"):
            continue
        if rel.split("/")[0] in ("entities", "topics", "sources"):
            actual.append((rel, f))
    orphans = [(rel, f) for rel, f in actual if rel not in idx_text]
    if orphans:
        issues.append({"kind": "orphan_pages", "detail": "以下页面不在 index.md 目录里（搜索/跳转会漏掉它们）：\n"
                     + "\n".join(f"- {rel}" for rel, _ in orphans),
                       "paths": [rel for rel, _ in orphans]})

    # 3. 重复实体页（同标题不同 slug）
    titles = {}
    for rel, f in actual:
        if not rel.startswith("entities/"):
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                titles.setdefault(line[2:].strip(), []).append(rel)
                break
    for title, paths in titles.items():
        if len(paths) > 1:
            issues.append({"kind": "duplicate_entity", "detail": f"实体「{title}」有多个页面：{', '.join(paths)}，建议合并"})

    # 4. spaced_reviews 里指向已不存在的页
    with get_conn() as conn:
        rows = conn.execute("SELECT page FROM spaced_reviews").fetchall()
    dead = [r["page"] for r in rows if not (WIKI_DIR / r["page"]).is_file()]
    if dead:
        issues.append({"kind": "dead_reviews", "detail": "间隔重现队列里有已删除的页面：\n" + "\n".join(f"- {p}" for p in dead),
                       "paths": dead})

    return {"ok": len(issues) == 0, "issues": issues, "checked": len(actual)}


@router.get("/lint")
def lint():
    """Wiki 体检（M5 记忆整理，Auto Dream 式）：只报告，修复需用户确认。"""
    return collect_lint()


class FixIn(BaseModel):
    paths: list[str]


@router.post("/fix-index")
def fix_index(body: FixIn):
    """安全修复：把孤儿页补进 index.md（只动目录，不动页面内容）"""
    from app.routers.raw import _update_index, _append_log

    fixed = []
    for rel in body.paths:
        f = WIKI_DIR / rel
        if not f.is_file() or rel in EXCLUDE:
            continue
        title = rel
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        section = rel.split("/")[0] if "/" in rel else "topics"
        _update_index(rel, title, section=section)
        fixed.append(rel)
    if fixed:
        _append_log(f"## [{datetime.now().date().isoformat()}] lint | 补 index：{', '.join(fixed)}")
    return {"ok": True, "fixed": fixed}


class ArchiveIn(BaseModel):
    path: str


@router.post("/archive")
def archive(body: ArchiveIn):
    """归档动作：记录当前哈希（工作流式，用户说整理/点归档时触发）"""
    p = _safe_path(body.path)
    if not p.is_file():
        raise HTTPException(404, "页面不存在")
    rel = p.relative_to(WIKI_DIR).as_posix()
    h = _file_hash(p)
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO wiki_pages (path, archived_hash, archived_at) VALUES (?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET archived_hash = excluded.archived_hash, archived_at = excluded.archived_at""",
            (rel, h, now),
        )
    return {"ok": True, "path": rel, "status": "clean"}
