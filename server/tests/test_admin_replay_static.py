"""관리자 화면 재생 정적 단언(스펙 074 §3.1·§3.3·§3.4) — 틀 주소·postMessage 대상·받는 출처는 replayframe.js 의 SITE_ORIGIN 한 상수
(065 screens.js 와 같은 값), 틀 주소를 쓰는 곳 하나, 틀 속성(053 그대로)·누를 수 없는 틀, 방문자 페이지 카드 둘의 '?' 두 줄(뜻 줄에
'동의한 방문자만, 서비스 종료까지 보관'), 5초 묶음은 방문자 페이지 하나. 그리기·부르기는 node 시험(test_admin_replay_*)이 본다.
"""

import re

from tests.test_admin_screen import PAGES
from tests.test_admin_scripts import _script
from tests.test_deploy import _text

HTML = _text("web/admin/visitors.html")


def test_frame_address_and_messages_use_one_site_origin() -> None:
    """§3.3·§3.4 — 틀로 보내는 postMessage 는 한 곳(대상 출처 SITE_ORIGIN, '*' 없음), 받는 준비 알림은 출처와 틀 창을 둘 다 본다,
    틀 주소는 load() 한 곳(SITE_ORIGIN + framePath() 또는 about:blank). 다른 재생 모듈에는 출처·postMessage 가 없다."""
    js = _script("replayframe.js")
    assert (
        js.count("const SITE_ORIGIN = 'https://kimptrack.com';") == 1
        and js.count("https://") == 1
    )
    assert (
        _script("screens.js").count("const SITE_ORIGIN = 'https://kimptrack.com';") == 1
    )
    assert (
        js.count("postMessage(") == 1
        and "if (win) win.postMessage(message, SITE_ORIGIN);" in js
    )
    assert "'*'" not in js and '"*"' not in js
    assert (
        "if (event.origin !== SITE_ORIGIN || !win || event.source !== win) return;"
        in js
    )
    assert "d.type !== 'kt-replay-ready' || d.v !== 1" in js
    assert js.count(".src") == 1 and "frame.src = url;" in js
    assert "const want = path && on ? `${SITE_ORIGIN}${path}` : 'about:blank';" in js
    for name in ("replaymodel.js", "replayview.js", "replaysheet.js", "replaycards.js"):
        code = _script(name)
        assert (
            "postMessage" not in code and "https://" not in code and ".src" not in code
        ), name


def test_frame_attributes_and_the_frame_cannot_be_touched() -> None:
    """§3.4 — 틀 속성은 053 그대로(sandbox="allow-scripts allow-same-origin"·referrerpolicy="strict-origin"), 키보드 초점이 틀 안으로
    가지 않고(Space·←/→ 는 판이 받는다), 틀 위 누름·끌기는 CSS 로 막는다(화면은 재생이 정한다)."""
    js = _script("replayframe.js")
    assert "frame.setAttribute('sandbox', 'allow-scripts allow-same-origin');" in js
    assert "frame.setAttribute('referrerpolicy', 'strict-origin');" in js
    assert "frame.setAttribute('tabindex', '-1');" in js
    css = _text("web/admin/admin.css")
    rule = re.search(r"^\.rp-stage iframe \{\n(.*?)^\}", css, re.S | re.M)
    assert rule and "pointer-events: none;" in rule.group(1)
    assert "transform: scale(var(--fit, 1));" in rule.group(1)


def test_visitors_page_has_the_two_cards_with_consent_and_retention_in_help() -> None:
    """§3.1 — 카드 '지금 보는 중'·'화면 기록'(표는 가로 스크롤 상자 안, 찾기 칸), '?' 는 071 두 줄이고 뜻 줄에 '동의한 방문자만, 서비스 종료까지 보관'."""
    for card, title in (("rplive", "지금 보는 중"), ("replay", "화면 기록")):
        head = re.search(
            rf'<section class="card s12" id="card-{card}" aria-labelledby="h-{card}">\s*<header class="card-head"><h2 id="h-{card}">{title}</h2>'
            r'<span class="unit">[^<]+</span><span class="help" title="([^"]+)">',
            HTML,
        )
        assert head, card
        what, how = head.group(1).split("&#10;")
        assert "동의한 방문자만, 서비스 종료까지 보관" in what, card
        assert 0 < len(what) <= 120 and 0 < len(how) <= 120
        assert (
            f'<div class="scroll"><table class="tbl" id="tbl-{card}"></table></div>'
            in HTML
        )
        assert f'<span class="tag" id="t-{card}"></span>' in HTML
    assert '<input class="input" id="rp-q" type="search"' in HTML
    assert (
        HTML.index('id="card-rplive"')
        < HTML.index('id="card-addrs"')
        < HTML.index('id="card-replay"')
    )


def test_the_five_second_loop_belongs_to_the_visitors_page_only() -> None:
    """§3.1 — 지금 묶음(5초)은 page.live 인 페이지 — 방문자 하나. 재생 판·카드는 방문자 페이지 모듈만 싣는다."""
    for page, (_, module, _) in PAGES.items():
        code = _script(module)
        assert ("  live: true," in code) == (page == "visitors.html"), page
        assert ("replaysheet.js" in code) == (page == "visitors.html"), page
    visitors = _script("visitors.js")
    assert "    replay: replayDetail," in visitors and "wireReplay();" in visitors
