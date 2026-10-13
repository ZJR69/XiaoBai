"""保存筛选（Vikunja saved filters 借鉴）：对话出结构化 JSON 定义 → 用户裁决入库 → 工作台页签一点即滤。
后端只做 CRUD + 白名单校验；求值在前端本地（evalFilter），零查询引擎。"""
import json
import re
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_conn

router = APIRouter(prefix="/api/filters", tags=["filters"])

# v1 契约（克制）：一层条件不嵌套。all 必填（&&），any 可选（||，与 all 取并集）
FILTER_FIELDS = ("done", "status", "priority", "due_at", "start_at", "project_id", "created_at")
FILTER_OPS = ("=", "!=", ">", ">=", "<", "<=", "in")
# 相对日期值：now / now±N(d|w) / now/d（取整当天 0 点）
REL_DATE_RE = re.compile(r"^now([+-]\d+[dw])?(/d)?$")


def validate_filter(filter_json: str) -> None:
    """筛选定义白名单校验（LLM 可能幻觉字段/运算符，非法一律 400 并列出原因）。"""
    try:
        spec = json.loads(filter_json)
    except (TypeError, ValueError):
        raise HTTPException(400, "filter 应为 JSON 字符串，如 {\"all\":[{\"field\":\"due_at\",\"op\":\"<\",\"value\":\"now+7d\"}]}")
    if not isinstance(spec, dict) or not isinstance(spec.get("all"), list) or not spec["all"]:
        raise HTTPException(400, "filter.all 必填且为非空数组（&& 语义）；可选 any 数组（|| 语义）")
    errors = []
    for group in ("all", "any"):
        conds = spec.get(group)
        if conds is None:
            continue
        if not isinstance(conds, list):
            errors.append(f"{group} 应为数组")
            continue
        for i, c in enumerate(conds):
            if not isinstance(c, dict):
                errors.append(f"{group}[{i}] 应为对象")
                continue
            f, op, val = c.get("field"), c.get("op"), c.get("value")
            if f not in FILTER_FIELDS:
                errors.append(f"{group}[{i}].field「{f}」不在白名单 {FILTER_FIELDS}")
            if op not in FILTER_OPS:
                errors.append(f"{group}[{i}].op「{op}」不在白名单 {FILTER_OPS}")
            if val is None:
                errors.append(f"{group}[{i}] 缺 value")
            # 日期类字段的值：合法日期串 / 相对日期表达式（= != > >= < <= 时）
            if f in ("due_at", "start_at", "created_at") and isinstance(val, str) and op != "in":
                if REL_DATE_RE.match(val):
                    pass
                else:
                    try:
                        datetime.fromisoformat(val[:10])
                    except ValueError:
                        errors.append(f"{group}[{i}].value「{val}」不是合法日期或相对日期（now/now±Nd/now±Nw/now/d）")
            # in 的值应为数组
            if op == "in" and not isinstance(val, list):
                errors.append(f"{group}[{i}] op=in 时 value 应为数组")
    if errors:
        raise HTTPException(400, "筛选定义不合法：" + "；".join(errors))


class FilterIn(BaseModel):
    title: str
    filter: str  # filter_json（字符串形式，对齐操作卡 patch 的扁平字段习惯）


@router.get("")
def list_filters():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM saved_filters ORDER BY COALESCE(position, 9999), id"
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("", status_code=201)
def create_filter(f: FilterIn):
    title = f.title.strip()
    if not title:
        raise HTTPException(400, "title 必填")
    validate_filter(f.filter)
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM saved_filters WHERE title = ?", (title,)).fetchone():
            raise HTTPException(400, f"已存在同名筛选「{title}」")
        nxt = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM saved_filters").fetchone()[0]
        cur = conn.execute(
            "INSERT INTO saved_filters (title, filter_json, position, created_at) VALUES (?, ?, ?, ?)",
            (title, f.filter.strip(), nxt, datetime.now().isoformat(timespec="seconds")),
        )
        return {"id": cur.lastrowid, "title": title}


@router.delete("/{filter_id}")
def delete_filter(filter_id: int):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM saved_filters WHERE id = ?", (filter_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "筛选不存在")
    return {"ok": True}
