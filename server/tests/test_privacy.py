"""개인정보 처리방침 계약 — nginx 위치 셋·privacy.html·링크·sitemap 을 파일로 읽어 단언한다 (스펙 032 §4·037 §4·051 §4·060 §4·067 §4).

머지가 곧 게시다. 사람이 채울 자리표시자(`〔`)가 남았거나, 페이지가 외부 자원·서버 호출을 부르게 되거나, 033 과 함께 쓰는
동의 계약(`kt.analytics` — 동의한 방문자만 분석, 기본 꺼짐)과 방침의 필수 안내가 빠지면 여기서 멈춘다. 개정(037)은 날짜 상수
(`PRIVACY_V2_EFFECTIVE` — 접속 기록 쓰임새, v3 은 `PRIVACY_V3_EFFECTIVE` — 051, v4 는 `PRIVACY_V4_EFFECTIVE` — 060)·페이지의
`<time>` 넷·sitemap·이력과, 바뀐 문장 대조표를 두 판(지금 판·바로 앞 판 사본)의 본문과 맞대 본다. v3(051)·v4(060)는 동의 안내
판을 올렸다 — 동의 창(033 clarity.js)과 방침 동의 상자는 같은 글이다. 판 값은 같은지만 본다(`NOTICE_DIGESTS` 의 마지막 키).
v5(067)는 서버 접속 기록에 관리자 화면의 IP 대역별 보기(2절 (라))를 더한다 — 동의 대상이 아니라 안내 판은 그대로다.
v7(076)은 화면 이동 기록을 더했고, v8(078 — 같은 날 두 번째 판)은 그 기록의 보유 기간(서비스를 끝낼 때까지)과 두는 곳
(Redis + Amazon S3)만 바꿔 안내 판을 올린다(`PRIVACY_V8_EFFECTIVE`). 받는 것·Clarity·화면 이용 통계의 글은 그대로다.
v9(075 — 새 날의 판)는 같은 세 칸 동의로 방문 통계를 Google Analytics(Google LLC, 미국)로도 보내 안내 판을 올린다 — 제공·국외 이전
칸의 받는 자에 Google 을 더하고, Clarity·화면 이용 통계·화면 이동 기록의 글은 그대로다. 서버 게이트가 없어 시행일 상수도 없다.
v10(079 — 같은 날 두 번째 판)은 동의 창의 버튼([모두 거부] → [하루 동안 안 보기]·[모두 동의])과 저장 규칙(일부만 체크한 저장은
`later` — 24시간 뒤 다시 묻기, 판 있는 `denied` 만 다시 묻지 않기)을 바꿔 동의 상자의 글이 달라져 안내 판을 올린다(`2026-10-17-2`).
받는 자·항목·목적·보유 기간은 그대로이고, v9 처럼 서버 게이트·시행일 상수가 없다.
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
    PRIVACY_V7_EFFECTIVE,
    PRIVACY_V8_EFFECTIVE,
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
EFFECTIVE = "2026-10-17"  # 지금 판의 시행일 — 페이지의 <time> 넷과 sitemap lastmod 가 같다 (037 §3.1·051 §3.1·067 §3.4)
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
    "_ga",  # 075 — Google Analytics 쿠키(동의한 경우에만, 철회하면 지움)
    'href="https://policies.google.com/privacy?hl=ko"',
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
    # 079 §3.1 — 하루 동안 묻지 않음(later)과 다시 물을 시각
    "<code>later</code>",
    "<code>kt.analytics.until</code>",
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
# 영역과 요소·마우스를 올려 둔 시간·떠날 때 보던 곳으로. 076 — 화면 이동 기록, 078 — 그 기록을 서비스를 끝낼 때까지 둔다.
# 075 — 방문 통계를 Google Analytics(미국)로 보내는 물음 둘, 저장 규칙에 'Clarity·Google Analytics와'.
# 079 — 저장 규칙: 동의 창에 [모두 동의]가 생겨 '저장한', 하나라도 빠지면 거부가 아니라 하루 동안 묻지 않다가 다시 묻는다
SHARED_SENTENCES = (
    "랜딩과 대시보드의 화면 이용 기록(클릭·스크롤 등)을 Microsoft Clarity(미국)로 보내 서비스 개선에 써도 될까요? "
    "Microsoft는 이 기록을 광고 등 자기 목적에도 씁니다. 랜딩과 대시보드의 방문 통계(본 페이지·머문 시간·들어온 길)를 "
    "Google Analytics(미국)로 보내도 될까요? Google의 광고·제품 개선에는 쓰지 않게 설정해 보냅니다. 또 KimpTrack 서버가 모든 페이지(랜딩·대시보드·처리방침·검색어 페이지)에서 "
    "화면의 영역과 요소(단추·링크 같은 것)별로 보인 시간·클릭 수·마우스를 올려 둔 시간과 떠날 때 보던 곳을 하루 합계로 모으고, "
    "방문마다 마우스·손가락 위치와 누른 곳·스크롤을 시각·IP 주소와 함께 모아 서비스를 끝낼 때까지 두고 운영자가 그 방문을 다시 보거나 "
    "지금 보는 방문을 따라 보는 데 써도 될까요? 동의하지 않아도 모든 기능을 그대로 씁니다.",
    "세 가지에 모두 동의하고 저장한 경우에만 Clarity·Google Analytics와 KimpTrack 영역 통계·화면 이동 기록이 돕니다 — "
    "Clarity는 셋이 다 있어야 돌아가므로 하나라도 빠지면 분석하지 않고, 하루 동안 묻지 않다가 다시 묻습니다.",
    "만 14세 미만은 동의하지 마세요.",
)
# 바로 앞 판의 동의 상자에서 바뀐 문장 — 사본은 동의 절에 세 칸의 알릴 사항만 남겨(037 §3.3) 이 문장들이 없어 대조표의
# '동의 관리' 행 '이전 판' 칸을 여기와 맞춘다(얼려 둔 글자). 079(v10) — 저장 규칙과 상자 아래 저장값 문장(국외 이전 칸의 거부 방법은
# 사본에 남아 사본과 맞춘다)
PREVIOUS_CONSENT_TEXT: tuple[str, ...] = (
    "세 가지에 모두 동의하고 [선택한 대로 저장]을 누른 경우에만 Clarity·Google Analytics와 KimpTrack 영역 통계·화면 이동 기록이 돕니다 — "
    "Clarity는 셋이 다 있어야 돌아가므로 하나라도 빠지면 거부로 저장합니다.",
    "하나라도 빠진 채 저장하거나 [모두 거부]·[동의 철회]를 누르면 denied를 저장하고(저장하지 못하면 값을 지워 정하지 않은 상태로 "
    "돌립니다) 판을 지우며, 이 사이트의 Clarity 쿠키 _clck·_clsk와 Google Analytics 쿠키 _ga·_ga_*를 지웁니다.",
)
# 079 §3.4 — 국외 이전 칸 '거부 방법·절차·효과' 의 첫 문장(동의 창에서 [모두 거부]가 빠져 버튼 이름 없이) — 이전 판, 이 판
TRANSFER_REFUSAL = (
    "이 칸에 체크하지 않거나 [모두 거부]를 누르면 이전되지 않습니다.",
    "이 칸에 체크하지 않으면 이전되지 않습니다.",
)
# 079 §3.3 — 상자 아래 저장값 문단에서 바뀐 문장(이 판) — 대조표 '동의 관리 — 저장값' 행의 '이 판' 칸과 같은 글자
STORAGE_RULE = (
    "여기서나 랜딩·대시보드에 뜨는 안내에서 하나라도 빠진 채 저장하면 later와 다시 물을 시각 kt.analytics.until(저장한 때부터 24시간 뒤)을 "
    "저장하고 판을 지웁니다 — 그 안내의 [하루 동안 안 보기]도 같은 값을 저장하며, 그동안은 분석하지 않다가 24시간이 지나면 다시 묻습니다. "
    "[모두 거부]·[동의 철회]를 누르면 denied와 그때 안내의 판 kt.analytics.v를 저장하고, 그 뒤로는 다시 묻지 않습니다. 저장하지 못하면 "
    "값을 지워 정하지 않은 상태로 돌립니다. 여기서 하나라도 빼고 저장하거나 [모두 거부]·[동의 철회]를 누르면 이 사이트의 Clarity 쿠키 "
    "_clck·_clsk와 Google Analytics 쿠키 _ga·_ga_*를 지웁니다. 이 판 전에 판 없이 저장된 denied(예전 안내의 [모두 거부]나 여기서 누른 "
    "[모두 거부]·[동의 철회])는 정하지 않은 것으로 보고 다시 묻습니다."
)
# 079 §3.4 — 7절 저장 장치 목록의 localStorage 줄(이전 판, 이 판)
LOCAL_STORAGE_LINE = (
    "localStorage kt.analytics — 화면 분석 동의(granted)·거부(denied) 선택, kt.analytics.v — 동의한 안내의 판, 둘 다 서버로 가지 않음",
    "localStorage kt.analytics — 화면 분석 동의(granted)·하루 동안 묻지 않음(later)·거부(denied) 선택, kt.analytics.v — 동의하거나 "
    "거부한 안내의 판, kt.analytics.until — 다시 물을 시각, 셋 다 서버로 가지 않음",
)
# 051 §3.3-4·060 §3.3-4 — '수집·이용' 칸 끝 한 줄(화면 이용 통계는 Microsoft 에 가지 않고 국내에) — 033 의 동의 창에도 같은 글
COLLECT_NOTE = "화면 이용 통계와 화면 이동 기록은 Microsoft·Google에 가지 않고 국내(AWS 서울 리전)의 KimpTrack 서버에 둡니다."
# 075 — 세 칸의 항목에 Clarity 항목 뒤로 더한 Google Analytics 항목(033 의 동의 창에도 같은 글), 받는 자의 보유 기간
GA_ITEMS = (
    "Google Analytics: 페이지 주소(검색어는 뺌)·페이지 제목·이전 페이지 주소, 페이지를 본 일·끝 가까이(90%) 내려 본 일·"
    "밖으로 나가는 링크를 누른 일·머문 시간, 기기 종류·브라우저·운영체제·화면 크기·언어, IP 주소(대략의 나라·도시 추정에 쓰고 "
    "저장하지 않음), Google Analytics가 브라우저와 방문을 알아보는 쿠키 _ga·_ga_*"
)
GA_PERIOD = (
    "Google Analytics의 기록은 2개월(사람을 가리키지 않는 합계 보고서는 기간 없이 남음)"
)
# 060 §3.3-3 — 수집·이용 칸 항목 끝·2절이 함께 적는 KimpTrack 서버의 새 항목(요소·떠날 때 보던 곳·깊이)
ELEMENT_ITEMS = (
    "영역 안의 요소(단추·링크·탭·입력칸·표의 코인 행 같은 것 — 입력한 글자·화면의 숫자는 빼고)별 클릭 수·마우스를 올려 둔 "
    "시간·화면에 보인 시간, 페이지를 떠날 때 보던 영역과 그 페이지에서 내려온 깊이"
)
# 076 §3.3 — 수집·이용 칸 항목 끝(KimpTrack 서버 통계 뒤)의 화면 이동 기록 — 033 의 동의 창에도 같은 글
REPLAY_ITEMS = (
    "페이지를 열 때마다 화면 이름·기기 종류·화면 크기, 마우스·손가락의 위치와 움직임, 누른 위치와 그곳의 영역 이름, 스크롤 위치, "
    "가장 크게 보던 영역, 탭을 숨기거나 다시 연 일·대시보드 탭을 바꾼 일과 그 시각, IP 주소(입력한 글자·누른 키·화면의 글자와 숫자는 빼고)"
)
# §3.3 — 철회 뒤 이미 보낸 기록을 바로 지우는 길(법 제37조 제3항, 절차는 033 런북)과 그 한계. 상자 아래 문단·3절에 따로
INQUIRY = '<a class="link" href="https://go.microsoft.com/fwlink/?linkid=2126612">개인정보 문의</a>'
DELETION = "운영자가 Clarity 프로젝트의 기록을 통째로 지우고(모든 방문자의 기록이 함께 지워집니다) 새 프로젝트로 다시 시작합니다"
MICROSOFT_KEEPS = (
    "Microsoft가 자기 목적에 쓰는 정보는 이것으로 지워지지 않으며 Microsoft에 직접 요청할 수 있습니다"
    f"({INQUIRY})"
)
# §3.3 — 상태 줄의 다섯 글자(스크립트가 그린다). 079 §3.3 — 아직 안 지난 later 는 '하루 동안 묻지 않음 — <다시 물을 날짜·시각 KST>
# 뒤 다시 물음'
STATES = ("정하지 않음", "동의함", "거부함", "GPC로 거부", "저장할 수 없음")
LATER_PREFIX = "하루 동안 묻지 않음 — "
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
        "(선택) 화면 이용 기록을 Microsoft·Google에 제공하는 데 동의합니다",
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
# 칸 체크·버튼 클릭·다른 탭의 storage 이벤트(키 하나·저장소 비우기) 뒤의 저장값·판·다시 물을 시각·쿠키·상태 글자·보이는·체크된·막힌
# 요소를 JSON 으로 낸다. 시계는 CLOCK 에 멈춰 있다(079 — later 의 24시간과 상태 줄의 KST 시각). 라이브러리 없음.
CLOCK = 1_800_000_000_000  # 2027-01-15 08:00 UTC = 17:00 KST
LATER_MS = 86_400_000
LATER = str(CLOCK + LATER_MS)
LATER_STATE = "하루 동안 묻지 않음 — 1월 16일 17:00 뒤 다시 물음"
HARNESS = (
    r"""
const [script, cases] = JSON.parse(require('fs').readFileSync(0, 'utf8'))
const KEY = 'kt.analytics'
const VKEY = 'kt.analytics.v'
const UKEY = 'kt.analytics.until'
const clock = """
    + str(CLOCK)
    + r"""
class FakeDate extends Date { static now() { return clock } }
const out = cases.map((c) => {
  const data = new Map()
  if (c.stored !== undefined) data.set(KEY, c.stored)
  if (c.v !== undefined) data.set(VKEY, c.v)
  if (c.until !== undefined) data.set(UKEY, c.until)
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
  new Function('window', 'document', 'navigator', 'location', 'Date', script)(
    window, document, navigator, { hostname: 'kimptrack.com' }, FakeDate)
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
    until: data.has(UKEY) ? data.get(UKEY) : null,
    cookies: [...jar].sort(), writes, state: el('an-value').textContent,
    shown: ids((e) => !e.hidden), checked: ids((e) => e.checked), disabled: ids((e) => e.disabled),
  }
})
process.stdout.write(JSON.stringify(out))
"""
)


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
    "2026-10-16": "7f7b822e5c299c40db5516b773d8a3007a9a39b9978dde07181653cb8f43e2a2",  # 076 — 화면 이동 기록(14일·IP)
    # 078 — 같은 날 두 번째 판: 화면 이동 기록을 서비스를 끝낼 때까지(수집·이용 칸 보유 기간)
    "2026-10-16-2": "b83a3bc62e98cc2fa0163589d24eb9c3801d1d7c912e51f129120625bc1a9a7a",
    # 075 — Google Analytics(받는 자에 Google LLC, 세 칸의 항목·목적·보유 기간)
    "2026-10-17": "9e3e5fc026394d9a13010ccbec89428dcf53ffbf545d6cc4cbe4d9090b2ffb8b",
    # 079 — 같은 날 두 번째 판: 국외 이전 칸 거부 방법에서 [모두 거부]를 뺌(동의 창에 그 버튼이 없다 — 받는 자·항목·목적·보유 기간은 그대로)
    "2026-10-17-2": "c6b8a8e81e75e2c6c39828ee3980c8a1ebd8d71968f4bf9c7431e37020e4dfcd",
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
# (이름, 경우, 뒤의 저장값, 뒤의 판, 뒤의 다시 물을 시각, 상태 글자, 보이는 상태 요소, 쿠키를 지웠나) — 경우: stored·v·until 처음 값
# (없으면 키 없음), gpc, fail(get·set·remove 예외, set:<키> 는 그 키만), steps(check:·uncheck:<칸 id> · click:<버튼 id>
# · other:<키>=<다른 탭이 쓴 값> · clear: 다른 탭이 저장소를 비움). 079 — 하나라도 빠진 저장은 later(지금 + 24시간)·판 지움,
# [모두 거부]·[동의 철회]는 denied + 지금 판, 판 없는 예전 denied·지났거나 없거나 숫자가 아니거나 24시간보다 먼 later 는 정하지 않음
# fmt: off
CONSENT_CASES = [
    ("값 없음", {}, None, None, None, "정하지 않음", UNDECIDED, False),
    ("지금 판의 동의", {"stored": "granted", "v": NOTICE}, "granted", NOTICE, None, "동의함", AGREED, False),
    ("예전 판의 동의 — 다시 묻는다", {"stored": "granted", "v": OLD}, "granted", OLD, None, "정하지 않음", UNDECIDED, False),
    ("판 없는 동의", {"stored": "granted"}, "granted", None, None, "정하지 않음", UNDECIDED, False),
    ("판 있는 거부", {"stored": "denied", "v": NOTICE}, "denied", NOTICE, None, "거부함", UNDECIDED, False),
    ("예전 판의 거부도 거부 — 다시 묻지 않는다", {"stored": "denied", "v": OLD}, "denied", OLD, None, "거부함", UNDECIDED, False),
    ("판 없는 예전 거부 — 다시 묻는다", {"stored": "denied"}, "denied", None, None, "정하지 않음", UNDECIDED, False),
    ("하루 동안 묻지 않는 중", {"stored": "later", "until": LATER}, "later", None, LATER, LATER_STATE, UNDECIDED, False),
    ("지난 later — 다시 묻는다", {"stored": "later", "until": str(CLOCK)}, "later", None, str(CLOCK), "정하지 않음", UNDECIDED, False),
    ("시각 없는 later", {"stored": "later"}, "later", None, None, "정하지 않음", UNDECIDED, False),
    ("숫자가 아닌 시각", {"stored": "later", "until": "soon"}, "later", None, "soon", "정하지 않음", UNDECIDED, False),
    ("24시간보다 먼 시각", {"stored": "later", "until": str(CLOCK + LATER_MS + 1)}, "later", None, str(CLOCK + LATER_MS + 1), "정하지 않음", UNDECIDED, False),
    ("그 밖의 값", {"stored": "off"}, "off", None, None, "정하지 않음", UNDECIDED, False),
    ("GPC 가 동의보다 앞선다", {"stored": "granted", "v": NOTICE, "gpc": True}, "granted", NOTICE, None, "GPC로 거부", {"an-state", "an-gpc"}, False),
    ("읽기 예외", {"fail": ["get"]}, None, None, None, "저장할 수 없음", {"an-state", "an-nostore"}, False),
    ("세 칸 모두 체크하고 저장", {"steps": [*ALL, "click:an-save"]}, "granted", NOTICE, None, "동의함", AGREED, False),
    ("한 칸 빠지면 하루 동안 묻지 않음", {"steps": [*ALL[:2], "click:an-save"]}, "later", None, LATER, LATER_STATE, UNDECIDED, True),
    ("칸 없이 저장", {"steps": ["click:an-save"]}, "later", None, LATER, LATER_STATE, UNDECIDED, True),
    ("[모두 거부] — 판과 함께", {"steps": [*ALL, "click:an-deny"]}, "denied", NOTICE, None, "거부함", UNDECIDED, True),
    ("하루 동안 묻지 않는 중의 [모두 거부] — 시각을 지운다", {"stored": "later", "until": LATER, "steps": ["click:an-deny"]}, "denied", NOTICE, None, "거부함", UNDECIDED, True),
    ("판 없는 예전 거부의 [모두 거부] — 판을 더한다", {"stored": "denied", "steps": ["click:an-deny"]}, "denied", NOTICE, None, "거부함", UNDECIDED, True),
    ("거부 뒤 세 칸 동의", {"stored": "denied", "v": NOTICE, "steps": [*ALL, "click:an-save"]}, "granted", NOTICE, None, "동의함", AGREED, False),
    ("하루 동안 묻지 않는 중의 세 칸 동의 — 시각을 지운다", {"stored": "later", "until": LATER, "steps": [*ALL, "click:an-save"]}, "granted", NOTICE, None, "동의함", AGREED, False),
    ("예전 판 동의를 지금 판으로", {"stored": "granted", "v": OLD, "steps": [*ALL, "click:an-save"]}, "granted", NOTICE, None, "동의함", AGREED, False),
    ("[동의 철회] — 판과 함께", {"stored": "granted", "v": NOTICE, "steps": ["click:an-withdraw"]}, "denied", NOTICE, None, "거부함", UNDECIDED, True),
    ("동의한 칸 하나를 빼고 저장 — 끄고 하루 뒤 다시 묻는다", {"stored": "granted", "v": NOTICE, "steps": ["uncheck:an-transfer", "click:an-save"]}, "later", None, LATER, LATER_STATE, UNDECIDED, True),
    ("쓰기 예외의 저장 — 실제 값 그대로", {"fail": ["set"], "steps": [*ALL, "click:an-save"]}, None, None, None, "정하지 않음", UNDECIDED, False),
    ("판을 못 쓰면 동의도 쓰지 않는다", {"fail": ["set:kt.analytics.v"], "steps": [*ALL, "click:an-save"]}, None, None, None, "정하지 않음", UNDECIDED, False),
    ("시각을 못 쓰면 later 도 쓰지 않는다", {"fail": ["set:kt.analytics.until"], "steps": ["click:an-save"]}, None, None, None, "정하지 않음", UNDECIDED, True),
    ("later 를 못 쓰면 값을 지운다", {"stored": "denied", "fail": ["set:kt.analytics"], "steps": ["click:an-save"]}, None, None, LATER, "정하지 않음", UNDECIDED, True),
    ("쓰기 예외의 일부 저장 — 동의를 지운다", {"stored": "granted", "v": NOTICE, "fail": ["set"], "steps": ["uncheck:an-transfer", "click:an-save"]}, None, None, None, "정하지 않음", UNDECIDED, True),
    ("쓰기 예외의 [동의 철회] — 값과 판을 지운다", {"stored": "granted", "v": NOTICE, "fail": ["set"], "steps": ["click:an-withdraw"]}, None, None, None, "정하지 않음", UNDECIDED, True),
    ("denied 를 못 쓰면 쓴 판도 지운다", {"stored": "granted", "v": NOTICE, "fail": ["set:kt.analytics"], "steps": ["click:an-withdraw"]}, None, None, None, "정하지 않음", UNDECIDED, True),
    ("지우기도 예외 — 동의가 남았다고 알린다", {"stored": "granted", "v": NOTICE, "fail": ["set", "remove"], "steps": ["click:an-withdraw"]}, "granted", NOTICE, None, "동의함", AGREED | {"an-stuck"}, True),
    ("다른 탭의 동의", {"steps": [f"other:kt.analytics.v={NOTICE}", "other:kt.analytics=granted"]}, "granted", NOTICE, None, "동의함", AGREED, False),
    ("다른 탭의 철회", {"stored": "granted", "v": NOTICE, "steps": [f"other:kt.analytics.v={NOTICE}", "other:kt.analytics=denied"]}, "denied", NOTICE, None, "거부함", UNDECIDED, False),
    ("다른 탭의 하루 동안 안 보기", {"steps": [f"other:kt.analytics.until={LATER}", "other:kt.analytics=later"]}, "later", None, LATER, LATER_STATE, UNDECIDED, False),
    ("다른 탭이 판을 바꿈", {"stored": "granted", "v": NOTICE, "steps": [f"other:kt.analytics.v={OLD}"]}, "granted", OLD, None, "정하지 않음", UNDECIDED, False),
    ("다른 탭이 저장소를 비움", {"stored": "granted", "v": NOTICE, "steps": ["clear:"]}, None, None, None, "정하지 않음", UNDECIDED, False),
]
# fmt: on
# §3.4-6 절 — 받는 곳마다 법 제28조의8 제2항 다섯 가지(동의 상자의 국외 이전 칸과 같은 이름) + 근거
TRANSFER_TERMS = CONSENT_CELLS[2][2] | {"근거"}
# 079 §3.4 — 맨 위 변경 안내 상자가 말하는 것(같은 날 두 번째 판의 시행 문장, 바뀌는 것 하나 — 동의 창의 버튼과 저장 규칙, 판 없는
# 예전 거부를 다시 묻는다, 받는 자·항목·목적·보유 기간은 그대로, 동의 상자의 글이 바뀌어 동의를 다시 묻는다)
CHANGE_NOTICE_SAYS = (
    "같은 날 먼저 게시한 판(2026년 10월 17일 판)을 바꿉니다",
    "바뀌는 것:",
    "[모두 동의]·[선택한 대로 저장]·[하루 동안 안 보기]",
    "[모두 거부]가 빠집니다",
    "아래 “화면 분석 동의 관리”의 [모두 거부]·[동의 철회]로 합니다",
    "하나라도 빼고 저장하거나 [하루 동안 안 보기]를 누르면 분석하지 않고, 24시간 동안 묻지 않다가 다시 묻습니다",
    "아래에서 [모두 거부]·[동의 철회]를 누르면 다시 묻지 않습니다",
    "이 판 전에 저장한 거부는 안내의 판 없이 저장되어 있어 정하지 않은 것으로 보고 다시 묻습니다",
    "받는 자·항목·목적·보유 기간은 그대로입니다",
    "동의 상자의 글이 바뀌어 동의 안내의 판을 올립니다",
    "이미 동의한 방문자에게도 다시 묻고",
    "Clarity·Google Analytics·화면 이용 통계·화면 이동 기록",
    "12절 대조표",
)
# 079 — 변경 안내의 링크 둘: 바로 앞 판(10월 17일 먼저 게시한 판 — v9)과 대조. 그 앞 판들은 12절 이력에서 잇는다
CHANGE_NOTICE_LINKS = (
    'href="/privacy-20261017.html"',
    'href="#s12"',
)
# 060 §3.3-8 — 12절 규칙(7일 전 게시 약속과 그 단서는 지웠다)·시행 문장(067 — 그 전날까지는 앞 판)·이력 첫 항목
CHANGE_RULE = (
    "이 방침을 바꿀 때는 시행일과 바뀐 문장의 전후 대조를 이 페이지에 알리고 이전 판을 남깁니다. "
    "동의를 받아 하는 처리가 늘면 바뀐 안내로 다시 동의를 받고, 그 동의를 받은 때부터 합니다."
)
# 079 — 같은 날 두 번째 판(060 의 v4·069·078 꼴): 머리 시행일 뒤·12절 시행 문장·이력 첫 항목에 차례
EFFECTIVE_SENTENCE = (
    "부터 시행하며(이날 두 번째 판), 같은 날 먼저 게시한 판을 바꿉니다."
)
NTH_TODAY = "(이날 두 번째 판)"
V10_HISTORY = (
    "(두 번째 판) — 화면 분석 동의 안내: 버튼을 [모두 동의]·[선택한 대로 저장]·[하루 동안 안 보기]로·하나라도 빼고 저장하면 "
    "24시간 뒤 다시 물음·판 없이 저장된 예전 거부도 다시 물음·동의 안내 판 올림"
)
# 075 — 같은 날 먼저 게시한 판(v9)의 이력 항목(다음 판에서도 남는다 — 사본 `privacy-20261017.html`)
V9_HISTORY = (
    "2026년 10월 17일 — 방문 통계(Google Analytics, 동의한 방문자만 — 본 페이지·머문 시간·들어온 길, Google LLC(미국)에 제공·이전, "
    "2개월)·동의 안내 판 올림"
)
# 078 — 같은 날 두 번째 판(v8)의 이력 항목(다음 판에서도 남는다 — 사본 `privacy-20261016-2.html`)
V8_HISTORY = "2026년 10월 16일(두 번째 판) — 화면 이동 기록: 보유 기간을 서비스를 끝낼 때까지로·두는 곳에 파일 저장소(Amazon S3)·동의 안내 판 올림"
# 076 — 같은 날 먼저 게시한 판(v7)의 이력 항목(다음 판에서도 남는다 — 사본 `privacy-20261016.html`). 그 판이 알린 14일 그대로
V7_HISTORY = (
    "2026년 10월 16일 — 화면 이동 기록(KimpTrack 서버, 동의한 방문자만 — 마우스 위치·누른 곳·스크롤과 IP 주소, 14일, 관리자 화면에서 "
    "다시 보기)·동의 안내 판 올림"
)
# 069 — 같은 날 두 번째 판(v6)의 이력 항목(다음 판에서도 남는다 — 사본 `privacy-20261014-2.html`)
V6_HISTORY = "2026년 10월 14일(두 번째 판) — 서버 접속 기록: IP 주소 전체·관리자 화면에서 IP 주소별로 보기(봇 판단·대략의 위치·망 사업자·DNS 이름)"
# 067 — 같은 날 먼저 게시한 판(v5)의 이력 항목(다음 판에서도 남는다 — 사본 `privacy-20261014.html`)
V5_HISTORY = "2026년 10월 14일 — 서버 접속 기록: 관리자 화면에서 가린 IP 대역별로 보기(보안·장애 확인)"
# 060 — 같은 날 두 번째 판(v4)의 이력 항목(다음 판에서도 남는다 — 사본 `privacy-20261009-2.html`)
V4_HISTORY = "2026년 10월 9일(두 번째 판) — 서버 접속 기록 쓰임새를 남은 기록 전체에·화면 이용 통계를 요소까지·바꾸는 방법"
# 067 §3.4 — v5 의 2절 서버 접속 기록 (라): 관리자 화면의 IP 대역별 보기(가린 그대로·관리자만) — 069 가 바꿨다(대조표 '이전 판')
ACCESS_BANDS = (
    "(라) 운영자는 보안(봇·스캐너 대응)과 장애 확인을 위해 관리자 화면에서 끝자리를 가린 IP 대역별로 접속 기록"
    "(시각·연 경로(쿼리 뺌)·응답·요청을 보낸 쪽의 종류·브라우저 정보에서 뽑은 기기·OS·브라우저 이름·들어온 출처의 사이트 주소)을 "
    "모아 보거나 시간순으로 볼 수 있습니다. 이 화면은 관리자 로그인 뒤에만 열리고, 가린 IP를 되돌리거나 밖으로 보내지 않습니다."
)
# 069 §3.7 — 2절 서버 접속 기록 (라): IP 주소별 보기·봇 판단·대략의 위치·망 사업자·역방향 DNS(관리자만·밖으로 보내지 않음)
ACCESS_ADDRS = (
    "(라) 운영자는 보안(봇·스캐너 대응)과 장애 확인을 위해 관리자 화면에서 IP 주소별로(이 판 시행 전 기록과 대역 보기는 끝자리를 "
    "가린 IP 대역별로) 접속 기록(시각·연 경로(쿼리 뺌)·응답·요청을 보낸 쪽의 종류·브라우저 정보에서 뽑은 기기·OS·브라우저 이름·"
    "들어온 출처의 사이트 주소)을 모아 보거나 시간순으로 볼 수 있습니다. 또 요청 수·연 경로·응답·스크립트 신호·망 같은 근거로 자동 "
    "요청(봇)인지 판단하고, 공개 IP 위치 자료(DB-IP Lite — 서버가 받아 두고 찾습니다)로 대략의 나라·지역·도시와 망 사업자 이름을, "
    "역방향 DNS로 그 IP 주소에 붙은 이름(그 IP 주소만 DNS에 물어 찾습니다)을 확인할 수 있습니다. 위치는 대략이며 주소(번지)는 알 수 "
    "없습니다. 이 화면은 관리자 로그인 뒤에만 열리고, 모은 기록을 밖으로 보내지 않습니다."
)
# 069 §3.7 — 본문 절마다 IP 전체 저장의 사실(대조표 밖)
FULL_IP_FACTS = {
    "glance": ("IP 주소는 전체를 남기고 검색어는 지운 채",),
    "s2": (
        "시각, IP 주소 전체, 접속 포트",
        "두 곳 모두 IP 주소 전체가 담기고",
        "이 판을 시행하기 전에 남은 줄에는 IP 앞부분만 있습니다",
        "(라)의 관리자 화면은 망 사업자 이름을 보입니다",
    ),
    "s8": ("쓰던 IP 주소와 대략의 시각을 알려 주어야",),
    "s9": ("IP 주소는 전체를 남기고 관리자 로그인 뒤에서만 봅니다",),
}
# v5 까지의 문장 — 본문(대조표 밖)에 남으면 거짓이다
NOT_FULL_IP = (
    "IP 끝자리와 검색어를 지운 채",
    "IPv4는 마지막 마디",
    "IP 끝자리를 지워 남기므로",
    "IP 앞부분만, 검색어",
)
# 067 §3.4 — 1절 목적의 서버 접속 기록 줄 끝
SECURITY_PURPOSE = "자동 요청(봇)의 규모 파악(서버 접속 기록) · 보안(봇·스캐너 대응)"
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
    "glance": ("화면 영역별 이용 통계", "하루 합계", "모두 동의한 경우에만"),
    "consent-title": (
        "화면의 영역과 요소(단추·링크 같은 것)별로 보인 시간·클릭 수·마우스를 올려 둔 시간과 떠날 때 보던 곳을 하루 합계로",
        "모든 페이지(랜딩·대시보드·처리방침·검색어 페이지)",
        "Clarity·Google Analytics와 KimpTrack 영역 통계·화면 이동 기록이 돕니다",
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
        "Redis에는 화면 이용 통계의 날짜별 합계와 화면 이동 기록(최근 14일 몫)",
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
# 10월 11일 판은 시행 전에 10월 9일 판(v3)으로 바뀌었고, 그 v3 은 같은 날 두 번째 판(v4 — 060)으로, v4 는 v5(067)로 바뀌었다
ARCHIVED_TEXT = {
    "privacy-20261001.html": "40976545d4ba5b8241e2107245a54d733767b0d40c81ca8ccc351fb6ebd795e3",
    "privacy-20261011.html": "a4d5b104cdb9af3c1a8e26c3499dd2907eaaf1f6af8c73a6bb2f586edace3099",
    "privacy-20261009.html": "5b35b358781a1d97869dd2d577493cf1997bdf3a8ef4e0fa6f1b69f64978fe23",
    # 067 — 같은 날 두 번째 판(v4 — 060)
    "privacy-20261009-2.html": "16d05bd15d313fb5fd620de7f94bb4ba3951cf9b88faac38f4d902d8e0cc52d6",
    # 069 — 2026년 10월 14일 먼저 게시한 판(v5 — 067)
    "privacy-20261014.html": "10964bbe2c0cb783021d3cac9a283bed58bc1f5a8201bb235b02215ac3ce2738",
    # 076 — 2026년 10월 14일 두 번째 판(v6 — 069)
    "privacy-20261014-2.html": "91d4c67d02af4c5b8500091be57abc7bece2e35712e6c6343066cb1817adc9c5",
    # 078 — 2026년 10월 16일 먼저 게시한 판(v7 — 076)
    "privacy-20261016.html": "b103b08cebd06fa356d769e54e17ccc1de07719ae7b6604c0b5bae7007c632bd",
    # 075 — 2026년 10월 16일 두 번째 판(v8 — 078)
    "privacy-20261016-2.html": "c47bf94483846faf9c8b8b6ef5f8312f2e87e7cabd8da69fef9236a6b6cdc9ba",
    # 079 — 2026년 10월 17일 먼저 게시한 판(v9 — 075)
    "privacy-20261017.html": "ecf9d1b7d79a77128b7ebfd8a8029c193e0486229514398423528d3a635afa76",
}
# 사본마다 그 판의 동의 안내 판 — 10월 11일 판은 시행 전에 바뀌어 안내 판을 올리지 않았다
ARCHIVED_NOTICE = {
    "privacy-20261001.html": "2026-10-01",
    "privacy-20261011.html": "2026-10-01",
    "privacy-20261009.html": "2026-10-09",
    "privacy-20261009-2.html": "2026-10-09-2",
    "privacy-20261014.html": "2026-10-09-2",
    "privacy-20261014-2.html": "2026-10-09-2",
    "privacy-20261016.html": "2026-10-16",
    "privacy-20261016-2.html": "2026-10-16-2",
    "privacy-20261017.html": "2026-10-17",
}
# 바로 앞 판의 사본 — 대조표가 맞대는 판. 파일 이름(시행일) 순이 아니라 게시한 차례의 끝이다(060 — 10월 9일 판(v3)은
# 10월 11일 판보다 나중에 게시했다)
PREVIOUS = list(ARCHIVED_TEXT)[-1]
# 사본 이름 `privacy-<시행일>.html` — 같은 날 둘째 판부터는 `-<차례>` 를 붙인다(067 — 060 §7 의 제안). 이력 항목의 날짜 뒤
# '(두 번째 판)' 과 사본 제목의 '두 번째' 가 그 차례다
ARCHIVE_NAME = r"privacy-(\d{4})(\d{2})(\d{2})(?:-(\d+))?\.html"
ORDINALS = {"두": 2, "세": 3, "네": 4}
NTH = {n: f" {word} 번째" for word, n in ORDINALS.items()}
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
    # 인라인만 — 같은 출처 파일은 052 의 /attention.js 와 073 의 /replay.js 둘(CSP 'self' 는 그 둘을 위해 연다)
    assert [src for tag, src in page.srcs if tag == "script"] == [
        "/attention.js",
        "/replay.js",
    ]
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
    assert f"'{LATER_PREFIX}'" in script
    assert "'granted'" in script and "'denied'" in script and "'later'" in script


def test_notice_version_is_shown_and_is_the_last_digest_key() -> None:
    """지금 판은 스크립트·보이는 글자가 같고 `NOTICE_DIGESTS` 의 마지막 키다 — 판 값은 같은지만 보고 날짜와 맞대지 않는다
    (§3.3 — 033 clarity.js·052 attention.js 의 판도 같다, 060 §3.2)."""
    html, _ = _read(PUBLIC / "privacy.html")
    assert NOTICE and re.fullmatch(r"[0-9A-Za-z.-]+", NOTICE), NOTICE
    assert f'<code id="an-version">{NOTICE}</code>' in html
    assert NOTICE == list(NOTICE_DIGESTS)[-1]
    # 051 §3.2 — v3 이 판 2026-10-09 를 열었고, 060 이 같은 날 두 번째 판을 더했다(예전 판 해시는 지우지 않는다)
    assert PRIVACY_V3_EFFECTIVE in NOTICE_DIGESTS
    # 079 — 같은 날 두 번째 판(v9 의 판 2026-10-17 다음)
    assert NOTICE == "2026-10-17-2" and OLD == "2026-10-17"


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
    # 075 — GA 쿠키 _ga 와 측정 ID 에 따라 이름이 다른 _ga_<기호> 도 거부·철회 때 지운다
    cookies = ["_clck", "_clsk", "_ga", "_ga_TEST123", "other"]
    results = _run_consent_script(
        [{**case, "cookies": cookies} for _, case, *_ in CONSENT_CASES]
    )
    for (name, _, stored, version, until, state, shown, cleared), got in zip(
        CONSENT_CASES, results, strict=True
    ):
        assert (got["stored"], got["v"], got["until"], got["state"]) == (
            stored,
            version,
            until,
            state,
        ), name
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
                "cookies": ["_clck", "_ga_TEST123"],
                "steps": ["click:an-withdraw"],
            }
        ]
    )
    for name in ("_clck", "_clsk", "_ga", "_ga_TEST123"):
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
        "어느 영역·요소를 많이·적게 보고 누르는지, 어디서 떠나는지 알아 화면을 고치기, "
        "운영자가 IP 주소별로 방문의 움직임을 다시 보거나 지금 보는 방문을 따라 보며 어디서 막히는지 알아 화면을 고치기, "
        "어느 페이지를 얼마나 오래 보고 어디서 들어오는지 알아 서비스를 고치기(방문 통계)"
    )
    # 075 — 세 칸의 항목마다 Clarity 항목 바로 뒤에 Google Analytics 항목(같은 글자)
    for notes in (collect, provide, transfer):
        assert f"sessionStorage _cltk). {GA_ITEMS}" in notes["항목"] or notes[
            "항목"
        ].endswith(f"sessionStorage _cltk). {GA_ITEMS}"), notes["항목"][:40]
    assert "방문 통계 2개월(Google Analytics" in collect["보유 기간"]
    # 051 §3.3-4 — 수집·이용 칸에만 KimpTrack 서버의 통계(항목·보유 기간·칸 끝 국내 보관 한 줄). 제공·국외 이전 칸은 그대로.
    # 060 §3.3-4 — 항목 끝은 'KimpTrack 서버:' 뒤에 2절과 같은 항목(요소·떠날 때 보던 곳·깊이) — Clarity 항목 뒤에 둔다
    kimptrack = collect["항목"].split(". KimpTrack 서버: ")
    assert len(kimptrack) == 2 and "sessionStorage _cltk" in kimptrack[0]
    # 076 §3.3 — 그 뒤에 화면 이동 기록의 항목(2절과 같은 낱말 — 입력한 글자·누른 키·화면 글자는 빼고)
    stats, replay = kimptrack[1].split(". KimpTrack 서버의 화면 이동 기록: ")
    assert "1초 이상 보였는지" in stats and stats.endswith(ELEMENT_ITEMS)
    assert replay == REPLAY_ITEMS
    # 078 §3.7 — 화면 이동 기록은 서비스를 끝낼 때까지(끝내면 지움)
    assert collect["보유 기간"].endswith(
        "KimpTrack 서버: 하루 합계로만 90일, 화면 이동 기록은 서비스를 끝낼 때까지(끝내면 지움)"
    )
    assert "14일" not in collect["보유 기간"]
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
    assert provide["받는 자"] == "Microsoft Corporation(미국), Google LLC(미국)"
    assert "Microsoft Advertising" in provide["받는 자의 목적"]
    assert "Google의 광고·제품 개선에는 쓰지 않게" in provide["받는 자의 목적"]
    for period in (provide["받는 자의 보유 기간"], transfer["목적·보유 기간"]):
        assert period.endswith(GA_PERIOD), period
    assert transfer["국가·시기·방법"].startswith("미국 — ")
    for word in ("수시로", "브라우저", "전송"):
        assert word in transfer["국가·시기·방법"], word
    assert transfer["받는 자·연락처"].startswith("Microsoft Corporation, ")
    assert "Redmond" in transfer["받는 자·연락처"]
    assert INQUIRY in raw[2]["받는 자·연락처"]
    assert "Google LLC, 1600 Amphitheatre Parkway" in transfer["받는 자·연락처"]
    assert (
        'href="https://policies.google.com/privacy?hl=ko"' in raw[2]["받는 자·연락처"]
    )
    assert "Microsoft·Google 서버로" in transfer["국가·시기·방법"]
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
    # 075 — 받는 자마다 한 묶음(Microsoft·Google)
    assert len(dls) == 2 and all(_terms(dl) == PROVISION_TERMS for dl in dls)
    assert [_notes(dl)["받는 자"] for dl in dls] == [
        "Microsoft Corporation(미국)",
        "Google LLC(미국)",
    ]
    assert "제17조 제1항 제1호" in provision and "제17조 제1항 제2호" not in provision
    assert "불이익이 없습니다" in provision
    groups = re.findall(
        r"<h3>(.*?)</h3>\s*<dl[^>]*>(.*?)</dl>", _section(html, "s6"), flags=re.S
    )
    microsoft = [body for name, body in groups if name.startswith("Microsoft")]
    analytics = [body for name, body in groups if "Google Analytics" in name]
    assert len(microsoft) == 1 and len(analytics) == 1
    for group in (microsoft[0], analytics[0]):
        basis = re.search(r"<dt>근거</dt><dd>(.*?)</dd>", group).group(1)
        assert "제28조의8 제1항 제1호" in basis and "제3호" not in basis
        assert "불이익이 없습니다" in group


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
    assert "Google LLC(미국)" in block.group(1)


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
    linked = re.findall(r'href="/(privacy-\d{8}(?:-\d+)?\.html)"', now)
    assert linked, "지금 판이 이전 판으로 잇지 않는다"
    paths = sorted(PUBLIC.glob("privacy-*.html"))
    # 이력의 옛 판마다 사본이 있고, 사본마다 이력에서 잇고, 얼려 둔 글자의 값이 있다
    assert {path.name for path in paths} == set(linked) == set(ARCHIVED_TEXT)
    for path in paths:
        html, page = _read(path)
        found = re.fullmatch(ARCHIVE_NAME, path.name)
        assert found, path.name
        assert "〔" not in html and "<script" not in html, path.name
        _assert_no_external_resources(path.name, page)
        y, m, d, nth = found.groups()
        day = f"{y}-{m}-{d}"
        assert page.times and set(page.times) == {day}, path.name
        # 같은 날 둘째 판부터는 제목에 차례(067 — `-2` 는 '두 번째 판')
        nth_word = "" if nth is None else NTH[int(nth)]
        assert page.titles == [
            f"개인정보 처리방침({_korean(day)}{nth_word} 판) — KimpTrack"
        ]
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


def test_v4_constant_is_the_element_stats_day_before_this_version() -> None:
    """060 §3.1 — 요소별 통계(061)의 게이트 `PRIVACY_V4_EFFECTIVE` 는 v4 의 시행일 그대로다. v5(067 — IP 대역별 보기)는 서버
    기록의 쓰임새라 게이트·동의가 없고 시행일은 페이지 <time> 넷·sitemap 에만 있다. 접속 기록 쓰임새에는 게이트가 없다
    (`PRIVACY_V2_EFFECTIVE` 가 첫 기록보다 앞 — 사람 결정 2026-10-09)."""
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", PRIVACY_V4_EFFECTIVE)
    assert PRIVACY_V4_EFFECTIVE == "2026-10-09" < EFFECTIVE
    assert PRIVACY_V2_EFFECTIVE < FIRST_LOG_DAY < PRIVACY_V4_EFFECTIVE


def test_effective_date_ties_the_four_times_and_the_history() -> None:
    """시행일은 머리·변경 안내·12절 시행 문장·이력 첫 항목의 <time> 넷과 sitemap(아래)에 같고, 한 곳만 고치면 멈춘다 (037 §3.1)."""
    html, _ = _read(PUBLIC / "privacy.html")
    assert EFFECTIVE >= PRIVACY_V3_EFFECTIVE
    times = re.findall(r'<time datetime="([^"]*)">([^<]*)</time>', html)
    assert times == [(EFFECTIVE, _korean(EFFECTIVE))] * 4
    tag = f'<time datetime="{EFFECTIVE}">'
    # 같은 날 둘째 판이면 머리 시행일 뒤에 차례를 적는다(060 꼴 — 069·078)
    assert (
        f'<p class="meta">시행일 {tag}{_korean(EFFECTIVE)}</time>{NTH_TODAY}</p>'
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
    assert _text(items[0]) == f"{_korean(EFFECTIVE)}{V10_HISTORY}"
    # 같은 날 먼저 게시한 판(v9 — 075)의 항목은 차례 없이 그 판의 사본 링크(079 — `privacy-20261017.html`)
    v9 = [i for i in items[1:] if _text(i).startswith(V9_HISTORY)]
    assert len(v9) == 1 and 'href="/privacy-20261017.html">그 판 보기</a>' in v9[0]
    # 같은 날 두 번째 판(v8 — 078)의 항목은 날짜 뒤 차례와 그 판의 사본 링크(075 — `-2`)
    v8 = [i for i in items[1:] if _text(i).startswith(V8_HISTORY)]
    assert len(v8) == 1 and 'href="/privacy-20261016-2.html">그 판 보기</a>' in v8[0]
    # 같은 날 먼저 게시한 판(v7 — 076)의 항목은 차례 없이 그 판의 사본 링크(`privacy-20261016.html`) — 그 판이 알린 14일 그대로
    v7 = [i for i in items[1:] if _text(i).startswith(V7_HISTORY)]
    assert len(v7) == 1 and 'href="/privacy-20261016.html">그 판 보기</a>' in v7[0]
    # 같은 날 두 번째 판(v6 — 069)의 항목은 날짜 뒤 차례와 그 판의 사본 링크(076 — `-2`)
    v6 = [i for i in items[1:] if _text(i).startswith(V6_HISTORY)]
    assert len(v6) == 1 and 'href="/privacy-20261014-2.html">그 판 보기</a>' in v6[0]
    # 같은 날 먼저 게시한 판(v5 — 067)의 항목은 차례 없이 그 판의 사본 링크(`privacy-20261014.html`)
    v5 = [i for i in items[1:] if _text(i).startswith(V5_HISTORY)]
    assert len(v5) == 1 and 'href="/privacy-20261014.html">그 판 보기</a>' in v5[0]
    # 같은 날 두 번째 판(v4 — 060)의 항목은 날짜 뒤 차례와 그 판의 사본 링크(067 — `-2`)
    v4 = [i for i in items if _text(i).startswith(V4_HISTORY)]
    assert len(v4) == 1 and 'href="/privacy-20261009-2.html">그 판 보기</a>' in v4[0]
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
    # 지금 판을 뺀 항목마다 그 판의 사본 링크 — 같은 날 둘째 판부터는 이름에 `-<차례>`(067)
    for item in items[1:]:
        y, m, d, word = re.match(
            r"(\d{4})년 (\d{1,2})월 (\d{1,2})일(?:\((\S+) 번째 판\))?", _text(item)
        ).groups()
        nth = "" if word is None else f"-{ORDINALS[word]}"
        assert f'href="/privacy-{y}{int(m):02d}{int(d):02d}{nth}.html"' in item, item


def test_change_notice_comes_first_and_names_what_changes() -> None:
    """맨 위 변경 안내 — 머리 시행일 줄 바로 아래, 소개 문단 앞. 시행 문장·바뀌는 것 하나·동의를 다시 물음·링크 둘 (067 §3.4·079 §3.4)."""
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
        f"변경 안내 이 판은 {_korean(EFFECTIVE)}부터 시행합니다 — 같은 날 먼저 게시한 판("
    )
    # 바뀌는 것은 하나라 번호 목록이 없다. 동의를 다시 묻는다는 문장은 바뀌는 것 뒤
    assert "<li>" not in notice
    assert text.index("보유 기간은 그대로입니다") < text.index(
        "이미 동의한 방문자에게도 다시 묻고"
    )


def test_body_sections_state_the_v2_facts() -> None:
    """본문 절마다 v2 의 사실 — 변경 안내·대조표가 아닌 곳에서. 지난 판 문장은 대조표에만 남는다 (037 §3.2)."""
    html, _ = _read(PUBLIC / "privacy.html")
    for label, words in V2_FACTS.items():
        text = _text(_without_diff(_section(html, label)))
        for word in words:
            assert word in text, (label, word)
    # (라) — 관리자 화면의 IP 주소별 보기(069 §3.7). 060 이 지운 시작 날짜 문장은 없다(남아 있는 기록 전체를 쓴다).
    # '(라)' 는 문단 머리와 (나)가 가리키는 곳 두 번
    s2 = _text(_section(_without_diff(html), "s2"))
    assert "(다)" in s2 and ACCESS_ADDRS in s2 and s2.count("(라)") == 2
    assert "이 쓰임새를 시작하는 날" not in s2
    assert SECURITY_PURPOSE in _text(_section(_without_diff(html), "s1"))
    # (다) — 062 가 짝에 붙이는 그날 첫·마지막 페이지 이름과 페이지 수(게이트를 당겨 v4 시행일부터 돈다 — 060 §7)
    assert ACCESS_FLOW_ITEMS in _text(_section(_without_diff(html), "s2"))
    outside = _without_diff(html)
    assert "24시간 요약" not in outside
    assert "90일이 지나면 지웁니다" not in outside
    visible = _text(_body(html))
    for word in PLAIN_ONLY:
        assert word not in visible, word


def test_body_sections_state_the_full_ip_facts() -> None:
    """069 §3.7 — 본문 절마다 IP 전체 저장의 사실(대조표 밖), v5 까지의 '가린 IP' 문장은 대조표에만. 1절 목적은 067 의 보안
    목적 그대로(바꾸지 않음)."""
    html, _ = _read(PUBLIC / "privacy.html")
    body = _without_diff(html)
    for label, words in FULL_IP_FACTS.items():
        text = _text(_section(body, label))
        for word in words:
            assert word in text, (label, word)
    visible = _text(_body(body))
    for gone in NOT_FULL_IP:
        assert gone not in visible, gone
    assert SECURITY_PURPOSE in _text(_section(body, "s1"))


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
    (v3,) = [
        _text(item)
        for item in _history(html)
        if _text(item).startswith(f"{_korean(PRIVACY_V3_EFFECTIVE)} — ")
    ]
    assert OLD_NAME in v3
    # 7절 저장 장치 목록 — 영역 통계는 쿠키·브라우저 저장소를 쓰지 않는다 (051 §3.3-8). 075 가 GA 쿠키 둘을 더했다
    devices = re.search(r"<ul>(.*?)</ul>", _section(body, "s7"), flags=re.S).group(1)
    assert len(re.findall(r"<li>", devices)) == 7 and "영역" not in devices


# 078 §3.7 — 2절 화면 이동 기록의 두는 곳(보유 기간·저장 장소)과 3절 파기 문장
REPLAY_STORE = (
    "AWS 서울 리전의 KimpTrack 서버 저장소(Redis — 최근 14일 몫)와 같은 리전의 파일 저장소(Amazon S3)에 서비스를 끝낼 때까지 "
    "두고, 서비스를 끝내면 지웁니다. Microsoft를 포함해 다른 곳으로 보내지 않고, 국외로 옮기지 않습니다."
)
REPLAY_DESTRUCTION = "화면 이동 기록은 서비스를 끝내면 운영자가 서버 저장소(Redis)와 파일 저장소(Amazon S3)에서 모두 지웁니다."
# 078 §3.7 — 화면 이동 기록의 보유 기간을 말하는 본문 문장(대조표 밖)이 쓰던 '14일' 꼴 — 남으면 거짓이다. Redis 가 최근 14일 몫만
# 쥔다는 저장 장소 낱말('최근 14일 몫')은 보유 기간이 아니라 뺀다
REPLAY_OLD_PERIODS = (
    "14일 동안 모아",
    "14일 동안 둡니다",
    "14일 동안 두고",
    "마지막 기록부터 14일",
    "화면 이동 기록(14일)",
    "보냄(14일)",
    "화면 이동 기록은 14일",
    "14일이 지나면 저장소가 스스로 지웁니다",
)
# 076 §3.2·078 §3.7 — 본문 절마다 화면 이동 기록(KimpTrack 서버)의 사실 — 받는 것·받지 않는 것·IP·쓰임(재생·따라 보기)·근거·
# 지우는 길, 078 의 보유 기간(서비스를 끝낼 때까지 — 끝내면 지움)과 두는 곳(Redis 최근 14일 몫 + Amazon S3, 서울 리전)
REPLAY_FACTS = {
    "glance": ("화면 이동 기록", "서비스를 끝낼 때까지 둡니다"),
    "consent-title": (
        "방문마다 마우스·손가락 위치와 누른 곳·스크롤을 시각·IP 주소와 함께 모아 서비스를 끝낼 때까지 두고",
        "KimpTrack 서버의 화면 이동 기록:",
        "화면 이동 기록은 서비스를 끝낼 때까지(끝내면 지움)",
    ),
    "s1": ("화면 이동 기록 — KimpTrack 서버, 동의한 방문자만",),
    "s2": (
        "화면 이동 기록 — KimpTrack 서버",
        "화면 이동 기록(동의한 경우만) 서비스를 끝낼 때까지",
        "마우스·손가락의 위치와 움직임(0.1초 간격까지)",
        "요청에 따라오는 IP 주소(전체)",
        "입력한 글자·누른 키·고른 글자·화면에 보이는 글자와 숫자·화면 그림은 받지 않고",
        "브라우저에 저장하지 않음",
        "2초마다",
        "다시 보거나(재생)",
        "몇 초 늦게 따라 봅니다",
        "지금의 페이지입니다",
        REPLAY_STORE,
        "거래소 원문을 두는 파일 저장소(Amazon S3)에는 화면 이동 기록을 원문과 나눠 둡니다",
        "화면 이동 기록도 정보주체의 동의",
        "그 IP 주소의 화면 이동 기록을 지웁니다",
    ),
    "s3": (REPLAY_DESTRUCTION,),
    "s7": (
        "KimpTrack 서버의 화면 이동 기록",
        "KimpTrack 서버로 보냄(서비스를 끝낼 때까지)",
        "화면 이동 기록은 서비스를 끝낼 때까지",
        "KimpTrack(화면 이용 통계·화면 이동 기록)",
    ),
    "s8": ("화면 이동 기록도 그때 쓰던 IP 주소를 알려 주면",),
    "s9": ("화면 이동 기록은 마우스·손가락 좌표와 스크롤 위치를 받지만",),
}


def test_body_sections_state_the_replay_facts() -> None:
    """076 §3.2·§3.3·078 §3.7 — 화면 이동 기록을 동의 상자·한눈에 보기·1·2·3·7·8·9절이 빠짐없이 말하고, 2절 소제목은 화면 이용
    통계와 Clarity 사이. 서버 상수 `PRIVACY_V8_EFFECTIVE`(keep 기준)는 v8(사본 `privacy-20261016-2.html`)의 시행일,
    `PRIVACY_V7_EFFECTIVE`(받기 게이트)는 같은 날이다. 075(v9)·079(v10)는 서버 게이트가 없어 상수를 더하지 않는다 — 바로 앞 판
    (v9 — 사본 `privacy-20261017.html`)은 지금 판과 같은 날 먼저 게시한 판이다."""
    html, _ = _read(PUBLIC / "privacy.html")
    body = _without_diff(html)
    for label, words in REPLAY_FACTS.items():
        text = _text(_section(body, label))
        for word in words:
            assert word in text, (label, word)
    s2 = _section(body, "s2")
    assert (
        s2.index("<h3>화면 이용 통계 — KimpTrack 서버</h3>")
        < s2.index("<h3>화면 이동 기록 — KimpTrack 서버</h3>")
        < s2.index("<h3>화면 이용 기록 — Microsoft Clarity</h3>")
    )
    y, m, d, nth = re.fullmatch(ARCHIVE_NAME, PREVIOUS).groups()
    assert PREVIOUS == "privacy-20261017.html" and nth is None
    assert PRIVACY_V7_EFFECTIVE == PRIVACY_V8_EFFECTIVE < f"{y}-{m}-{d}" == EFFECTIVE


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
    # 079 §3.4 — 바뀐 문장마다 한 행: 동의 상자 셋(저장 규칙·국외 이전 칸 거부 방법·상자 아래 저장값)과 7절 저장 장치 목록의
    # localStorage 줄. 지운 문장·새로 넣은 문장은 없다
    names = {row[0]: row for row in rows}
    assert list(names) == [
        "동의 관리 — 저장 규칙",
        "동의 관리 — 국외 이전 거부 방법",
        "동의 관리 — 저장값",
        "7절 localStorage kt.analytics",
    ]
    assert names["동의 관리 — 저장 규칙"][1:3] == [
        PREVIOUS_CONSENT_TEXT[0],
        SHARED_SENTENCES[1],
    ]
    assert names["동의 관리 — 국외 이전 거부 방법"][1:3] == list(TRANSFER_REFUSAL)
    assert names["동의 관리 — 저장값"][1:3] == [PREVIOUS_CONSENT_TEXT[1], STORAGE_RULE]
    assert names["7절 localStorage kt.analytics"][1:3] == list(LOCAL_STORAGE_LINE)
    assert not [row for row in rows if REMOVED in row[1:3] or ADDED in row[1:3]]
    for sentence in PREVIOUS_CONSENT_TEXT:
        assert [row for row in rows if row[1] == sentence], sentence
    # 받는 자·항목·보유 기간은 그대로다 — 바뀐 문장은 받는 곳·받는 것·두는 기간을 새로 말하거나 빼지 않는다(079 §3.4)
    for _, old, new, _ in rows:
        for word in (
            "Microsoft",
            "보유 기간",
            "항목",
            "IP 주소",
            "입력",
            "개월",
            "90일",
        ):
            assert (word in new) == (word in old), (word, new)


# 075 §3.5 — 본문 절마다 방문 통계(Google Analytics)의 사실 — 받는 것·받지 않는 것·받는 곳·쓰임·끈 설정·2개월·쿠키·근거·지우는 길
GA_FACTS = {
    "glance": ("Google Analytics로 방문 통계를 보며", "Microsoft·Google 제공"),
    "consent-title": (
        "Google Analytics(미국)로 보내도 될까요?",
        "Google Analytics 쿠키 _ga·_ga_*를 지웁니다",
        "쿠키 _ga의 값을 알려 주면 그 브라우저의 기록만",
        "지우는 시점은 Google 기준입니다",
    ),
    "s1": ("방문 통계 — Google Analytics, 동의한 방문자만",),
    "s2": (
        "방문 통계 — Google Analytics",
        "방문 통계(Google Analytics — 동의한 경우만) 2개월",
        "페이지 주소(검색어와 # 뒤는 뺌)",
        "IP 주소는 저장하지 않음",
        "입력한 글자·마우스 위치·화면 내용은 받지 않고",
        "받는 곳은 Google LLC(미국)입니다",
        "광고용 저장·광고 개인화·Google 신호를 끈 채 보내고",
        "데이터 공유 설정을 모두 꺼",
        "Google이 2개월 동안 두고 그 뒤 지웁니다",
        "합계 보고서(날짜별 방문 수 같은 것)는 기간 없이 남습니다",
        "쿠키는 마지막 방문부터 2개월",
        "방문 통계(Google Analytics)도 같은 세 가지 동의로 처리합니다",
    ),
    "s3": (
        "Google Analytics 기록은 Google이 보관하며, 2개월이 지나면 Google이 지웁니다",
    ),
    "s4": ("방문 통계는 Google에 제공됩니다", "Google LLC(미국)"),
    "s6": (
        "Google LLC — 방문 통계(Google Analytics)",
        "Google 서버로 네트워크를 통해 전송",
    ),
    "s7": (
        "쿠키 _ga — Google Analytics",
        "쿠키 _ga_* — Google Analytics",
        "마지막 방문부터 2개월",
        "Google LLC(미국)",
    ),
    "s8": ("Google Analytics 기록은 쿠키 _ga의 값을 알려 주면",),
    "s9": ("Google Analytics에는 페이지 주소의 검색어를 빼고",),
}


def _periods(page: str) -> dict[str, str]:
    """2절 보유 기간 표 — {기록: 보유 기간} (태그 뗀 글자, 표의 차례)."""
    table = re.search(r'<table class="tbl">(.*?)</table>', _section(page, "s2"), re.S)
    return {
        _text(name): _text(value)
        for name, value in re.findall(
            r"<th scope=\"row\">(.*?)</th>\s*<td>(.*?)</td>", table.group(1), re.S
        )
    }


def _s2_block(page: str, title: str) -> str:
    """2절 소절 하나(소제목부터 다음 소제목 앞까지)의 태그 뗀 글자 — 대조표 밖."""
    s2 = _section(_without_diff(page), "s2")
    start = s2.index(f"<h3>{title}</h3>")
    return _text(s2[start : s2.index("<h3>", start + 4)])


def test_body_sections_state_the_google_analytics_facts() -> None:
    """075 §3.5 — 방문 통계(Google Analytics)를 본문 절마다 알리고, 2절의 소절은 Clarity 소절 바로 뒤, 보유 기간 표의 행은 Clarity
    행 바로 뒤(2개월). v9 가 바꾼 글자는 그 판의 사본(`privacy-20261017.html`)이 얼려 두었다. 화면 이동 기록의 지난 보유 기간
    꼴('14일')은 12절 이력 밖에 없다(078)."""
    html, _ = _read(PUBLIC / "privacy.html")
    body = _without_diff(html)
    for label, words in GA_FACTS.items():
        text = _text(_section(body, label))
        for word in words:
            assert word in text, (label, word)
    s2 = _section(body, "s2")
    assert (
        s2.index("<h3>화면 이용 기록 — Microsoft Clarity</h3>")
        < s2.index("<h3>방문 통계 — Google Analytics</h3>")
        < s2.index("<h3>관리자 접속 기록(운영자만)</h3>")
    )
    visible = _text(_body(body))
    history = " ".join(_text(item) for item in _history(html))
    rest = visible.replace(history, "")
    for old in REPLAY_OLD_PERIODS:
        assert old not in rest, old
    periods = list(_periods(html).items())
    ga = ("방문 통계(Google Analytics — 동의한 경우만)", "2개월")
    assert periods[periods.index(ga) - 1][0] == "화면 녹화(Clarity — 동의한 경우만)"


def test_v10_changes_only_the_consent_buttons_and_storage_rule() -> None:
    """079 §3.4 — v10 은 동의 창 버튼·저장 규칙의 글만 바꾼다. 받는 자·항목·목적·보유 기간(동의 상자 세 칸의 알릴 사항·칸 끝 줄,
    2절 보유 기간 표·소절)은 바로 앞 판(v9 사본)과 같은 글자이고, 국외 이전 칸의 거부 방법 첫 문장만 [모두 거부]가 빠졌다. 머리
    질문은 v9 가 알린 그대로다. 본문(대조표 밖)에는 일부만 체크한 저장을 거부로 저장한다는 문장과 칸의 [모두 거부] 문장이 없다."""
    html, _ = _read(PUBLIC / "privacy.html")
    archived, _ = _read(PUBLIC / PREVIOUS)
    assert _periods(html) == _periods(archived)
    for title in (
        "화면 이용 통계 — KimpTrack 서버",
        "화면 이동 기록 — KimpTrack 서버",
        "화면 이용 기록 — Microsoft Clarity",
        "방문 통계 — Google Analytics",
    ):
        assert _s2_block(html, title) == _s2_block(archived, title), title
    cells = _consent_box(html)[1]
    new_notes = [
        _notes(re.search(r"<dl[^>]*>(.*?)</dl>", c, re.S).group(1)) for c in cells
    ]
    old_section = _section(archived, "consent-title")
    old_notes = [
        _notes(dl) for dl in re.findall(r"<dl[^>]*>(.*?)</dl>", old_section, re.S)
    ]
    assert [re.search(r"<span>(.*?)</span>", c).group(1) for c in cells] == (
        re.findall(r"<h3>(.*?)</h3>", old_section)
    )
    old_refusal, new_refusal = TRANSFER_REFUSAL
    refusal = old_notes[2]["거부 방법·절차·효과"]
    assert refusal.startswith(old_refusal)
    old_notes[2]["거부 방법·절차·효과"] = refusal.replace(old_refusal, new_refusal)
    assert new_notes == old_notes
    assert _cell_notes(cells[0]) == _cell_notes(old_section)
    # 머리 질문 — v9 가 대조표에 실은 '이 판' 그대로
    v9 = {row[0]: row for row in _diff_rows(archived)}
    assert v9["동의 관리 — 질문"][2] == SHARED_SENTENCES[0]
    visible = _text(_without_diff(_body(html)))
    for gone in (
        "거부로 저장",
        old_refusal,
        "하나라도 빠진 채 저장하거나 [모두 거부]",
        LOCAL_STORAGE_LINE[0],
    ):
        assert gone not in visible, gone


def test_section_12_rule_drops_the_seven_day_promise() -> None:
    """060 §3.3-8 — 12절 규칙은 '시행일과 전후 대조를 알리고 이전 판을 남긴다·동의 처리가 늘면 다시 동의' 이고, 7일 전 게시
    약속과 그 단서는 본문(대조표 밖) 어디에도 없다 — 그 약속을 지운 v4 의 대조표(사본 `privacy-20261009-2.html`)에만 남는다."""
    html, _ = _read(PUBLIC / "privacy.html")
    visible = _text(_without_diff(_body(html)))
    assert CHANGE_RULE in _text(_section(_without_diff(html), "s12"))
    for gone in ("7일 전", "7×24", "늘거나 새로 생기면", "새로 동의를 받아서만"):
        assert gone not in visible, gone
    v4, _ = _read(PUBLIC / "privacy-20261009-2.html")
    assert "7일 전까지" in _text(re.search(DIFF_TABLE, v4, flags=re.S).group(0))


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
