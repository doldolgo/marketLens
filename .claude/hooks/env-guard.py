#!/usr/bin/env python3
"""Bash 명령이 .env 파일을 건드리면 차단하는 훅 (CLAUDE.md §5).

.env.example 은 허용. 값이 필요한 건 앱 런타임이지 세션이 아니다.
"""
import json
import re
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

cmd = (data.get("tool_input") or {}).get("command", "")
tokens = re.findall(r"\S+", cmd)
bad = [
    t for t in tokens
    if re.search(r"(^|/)\.env(\.\w+)?[\"']?$", t) and ".env.example" not in t
]
if bad:
    sys.stderr.write(
        f"차단: {' '.join(sorted(set(bad)))} — .env 는 읽기·쓰기·출력 전부 금지 (CLAUDE.md §5). "
        "키 목록은 server/.env.example 이 진실이고, 값 관리는 사람이 한다.\n"
    )
    sys.exit(2)
sys.exit(0)
