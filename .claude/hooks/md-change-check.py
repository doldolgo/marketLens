#!/usr/bin/env python3
"""docs/*.md·CLAUDE.md 편집 후 CLAUDE.md §6 검증 루프를 상기시키는 훅.

구현 코드(server/ 또는 web/)가 없으면 침묵한다 — 설계 단계엔 돌릴 검증이 없다.
"""
import json
import os
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

file_path = (data.get("tool_input") or {}).get("file_path", "")
root = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or ""
if not file_path.endswith(".md") or not root:
    sys.exit(0)

rel = os.path.relpath(file_path, root)
if not (rel.startswith("docs/specs/") or rel.startswith("docs/context/") or rel.startswith("docs/runbooks/") or rel == "CLAUDE.md"):
    sys.exit(0)
if not (os.path.isdir(os.path.join(root, "server")) or os.path.isdir(os.path.join(root, "web"))):
    sys.exit(0)

sys.stderr.write(
    f"[md-change-check] {rel} 변경됨 — CLAUDE.md §6 문서 변경 검증 루프를 돌리세요: "
    "server(ruff check . && pytest -q), web(npm run lint && npm run build). "
    "구현된 기능의 스펙이면 그 스펙 §4 검증도 다시 실행해 md↔코드 일치를 확인한 뒤 커밋합니다.\n"
)
sys.exit(2)
