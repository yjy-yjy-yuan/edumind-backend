"""M4 前端联调约束测试：前端仅通过 EduMind 后端调用。"""

from __future__ import annotations

import re
from pathlib import Path


def test_frontend_api_does_not_call_vinci_directly():
    frontend_api_dir = Path("/Users/yuan/final-work/EduMind/mobile-frontend/src/api")
    assert frontend_api_dir.exists(), "前端 API 目录不存在，无法执行联调约束校验"

    suspicious = []
    request_call_url_re = re.compile(r"request\s*\(\s*\{[\s\S]*?url\s*:\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
    fetch_url_re = re.compile(r"fetch\s*\(\s*['\"]([^'\"]+)['\"]")

    for js_file in frontend_api_dir.glob("*.js"):
        text = js_file.read_text(encoding="utf-8")
        for token in request_call_url_re.findall(text):
            normalized = token.strip().lower()
            if "vinci" in normalized:
                suspicious.append((js_file.name, f"request.url 直连 Vinci: {token}"))
            elif token.startswith("http://") or token.startswith("https://"):
                suspicious.append((js_file.name, f"request.url 使用绝对地址: {token}"))
            elif not token.startswith("/api/"):
                suspicious.append((js_file.name, f"request.url 非后端代理路径: {token}"))

        for token in fetch_url_re.findall(text):
            normalized = token.strip().lower()
            if "vinci" in normalized:
                suspicious.append((js_file.name, f"fetch 直连 Vinci: {token}"))
            elif token.startswith("http://") or token.startswith("https://"):
                suspicious.append((js_file.name, f"fetch 使用绝对地址: {token}"))
            elif not token.startswith("/api/"):
                suspicious.append((js_file.name, f"fetch 非后端代理路径: {token}"))

    assert not suspicious, f"发现前端直连非后端地址: {suspicious}"
