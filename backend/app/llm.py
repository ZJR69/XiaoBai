"""DeepSeek LLM 客户端（key 从环境变量或 backend/.env 读取，不入库）"""
import json
import os
from pathlib import Path

# 轻量 .env 解析（避免额外依赖）：backend/.env
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def _load_dotenv() -> None:
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")


def llm_available() -> bool:
    return bool(API_KEY)


def chat_completion(messages: list[dict], temperature: float = 0.7) -> str:
    """调用 DeepSeek chat API，返回助手回复文本。"""
    import httpx  # 延迟导入，无 key 场景不依赖网络栈

    resp = httpx.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={"model": MODEL, "messages": messages, "temperature": temperature},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def extract_json(text: str) -> dict | list | None:
    """从 LLM 回复中提取 JSON（容忍 ```json 包裹）"""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
