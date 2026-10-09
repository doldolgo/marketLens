"""개인정보 처리방침 계약 — nginx 위치 셋·privacy.html·링크·sitemap 을 파일로 읽어 단언한다 (스펙 032 §4·037 §4·051 §4·060 §4).

머지가 곧 게시다. 사람이 채울 자리표시자(`〔`)가 남았거나, 페이지가 외부 자원·서버 호출을 부르게 되거나, 033 과 함께 쓰는
동의 계약(`kt.analytics` — 동의한 방문자만 분석, 기본 꺼짐)과 방침의 필수 안내가 빠지면 여기서 멈춘다. 개정(037)은 날짜 상수
(`PRIVACY_V2_EFFECTIVE` — 접속 기록 쓰임새, v3 은 `PRIVACY_V3_EFFECTIVE` — 051, v4 는 `PRIVACY_V4_EFFECTIVE` — 060)·페이지의
`<time>` 넷·sitemap·이력과, 바뀐 문장 대조표를 두 판(지금 판·바로 앞 판 사본)의 본문과 맞대 본다. v3(051)·v4(060)는 동의 안내
판을 올렸다 — 동의 창(033 clarity.js)과 방침 동의 상자는 같은 글이다. 판 값은 같은지만 본다(`NOTICE_DIGESTS` 의 마지막 키).
실제로 브라우저에서 CSP·버튼을 보는 검증은 스펙 §5 의 로컬 Docker·브라우저 명령이다.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app.core.config import (
    PRIVACY_V2_EFFECTIVE,
    PRIVACY_V3_EFFECTIVE,
    PRIVACY_V4_EFFECTIVE,
)
from tests.test_deploy import (
    PUBLIC_API,
    ROOT,
    _args,
    _locations,
    _public_server,
    _route,
)

PUBLIC = ROOT / "web/public"
CANONICAL = "https://kimptrack.com/privacy"
EFFECTIVE = "2026-10-09"  # 지금 판의 시행일 — 페이지의 <time> 넷과 sitemap lastmod 가 같다 (037 §3.1·051 §3.1·060 §3.3)
# 052 — 같은 출처 스크립트 /attention.js 하나('self')와 그 비콘(connect-src 'self'). 세 정적 페이지가 같은 문자열이다.
# 053 — 틀에는 자기 출처와 관리자 화면(덮어 보기)만
CSP = (
    "default-src 'none'; connect-src 'self'; img-src 'self'; style-src 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'self' https://admin.kimptrack.com"
)
# §3.4 — 법 제30조 제1항·시행령 제31조 제1항 순서. 해당 없는 절(민감정보·가명정보 등)은 두지 않는다
SECTIONS = [
    "1. 개인정보의 처리 목적",
    "2. 처리하는 개인정보의 항목과 보유 기간",
    "3. 개인정보의 파기",
    "4. 개인정보의 제3자 제공",
    "5. 개인정보 처리의 위탁",
    "6. 개인정보의 국외 이전",
    "7. 자동으로 수집하는 장치의 설치·운영과 거부",
    "8. 정보주체의 권리와 행사 방법",
    "9. 개인정보의 안전성 확보 조치",
    "10. 개인정보 보호책임자",
    "11. 권익침해 구제 방법",
    "12. 개인정보 처리방침의 변경",
]
# 자원을 부르는 <link rel> — canonical 과 <a href> 는 주소일 뿐 요청을 만들지 않는다
RESOURCE_RELS = {
    "stylesheet",
    "preload",
    "modulepreload",
    "preconnect",
    "dns-prefetch",
    "icon",
}
# 인라인 스크립트 본문에 없어야 하는 요청 수단 — 본문 글자는 보지 않는다(요청 자체는 CSP 가 막는다)
NO_CALLS = ("fetch(", "new WebSocket(", "XMLHttpRequest", "sendBeacon", "clarity.ms")
# §3.3·§3.4 — 033 과 함께 쓰는 저장값, 동의·철회 안내, Clarity 약관이 요구하는 링크, 구제 기관 넷
MUST_SAY = (
    "kt.analytics",
    "denied",
    "granted",
    "globalPrivacyControl",
    "_clck",
    "_clsk",
    "_cltk",
    "Safari",
    "7일",
    "운영 알림",  # §3.4-6 — 알림에 방문자 정보가 가지 않는다(025 제안·034 가 이 문장에 기댄다)
    'href="https://privacy.microsoft.com/ko-kr/privacystatement"',
    'href="https://optout.aboutads.info/"',
    "개인정보분쟁조정위원회 1833-6972",
    "개인정보침해신고센터 118",
    "대검찰청 1301",
    "경찰청 182",
    "아무것도 고르지 않으면 분석하지 않습니다",  # §3.3 — 값이 없으면 꺼짐(동의 방식)
    '<section id="consent"',  # 033 띠의 '처리방침에서 자세히 보기'·거부 칸 링크가 /privacy#consent 로 온다
    # §3.3 — 033 과 같은 다른 탭 반영 문장
    "철회하면 그 탭이 한 번 새로고침되어 분석을 멈추고, 동의하면 그 탭에서도 분석을 시작합니다",
    "kt.analytics.v",  # §3.3 — 동의한 안내의 판
    "나이를 확인하지 않",  # §3.4-2 — 만 14세 미만(사람 확인 §7)
)
# 동의 방식(사람 결정 2026-10-01)과 맞지 않는 문장 — 페이지에 있으면 거짓이다
# 파기는 날수 기한을 약속하지 않는다(§3.5 — '90일 뒤 지웁니다' 같은 단정 없음), Microsoft 의 자기 목적 이용은
# 어디서나 '씁니다' 로 단정한다(§3.4-2 — '쓸 수 있어' 로 흐리지 않는다). 버튼 하나로 받던 동의([동의]·[허용])는 없다
MUST_NOT_SAY = (
    "허용으로 봅니다",
    "다시 허용",
    "유럽 시간대",
    "5일 안에",
    "90일 뒤 지웁니다",
    "쓸 수 있어",
    "[동의]",
    "[허용]",
)
# §3.3 — 033 의 띠와 같은 문장(머리·셋 모두 규칙·나이). 동의 상자 안 세 칸보다 앞에 있다. 051 §3.3-4 — 머리 질문에
# KimpTrack 서버의 통계(모든 페이지), 셋 모두 규칙에 'Clarity와 KimpTrack 영역 통계가 돕니다'. 060 §3.3-4 — 그 통계를 화면의
# 영역과 요소·마우스를 올려 둔 시간·떠날 때 보던 곳으로
SHARED_SENTENCES = (
    "랜딩과 대시보드의 화면 이용 기록(클릭·스크롤 등)을 Microsoft Clarity(미국)로 보내 서비스 개선에 써도 될까요? "
    "Microsoft는 이 기록을 광고 등 자기 목적에도 씁니다. 또 KimpTrack 서버가 모든 페이지(랜딩·대시보드·처리방침·검색어 페이지)에서 "
    "화면의 영역과 요소(단추·링크 같은 것)별로 보인 시간·클릭 수·마우스를 올려 둔 시간과 떠날 때 보던 곳을 하루 합계로 모아도 될까요? "
    "동의하지 않아도 모든 기능을 그대로 씁니다.",
    "세 가지에 모두 동의하고 [선택한 대로 저장]을 누른 경우에만 Clarity와 KimpTrack 영역 통계가 돕니다 — Clarity는 셋이 다 "
    "있어야 돌아가므로 하나라도 빠지면 거부로 저장합니다.",
    "만 14세 미만은 동의하지 마세요.",
)
# 060 §3.3-9 — 바로 앞 판(2026-10-09 먼저 게시한 판, 안내 판 2026-10-09)의 동의 상자에서 바뀐 문장. 사본은 동의 절에 세 칸의
# 알릴 사항만 남겨(037 §3.3) 이 문장들이 없다 — 대조표의 '동의 관리' 행 '이전 판' 칸을 여기와 맞춘다(얼려 둔 글자)
PREVIOUS_CONSENT_TEXT = (
    "또 KimpTrack 서버가 모든 페이지(랜딩·대시보드·처리방침·검색어 페이지)에서 화면 영역별로 보인 시간·클릭 수를 하루 합계로 "
    "모아도 될까요?",
    "지금 안내의 판은 2026-10-09입니다.",
)
# 051 §3.3-4·060 §3.3-4 — '수집·이용' 칸 끝 한 줄(화면 이용 통계는 Microsoft 에 가지 않고 국내에) — 033 의 동의 창에도 같은 글
COLLECT_NOTE = "화면 이용 통계는 Microsoft에 가지 않고 국내(AWS 서울 리전)의 KimpTrack 서버에 둡니다."
# 060 §3.3-3 — 수집·이용 칸 항목 끝·2절이 함께 적는 KimpTrack 서버의 새 항목(요소·떠날 때 보던 곳·깊이)
ELEMENT_ITEMS = (
    "영역 안의 요소(단추·링크·탭·입력칸·표의 코인 행 같은 것 — 입력한 글자·화면의 숫자는 빼고)별 클릭 수·마우스를 올려 둔 "
    "시간·화면에 보인 시간, 페이지를 떠날 때 보던 영역과 그 페이지에서 내려온 깊이"
)
# §3.3 — 철회 뒤 이미 보낸 기록을 바로 지우는 길(법 제37조 제3항, 절차는 033 런북)과 그 한계. 상자 아래 문단·3절에 따로
INQUIRY = '<a class="link" href="https://go.microsoft.com/fwlink/?linkid=2126612">개인정보 문의</a>'
DELETION = "운영자가 Clarity 프로젝트의 기록을 통째로 지우고(모든 방문자의 기록이 함께 지워집니다) 새 프로젝트로 다시 시작합니다"
MICROSOFT_KEEPS = (
    "Microsoft가 자기 목적에 쓰는 정보는 이것으로 지워지지 않으며 Microsoft에 직접 요청할 수 있습니다"
    f"({INQUIRY})"
)
# §3.3 — 상태 줄의 다섯 글자(스크립트가 그린다)
STATES = ("정하지 않음", "동의함", "거부함", "GPC로 거부", "저장할 수 없음")
# §3.3 — 동의 세 칸(법 제22조 제1항 — 나눠 각각 받는다): 체크 상자 id, 체크 글자, 그 동의의 알릴 사항
# (수집·이용 제15조 제2항 · 제3자 제공 제17조 제2항 · 국외 이전 제28조의8 제2항 — 033 의 띠와 같은 글자),
# 눈에 띄게 적는 중요한 내용(제22조 제2항·시행령 제17조 제3항 — 보유 기간, 받는 자와 그 목적·보유 기간)
CONSENT_CELLS = (
    (
        "an-collect",
        "(선택) 화면 이용 기록의 수집·이용에 동의합니다",
        {"항목", "목적", "보유 기간", "거부 권리·불이익"},
        {"보유 기간"},
    ),
    (
        "an-provide",
        "(선택) 화면 이용 기록을 Microsoft에 제공하는 데 동의합니다",
        {
            "받는 자",
            "받는 자의 목적",
            "항목",
            "받는 자의 보유 기간",
            "거부 권리·불이익",
        },
        {"받는 자", "받는 자의 목적", "받는 자의 보유 기간"},
    ),
    (
        "an-transfer",
        "(선택) 화면 이용 기록을 미국으로 이전하는 데 동의합니다",
        {
            "받는 자·연락처",
            "국가·시기·방법",
            "항목",
            "목적·보유 기간",
            "거부 방법·절차·효과",
        },
        {"받는 자·연락처", "목적·보유 기간"},
    ),
)
BOXES = [cell[0] for cell in CONSENT_CELLS]
# §3.3 — 세 칸의 항목 칸이 2절 없이도 다 읽히는지 보는 Clarity 항목들
CLARITY_ITEMS = (
    "페이지 주소",
    "이전 페이지 주소",
    "누른 링크의 주소",
    "클릭·스크롤·마우스 움직임·화면 크기",
    "화면 내용",
    "고른 코인 이름",
    "기기·브라우저·운영체제",
    "IP 주소",
    "_clck",
    "_clsk",
    "_cltk",
)
# §3.4-4 — 제17조 제2항 다섯 가지(동의 상자의 제공 칸과 같은 이름) + 근거
PROVISION_TERMS = CONSENT_CELLS[1][2] | {"근거"}
# §3.4-7 — 작성지침의 행태정보 항목
BEHAVIOR_TERMS = {
    "수집 항목",
    "수집 방법",
    "목적",
    "보유 기간",
    "수집하는 사업자",
    "거부 방법",
}
# §3.3 — 동의 관리 스크립트를 node 로 돌리는 가짜 브라우저. 표준 입력의 [스크립트, 경우들] 을 받아 경우마다 새로 돌리고,
# 칸 체크·버튼 클릭·다른 탭의 storage 이벤트(키 하나·저장소 비우기) 뒤의 저장값·판·쿠키·상태 글자·보이는·체크된·막힌 요소를 JSON 으로 낸다.
# 라이브러리 없음.
HARNESS = r"""
const [script, cases] = JSON.parse(require('fs').readFileSync(0, 'utf8'))
const KEY = 'kt.analytics'
const VKEY = 'kt.analytics.v'
const out = cases.map((c) => {
  const data = new Map()
  if (c.stored !== undefined) data.set(KEY, c.stored)
  if (c.v !== undefined) data.set(VKEY, c.v)
  const fail = new Set(c.fail || [])
  const localStorage = {
    getItem: (k) => { if (fail.has('get')) throw new Error('get'); return data.has(k) ? data.get(k) : null },
    setItem: (k, v) => {
      if (fail.has('set') || fail.has('set:' + k)) throw new Error('QuotaExceededError')
      data.set(k, String(v))
    },
    removeItem: (k) => { if (fail.has('remove')) throw new Error('remove'); data.delete(k) },
  }
  const els = {}
  const el = (id) => (els[id] = els[id] || {
    hidden: c.hidden.includes(id), disabled: c.disabled.includes(id), checked: false, textContent: '', on: {},
    addEventListener(type, fn) { this.on[type] = fn },
  })
  const jar = new Set(c.cookies || [])
  const writes = []
  const document = {
    getElementById: el,
    get cookie() { return [...jar].map((n) => n + '=1').join('; ') },
    set cookie(line) {
      writes.push(line)
      const name = line.split('=')[0].trim()
      if (/;\s*max-age=0\s*(;|$)/i.test(line)) jar.delete(name); else jar.add(name)
    },
  }
  const listeners = {}
  const window = { localStorage, addEventListener: (type, fn) => { listeners[type] = fn } }
  const navigator = c.gpc ? { globalPrivacyControl: true } : {}
  new Function('window', 'document', 'navigator', 'location', script)(
    window, document, navigator, { hostname: 'kimptrack.com' })
  for (const step of c.steps || []) {
    const at = step.indexOf(':')
    const kind = step.slice(0, at), arg = step.slice(at + 1)
    if (kind === 'click') el(arg).on.click()
    else if (kind === 'check') el(arg).checked = true
    else if (kind === 'uncheck') el(arg).checked = false
    else if (kind === 'clear') { data.clear(); listeners.storage({ key: null }) } // 다른 탭이 저장소를 비움
    else { const [k, v] = arg.split('='); data.set(k, v); listeners.storage({ key: k }) } // 'other:<키>=<값>'
  }
  const ids = (pick) => Object.keys(els).filter((id) => pick(els[id])).sort()
  return {
    stored: data.has(KEY) ? data.get(KEY) : null, v: data.has(VKEY) ? data.get(VKEY) : null,
    cookies: [...jar].sort(), writes, state: el('an-value').textContent,
    shown: ids((e) => !e.hidden), checked: ids((e) => e.checked), disabled: ids((e) => e.disabled),
  }
})
process.stdout.write(JSON.stringify(out))
"""


def _notice_version() -> str:
    """동의 관리 스크립트의 지금 판 — 033 clarity.js 의 판과 같아야 한다 (§3.3)."""
    html = (PUBLIC / "privacy.html").read_text("utf-8")
    found = re.search(r"const VERSION = '([^']*)'", html)
    return found.group(1) if found else ""


NOTICE = _notice_version()
# §3.3 — 판마다 세 칸의 글자(체크 글자와 알릴 사항·칸 끝 줄, 태그 뗀)의 sha256. 글자를 바꾸면 멈춘다 — 받는 자·항목·목적·보유
# 기간이 바뀌었으면 판을 올리고(스크립트·보이는 판 글자·033 clarity.js·052 attention.js) 새 판을 끝에 더한다(예전 판은 지우지
# 않는다 — 이전 판 사본이 그 판의 값과 맞댄다). 지금 판은 마지막 키다 — 판 값은 같은지만 보고 날짜와 맞대지 않는다(060 §3.2 —
# 같은 날 두 번째 판은 '2026-10-09-2'). 문장만 다듬었으면 지금 판의 값만 고치고 PR 본문에 이유를 적는다
NOTICE_DIGESTS = {
    "2026-10-01": "7ee34255a0120818e76ffc210a12eb5cd556773eb6b06462a62cebb4a4bc6bc1",
    "2026-10-09": "a32da2cb07e8dd922855849ed03198d918a7c36891b7339a65d830941705cb9b",  # 051 — 화면 영역 이용 통계(KimpTrack 서버)
    "2026-10-09-2": "738a2acbd578635e944e5c2e040911c8621616c5852b2c7b3a60a65b3ec7ede5",  # 060 — 화면 요소·떠날 때 보던 곳·깊이
}
# 바로 앞 판 — 이 판의 동의로는 켜지 않고 다시 묻는다 (060 §3.6 — 2026-10-09 의 granted 는 '정하지 않음'). 판 값의 글자 순이
# 아니라 NOTICE_DIGESTS 에 더한 차례로 고른다
_VERSIONS = list(NOTICE_DIGESTS)
OLD = (
    _VERSIONS[_VERSIONS.index(NOTICE) - 1] if NOTICE in _VERSIONS[1:] else "2026-09-01"
)
# 스크립트가 숨기고 보이는 상태 요소. 버튼 줄 안의 [모두 거부]는 an-actions 가 보일 때만 센다
STATUS_IDS = {"an-state", "an-actions", "an-gpc", "an-nostore", "an-stuck"}
# 버튼 줄은 늘 [선택한 대로 저장]·[모두 거부], 동의함이면 세 칸 앞 상태 줄 옆에 [동의 철회]도
UNDECIDED = {"an-state", "an-actions", "an-deny"}
AGREED = UNDECIDED | {"an-withdraw"}
ALL = [f"check:{box}" for box in BOXES]
# (이름, 경우, 뒤의 저장값, 뒤의 판, 상태 글자, 보이는 상태 요소, 쿠키를 지웠나) — 경우: stored·v 처음 값(없으면 키 없음),
# gpc, fail(get·set·remove 예외, set:<키> 는 그 키만), steps(check:·uncheck:<칸 id> · click:<버튼 id> · other:<키>=<다른 탭이 쓴 값>
# · clear: 다른 탭이 저장소를 비움)
# fmt: off
CONSENT_CASES = [
    ("값 없음", {}, None, None, "정하지 않음", UNDECIDED, False),
    ("지금 판의 동의", {"stored": "granted", "v": NOTICE}, "granted", NOTICE, "동의함", AGREED, False),
    ("예전 판의 동의 — 다시 묻는다", {"stored": "granted", "v": OLD}, "granted", OLD, "정하지 않음", UNDECIDED, False),
    ("판 없는 동의", {"stored": "granted"}, "granted", None, "정하지 않음", UNDECIDED, False),
    ("거부", {"stored": "denied"}, "denied", None, "거부함", UNDECIDED, False),
    ("그 밖의 값", {"stored": "off"}, "off", None, "정하지 않음", UNDECIDED, False),
    ("GPC 가 동의보다 앞선다", {"stored": "granted", "v": NOTICE, "gpc": True}, "granted", NOTICE, "GPC로 거부", {"an-state", "an-gpc"}, False),
    ("읽기 예외", {"fail": ["get"]}, None, None, "저장할 수 없음", {"an-state", "an-nostore"}, False),
    ("세 칸 모두 체크하고 저장", {"steps": [*ALL, "click:an-save"]}, "granted", NOTICE, "동의함", AGREED, False),
    ("한 칸 빠지면 거부", {"steps": [*ALL[:2], "click:an-save"]}, "denied", None, "거부함", UNDECIDED, True),
    ("칸 없이 저장", {"steps": ["click:an-save"]}, "denied", None, "거부함", UNDECIDED, True),
    ("[모두 거부]", {"steps": [*ALL, "click:an-deny"]}, "denied", None, "거부함", UNDECIDED, True),
    ("거부 뒤 세 칸 동의", {"stored": "denied", "steps": [*ALL, "click:an-save"]}, "granted", NOTICE, "동의함", AGREED, False),
    ("예전 판 동의를 지금 판으로", {"stored": "granted", "v": OLD, "steps": [*ALL, "click:an-save"]}, "granted", NOTICE, "동의함", AGREED, False),
    ("[동의 철회]", {"stored": "granted", "v": NOTICE, "steps": ["click:an-withdraw"]}, "denied", None, "거부함", UNDECIDED, True),
    ("동의한 칸 하나를 빼고 저장 — 철회", {"stored": "granted", "v": NOTICE, "steps": ["uncheck:an-transfer", "click:an-save"]}, "denied", None, "거부함", UNDECIDED, True),
    ("쓰기 예외의 저장 — 실제 값 그대로", {"fail": ["set"], "steps": [*ALL, "click:an-save"]}, None, None, "정하지 않음", UNDECIDED, False),
    ("판을 못 쓰면 동의도 쓰지 않는다", {"fail": ["set:kt.analytics.v"], "steps": [*ALL, "click:an-save"]}, None, None, "정하지 않음", UNDECIDED, False),
    ("쓰기 예외의 [동의 철회] — 값과 판을 지운다", {"stored": "granted", "v": NOTICE, "fail": ["set"], "steps": ["click:an-withdraw"]}, None, None, "정하지 않음", UNDECIDED, True),
    ("지우기도 예외 — 동의가 남았다고 알린다", {"stored": "granted", "v": NOTICE, "fail": ["set", "remove"], "steps": ["click:an-withdraw"]}, "granted", NOTICE, "동의함", AGREED | {"an-stuck"}, True),
    ("다른 탭의 동의", {"steps": [f"other:kt.analytics.v={NOTICE}", "other:kt.analytics=granted"]}, "granted", NOTICE, "동의함", AGREED, False),
    ("다른 탭의 철회", {"stored": "granted", "v": NOTICE, "steps": ["other:kt.analytics=denied"]}, "denied", NOTICE, "거부함", UNDECIDED, False),
    ("다른 탭이 판을 바꿈", {"stored": "granted", "v": NOTICE, "steps": [f"other:kt.analytics.v={OLD}"]}, "granted", OLD, "정하지 않음", UNDECIDED, False),
    ("다른 탭이 저장소를 비움", {"stored": "granted", "v": NOTICE, "steps": ["clear:"]}, None, None, "정하지 않음", UNDECIDED, False),
]
# fmt: on
# §3.4-6 절 — 받는 곳마다 법 제28조의8 제2항 다섯 가지(동의 상자의 국외 이전 칸과 같은 이름) + 근거
TRANSFER_TERMS = CONSENT_CELLS[2][2] | {"근거"}
# 060 §3.3-2 — 맨 위 변경 안내 상자가 말하는 것(시행 문장, 바뀌는 것 셋 — 접속 기록 쓰임새(남은 기록 전체)·화면 이용 통계의
# 2절 요약 한 줄·바꾸는 방법, 다시 묻기)
CHANGE_NOTICE_SAYS = (
    "같은 날 먼저 게시한 판(2026년 10월 9일 판)을 바꿉니다",
    "바뀌는 것은 셋입니다",
    "30일 요약",
    "나라와 망 종류",
    "하루 한 번 세는 값",
    "남아 있는 기록 전체(로그 보관 기간 안)",
    "(전에는 2026년 10월 11일 기록부터)",
    "화면 이용 통계",
    "화면 요소",
    "마우스를 올려 둔 시간",
    "떠날 때 보던 영역",
    "내려온 깊이",
    "하루 합계로만",
    "저장하지 않습니다",
    "전후 대조",
    "다시 동의를 받습니다",
    "이미 동의했어도 한 번 더 묻습니다",
    "12절 대조표",
)
# 060 §3.3-2 — 변경 안내의 링크 넷: 이전 판 셋(10월 9일 판·10월 11일 판(시행 전 바뀜)·10월 1일 판)과 대조
CHANGE_NOTICE_LINKS = (
    'href="/privacy-20261009.html"',
    'href="/privacy-20261011.html"',
    'href="/privacy-20261001.html"',
    'href="#s12"',
)
# 060 §3.3-8 — 12절 규칙(7일 전 게시 약속과 그 단서는 지웠다)·시행 문장·이력 첫 항목
CHANGE_RULE = (
    "이 방침을 바꿀 때는 시행일과 바뀐 문장의 전후 대조를 이 페이지에 알리고 이전 판을 남깁니다. "
    "동의를 받아 하는 처리가 늘면 바뀐 안내로 다시 동의를 받고, 그 동의를 받은 때부터 합니다."
)
EFFECTIVE_SENTENCE = (
    "부터 시행하며(이날 두 번째 판), 같은 날 먼저 게시한 판을 바꿉니다."
)
V4_HISTORY = "(두 번째 판) — 서버 접속 기록 쓰임새를 남은 기록 전체에·화면 이용 통계를 요소까지·바꾸는 방법"
# 060 §7 — 2절 (다): 062 가 짝(하루 한 번 세는 값)에 붙이는 그날 첫·마지막 페이지 이름과 페이지 수. 게이트를 v4 시행일로
# 당겨 이 처리도 그날 기록부터 돌므로 v4 가 함께 알린다
ACCESS_FLOW_ITEMS = (
    "처음과 마지막에 연 페이지(랜딩·대시보드 탭·처리방침·검색어 페이지 같은 페이지 이름)와 연 페이지 수"
    "(어디로 들어와 어디서 나가는지 보려고)"
)
# 051 §3.3-10 — 이력의 v3 항목 설명(다음 판에서도 남는다)
V3_HISTORY_SAYS = ("화면 영역 이용 통계", "동의한 방문자만", "동의 안내 판 올림")
# 037 이 게시한 v2 판의 날짜 — 시행 전에 v3 으로 바뀌었다(사본 privacy-20261011.html). 060 부터 PRIVACY_V2_EFFECTIVE 는 이 날이
# 아니라 접속 기록 쓰임새를 시작한 날이라, 이력의 v2 항목은 이 날짜로 찾는다
V2_DAY = "2026-10-11"
# 037 §3.2-6 — 이력의 v2 항목 설명(다음 판에서도 남는다)
V2_HISTORY_SAYS = (
    "최근 30일까지",
    "나라와 망 종류",
    "하루 한 번 세는 값",
    "보낸 때부터",
)
# 037 §3.2 — 본문 절(변경 안내·대조표 밖)마다 v2 의 사실. 12절은 시행 문장과 이력(CloudWatch 사실 줄)
V2_FACTS = {
    "glance": ("걸리는 때는",),
    "s1": ("자동 요청(봇)", "망(국내 통신사·데이터센터 등)"),
    "s2": (
        "최근 30일까지",
        "서버 메모리에서만",
        "다시 시작하면 사라집니다",
        "DB-IP Lite",
        "밖으로 보내지 않습니다",
        "망 종류만",
        "되돌릴 수 없는 값",
        "날마다",
        "운영체제",
        "스크립트",
        "다시 온",
        "보낸 때부터 90일",
    ),
    "s3": ("며칠 걸릴 수 있습니다",),
    # 060 §3.3-8 — 12절의 변경 규칙은 v4 가 바꿨다. v2 의 사실로 남는 것은 이력의 CloudWatch 사실 줄
    "s12": (
        "보낸 시각으로 찍혀",
        "최대 3일",
        "줄마다 골라 지울 수 없어",
    ),
}
# 051 §3.3·060 §3.3 — 본문 절(변경 안내·대조표 밖)마다 화면 이용 통계(KimpTrack 서버)의 사실. v3 의 영역 통계에 v4 가 요소·떠날 때
# 보던 곳·깊이를 더하고 이름을 '화면 이용 통계' 로 바꿨다
SCREEN_USE_FACTS = {
    "glance": ("화면 영역별 이용 통계", "하루 합계", "둘 다 동의한 경우에만"),
    "consent-title": (
        "화면의 영역과 요소(단추·링크 같은 것)별로 보인 시간·클릭 수·마우스를 올려 둔 시간과 떠날 때 보던 곳을 하루 합계로",
        "모든 페이지(랜딩·대시보드·처리방침·검색어 페이지)",
        "Clarity와 KimpTrack 영역 통계가 돕니다",
    ),
    "s1": (
        "어느 화면 영역·요소를 많이·적게 보고 누르는지, 어디서 떠나는지",
        "화면 이용 통계 — KimpTrack 서버, 동의한 방문자만",
    ),
    "s2": (
        "화면 이용 통계 — KimpTrack 서버",
        "화면 이용 통계(동의한 경우만)",
        "하루 합계 90일",
        "768px",
        "1초 이상 보였는지",
        "클릭 수",
        ELEMENT_ITEMS,
        "페이지를 본 횟수",
        "그날(한국 시간) 합계에 더하기만",
        "IP도 저장하지 않습니다",
        "서버 접속 기록에도 남기지 않습니다",
        "Microsoft를 포함해 다른 곳으로 보내지 않습니다",
        "Redis에는 화면 이용 통계의 날짜별 합계만",
        "화면 이용 통계도 정보주체의 동의",
        "제15조 제1항 제1호",
        "한 사람분을 골라 지울 수 없고",
        "그 뒤로는 보내지 않습니다",
    ),
    "s3": ("화면 이용 통계는 날짜별 합계가 90일을 넘으면 서버가 지웁니다",),
    "s7": (
        "KimpTrack 서버로 — 화면의 영역·요소별 보인 시간·클릭 수·마우스를 올려 둔 시간과 떠날 때 보던 영역·내려온 깊이를 "
        "브라우저 스크립트가 화면을 떠날 때 보냄(하루 합계로 90일)",
    ),
    "s9": (
        "화면 이용 통계는 영역·요소 이름과 숫자만 보내고, 화면의 글자·입력값·좌표는 보내지 않습니다",
    ),
    "s12": ("2026년 10월 1일 판이 적은 90일",),
}
# 060 §3.3 — 이름을 바꾼 뒤 본문(대조표 밖)에 남으면 안 되는 옛 이름. 12절 이력의 v3 항목은 그 판이 쓴 이름 그대로라 뺀다
OLD_NAME = "화면 영역 이용 통계"
# 051 §3.3-6 — IP 는 남용 막기에만, 서버 메모리에서 10분
IP_TEN_MINUTES = (
    r"IP를 서버 메모리에서만 10분 동안 셉니다\(서버를 다시 시작하면 사라집니다\)"
)
# 051 §3.3-10 — 대조표에서 새로 넣은 문장의 '이전 판' 칸
ADDED = "(없음)"
# 060 §3.3-5 — 대조표에서 지운 문장의 '이 판' 칸(새로 넣은 문장의 '(없음)' 과 짝)
REMOVED = "(지움)"
# 060 §3.1 — 접속 기록을 남기기 시작한 날(027 caddy 접속 로그). 게이트 상수는 이보다 앞서 게이트가 없는 것과 같다
FIRST_LOG_DAY = "2026-09-29"
# 방문자가 읽는 글에 쓰지 않는 낱말(037 §3.2-4 — 평이한 말로)
PLAIN_ONLY = ("열쇠", "해시")
DIFF_TABLE = r'<table class="tbl diff">.*?</table>'
# 037 §3.3 — 이전 판 사본은 얼려 둔 파일이다. 사본마다 보이는 글(<body> 의 태그 뗀 글자)의 sha256 — 사본을 새로 둘 때 게시한
# 차례대로 끝에 더하고 그 뒤로는 고치지 않는다(소개 문단·12절·동의 절 아래 줄처럼 대조 범위 밖의 글자도 여기서 멈춘다).
# 10월 11일 판은 시행 전에 10월 9일 판(v3)으로 바뀌었고, 그 v3 은 같은 날 두 번째 판(v4 — 060)으로 바뀌었다
ARCHIVED_TEXT = {
    "privacy-20261001.html": "40976545d4ba5b8241e2107245a54d733767b0d40c81ca8ccc351fb6ebd795e3",
    "privacy-20261011.html": "a4d5b104cdb9af3c1a8e26c3499dd2907eaaf1f6af8c73a6bb2f586edace3099",
    "privacy-20261009.html": "5b35b358781a1d97869dd2d577493cf1997bdf3a8ef4e0fa6f1b69f64978fe23",
}
# 사본마다 그 판의 동의 안내 판 — 10월 11일 판은 시행 전에 바뀌어 안내 판을 올리지 않았다
ARCHIVED_NOTICE = {
    "privacy-20261001.html": "2026-10-01",
    "privacy-20261011.html": "2026-10-01",
    "privacy-20261009.html": "2026-10-09",
}
# 바로 앞 판의 사본 — 대조표가 맞대는 판. 파일 이름(시행일) 순이 아니라 게시한 차례의 끝이다(060 — 10월 9일 판(v3)은
# 10월 11일 판보다 나중에 게시했다)
PREVIOUS = list(ARCHIVED_TEXT)[-1]
# 037 §3.3 — 사본의 동의 절 상자 아래 한 줄(그 판의 세 칸은 읽기만, 고르는 곳은 지금 판)
ARCHIVED_CONSENT_LINE = (
    "<p>화면 분석 동의·철회는 지금 판의 “화면 분석 동의 관리”"
    '(<a class="link" href="/privacy#consent">/privacy#consent</a>)에서 고릅니다.</p>'
)


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang = ""
        self.titles: list[str] = []
        self.h2: list[str] = []
        self.links: list[dict[str, str]] = []
        self.srcs: list[tuple[str, str]] = []
        self.scripts: list[str] = []
        self.times: list[str] = []
        self._key: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "link":
            self.links.append(a)
        elif tag == "time":
            self.times.append(a.get("datetime", ""))
        if "src" in a:
            self.srcs.append((tag, a["src"]))
        if tag in ("title", "h2", "script"):
            self._key, self._buf = tag, []

    def handle_endtag(self, tag: str) -> None:
        if tag != self._key:
            return
        text = "".join(self._buf)
        if tag == "title":
            self.titles.append(text)
        elif tag == "h2":
            self.h2.append(" ".join(text.split()))
        else:
            self.scripts.append(text)
        self._key = None

    def handle_data(self, data: str) -> None:
        if self._key:
            self._buf.append(data)


def _read(path: Path) -> tuple[str, _Page]:
    html = path.read_text("utf-8")
    page = _Page()
    page.feed(html)
    return html, page


def _section(html: str, label: str) -> str:
    """`<section aria-labelledby="…">` 하나의 본문."""
    start = re.search(rf'<section[^>]* aria-labelledby="{label}"', html)
    assert start, label
    body = html[start.start() :]
    return body[: body.index("</section>")]


def _terms(dl: str) -> set[str]:
    return set(re.findall(r"<dt>(.*?)</dt>", dl))


def _notes(dl: str) -> dict[str, str]:
    return dict(re.findall(r"<dt>(.*?)</dt><dd>(.*?)</dd>", dl, flags=re.S))


def _text(fragment: str) -> str:
    """태그를 뗀 글자 — 033 의 띠와 같은 비교."""
    return " ".join(re.sub(r"<[^>]+>", "", fragment).split())


def _consent_box(html: str) -> tuple[str, list[str]]:
    """동의 상자(`<div class="consent">` 부터 절 끝까지)와 그 안의 세 칸."""
    section = _section(html, "consent-title")
    box = section[section.index('<div class="consent">') :]
    return box, re.findall(r'<div class="cell">(.*?)</div>', box, flags=re.S)


def _digest(cells: list[tuple[str, str]]) -> str:
    """(칸 이름, 알릴 사항) 셋의 태그 뗀 글자의 sha256 — 판에 묶인 글자 (§3.3)."""
    lines = []
    for label, notes in cells:
        lines.append(_text(label))
        lines += [f"{_text(dt)}: {_text(dd)}" for dt, dd in _notes(notes).items()]
        # 칸 끝 한 줄(051 — 그 전 판에는 없어 그 판들의 값은 그대로다)
        lines += [_text(note) for note in _cell_notes(notes)]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _cell_notes(cell: str) -> list[str]:
    """칸 끝 줄 `<p class="note">` 의 원문 (051 §3.3-4)."""
    return re.findall(r'<p class="note">(.*?)</p>', cell, flags=re.S)


def _notice_digest(html: str) -> str:
    """지금 판의 세 칸 — 체크 글자와 그 아래 알릴 사항."""
    cells = _consent_box(html)[1]
    return _digest(
        [(re.search(r"<span>(.*?)</span></label>", c).group(1), c) for c in cells]
    )


def _archived_notice_digest(html: str) -> str:
    """이전 판 사본의 동의 절 — 입력칸 없이 칸 이름(h3)과 알릴 사항(dl)만 남은 정적 글 (037 §3.3)."""
    section = _section(html, "consent-title")
    return _digest(
        re.findall(
            r'<h3>(.*?)</h3>\s*(<dl[^>]*>.*?</dl>(?:\s*<p class="note">.*?</p>)?)',
            section,
            re.S,
        )
    )


def _korean(day: str) -> str:
    """'2026-10-11' → '2026년 10월 11일' — 페이지의 날짜 글자(앞 0 없음)."""
    d = date.fromisoformat(day)
    return f"{d.year}년 {d.month}월 {d.day}일"


def _without_diff(html: str) -> str:
    """12절 대조표를 뺀 글 — 두 판의 문장이 함께 있는 곳은 대조표뿐이다 (037 §4)."""
    return re.sub(DIFF_TABLE, "", html, flags=re.S)


def _body(html: str) -> str:
    """방문자가 보는 글 — <body> 안에서 스크립트를 뺀 것."""
    body = html[html.index("<body>") : html.index("</body>")]
    return re.sub(r"<script>.*?</script>", "", body, flags=re.S)


def _body_sections(html: str) -> str:
    """소개 문단·한눈에 보기·1~11절의 태그 뗀 글자 — 대조표로 두 판을 맞대 보는 범위 (037 §4)."""
    lead = re.search(r'<p class="lead">(.*?)</p>', html, flags=re.S)
    assert lead, "소개 문단이 없다"
    labels = ["glance", *(f"s{n}" for n in range(1, 12))]
    return " ".join(
        [_text(lead.group(1)), *(_text(_section(html, label)) for label in labels)]
    )


def _change_notice(html: str) -> str:
    found = re.search(
        r'<aside class="notice" id="changes"[^>]*>(.*?)</aside>', html, re.S
    )
    assert found, "변경 안내 상자가 없다 (037 §3.2-2)"
    return found.group(1)


def _history(html: str) -> list[str]:
    """12절 변경 이력의 항목(최신 위)."""
    found = re.search(r'<ul class="history">(.*?)</ul>', _section(html, "s12"), re.S)
    assert found, "12절에 변경 이력이 없다"
    return re.findall(r"<li>(.*?)</li>", found.group(1), flags=re.S)


def _diff_rows(html: str) -> list[list[str]]:
    """12절 대조표의 행 — [절, 이전 판, 이 판, 바뀐 점] 의 태그 뗀 글자 (037 §3.2-6)."""
    table = re.search(DIFF_TABLE, _section(html, "s12"), flags=re.S)
    assert table, "12절에 대조표가 없다"
    body = table.group(0)[table.group(0).index("<tbody>") :]
    return [
        [_text(cell) for cell in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)]
        for row in re.findall(r"<tr>(.*?)</tr>", body, flags=re.S)
    ]


def _run_consent_script(cases: list[dict]) -> list[dict]:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node 가 없다 — CI 러너(ubuntu-latest)에는 있어야 한다")
        pytest.skip("node 가 없어 동의 관리 스크립트를 돌리지 못한다")
    html, page = _read(PUBLIC / "privacy.html")
    hidden = re.findall(r'<[^>]* id="(an-[\w-]+)"[^>]*\shidden[\s>]', html)
    disabled = re.findall(r'<[^>]* id="(an-[\w-]+)"[^>]*\sdisabled[\s>/]', html)
    payload = json.dumps(
        [
            "".join(page.scripts),
            [{**c, "hidden": hidden, "disabled": disabled} for c in cases],
        ]
    )
    done = subprocess.run(
        [node, "-e", HARNESS],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _external(url: str) -> bool:
    return url.startswith(("http://", "https://", "//"))


def _assert_no_external_resources(name: str, page: _Page) -> None:
    for tag, src in page.srcs:
        assert not _external(src), (name, tag, src)
    for link in page.links:
        rels = set(link.get("rel", "").lower().split())
        if rels & RESOURCE_RELS:
            assert not _external(link.get("href", "")), (name, link)


# --- nginx: 공개 server 의 위치 셋 (§3.1) -------------------------------------------


def test_privacy_location_serves_the_file_with_no_cache_and_csp_always() -> None:
    block = _locations(_public_server())[("=", "/privacy")]
    assert _args(block, "try_files") == [["/privacy.html", "=404"]]
    headers = _args(block, "add_header")
    assert ["Cache-Control", "no-cache", "always"] in headers
    assert ["Content-Security-Policy", CSP, "always"] in headers
    assert len(headers) == 2
    assert not _args(block, "proxy_pass") and not _args(block, "alias")


def test_file_name_urls_fold_into_privacy() -> None:
    """/app/privacy.html 은 dist 의 사본이 /app/ alias 로 CSP 없이 나가는 주소라 막는다."""
    locations = _locations(_public_server())
    for path in ("/privacy.html", "/app/privacy.html"):
        assert locations[("=", path)] == [(["return", "301", "/privacy"], None)], path


def test_privacy_routing_keeps_the_allowlist_and_has_no_regex() -> None:
    locations = _locations(_public_server())
    assert _route("/privacy") == ("=", "/privacy")
    assert _route("/app/privacy.html") == ("=", "/app/privacy.html")
    # /privacy/ 는 루트 location / 의 try_files $uri =404 로 404 다
    assert _route("/privacy/") == ("/",)
    assert not [key for key in locations if key[0] in ("~", "~*")]
    open_api = {
        key[1] for key in locations if key[0] == "=" and key[1].startswith("/api/")
    }
    assert open_api == set(PUBLIC_API)


# --- privacy.html (§3.1·§3.3·§3.4·§3.6) ---------------------------------------------


def test_page_has_no_placeholder_and_the_twelve_sections() -> None:
    html, page = _read(PUBLIC / "privacy.html")
    assert "〔" not in html, "사람이 채울 자리표시자가 남았다 (§3.6)"
    assert page.lang == "ko"
    assert page.titles == ["개인정보 처리방침 — KimpTrack"]
    canonical = [link["href"] for link in page.links if link.get("rel") == "canonical"]
    assert canonical == [CANONICAL]
    assert [h for h in page.h2 if re.match(r"\d+\. ", h)] == SECTIONS
    assert "화면 분석 동의 관리" in page.h2


def test_page_loads_nothing_external_and_scripts_make_no_requests() -> None:
    html, page = _read(PUBLIC / "privacy.html")
    _assert_no_external_resources("privacy.html", page)
    styles = "".join(re.findall(r"<style\b.*?</style>", html, flags=re.S))
    assert "@import" not in styles and "url(" not in styles
    assert page.scripts, "동의 관리 버튼 스크립트가 없다"
    # 인라인만 — 같은 출처 파일은 052 의 /attention.js 하나뿐(CSP 'self' 는 그 하나를 위해 연다)
    assert [src for tag, src in page.srcs if tag == "script"] == ["/attention.js"]
    for body in page.scripts:
        for call in NO_CALLS:
            assert call not in body, call


def test_page_states_the_consent_contract_and_required_notices() -> None:
    html, page = _read(PUBLIC / "privacy.html")
    for text in MUST_SAY:
        assert text in html, text
    for text in MUST_NOT_SAY:
        assert text not in html, text
    # 같은 모양의 버튼 — 세 칸 뒤 [선택한 대로 저장]·[모두 거부], 동의한 동안은 세 칸 앞 상태 줄 옆에 [동의 철회] —
    # 과 자바스크립트가 꺼졌을 때의 안내 (§3.3·§3.7)
    for button in (
        '<button class="btn" type="button" id="an-save">선택한 대로 저장</button>',
        '<button class="btn" type="button" id="an-deny">모두 거부</button>',
        '<button class="btn" type="button" id="an-withdraw" hidden>동의 철회</button>',
    ):
        assert button in html, button
    assert "<noscript>" in html
    # 두 버튼의 너비는 글자 길이가 아니라 같은 칸 너비를 따른다. hidden 은 .btn 의 display 를 이긴다 — 없으면
    # [모두 거부]·[동의 철회]와 GPC·저장 불가 때의 버튼 줄이 늘 보인다. 버튼 하나만 다르게 그리는 규칙은 없다
    styles = "".join(re.findall(r"<style\b.*?</style>", html, flags=re.S))
    assert re.search(
        r"\.actions \{[^}]*grid-template-columns: repeat\(auto-fit", styles
    )
    assert "[hidden] { display: none !important; }" in styles
    assert "#an-" not in styles
    # 중요한 내용 표시 — 다른 내용보다 20% 크게·굵게·밑줄 (§3.3 — 법 제22조 제2항)
    assert re.search(
        r"\.key \{[^}]*font-size: 1\.2em;[^}]*font-weight: 700;[^}]*text-decoration: underline;",
        styles,
    )
    # 세 칸은 처음에 모두 빈 칸 — 미리 체크하지 않는다
    boxes = re.findall(r"<input [^>]*>", html)
    assert len(boxes) == 3 and all('type="checkbox"' in box for box in boxes)
    assert not [box for box in boxes if re.search(r"\schecked[\s/>=]", box)]
    script = "".join(page.scripts)
    for state in STATES:
        assert f"'{state}'" in script, state
    assert "'granted'" in script and "'denied'" in script


def test_notice_version_is_shown_and_is_the_last_digest_key() -> None:
    """지금 판은 스크립트·보이는 글자가 같고 `NOTICE_DIGESTS` 의 마지막 키다 — 판 값은 같은지만 보고 날짜와 맞대지 않는다
    (§3.3 — 033 clarity.js·052 attention.js 의 판도 같다, 060 §3.2)."""
    html, _ = _read(PUBLIC / "privacy.html")
    assert NOTICE and re.fullmatch(r"[0-9A-Za-z.-]+", NOTICE), NOTICE
    assert f'<code id="an-version">{NOTICE}</code>' in html
    assert NOTICE == list(NOTICE_DIGESTS)[-1]
    # 051 §3.2 — v3 이 판 2026-10-09 를 열었고, 060 이 같은 날 두 번째 판을 더했다(예전 판 해시는 지우지 않는다)
    assert PRIVACY_V3_EFFECTIVE in NOTICE_DIGESTS
    assert NOTICE == "2026-10-09-2" and OLD == "2026-10-09"


def test_notice_version_is_tied_to_the_notice_text() -> None:
    """세 칸의 글자가 바뀌면 멈춘다 — 판을 올릴지(받는 자·항목·목적·보유 기간) 해시만 고칠지(문장 다듬기) 사람이 고른다 (§3.3)."""
    html, _ = _read(PUBLIC / "privacy.html")
    assert NOTICE in NOTICE_DIGESTS, NOTICE
    digest = _notice_digest(html)
    assert digest == NOTICE_DIGESTS[NOTICE], (
        f"세 칸의 글자가 판 {NOTICE} 때와 다르다 — 판을 올리거나 해시를 {digest} 로 고친다"
    )


def test_consent_script_shows_the_stored_value_and_the_buttons_write_it() -> None:
    """동의 관리 스크립트를 node 로 돌린다 — 상태 글자·보이는 요소·칸·저장값·판·쿠키가 실제 저장값과 같다 (§3.3)."""
    cookies = ["_clck", "_clsk", "other"]
    results = _run_consent_script(
        [{**case, "cookies": cookies} for _, case, *_ in CONSENT_CASES]
    )
    for (name, _, stored, version, state, shown, cleared), got in zip(
        CONSENT_CASES, results, strict=True
    ):
        assert (got["stored"], got["v"], got["state"]) == (stored, version, state), name
        visible = set(got["shown"])
        if "an-actions" not in visible:
            visible -= {"an-deny"}
        assert visible & (STATUS_IDS | {"an-deny", "an-withdraw"}) == shown, name
        # 칸은 저장된 선택을 보인다 — 동의함이면 셋 다, 아니면 빈 칸. 버튼 줄이 없으면 칸도 막는다
        assert got["checked"] == (sorted(BOXES) if state == "동의함" else []), name
        usable = "an-actions" in shown
        assert set(got["disabled"]) == (set() if usable else set(BOXES)), name
        assert got["cookies"] == (["other"] if cleared else cookies), name


def test_withdrawal_expires_clarity_cookies_in_both_domain_shapes() -> None:
    (got,) = _run_consent_script(
        [
            {
                "stored": "granted",
                "v": NOTICE,
                "cookies": ["_clck"],
                "steps": ["click:an-withdraw"],
            }
        ]
    )
    for name in ("_clck", "_clsk"):
        lines = [line for line in got["writes"] if line.startswith(f"{name}=;")]
        assert all("Max-Age=0" in line and "path=/" in line for line in lines), name
        assert any("domain=" not in line for line in lines), name
        assert any(line.endswith("; domain=kimptrack.com") for line in lines), name


def test_consent_box_splits_three_consents_before_the_buttons() -> None:
    """동의를 셋으로 나눠 칸마다 그 동의의 알릴 사항을 두고, 지금 상태·[동의 철회]는 칸 앞, 저장·거부는 칸 뒤 (§3.3 — 법 제22조)."""
    html, _ = _read(PUBLIC / "privacy.html")
    box, cells = _consent_box(html)
    # 033 의 띠와 같은 문장, 지금 상태와 [동의 철회] — 세 칸보다 앞(/privacy#consent 로 와서 첫 화면에서 철회한다, 제38조 제4항)
    first_cell = box.index('<div class="cell">')
    for sentence in SHARED_SENTENCES:
        assert 0 <= box.find(sentence) < first_cell, sentence
    for element in ("an-state", "an-withdraw"):
        assert box.index(f'id="{element}"') < first_cell, element
    # [선택한 대로 저장]·[모두 거부] — 알릴 사항을 읽은 뒤 고르게 마지막 칸 뒤
    last_cell_end = box.index("</div>", box.rindex('<div class="cell">'))
    for element in ("an-save", "an-deny"):
        assert box.index(f'id="{element}"') > last_cell_end, element
    assert len(cells) == len(CONSENT_CELLS)
    for cell, (box_id, label, terms, key_terms) in zip(
        cells, CONSENT_CELLS, strict=True
    ):
        assert re.search(
            rf'<label class="pick"><input type="checkbox" id="{box_id}" disabled /><span>'
            rf"{re.escape(label)}</span></label>",
            cell,
        ), box_id
        dls = re.findall(r"<dl[^>]*>(.*?)</dl>", cell, flags=re.S)
        assert len(dls) == 1 and _terms(dls[0]) == terms, box_id
        # 033 의 띠가 이 칸들을 옮긴다 — 랜딩·대시보드에는 방침의 절이 없으니 칸은 다른 절을 가리키지 않는다
        assert not re.search(r"\d+절", dls[0]), box_id
        notes = _notes(dls[0])
        # 중요한 내용만 칸 전체를 강조 표시로 감싼다 (제22조 제2항·시행령 제17조 제3항)
        for term, value in notes.items():
            key = re.fullmatch(r'<strong class="key">.*</strong>', value, flags=re.S)
            assert bool(key) == (term in key_terms), (box_id, term)
            assert value.count('class="key"') == (term in key_terms), (box_id, term)
        assert "등" not in notes["항목"], box_id
        for item in CLARITY_ITEMS:
            assert item in notes["항목"], (box_id, item)
        # 거부 칸은 거부해도 불이익이 없다는 것과 철회하는 곳을 적는다 (법 제28조의8 제2항 제5호·제38조 제4항)
        refusal = notes.get("거부 권리·불이익") or notes["거부 방법·절차·효과"]
        assert "불이익이 없습니다" in refusal, box_id
        assert 'href="/privacy#consent"' in refusal and "[동의 철회]" in refusal
    raw = [_notes(cell) for cell in cells]
    collect, provide, transfer = (
        {term: _text(value) for term, value in notes.items()} for notes in raw
    )
    assert collect["목적"] == (
        "KimpTrack의 화면 이용 분석 — 어떤 화면·기능이 쓰이고 어디서 막히는지 알아 서비스를 고치기, "
        "어느 영역·요소를 많이·적게 보고 누르는지, 어디서 떠나는지 알아 화면을 고치기"
    )
    # 051 §3.3-4 — 수집·이용 칸에만 KimpTrack 서버의 통계(항목·보유 기간·칸 끝 국내 보관 한 줄). 제공·국외 이전 칸은 그대로.
    # 060 §3.3-4 — 항목 끝은 'KimpTrack 서버:' 뒤에 2절과 같은 항목(요소·떠날 때 보던 곳·깊이) — Clarity 항목 뒤에 둔다
    kimptrack = collect["항목"].split(". KimpTrack 서버: ")
    assert len(kimptrack) == 2 and "sessionStorage _cltk" in kimptrack[0]
    assert "1초 이상 보였는지" in kimptrack[1] and kimptrack[1].endswith(ELEMENT_ITEMS)
    assert "KimpTrack 서버: 하루 합계로만 90일" in collect["보유 기간"]
    assert [_text(note) for note in _cell_notes(cells[0])] == [COLLECT_NOTE]
    for cell in cells[1:]:
        assert not _cell_notes(cell) and "KimpTrack 서버" not in cell
    for period in (
        collect["보유 기간"],
        provide["받는 자의 보유 기간"],
        transfer["목적·보유 기간"],
    ):
        assert "녹화 30일" in period and "최대 9개월" in period, period
    for period in (provide["받는 자의 보유 기간"], transfer["목적·보유 기간"]):
        assert "Microsoft 개인정보처리방침이 정한 기간" in period, period
    assert provide["받는 자"] == "Microsoft Corporation(미국)"
    assert "Microsoft Advertising" in provide["받는 자의 목적"]
    assert transfer["국가·시기·방법"].startswith("미국 — ")
    for word in ("수시로", "브라우저", "전송"):
        assert word in transfer["국가·시기·방법"], word
    assert transfer["받는 자·연락처"].startswith("Microsoft Corporation, ")
    assert "Redmond" in transfer["받는 자·연락처"]
    assert INQUIRY in raw[2]["받는 자·연락처"]
    assert "Microsoft Advertising" in transfer["목적·보유 기간"]


def test_withdrawal_deletion_is_stated_under_the_box_and_in_section_3() -> None:
    """철회 뒤 이미 보낸 기록을 지우는 길과 그 한계 — 띠에서 /privacy#consent 로 온 방문자가 처음 읽는 상자 아래 문단과
    3절에 따로 적는다 (§3.3 — 법 제37조 제3항). 8절은 아래 테스트가 본다."""
    html, _ = _read(PUBLIC / "privacy.html")
    section = _section(html, "consent-title")
    under_box = section[section.rindex("</div>") :]
    assert DELETION in under_box
    assert '<a class="link" href="#s10">10절</a>의 메일로 요청' in under_box
    destruction = _section(html, "s3")
    assert "운영자가 Clarity 프로젝트의 기록을 통째로 지웁니다" in destruction
    for part in (under_box, destruction):
        assert "지우는 시점은 Microsoft 기준" in part
        assert MICROSOFT_KEEPS in part


def test_clarity_rests_on_consent_for_collection_provision_and_transfer() -> None:
    """사람 결정 2026-10-01 — 수집·제공은 동의, 국외 이전은 별도 동의 (§3.6)."""
    html, _ = _read(PUBLIC / "privacy.html")
    assert "제15조 제1항 제1호" in _section(html, "s2")
    # 철회 뒤 이미 보낸 기록 — 한 사람분은 못 지우니 프로젝트를 통째로 지운다 (§3.3 — 법 제37조 제3항)
    assert "프로젝트의 기록을 통째로 지우고" in _section(html, "s8")
    provision = _section(html, "s4")
    dls = re.findall(r"<dl[^>]*>(.*?)</dl>", provision, flags=re.S)
    assert len(dls) == 1 and _terms(dls[0]) == PROVISION_TERMS
    assert "제17조 제1항 제1호" in provision and "제17조 제1항 제2호" not in provision
    assert "불이익이 없습니다" in provision
    groups = re.findall(
        r"<h3>(.*?)</h3>\s*<dl[^>]*>(.*?)</dl>", _section(html, "s6"), flags=re.S
    )
    microsoft = [body for name, body in groups if name.startswith("Microsoft")]
    assert len(microsoft) == 1
    basis = re.search(r"<dt>근거</dt><dd>(.*?)</dd>", microsoft[0]).group(1)
    assert "제28조의8 제1항 제1호" in basis and "제3호" not in basis
    assert "불이익이 없습니다" in microsoft[0]


def test_behavioral_information_has_the_guideline_items() -> None:
    """행태정보를 제3자가 광고 목적에도 쓸 수 있다 — 7절 소항목에 작성지침 항목 (§3.4-7)."""
    html, _ = _read(PUBLIC / "privacy.html")
    section = _section(html, "s7")
    block = re.search(
        r"<h3>행태정보의 수집·이용·제공과 거부</h3>.*?<dl[^>]*>(.*?)</dl>",
        section,
        flags=re.S,
    )
    assert block and _terms(block.group(1)) == BEHAVIOR_TERMS
    assert "Microsoft Corporation" in block.group(1) and "광고" in block.group(1)


def test_every_overseas_recipient_lists_the_five_items() -> None:
    html, _ = _read(PUBLIC / "privacy.html")
    section = _section(html, "s6")
    groups = re.findall(r"<h3>(.*?)</h3>\s*<dl[^>]*>(.*?)</dl>", section, flags=re.S)
    assert len(groups) >= 5
    for name, body in groups:
        assert _terms(body) == TRANSFER_TERMS, name


def test_archived_versions_are_static_and_external_free() -> None:
    """이전 판 privacy-<시행일>.html 은 location / 로 CSP 없이 나간다 — 스크립트 없는 정적 사본이고, 검색에는 지금 판만
    나오며, 그 판에서 방문자가 무엇에 동의했는지(세 칸의 알릴 사항)를 입력칸 없이 보여 준다 (032 §3.6·037 §3.3)."""
    now, _ = _read(PUBLIC / "privacy.html")
    linked = re.findall(r'href="/(privacy-\d{8}\.html)"', now)
    assert linked, "지금 판이 이전 판으로 잇지 않는다"
    paths = sorted(PUBLIC.glob("privacy-*.html"))
    # 이력의 옛 판마다 사본이 있고, 사본마다 이력에서 잇고, 얼려 둔 글자의 값이 있다
    assert {path.name for path in paths} == set(linked) == set(ARCHIVED_TEXT)
    for path in paths:
        html, page = _read(path)
        assert re.fullmatch(r"privacy-\d{8}\.html", path.name), path.name
        assert "〔" not in html and "<script" not in html, path.name
        _assert_no_external_resources(path.name, page)
        stamp = path.name[len("privacy-") : -len(".html")]
        day = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}"
        assert page.times and set(page.times) == {day}, path.name
        assert page.titles == [f"개인정보 처리방침({_korean(day)} 판) — KimpTrack"]
        # 같은 날 판이 둘이면 날짜 뒤 괄호로 어느 판인지 밝힌다(060 — '(그날 먼저 게시한 판)')
        assert re.search(rf"{_korean(day)} 시행 판(\([^)<]*\))?의 사본입니다", html), (
            path.name
        )
        assert '<meta name="robots" content="noindex" />' in html, path.name
        assert not [link for link in page.links if link.get("rel") == "canonical"]
        assert 'href="/privacy"' in html, path.name
        # 동의 절 상자(세 칸의 알릴 사항 — 그 안에도 /privacy#consent 가 있다) 뒤의 한 줄
        consent = _section(html, "consent-title")
        assert ARCHIVED_CONSENT_LINE in consent[consent.rindex("</div>") :], path.name
        assert "<input" not in html and "<button" not in html, path.name
        text = hashlib.sha256(_text(_body(html)).encode()).hexdigest()
        assert text == ARCHIVED_TEXT[path.name], (
            f"{path.name} 의 글자가 얼려 둔 때와 다르다 — 사본은 고치지 않는다(지금 값 {text})"
        )
        # 그 판이 시행될 때의 안내 판 — 세 칸의 글자가 그 판의 해시와 같다
        # 10월 11일 판은 시행 전에 v3(10월 9일)으로 바뀌어, 그 사본의 동의 안내는 10월 1일 판이다(051 §3.1 사람 결정)
        version = ARCHIVED_NOTICE[path.name]
        assert _archived_notice_digest(html) == NOTICE_DIGESTS[version], path.name


# --- 처리방침 v2 — 시행일·변경 안내·대조표 (037 §3) ----------------------------------


def test_v2_gate_constant_is_before_the_first_log_line() -> None:
    """038·039 의 날짜 게이트가 읽는 한 곳 — 게이트 없음: 첫 접속 기록보다 앞선 날이라 남아 있는 기록 전체를 쓴다
    (037 §3.1·060 §3.1 — 사람 결정 2026-10-09)."""
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", PRIVACY_V2_EFFECTIVE)
    assert PRIVACY_V2_EFFECTIVE < FIRST_LOG_DAY


def test_v3_effective_constant_is_a_date_after_the_first_version() -> None:
    """052 의 날짜 게이트가 읽는 한 곳 — 영역 통계를 받기 시작한 날. 사람 결정(2026-10-09)으로 v2(10-11)보다 앞서 시행해 v2 는
    시행 전에 바뀌었고, 같은 날 v4 가 v3 을 바꿨다(그대로 둔다 — 051 §3.1·060 §3.1)."""
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", PRIVACY_V3_EFFECTIVE)
    assert date.fromisoformat(PRIVACY_V3_EFFECTIVE) > date(2026, 10, 1)
    assert PRIVACY_V3_EFFECTIVE == "2026-10-09"


def test_v4_constant_is_the_page_effective_date() -> None:
    """060 §3.1 — 요소별 통계(061)의 게이트 `PRIVACY_V4_EFFECTIVE` 는 지금 판의 시행일(페이지 <time> 넷·sitemap)과 같다.
    접속 기록 쓰임새에는 게이트가 없다(`PRIVACY_V2_EFFECTIVE` 가 첫 기록보다 앞 — 사람 결정 2026-10-09)."""
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", PRIVACY_V4_EFFECTIVE)
    assert PRIVACY_V4_EFFECTIVE == EFFECTIVE == "2026-10-09"
    assert PRIVACY_V2_EFFECTIVE < FIRST_LOG_DAY < PRIVACY_V4_EFFECTIVE


def test_effective_date_ties_the_four_times_and_the_history() -> None:
    """시행일은 머리·변경 안내·12절 시행 문장·이력 첫 항목의 <time> 넷과 sitemap(아래)에 같고, 한 곳만 고치면 멈춘다 (037 §3.1)."""
    html, _ = _read(PUBLIC / "privacy.html")
    assert EFFECTIVE >= PRIVACY_V3_EFFECTIVE
    times = re.findall(r'<time datetime="([^"]*)">([^<]*)</time>', html)
    assert times == [(EFFECTIVE, _korean(EFFECTIVE))] * 4
    tag = f'<time datetime="{EFFECTIVE}">'
    # 060 §3.3-1 — 같은 날 두 번째 판이라 머리 시행일 바로 뒤에 그것을 적는다
    assert (
        f'<p class="meta">시행일 {tag}{_korean(EFFECTIVE)}</time>(이날 두 번째 판)</p>'
        in html
    )
    assert tag in _change_notice(html)
    rule = re.search(r"<p>(.*?)</p>", _section(html, "s12"), flags=re.S).group(1)
    assert tag in rule and "부터 시행" in rule
    assert (
        _text(rule)
        == f"이 방침은 {_korean(EFFECTIVE)}{EFFECTIVE_SENTENCE} {CHANGE_RULE}"
    )
    items = _history(html)
    assert items[0].startswith(tag)
    assert _text(items[0]) == f"{_korean(EFFECTIVE)}{V4_HISTORY}"
    # v3 가 열린 날의 항목도 다음 판에서 남는다 — 052 의 게이트가 그날을 읽는다 (051 §3.3-10). 060 — 그 판의 사본 링크
    v3 = [
        i for i in items if _text(i).startswith(f"{_korean(PRIVACY_V3_EFFECTIVE)} — ")
    ]
    assert len(v3) == 1
    for word in V3_HISTORY_SAYS:
        assert word in _text(v3[0]), word
    assert 'href="/privacy-20261009.html">그 판 보기</a>' in v3[0]
    # v2 판(시행 전에 바뀜)의 항목은 다음 판에서도 이력에 남는다 — 그 판이 알린 접속 기록 쓰임새를 038·039 가 연다
    v2 = f"{_korean(V2_DAY)} — "
    opened = [item for item in items if _text(item).startswith(v2)]
    assert len(opened) == 1
    # 그 항목의 설명(아래 사실 문단 앞) — 무엇이 바뀌었는지 (037 §3.2-6)
    headline = _text(opened[0].split("<p>")[0])
    for word in V2_HISTORY_SAYS:
        assert word in headline, word
    # 지금 판을 뺀 항목마다 그 판의 사본 링크
    for item in items[1:]:
        y, m, d = re.match(r"(\d{4})년 (\d{1,2})월 (\d{1,2})일", _text(item)).groups()
        assert f'href="/privacy-{y}{int(m):02d}{int(d):02d}.html"' in item, item


def test_change_notice_comes_first_and_names_what_changes() -> None:
    """맨 위 변경 안내 — 머리 시행일 줄 바로 아래, 소개 문단 앞. 시행 문장·바뀌는 것 셋(차례대로)·다시 묻기·링크 넷 (060 §3.3-2)."""
    html, page = _read(PUBLIC / "privacy.html")
    assert "변경 안내" in page.h2
    start = html.index('id="changes"')
    assert html.index('<p class="meta">시행일') < start < html.index('<p class="lead">')
    notice = _change_notice(html)
    assert f'href="/{PREVIOUS}"' in notice
    assert re.findall(r'href="([^"]*)"', notice) == [
        link.split('"')[1] for link in CHANGE_NOTICE_LINKS
    ]
    text = _text(notice)
    for word in CHANGE_NOTICE_SAYS:
        assert word in text, word
    assert text.startswith(
        f"변경 안내 이 판은 {_korean(EFFECTIVE)}부터 시행합니다 — 같은 날 먼저 게시한 판"
    )
    # 바뀌는 것 셋 — 번호 목록, 차례는 접속 기록 쓰임새 → 화면 이용 통계 → 바꾸는 방법. 쓰임새의 날짜는 상수 한 곳에서
    changes = re.findall(r"<li>(.*?)</li>", notice, flags=re.S)
    assert len(changes) == 3
    first, second, third = (_text(item) for item in changes)
    assert (
        "서버 접속 기록의 30일 요약·나라와 망 종류 추정·하루 한 번 세는 값을 남아 있는 기록 전체(로그 보관 기간 안)에 "
        "씁니다(전에는 2026년 10월 11일 기록부터)."
    ) in first
    assert "화면 이용 통계" in second and "(2절)" in second
    assert (
        "처음과 마지막에 연 페이지와 연 페이지 수" in first
    )  # 062 의 짝 값(2절 (다)) — 060 §7
    assert "(12절)" in third and "7일" not in third
    # 다시 묻기는 셋 뒤
    assert text.index("12절)") < text.index("이미 동의했어도 한 번 더 묻습니다")


def test_body_sections_state_the_v2_facts() -> None:
    """본문 절마다 v2 의 사실 — 변경 안내·대조표가 아닌 곳에서. 지난 판 문장은 대조표에만 남는다 (037 §3.2)."""
    html, _ = _read(PUBLIC / "privacy.html")
    for label, words in V2_FACTS.items():
        text = _text(_without_diff(_section(html, label)))
        for word in words:
            assert word in text, (label, word)
    # (라) — 시작 날짜 문장은 없다: 남아 있는 기록 전체를 쓴다(060 §3.1·§3.3-5 — 대조표의 이전 판 칸에만 남는다)
    s2 = _text(_section(_without_diff(html), "s2"))
    assert "(다)" in s2 and "(라)" not in s2
    # (다) — 062 가 짝에 붙이는 그날 첫·마지막 페이지 이름과 페이지 수(게이트를 당겨 v4 시행일부터 돈다 — 060 §7)
    assert ACCESS_FLOW_ITEMS in _text(_section(_without_diff(html), "s2"))
    outside = _without_diff(html)
    assert "24시간 요약" not in outside
    assert "90일이 지나면 지웁니다" not in outside
    visible = _text(_body(html))
    for word in PLAIN_ONLY:
        assert word not in visible, word


def test_body_sections_state_the_screen_use_facts() -> None:
    """본문 절마다 화면 이용 통계의 사실 — 무엇을(영역·요소·떠날 때 보던 곳·깊이)·어떻게·얼마 동안·근거·파기 (051 §3.3·060 §3.3)."""
    html, _ = _read(PUBLIC / "privacy.html")
    body = _without_diff(html)
    for label, words in SCREEN_USE_FACTS.items():
        text = _text(_section(body, label))
        for word in words:
            assert word in text, (label, word)
    assert re.search(IP_TEN_MINUTES, _text(_section(body, "s2")))
    # 2절 소제목은 Clarity 소제목 앞 (051 §3.3-6), 이름은 '화면 이용 통계 — KimpTrack 서버' (060 §3.3-3)
    s2 = _section(body, "s2")
    assert s2.index("<h3>화면 이용 통계 — KimpTrack 서버</h3>") < s2.index(
        "<h3>화면 이용 기록 — Microsoft Clarity</h3>"
    )
    # 옛 이름은 대조표 밖에서는 12절 이력의 v3 항목(그 판이 쓴 이름)에만 남는다
    visible = _text(_body(body))
    assert visible.count(OLD_NAME) == 1
    assert OLD_NAME in _text(_history(html)[1])
    # 7절 저장 장치 목록은 그대로 — 영역 통계는 쿠키·브라우저 저장소를 쓰지 않는다 (051 §3.3-8)
    devices = re.search(r"<ul>(.*?)</ul>", _section(body, "s7"), flags=re.S).group(1)
    assert len(re.findall(r"<li>", devices)) == 5 and "영역" not in devices


def test_diff_table_rows_match_both_versions() -> None:
    """대조표의 '이전 판' 은 바로 앞 판 사본 본문에, '이 판' 은 지금 판 본문(대조표 밖)에 그대로 있고, 지금 판의 소개 문단·
    한눈에 보기·1~11절에서 행마다 '이 판' 을 '이전 판' 으로 되돌리면(새로 넣은 문장은 빼면) 사본과 같다 — 바뀐 문장을 빠짐없이
    싣는다 (037 §3.2-6·051 §3.3-10). 동의 관리 행은 사본에 상자 문장이 없어 얼려 둔 앞 판 문장과 맞추고 지금 판 동의 절에서 찾는다."""
    html, _ = _read(PUBLIC / "privacy.html")
    archived, _ = _read(PUBLIC / PREVIOUS)
    rows = _diff_rows(html)
    assert rows and all(len(row) == 4 for row in rows), rows
    now = _text(_without_diff(_body(html)))
    consent = _text(_section(_without_diff(html), "consent-title"))
    before = _text(_body(archived))
    restored = _body_sections(html)
    expected = _body_sections(archived)
    for section, old, new, why in rows:
        assert why, section
        added, removed = old == ADDED, new == REMOVED
        assert not (added and removed), section
        # 지운 문장(060 §3.3-5)은 지금 판 본문에 없고 사본에만 있다
        assert (removed and old not in now) or (new and new in now), (section, new)
        assert added or old in before or old in PREVIOUS_CONSENT_TEXT, (section, old)
        if section.startswith("동의 관리"):
            assert removed or new in consent, (section, new)
            continue  # 동의 절은 사본과 모양이 달라 되돌려 맞대는 범위 밖이다
        if section.startswith("12절"):
            continue  # 12절은 시행 문장·이력도 판마다 달라 되돌려 맞대는 범위 밖이다 — 전후 글자만 본다
        if removed:
            assert old in expected, (section, old)
            expected = expected.replace(old, "")  # 지운 문장은 사본 쪽에서 빼고 맞댄다
            continue
        assert new in restored, (section, new)
        restored = restored.replace(new, "" if added else old)
    assert " ".join(restored.split()) == " ".join(expected.split())
    # 060 §3.3 — 동의 상자의 바뀐 문장(질문·판)은 행이 있고, 바뀐 절마다 행이 있다. 새로 넣은 문장은 없다(모두 고친 문장)
    names = {row[0]: row for row in rows}
    assert len(names) == len(rows)
    for name in (
        "동의 관리 질문",
        "동의 관리 수집·이용 칸 항목",
        "동의 관리 수집·이용 칸 목적",
        "동의 관리 수집·이용 칸 끝",
        "동의 관리 안내의 판",
        "1절 목적",
        "2절 표",
        "2절 서버 접속 기록 (다)",
        "2절 서버 접속 기록 (라)",
        "2절 화면 이용 통계",
        "3절",
        "7절 행태정보",
        "9절",
        "12절 변경",
    ):
        assert names[name][1] not in ("", ADDED), name
    for sentence in PREVIOUS_CONSENT_TEXT:
        assert [row for row in rows if row[1] == sentence], sentence
    # (라)는 지운 문장 — 이 판 칸 '(지움)'(060 §3.3-5)
    assert names["2절 서버 접속 기록 (라)"][2] == REMOVED
    # 12절 — 지운 약속은 이전 판 칸에만, 이 판 칸은 지금 규칙 그대로
    old_rule, new_rule = names["12절 변경"][1:3]
    assert "7일 전까지" in old_rule and new_rule == CHANGE_RULE


def test_section_12_rule_drops_the_seven_day_promise() -> None:
    """060 §3.3-8 — 12절 규칙은 '시행일과 전후 대조를 알리고 이전 판을 남긴다·동의 처리가 늘면 다시 동의' 이고, 7일 전 게시
    약속과 그 단서는 본문(대조표 밖) 어디에도 없다 — 대조표의 이전 판 칸에만 남는다."""
    html, _ = _read(PUBLIC / "privacy.html")
    visible = _text(_without_diff(_body(html)))
    assert CHANGE_RULE in _text(_section(_without_diff(html), "s12"))
    for gone in ("7일 전", "7×24", "늘거나 새로 생기면", "새로 동의를 받아서만"):
        assert gone not in visible, gone
    assert "7일 전까지" in _text(re.search(DIFF_TABLE, html, flags=re.S).group(0))


# --- 링크·sitemap (§3.2) ------------------------------------------------------------


def test_landing_footer_links_to_privacy_in_the_same_tab() -> None:
    html = (PUBLIC / "landing.html").read_text("utf-8")
    footer = html[html.index("<footer") : html.index("</footer>")]
    link = re.search(r'<a [^>]*href="/privacy"[^>]*>개인정보 처리방침</a>', footer)
    assert link and "target=" not in link.group(0)


def test_dashboard_header_opens_privacy_in_a_new_tab() -> None:
    app = (ROOT / "web/src/App.tsx").read_text("utf-8")
    assert 'href="/privacy"' in app
    assert 'target="_blank"' in app and 'rel="noopener"' in app


def test_sitemap_lists_privacy_with_the_effective_date() -> None:
    sitemap = (PUBLIC / "sitemap.xml").read_text("utf-8")
    assert f"<loc>{CANONICAL}</loc><lastmod>{EFFECTIVE}</lastmod>" in sitemap
    _, page = _read(PUBLIC / "privacy.html")
    assert page.times and set(page.times) == {EFFECTIVE}
