"""文件资产层：投放区（dropzone）扫描 + 消化引擎 Ingest。

设计依据 PRD 4.3 节（2026-09-18 定案）：
- 独立投放区：用户丢文件进来 = 明确表达消化意图；目录外一概不碰
- 文件级状态标注：未归档（untracked）/ 已归档（digested）/ 有修改（modified）
- 消化 = Karpathy 模式：提取文本 → LLM 摘要 → 写 wiki sources 页 → 更新 index/log
- 防污染铁律：digest 接口只由用户确认后调用（前端按钮/对话提案触发）
"""
import hashlib
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import llm
from app.database import get_conn

router = APIRouter(prefix="/api/raw", tags=["raw"])

# backend/app/routers/raw.py → storage/raw/dropzone
DROPZONE = Path(__file__).resolve().parents[3] / "storage" / "raw" / "dropzone"

SUPPORTED = {
    ".md": "markdown", ".markdown": "markdown", ".txt": "text",
    ".docx": "docx", ".pdf": "pdf",
}


def _file_hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _extract_text(p: Path) -> str:
    """提取文本：md/txt 直读；docx/pdf 用库提取；未知格式返回空（仅登记）"""
    kind = SUPPORTED.get(p.suffix.lower())
    if kind in ("markdown", "text"):
        return p.read_text(encoding="utf-8", errors="replace")
    if kind == "docx":
        import docx
        return "\n".join(para.text for para in docx.Document(str(p)).paragraphs if para.text.strip())
    if kind == "pdf":
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(p)).pages)
    return ""


def _safe_path(rel: str) -> Path:
    p = (DROPZONE / rel).resolve()
    if not p.is_relative_to(DROPZONE):
        raise HTTPException(400, "非法路径")
    return p


# ── 扫描 ──

@router.get("/scan")
def scan():
    """全量扫描投放区，返回文件列表 + 状态（untracked/digested/modified）。"""
    DROPZONE.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        known = {r["path"]: dict(r) for r in conn.execute("SELECT * FROM raw_files").fetchall()}

    files = []
    now = datetime.now().isoformat(timespec="seconds")
    for f in sorted(DROPZONE.rglob("*")):
        if f.is_dir() or f.name.startswith("~$") or f.name.startswith("."):
            continue
        rel = f.relative_to(DROPZONE).as_posix()
        h = _file_hash(f)
        rec = known.get(rel)
        if not rec:  # 新文件
            with get_conn() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO raw_files (path, size, status, first_seen_at) VALUES (?, ?, 'untracked', ?)",
                    (rel, f.stat().st_size, now),
                )
            status = "untracked"
        elif rec["status"] == "digested" and rec["archived_hash"] != h:
            status = "modified"
        else:
            status = rec["status"]
        files.append({
            "path": rel,
            "name": f.name,
            "size": f.stat().st_size,
            "status": status,
            "supported": f.suffix.lower() in SUPPORTED,
            "digested_at": rec["digested_at"] if rec else None,
            "source_page": rec["source_page"] if rec else None,
        })
    return {"dropzone": str(DROPZONE), "files": files}


# ── 消化引擎 ──

class DigestIn(BaseModel):
    paths: list[str]  # 用户勾选确认的文件（相对路径）


INGEST_PROMPT = """你是小白的消化引擎（Karpathy wiki 模式）。把以下资料消化成一篇 wiki 摘要页。

要求：
- 输出 markdown，第一行是 "# <合适的页标题>"
- frontmatter 之后正文结构：核心要点（3-6 条）、关键概念、与用户知识体系的可能关联
- 提炼而非复述：这份资料里值得长期记住的东西
- 如果内容是课程类临时资料，直接输出"SKIP：课程类临时资料，仅索引不消化"
- 中文输出

资料（文件名：{filename}）：
{text}"""


@router.post("/digest")
def digest(body: DigestIn):
    """消化选定文件：提取文本 → LLM 摘要 → wiki/sources/ 页 → 更新 index/log/登记表。
    防污染：本接口只应被用户确认动作触发。"""
    if not llm.llm_available():
        raise HTTPException(503, "未配置 DEEPSEEK_API_KEY，消化引擎不可用")
    results = []
    now = datetime.now().isoformat(timespec="seconds")
    today = now[:10]

    for rel in body.paths:
        p = _safe_path(rel)
        if not p.is_file():
            results.append({"path": rel, "ok": False, "error": "文件不存在"})
            continue
        h = _file_hash(p)

        # 提取文本（未知格式仅登记）
        try:
            text = _extract_text(p)
        except Exception as e:
            results.append({"path": rel, "ok": False, "error": f"文本提取失败：{e}"})
            continue
        if not text.strip():
            _register(rel, p, h, None, now)
            results.append({"path": rel, "ok": True, "note": "不可读格式，仅登记位置"})
            continue

        # LLM 摘要（超长截断，成本控制）
        try:
            summary = llm.chat_completion(
                [{"role": "user", "content": INGEST_PROMPT.format(filename=p.name, text=text[:60000])}],
                temperature=0.3,
            )
        except Exception as e:
            results.append({"path": rel, "ok": False, "error": f"LLM 消化失败：{e}"})
            continue

        if summary.strip().startswith("SKIP"):
            _register(rel, p, h, None, now)
            results.append({"path": rel, "ok": True, "note": "课程类资料，仅索引"})
            continue

        # 写 wiki sources 页
        slug = re.sub(r'[\\/:*?"<>|\s]+', "-", p.stem)[:60]
        page_rel = f"sources/{slug}.md"
        page = DROPZONE.parent.parent / "wiki" / page_rel
        page.parent.mkdir(parents=True, exist_ok=True)
        front = (
            f"---\ntype: source\ncreated: {today}\nupdated: {today}\n"
            f"source: raw/dropzone/{rel}\n---\n\n"
        )
        page.write_text(front + summary.strip() + "\n", encoding="utf-8")

        # 更新 index / log
        _update_index(page_rel, p.name)
        _append_log(f"## [{today}] ingest | {p.name}")

        _register(rel, p, h, page_rel, now)
        results.append({"path": rel, "ok": True, "source_page": page_rel})

    return {"results": results}


def _register(rel: str, p: Path, h: str, page: str | None, now: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO raw_files (path, size, archived_hash, status, source_page, first_seen_at, digested_at)
               VALUES (?, ?, ?, 'digested', ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                 size = excluded.size, archived_hash = excluded.archived_hash,
                 status = 'digested', source_page = excluded.source_page, digested_at = excluded.digested_at""",
            (rel, p.stat().st_size, h, page, now, now),
        )


INDEX_SECTIONS = {
    "sources": "## sources（原始资料摘要）",
    "entities": "## entities（实体：人物/组织/课程/项目）",
    "topics": "## topics（主题/概念）",
}


def _update_index(page_rel: str, name: str, section: str = "sources"):
    """index.md 指定节追加条目（若不存在）。节缺失时在文件末尾补建该节。"""
    idx = DROPZONE.parent.parent / "wiki" / "index.md"
    if not idx.exists():
        return
    content = idx.read_text(encoding="utf-8")
    if page_rel in content:
        return
    heading = INDEX_SECTIONS.get(section)
    if not heading:
        return
    if heading not in content:
        # 节缺失：末尾补建（不静默丢条目）
        content = content.rstrip() + f"\n\n{heading}\n- [{name}]({page_rel})\n"
    else:
        # 清掉「（暂无）」占位
        content = content.replace(f"{heading}\n（暂无）", heading)
        content = content.replace(f"{heading}\n", f"{heading}\n- [{name}]({page_rel})\n", 1)
    idx.write_text(content, encoding="utf-8")


def _append_log(line: str):
    log = DROPZONE.parent.parent / "wiki" / "log.md"
    with log.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
