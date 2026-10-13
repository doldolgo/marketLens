# 044 — kimp-pages

상태: DONE | 의존: 022 landing(정적 파일·검색 구성·요약 API `GET /landing`) · 032 privacy(번들 밖 정적 페이지와 nginx 위치 셋의 모양) · 013 premium-events(사건 기준) · 014 premium-1m(봉 계층·보관)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
"김프 차트"·"김프 히스토리" 를 검색한 사람이 KimpTrack 의 페이지를 만나 바로 대시보드 기록 탭으로 들어오게 한다. 대시보드(`/app/`)는 React 라 색인하지 않으므로, 검색어마다 정적 페이지 하나를 둔다 — 네이버는 하위 페이지를 검색어별로 따로 잡는다(2026-10-08 조사 — 김프왔다의 "김프 차트"·"김프 계산기" 페이지). 랜딩 한 장에 검색어를 더 얹는 것보다 페이지당 검색어 하나가 검색에 유리하다.

## 2. 범위
- 만드는 것: `web/public/kimp-chart.html`·`web/public/kimp-history.html`(번들 밖 정적 한 파일씩 — 032 의 privacy.html 과 같은 방식), 공개 nginx 위치 셋씩(`= /<slug>`·`= /<slug>.html`·`= /app/<slug>.html`), `sitemap.xml` 두 줄, 랜딩 바닥 나브·질문과 답의 링크, 계약 테스트 `server/tests/test_kimp_pages.py`.
- 하지 않는 것: 서버 코드·API 변경, 코인별 페이지, 김프 계산기 페이지(후속 후보), 한글 경로(`/김프-차트` — 퍼센트 인코딩이 공유·nginx 정확 일치를 지저분하게 한다), FAQPage 마크업(022 와 같은 이유), 대시보드 화면 변경.
- 바꾸는 기존 것: 022 §3.1 경로 표·§3.3-8 바닥 나브·§3.3-7 질문과 답의 "김프 차트와 과거 기록" 답·§3.6 sitemap 줄 수, `test_landing_seo.py` 의 sitemap 단언(loc 네 줄).

## 3. 동작

### 3.1 경로 (nginx)
| 요청 | 응답 |
|---|---|
| `/kimp-chart` | `kimp-chart.html` |
| `/kimp-chart.html`·`/app/kimp-chart.html` | `301 /kimp-chart` |
| `/kimp-history` | `kimp-history.html` |
| `/kimp-history.html`·`/app/kimp-history.html` | `301 /kimp-history` |
| `/kimp-chart/`·`/kimp-history/` | 404(루트 location 의 `try_files`) |

- 공개 nginx(:80)의 정확 일치 위치. `= /<slug>` 는 `try_files /<slug>.html =404` 에 머리 둘을 `always` 로 붙인다 — `Cache-Control: no-cache`(고치면 다음 방문에 바로, 그대로면 304)와 CSP. `/app/<slug>.html` 을 막는 이유는 032 와 같다(dist 의 파일이 `/app/` alias 로 CSP 없이 200 으로 나간다). 정규식 위치는 두지 않는다(028).
- 모양 — 본문은 내용 폭(최대 1120px) 가운데의 한 칸 578px(17px 기준 34em — 글자가 큰 h1·h2 도 같은 왼쪽 선에 서도록 px). 절 위 구분선도 그 폭으로 가운데에 긋고, 화면 그림·코인 바로 가기는 880px·지난 7일 사건 표는 48em 까지 넓혀 가운데에 둔다(2026-10-09 사람 요구 — 왼쪽으로 치우쳐 보임).
- CSP — kimp-chart 는 privacy 와 같은 값(`default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`). kimp-history 는 `/api/landing` 을 부르므로 `connect-src 'self'` 를 더한 값. 그 밖의 요청(외부 글꼴·그림·WebSocket)은 둘 다 막힌다.

### 3.2 공통 — 검색 구성
- `lang="ko"`, `<title>`·`og:title` 은 **검색어가 앞, 브랜드가 뒤**(022 §3.6 과 같은 결정), 60자 이내. `description`·`og:description` 은 검색어를 담은 한 문장 110자 이내. '김프가'(경쟁 사이트 이름)와 수익을 약속하는 말(이득·이익·수익·차익·무위험)은 제목·설명에 쓰지 않는다 — 본문에서는 "수익이 난다는 뜻이 아닙니다" 처럼 부정하는 자리에만.
- `<meta name="robots" content="index, follow, max-image-preview:large">`, canonical·`og:url` 은 `https://kimptrack.com/<slug>`, 아이콘은 처리방침과 같은 상대 경로 두 줄(`/favicon.ico`·`/apple-touch-icon.png` — CSP `img-src 'self'` 가 어느 호스트에서나 통하게), `og:image` 는 랜딩의 `og-v3.png`(페이지 전용 그림은 만들지 않는다).
- JSON-LD 한 블록 — `WebPage`(url, name = title, description = description, inLanguage ko-KR, `isPartOf` = 랜딩의 `#website`, `primaryImageOfPage` = `landing/history.png` 2040×1230, `dateModified`). 평점·리뷰 없음.
- h1 하나(검색어로 시작), h2 셋 이상. 글꼴은 시스템 글꼴(랜딩의 서브셋 글꼴은 이 페이지 글자를 담지 않는다), 색 값은 랜딩과 같은 토큰. 외부 자원은 하나도 부르지 않는다.
- 바닥 — 바로 가기(첫 화면·실시간 김프 표·다른 검색어 페이지·`/#kimp`·개인정보 처리방침), 참고값 안내 한 줄(랜딩과 같은 문장), "설명을 마지막으로 고친 날" `<time>`. 두 페이지의 `<time>`·`WebPage.dateModified`·sitemap `lastmod` 가 같은 날짜다 — 설명 글을 실제로 고칠 때만 올린다.
- 서로 링크한다(차트 ↔ 히스토리), 둘 다 `/app/?tab=history` 로 보낸다. 랜딩은 바닥 나브 두 칸과 질문과 답 "김프 차트와 과거 기록도 볼 수 있나요?" 의 답에서 두 페이지로 링크한다.

### 3.3 kimp-chart — "김프 차트"
- 제목 "김프 차트 - 실시간·과거 김치프리미엄 봉 차트 | KimpTrack", h1 "김프 차트 — 실시간·과거 김치프리미엄 봉 차트".
- 본문은 전부 정적(스크립트 없음): 볼 수 있는 것(봉 종류 8개 1분·3분·5분·15분·30분·1시간·4시간·1일 — KST 벽시계 정렬, 해외 거래소별 카드·국내 1개 캔들·2개 선, 김프·역프 따로, 입출금 띠, 시간축·십자선 연동, 사건 음영 — 014·015·024 의 동작 그대로) · 랜딩의 기록 탭 스크린샷(`landing/history*.{png,webp}` 재사용) · 읽는 법(봉 = 맨 위 호가 원값의 OHLC, 환율 = USDT 호가, 띠가 막힘이면 옮길 수 없던 값, 보관 1분 7일·5분 30일·1시간 90일·4시간 1년·일봉 무제한 — 014 §3.4) · 코인별 바로 가기 여섯(`/app/?tab=history&sym=BTC` 등 — 심볼은 다섯 거래소에 다 있는 것) · 질문과 답 넷(실시간인가·가격 차트와 차이·거래소 앱·트레이딩뷰와 차이·수수료).
- 실시간 값은 넣지 않는다 — 검색 결과에 '지금 값' 으로 남지 않게.

### 3.4 kimp-history — "김프 히스토리"
- 제목 "김프 히스토리 - 지난 김프·역프 사건 기록 | KimpTrack", h1 "김프 히스토리 — 지난 김프·역프 사건 기록".
- **지난 7일 사건 표** — 랜딩 022 §3.2 의 `GET /api/landing` 하나만 부르고 `events` 만 쓴다(`count`·`kimp`·`reverse`·`open` 과 `top[]` 의 `sym`·`dom`·`fx`·`dir`·`maxPercent`·`durationSeconds`·`endTs`). 요약 한 문장 + 표(코인·경로·방향·최고·지속·끝난 시각 — 랜딩의 사건 표와 같은 열·같은 표기, 끝난 시각은 KST), 코인 칸은 기록 탭 링크(`/app/?tab=history&sym=&h.dir=&h.dom=&h.fx=`), 행 클릭 = 그 링크. 보이는 동안 10초 폴링, 숨으면 멈추고 다시 보이면 즉시 1회, bfcache 복원 때 1회. 첫 요청 실패면 안내 한 줄("사건 기록을 불러오지 못했습니다."), 그 뒤 실패는 직전 표를 둔다. 같은 글이면 DOM 을 건드리지 않는다. 표를 감싼 상자는 `data-nosnippet`, `<noscript>` 한 줄.
- 정적 본문: 남는 것(사건 기준 1.0% 진입·0.5% 이탈·1분 초과 — 013, 항목, 기간 선택 1h·4h·8h·12h·24h·3d 와 필터, 과거 김프 기록은 봉 차트로) · 보는 법 네 단계 · 질문과 답 셋(얼마나 과거까지·왜 실시간 표보다 큰가·돈을 번다는 뜻인가 — 아니요).

## 4. 검증
- nginx: `= /kimp-chart`·`= /kimp-history` 가 `try_files /<slug>.html =404` + 머리 둘(no-cache·각자의 CSP)뿐이다 / `/<slug>.html`·`/app/<slug>.html` 은 `301 /<slug>` / `/<slug>/` 는 루트 location(404) / 정규식 location 없음·공개 `/api` 허용 목록은 그대로(028).
- 페이지: title 이 검색어로 시작·60자 이내, description 에 검색어·110자 이내, '김프가'·수익 약속 없음, robots index, og·canonical 이 `/<slug>`, h1 하나(검색어로 시작)·h2 셋 이상, JSON-LD WebPage 가 보이는 값과 같고 그림 파일이 있음, 외부 자원 0, kimp-history 의 스크립트만 `/api/landing` 을 부르고(WebSocket·clarity 없음) 실시간 자리는 `data-nosnippet`, 서로·대시보드 기록 탭·첫 화면·처리방침 링크, `<time>`·`dateModified`·sitemap `lastmod` 같음.
- 랜딩: 바닥 나브와 질문과 답에 두 링크. `test_landing_seo.py` 의 sitemap 단언은 loc 네 줄(`/`·`/kimp-chart`·`/kimp-history`·`/privacy`).
- 수동: 로컬 dist 를 띄워 두 페이지를 1200px·390px 로 본다(표가 차고 가로 스크롤 없음) / 머지·배포 뒤 `curl -sI https://kimptrack.com/kimp-chart`(200·no-cache·CSP)·`/kimp-history`·`/kimp-chart.html`(301) 과 사이트맵.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# 자동 (2026-10-08)
cd server && ruff check . && ruff format --check . && pytest -q      # test_kimp_pages 7 새로
cd web && npm run lint && npm run build
uv run web/scripts/subset-landing-font.py                           # 랜딩 글 바뀜(바닥 나브·질문과 답)
# 수동 (2026-10-08) — 로컬 dist 를 python http.server 로 띄워 헤드리스 크롬 1200px 스크린샷으로 두 페이지 확인
```

## 6. 갱신할 문서
- `docs/context/status.md` — `landing` 행 web 칸에 "검색어 페이지 둘(`/kimp-chart` 정적·`/kimp-history` 7일 사건 표 — 044)" 을 더한다.
- `CLAUDE.md` — 스펙 인덱스 044 행 DONE.
- `docs/specs/022-landing.md` — §3.1 경로 표에 `/kimp-chart`·`/kimp-history` 행, §3.3-7 질문과 답 "김프 차트와 과거 기록" 답에 두 링크, §3.3-8 바닥 나브에 두 칸, §3.6 sitemap 줄 네 개.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것: `web/public/kimp-chart.html`·`web/public/kimp-history.html`·`web/nginx.conf` 위치 여섯·`web/public/sitemap.xml` 두 줄·`web/public/landing.html` 링크 셋·`server/tests/test_kimp_pages.py`·`server/tests/test_landing_seo.py` sitemap 단언. `server/tests/test_observability.py` 의 공개 nginx 위치 목록에 여섯 줄.
- 추측한 지점: og 그림은 랜딩의 것을 재사용(페이지 전용 그림은 만들지 않음) / 코인별 바로 가기는 BTC·ETH·XRP·SOL·DOGE 다섯 + 전체 / "2026년 9월부터 기록" 은 014 배포일(2026-09-07) 기준.
- 남은 빚: 검색엔진 등록(구글 서치콘솔·네이버 서치어드바이저)은 사람 몫 — 등록 뒤 두 페이지도 색인 요청. 김프 계산기 페이지는 후속 후보.
