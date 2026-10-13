# 관리자 화면 디자인 조사 — 참고 대시보드에서 가져올 것

2026-10-14 조사. 대상은 관리자 화면(개요·서버·트래픽·화면) 개편과 새 요청 셋이다 — ① 모든 그래프에서 상세 보기 ② 서버 기록의 IP 대역(/24) 방문자 분석 ③ Clarity 를 더 자세히.
출처 링크를 단 내용은 그 문서나 소스 코드로 확인했다. 확인하지 못한 것은 **(미확인)** 으로 적었다. 색 수치는 직접 잰 값이다 — 색 거리 = OKLab 거리×100(정상 시각 15 이상·색각 이상 8 이상이면 구분된다고 본다), 색각 이상은 Machado 2009 모의, 대비 = WCAG 대비.

## 0. 요약

- 지금 화면(058 스펙 기준)이 잘 안 읽히는 까닭으로 짚이는 것:
  - 차트 색 1·2번(강조 #9184d9·보조 #6f9bee)의 거리가 정상 시각 7.0, 녹색맹 모의 3.6 — 계열이 둘만 돼도 구분이 어렵다.
  - 차트 색 3~5번이 정상·주의·문제 색이라 셋째 계열(예: serve 상자 선)이 '정상' 으로 읽힐 수 있다. 정상↔문제 두 색은 녹색맹 모의 5.6 이라 색만으로는 상태가 갈리지 않는다.
  - 반원 게이지 9개(개요)·도넛 6개(트래픽)·둘째 y축(24시간 트래픽)이 자리와 시선을 먹는다.
- 추천 골격: 왼쪽 세로 내비 + 페이지 머리 오른쪽의 시간 선택기 하나 + 모든 카드의 '상세 보기' → 같은 페이지 위에 뜨는 큰 판(주소 `#` 에 상태를 담아 링크·뒤로 가기가 된다).
- 색은 역할 둘로만 쓴다. 상태 4색 = 뜻, 차트 8색 = 누구(상자·거래소). 둘을 섞지 않고, 상태는 늘 모양+글과 함께.
- 순위·구성비는 도넛 대신 '글 뒤 막대' 상위 목록(5~8행 + 전체 보기)으로, 거르기는 행을 눌러서.
- 사람이 정할 것 둘: IP 대역 화면(지금 계약은 IP 를 응답에 내지 않는다), Clarity 추이를 위한 날별 기록 저장 — §7.

## 1. 레퍼런스

| 이름 | URL | 우리에게 가져올 점 |
|---|---|---|
| Grafana | [모범 사례](https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/best-practices/) · [대시보드 쓰기](https://grafana.com/docs/grafana/latest/dashboards/use-dashboards/) · [Inspect](https://grafana.com/docs/grafana/latest/panels-visualizations/panel-inspector/) · [설정](https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/modify-dashboard-settings/) · [State timeline](https://grafana.com/docs/grafana/latest/panels-visualizations/visualizations/state-timeline/) · [annotations](https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/annotate-visualizations/) | 일반 → 구체로 내려가는 위계, 쌓기 조심, 축 정규화(CPU 는 %). 패널 전체 화면(`v`·`Esc`), 끌어서 구간 확대, Inspect → Data → CSV. 'Shared crosshair'(한 그래프에 올리면 모든 그래프에 십자선). 상태 구간 띠(같은 값 합치기), 경보·배포를 세로선·구간으로 겹치기. |
| Datadog | [Timeseries(Compare time·마커)](https://docs.datadoghq.com/dashboards/widgets/timeseries/) · [전체 화면·전역 시간](https://docs.datadoghq.com/dashboards/widgets/configuration/) · [Host Map](https://docs.datadoghq.com/infrastructure/hostmap/) | 위젯 확대(inspect) 안 **Compare Time** — 현재·이전 두 그래프, 오프셋 Period/Day/Week/Month/Custom, Overlay↔Grid 전환. 전체 화면에서 기간 앞뒤 이동·CSV 내려받기. 마커 = 선/범위 + 상태색·점선 + 이름표. 전역 시간과 위젯 자체 시간을 나눔. Host Map = 칸 색이 지표. |
| Vercel Web Analytics · Geist | [Analytics](https://vercel.com/docs/analytics) · [색](https://vercel.com/geist/colors) · [재질](https://vercel.com/geist/materials) · [글자](https://vercel.com/geist/typography) | 패널 = 상위 목록 + View All + CSV(250행까지). 색 10단계에 역할이 붙어 있다: 1~3 바탕(기본·마우스 올림·누름), 4~6 테두리(같은 셋), 7·8 대비 큰 바탕, 9 보조 글·아이콘, 10 본문 글·아이콘. 모서리 6px(기본·툴팁)·12px(메뉴·모달)·16px(전체 화면). 글자 14px 가 가장 흔하고 13px 보조(숫자는 tabular), 12px 셋째. |
| Cloudflare Security Analytics·Events | [Analytics](https://developers.cloudflare.com/waf/analytics/security-analytics/) · [Events](https://developers.cloudflare.com/waf/analytics/security-events/) | 출처별 상위(IP·UA·경로·나라·호스트·ASN) 값에 마우스를 올리면 **Filter / Exclude**. 표본 로그는 행을 펼쳐 필드를 보고, 필드에서 다시 Filter/Exclude, 열 고르기, 한 건 JSON 내보내기 → IP 대역 화면의 틀. |
| Stripe Dashboard 홈 차트 | [홈 차트](https://support.stripe.com/questions/dashboard-home-charts-overview) | 드롭다운 셋: 기간(프리셋·직접) · 단위(시·일·주·월, 기간에 맞춰 가까운 단위로) · 비교 기간, 이 순서로. 비교선을 어떻게 그리는지는 **(미확인)**. |
| PostHog | [Web analytics](https://posthog.com/docs/web-analytics/dashboard) · [Paths](https://posthog.com/docs/product-analytics/paths) · [Heatmaps](https://posthog.com/docs/toolbar/heatmaps) (사이트가 이 환경에서 접속 거부 → [GitHub 원문](https://github.com/PostHog/posthog.com/tree/master/contents/docs)으로 확인) | 큰 수마다 이전 기간 대비 증감. 경로 행을 누르면 대시보드 전체가 그 경로로 걸러짐. 활동 시간 열지도(요일×시). Paths: 시작/끝 지정, 최소 인원으로 드문 길 숨김, 마우스 올리면 들고나는 고리 강조. Clickmap: 요소 오른쪽 위 = 클릭 수, 왼쪽 위 = 분노 클릭, 기본 빨강~초록이 안 보일 수 있어 다른 그라데이션·단색 고르기. |
| Plausible | [둘러보기](https://plausible.io/docs/guided-tour) · [비교](https://plausible.io/docs/compare-stats) · [bar.js](https://github.com/plausible/analytics/blob/master/assets/js/dashboard/stats/bar.js) | 위 큰 수를 누르면 큰 그래프의 지표가 바뀜. 비교 = 이전 기간·작년·직접(`X` 키), 기본 '요일 맞추기'. 목록 펼치기 → 전체 목록·지표 더·머리 눌러 정렬, 행 누르면 거르기. 행 뒤 막대 폭 = 값 ÷ 최댓값(소스 확인). |
| Linear | [UI 개편기](https://linear.app/now/how-we-redesigned-the-linear-ui) | 테마를 98개 변수 대신 바탕·강조·대비 3값에서 LCH 로 생성. 강조색을 덜 써 바탕을 중립으로. 사이드바·탭·머리 정렬로 잡음 줄이기. 어두운 테마의 글·아이콘을 더 밝게. |
| shadcn/ui 대시보드 · Tremor | [대시보드 예](https://ui.shadcn.com/examples/dashboard) · [Sidebar](https://ui.shadcn.com/docs/components/sidebar) · [Tracker](https://npm.tremor.so/docs/visualizations/tracker) · [Bar List](https://npm.tremor.so/docs/visualizations/bar-list) | 카드 = 제목 · 큰 수 · 증감 배지(+12.5%) · 바닥 한두 줄. 차트 카드 안 기간 토글(3달·30일·7일). 사이드바 `collapsible="icon"`(아이콘만 남기고 접기), ⌘/Ctrl+B, 폭 16rem. Tracker = 칸마다 색+툴팁(가동 99.1% 카드), Bar List = 이름이 안에 든 가로 막대(큰 순). |
| Atlassian Statuspage · Sentry Crons | [90일 가동 막대](https://support.atlassian.com/statuspage/docs/display-historical-uptime-of-components/) · [Crons 상세](https://docs.sentry.io/product/crons/job-monitoring/) | 날 칸 90개: 끊긴 분에 따라 초록→빨강, 자료 없음 회색, 부분 장애는 0.3배로 셈, 칸에 올리면 그날 사건 링크. Sentry: 날별 성공·실패·누락 막대 + 실행 시간 평균 선 + 최근 체크인 표 + 이슈 목록 → 수집 상태 상세. |

## 2. 디자인 원칙

1. **페이지마다 질문 하나, 위에서 아래로.** 개요 "지금 문제 있나", 서버 "어느 상자의 무엇이", 트래픽 "누가 어디서 와서 어디로". 상태 띠 → 큰 수 → 추이 → 목록·표 순으로 일반에서 구체로(Grafana 'tell a story'). 한 줄에는 같은 무게의 카드만 둔다.
2. **카드 해부.** 머리 = 제목(2~6낱말) + 흐린 글로 단위·기간('CPU % · 24시간') + 오른쪽에 부분 상태 배지와 상세 보기 단추(⤢). 몸 = 그림 하나. 바닥(있을 때만) = 비교 한 줄('어제 같은 때보다 ▲ 12%'). 설명은 제목 옆 '?' 하나.
3. **숫자.** 큰 수 28~32px·굵게, 단위는 14px 흐린 글로 옆에. 증감 배지는 12px '▲ 12%' 처럼 화살표+글, 색은 기본 글색 — 5xx 처럼 '오르면 나쁜' 지표가 섞여 있어 증감에 초록·빨강을 붙이지 않는다. 문턱을 넘은 값만 상태색. 큰 수 아래 작은 선 그림 높이 32~40px([Grafana Stat](https://grafana.com/docs/grafana/latest/panels-visualizations/visualizations/stat/) 은 값 뒤에 선 그림을 깐다).
4. **색은 두 역할만.** 상태 4색(정상·주의·문제·자료 없음) = 뜻, 차트 8색 = 누구. 계열에 상태색을 쓰지 않고 상태 표시에 계열색을 쓰지 않는다. 같은 대상은 모든 차트에서 같은 색([Grafana 'Classic palette (by series name)'](https://grafana.com/docs/grafana/latest/panels-visualizations/configure-standard-options/)). 상태는 늘 모양+글과 함께(● 정상 ▲ 주의 ■ 문제 ○ 자료 없음). 강조색은 고른 메뉴·초점 테두리·계열 하나짜리 차트의 지금 기간 선에만.
5. **정상은 조용히.** 정상은 옅게 두고 문제만 진하게 칠한다. 상태 띠도 정상이면 '● 정상 · 14:03 확인' 한 줄. 끊김 띠는 정상 구간을 얇고 옅게, 끊김 구간을 칸 높이 전체로 — 색 말고 굵기로도 갈린다(Statuspage 는 정상 칸도 초록으로 칠하지만 우리는 줄이 많아 시끄럽다 — 판단).
6. **여백·격자.** 4px 단위(4·8·12·16·24·32·48). 카드 안쪽 16px, 카드 사이 16px(1440px 이상 24px). 12칸 격자에 카드 폭 3·4·6·12칸, 최대 폭 1600px.
7. **어두운 테마 대비.** 바탕 3단(페이지 < 카드 < 떠 있는 판)을 명도 조금(대비 약 1.1)과 1px 테두리로 가르고, 그림자는 떠 있는 것에만. 글 3단은 카드 위 4.5:1 이상, 차트 선·상태색은 3:1 이상(§5 수치). Linear 처럼 글을 충분히 밝게.
8. **차트는 얇고 조용하게.** 선 1.5~2px, 점은 마우스를 올릴 때만, 격자는 옅은 가로선 3~4개(세로선·축선 없음), 눈금 글 11px 흐린 글, 시간 눈금은 '14:00'·'10-14' 처럼 짧게. 계열 셋 이하면 범례 대신 제목 옆 색 칩이나 선 끝 이름표. 툴팁 = 시각 한 줄 + 계열별 값(큰 순) + 이전 기간 차. 둘째 y축 대신 같은 시간 축의 위아래 두 칸(십자선 공유) — Grafana 는 단위가 다르면 좌우 축을 권하지만 어느 선이 어느 축인지 헷갈려 칸을 나눈다. 쌓기·면적은 합에 뜻이 있을 때만, 들어옴/나감은 위·아래로 뒤집어([Grafana 'Negative Y'](https://grafana.com/docs/grafana/latest/panels-visualizations/visualizations/time-series/)).
9. **도넛·게이지를 줄인다.** 구성비는 100% 가로 띠 하나(조각 다섯까지, 나머지 '기타'), 순위는 상위 목록. 반원 게이지는 큰 수 + 문턱 눈금이 있는 짧은 막대로. 게이지는 예산 하나만 남긴다.
10. **상위 목록.** 행 = 이름 · 값 · 비율, 글 뒤에 값 ÷ 최댓값 폭의 옅은 막대(Plausible). 5~8행 + '전체 보기'(상세 판 — 정렬·찾기·CSV). 행을 누르면 페이지 전체를 그 값으로 거르고(PostHog·Plausible), 마우스를 올리면 '이것만 / 빼기'(Cloudflare). 걸어 둔 거르기는 머리 아래 칩 줄에 모이고 ×로 지운다.
11. **빈 상태·불러오는 중·실패.** 카드 틀과 높이를 먼저 그리고 그림 자리에 옅은 뼈대. 0 은 '0', 값 없음은 '–', 아직 모으지 않은 자료는 '자료 없음 · 10-09 부터 모음' 한 줄. 실패하면 마지막 값을 흐리게 두고 머리에 '불러오지 못함 · 3분 전 값'. 10초 갱신에 깜박이지 않게 애니메이션은 끈다.
12. **좁은 화면.** 1280px 이상 3~4열, 768~1279px 2열, 768px 미만 1열. 상세 판은 좁은 화면에서 화면 전체. 툴팁은 차트 안에 가두고 표는 카드 안에서 가로로 스크롤. 누르는 칸은 40px 이상.

## 3. 화면 골격

### 3.1 내비 — 왼쪽 세로 내비 추천

- 모양: 아이콘+이름 200px, 768~1279px 에선 아이콘만 56px, 768px 미만은 위 줄의 ☰ 로 펼친다(shadcn Sidebar 의 `collapsible="icon"`·모바일 시트와 같은 방식). 아이콘은 로컬 SVG 묶음을 `<use href>` 로(CSP `'self'` 그대로, 글꼴 아이콘·CDN 없이).
- 이유 ① 페이지가 여섯으로 는다(개요·서버·트래픽·방문자·Clarity·화면). 위 탭 여섯에 시간 선택기·비교·상태 점·갱신 시각·로그아웃까지 한 줄에 넣으면 1280px 에서 넘친다. ② 내비 항목마다 상태 모양(▲ 서버)을 붙일 자리가 생겨 '어디로 가야 하나' 를 내비가 알려 준다. ③ 넓은 화면에서 남는 건 가로, 모자란 건 세로 — 머리 줄을 하나 덜어 차트에 준다. ④ 화면 페이지는 아이콘만으로 접어 실제 페이지 틀을 넓힌다.
- 위 탭이 나은 때: 페이지가 서넛에 머물 때. 그때는 지금 머리 줄을 그대로 둔다.

### 3.2 전역 시간 선택기

- 페이지 머리 오른쪽에 하나. 프리셋 단추만 둔다(직접 날짜 고르기는 미룸). 페이지마다 피드가 주는 것만: 개요 1시간·6시간·24시간 / 서버 6시간·24시간·7일·30일 / 트래픽·방문자 24시간·7일·30일 / Clarity 어제·3일(+쌓인 날만큼 7일·30일) / 화면 오늘·7일·30일·90일.
- 옆에 '비교' 토글: 없음 · 이전 기간 · 지난주 같은 때(7일 배수라 요일이 맞는다 — Plausible '요일 맞추기', Datadog Day/Week). 단축키 `X`(Plausible 과 같다).
- 고른 값은 주소에 `?range=24h&cmp=prev` 로 — 새로고침·링크·상세 판이 같은 창을 쓴다(Grafana 도 `viewPanel`·`from`·`to` 를 주소에 둔다 — [딥링크](https://grafana.com/docs/grafana/v13.1/developer-resources/mcp/guides/generate-deeplinks-to-grafana.md)).
- 카드가 다른 창을 쓰면(Clarity 페이지별 72시간 등) 카드 머리에 '최근 72시간' 배지(Datadog 의 위젯 자체 시간). 머리 끝에는 '10초마다 · 14:03:21'.

### 3.3 카드 → 상세 보기 — 같은 페이지 위의 판 추천

- 추천: 같은 페이지 위에 거의 전체 화면 크기의 판(뒤는 어둡게 가림, `Esc`·× 로 닫기, 좁은 화면은 화면 전체). 주소에 `#detail=cpu` 를 붙여 링크로 열리고 뒤로 가기로 닫힌다.
- 이유: Grafana(패널 전체 화면 보기 — 주소에 `viewPanel`)와 Datadog(전체 화면·inspect)이 대시보드 위에서 크게 띄우는 방식이다. 뒤 카드의 문맥과 이미 받은 자료를 그대로 쓰고, 판 하나가 카드 설정을 받아 그리므로 카드마다 페이지를 만들 필요가 없다. 별도 페이지는 대상 하나를 깊게 볼 때만 고려하는데, IP 대역 하나도 판 안의 '◀ 이전 / 다음 ▶' 로 충분하다.
- 판에 넣을 것(위 → 아래):
  1. 머리: 제목·단위 · 계열 칩(누르면 숨김/보임) · 기간 프리셋(카드보다 길게, 자료가 있는 만큼 최대 90일) · 비교(없음/이전 기간/지난주) · CSV · ×.
  2. 큰 그래프(높이 55vh 안팎): 휠·끌기로 확대·이동, 아래 미니맵 슬라이더, 끌어서 구간을 고르면 그 구간으로 확대(Grafana), '원래대로'. 경보·배포·장애 구간을 세로선·옅은 띠로 겹치고(annotations·event overlay), 기준선(75·85%)은 점선+이름표(Datadog 마커).
  3. 통계 표(지금 보이는 구간 기준, 계열마다 한 행): 최소 · 평균 · 최대 · p95 · 마지막 · 이전 기간 대비(차, %). Grafana 시계열 범례의 '표' 모드(값 칸에 계산 여러 개)처럼 행 = 계열.
  4. 원자료 표: 시각(KST) · 계열별 값(+이전 기간 값), 최근 순 200행씩, 머리 눌러 정렬. CSV 는 보이는 구간·계열 그대로, 시각은 UTC·KST 두 칸(Grafana Inspect → Data, Datadog 전체 화면과 같은 자리).
  5. 관련 카드 한 줄(CPU → 크레딧·WS 접속, 5xx → 최근 5xx 표).
- 확대한 구간이 바뀌면 그 구간에 맞는 단위로 서버에 다시 부른다 — 점 수를 그림 폭에 맞춘다(Grafana 'Max data points' 기본값 = 패널 폭 픽셀, [쿼리 옵션](https://grafana.com/docs/grafana/latest/panels-visualizations/query-transform-data/)).

## 4. 페이지별 배치

```text
[개요] — 지금 문제 있나                       [1시간|6시간|24시간] [비교]  10초마다 14:03:21
┌ 상태 띠: ■ 문제 업비트 끊김 3분 · ▲ 주의 data CPU 78% · ▲ 주의 5xx 4건/1시간 ┐  (없으면 ● 정상 한 줄)
[지금 접속 41 ▁▃▅] [오늘 방문자 312 ▲8%] [수집 성공 99.9%] [경보 0] [비용 $41/$80 ━━━──]
[거래소마다 한 줄(현물·perp): 이름 · 24시간 칸 띠(정상 옅게·끊김 빨강) · 마지막 성공 n초 전 · 1시간 성공률]
[상자 표: 상자 | CPU ▬▬ 42% | 메모리 | 디스크 | 크레딧 | 24시간 CPU 선]   ← 게이지 9개 대신 표 하나
[24시간 트래픽: ① 사람 모양 페이지 막대 ② 5xx 막대 ③ WS 접속 선 — 위아래 세 칸, 십자선 공유]
```
- 칩을 누르면 해당 페이지로 가서 그 카드의 상세 판을 연다.

```text
[서버] — 어느 상자의 무엇이                   [6시간|24시간|7일|30일] [비교]
거르기: [■ collect] [■ data] [■ serve]   ← 범례 = 거르기, 색은 차트 1·2·3번 고정
[CPU % · 75·85 점선               ⤢] [네트워크 ↑들어옴 ↓나감(뒤집은 면적) ⤢]   두 열, 시간 축·십자선·확대 공유
[메모리 %                          ⤢] [디스크 % · 75·85 점선              ⤢]
[CPU 크레딧 잔량 선 + 사용 막대    ⤢] [디스크 입출력 ↑읽기 ↓쓰기          ⤢]
[수집 상태: 거래소 × 시간 상태 띠 | 성공률 | 끊김 수 | 마지막 끊김                                ⤢]
[경보: 이름 알약(모양+글) + 7일 사건 줄(ALARM 구간 띠·알림 점) ⤢] [비용: 실제·예측 막대 + 예산선 ⤢]
[WS 접속 수 ⤢] [점검(canary) 시간·오류 점 ⤢] [상태 검사 실패 구간 ⤢]
```
- 수집 상태 상세 판 = Sentry Crons 처럼 날별 성공·끊김 막대 + 끊김 목록 표(시작·길이·원인 분류).

```text
[트래픽] — 누가 어디서 와서 어디로            [24시간|7일|30일] [비교]  거르기: [나라 = KR ×]
[방문자 ▲8%] [사람 모양 페이지] [지금 접속] [봇 비율] [5xx]   ← 누르면 아래 큰 추이의 지표가 바뀜
[큰 추이: 고른 지표 + 이전 기간 회색 점선                                                     ⤢]
[흐름: 들어온 길 → 첫 페이지 → 마지막 페이지 · 드문 길은 '기타' · 마우스 올리면 앞뒤 강조       ⤢]
[들어온 길·출처 목록 ▬] [나라·망 종류 목록 ▬] [첫 페이지·마지막 페이지 목록 ▬]
[기기·OS·브라우저 100% 띠 셋] [요청 종류 여덟 100% 띠] [요일×시간 열지도(한 색 진하기)]
[응답 상태 100% 띠 + 최근 5xx 표] [WS 머문 시간 막대]
```
- 도넛 여섯 → 100% 띠와 목록. 목록 행을 누르면 페이지 전체가 걸러지고 머리 아래 칩이 생긴다.

```text
[방문자 · IP 대역] (새 페이지)                [24시간|7일|30일]   찾기 [1.2.3.___]
거르기: [망 종류: 데이터센터 ×] [봇 포함] [나라: 전체 ▾]
[대역 수] [데이터센터 대역의 요청 비중] [상위 10 대역의 요청 비중] [오늘 처음 본 대역]
[표: 대역 | 나라·망 종류 | 요청 ▬▬ | 사람 모양 페이지 | 짝 수 | 봇 % | 5xx | 처음~마지막 | 24시간 선]
  └ 행 → 상세 판: 분당 요청 막대 · 짝별 UA 목록 · 경로 상위 · 상태 코드 띠 · 첫/마지막 페이지
    · 최근 요청 50줄(가린 IP 그대로, 행 펼치기) · [이 대역만 트래픽 보기] [빼고 보기] · ◀ 이전/다음 ▶
```
- 틀은 Cloudflare Security Events(출처별 상위 + Filter/Exclude + 표본 로그 펼치기), 대역별 UA 목록은 GoAccess 의 호스트 패널([features](https://goaccess.io/features)). 개인정보 쪽은 §7.

```text
[Clarity]                       [어제|3일|7일|30일]   마지막 받음 2시간 전 · 다음 2시간 뒤
[세션] [봇 세션] [사용자] [페이지/세션] [평균 스크롤 깊이] [활성 시간/전체 시간]   ← 큰 수 + 날별 작은 선
[불만 신호 여섯: 신호가 있던 세션 % 막대 + 전날 대비 (죽은 클릭·분노 클릭·과한 스크롤·빠른 복귀·스크립트 오류·오류 클릭) ⤢]
[페이지 × 기기 표: 세션 | 스크롤 | 활성 시간 | 신호 여섯 칸(칸 색 = 한 색 진하기, 표본 적음은 흐림) ⤢]
[나라 목록 ▬] [기기·브라우저·OS 100% 띠] [출처(Referrer) 목록 ▬] [Channel 목록 ▬ — 차원 호출이 더 필요, §7]
[맞대기: Clarity 세션(동의한 방문자만) vs 우리 방문자 — 날별 두 선, 둘의 비 = 동의 비율의 어림 ⤢]
```
- 제약([Data Export API](https://learn.microsoft.com/en-us/clarity/setup-and-installation/clarity-data-export-api)): 프로젝트당 하루 10회, 최근 1~3일만, 차원 셋까지, 응답 1,000행·페이지 없음. 7일·30일과 '전날 대비' 는 날별 기록이 쌓여야 한다(§7). 신호 뜻은 [Semantic metrics](https://learn.microsoft.com/en-us/clarity/insights/semantic-metrics) 문구를 '?' 에.

```text
[화면] (059 배치 유지)  [좁은 띠: 기간·기기·보기·화면 목록(글 뒤 막대 pv)] [실제 페이지 틀 + 요소 상자] [탭: 요소 순위 | 영역 | 떠난 곳·깊이]
```
- 요소 상자 꼬리표는 PostHog clickmap 자리를 따른다: 오른쪽 위 클릭 수, (수집하게 되면) 왼쪽 위 분노 클릭.
- 다섯 단계 색은 빨강~초록이 아니라 한 색 진하기(§5 `--seq-*`). PostHog 문서도 기본 빨강~초록이 안 보일 수 있다고 적는다.
- 요소 표 행의 ⤢ → 상세 판(날별 클릭·보인 시간 추이 + 이전 기간). 이 페이지에선 내비를 아이콘만으로 접는다.

## 5. 토큰 제안 (어두운 테마 하나)

```css
:root {
  /* 바탕 3단 + 선 — 카드/페이지 대비 1.12, 떠 있는 판/카드 1.13 */
  --bg:#121420; --surface:#1c1f2b; --raised:#252938;
  --line:#2c3040; --line-strong:#3a3f52; --grid:#262a37;
  /* 글 3단 — 카드 위 13.9 · 7.1 · 5.3 : 1 (text-3 은 떠 있는 판 위에서도 4.7:1) */
  --text-1:#ececf1; --text-2:#a7aab9; --text-3:#8e92a4;
  --accent:#9184d9;   /* 카드 위 5.1:1 — 고른 것·초점·계열 하나짜리 차트 */
  /* 상태 4색 — 카드 위 7.3 · 7.5 · 5.1 · 5.3 : 1, 늘 모양(● ▲ ■ ○)+글과 함께 */
  --ok:#5fbf8f; --warn:#e0a458; --bad:#e0697d; --none:#8e92a4;
  /* 차트 8색 — 순서 고정(순서가 색각 이상 대책), 파랑·주황·청록·노랑·자홍·초록·보라·빨강 */
  --c1:#3987e5; --c2:#d95926; --c3:#199e70; --c4:#c98500;
  --c5:#d55181; --c6:#008300; --c7:#9085e9; --c8:#e66767;
  /* 한 색 진하기(열지도·요소 단계·표 칸) — 낮음 → 높음 */
  --seq-1:#184f95; --seq-2:#256abf; --seq-3:#3987e5; --seq-4:#6da7ec; --seq-5:#b7d3f6;
  /* 글자 · 간격 · 모서리 · 그림자 */
  --fs-11:11px; --fs-12:12px; --fs-13:13px; --fs-14:14px; --fs-16:16px; --fs-20:20px; --fs-24:24px; --fs-32:32px;
  --sp-1:4px; --sp-2:8px; --sp-3:12px; --sp-4:16px; --sp-6:24px; --sp-8:32px; --sp-12:48px;
  --r-sm:6px; --r-md:12px; --r-lg:16px;
  --shadow-pop:0 8px 24px rgb(0 0 0 / .45);  --shadow-sheet:0 24px 64px rgb(0 0 0 / .6);  --scrim:rgb(8 9 14 / .72);
}
```
- 차트 8색 검증(dataviz 검증기, 카드·페이지 바탕 둘 다): 이웃한 쌍 모두 통과 — 색각 이상 최저 8.4, 정상 시각 최저 19.3, 모두 3:1 이상. 1~3번은 모든 쌍 통과(색각 이상 9.4, 정상 20.9) → **collect = 파랑, data = 주황, serve = 청록** 고정. 058 의 순서는 1·2번이 정상 시각 7.0 으로 실패.
- 상태색과 차트색이 가까운 쌍(정상 시각): 빨강(c8)↔문제 3.0, 자홍(c5)↔문제 5.9, 노랑(c4)↔주의 9.4, 주황(c2)↔문제 9.5, 청록(c3)↔정상 11.3. 상태는 모양+글이 있어 견디지만, c5·c8 은 상태 칩 곁 차트에 되도록 쓰지 않는다. 계열이 다섯을 넘으면 고른 것만 색, 나머지는 `--text-3` 옅게(또는 작은 그림 여러 개).
- 떠 있는 판(`--raised`) 위에선 c6 초록이 2.9:1 로 모자라다 → 차트는 늘 `--surface` 위에 그린다(상세 판의 그래프 칸도).
- 한 색 진하기는 단계 검증 통과(같은 색상, 밝기 단조, 가장 어두운 단계도 바탕과 2.0:1).
- 글자 쓰임: 11 차트 눈금 / 12 배지·범례·표 머리 / 13 표 본문·카드 바닥 / 14 본문·내비·카드 제목(600) / 16 상세 판 제목 / 20 페이지 제목 / 24 상세 판 통계 / 32 큰 수. 굵기는 400·600 둘만, 줄 높이 본문 1.5·표 1.4·큰 수 1.1, 표·축·10초마다 바뀌는 큰 수는 `tabular-nums`. 글꼴은 지금처럼 시스템 글꼴(CSP 가 웹 글꼴을 막는다).
- 모서리 6(칩·입력칸·툴팁)·12(카드·메뉴)·16(상세 판)은 Geist 재질과 같다. 카드는 그림자 없이 1px `--line`, 그림자는 툴팁·메뉴(`--shadow-pop`)와 상세 판(`--shadow-sheet` + `--scrim`)만. 초점 테두리는 지금처럼 2px `--accent`.
- 058 과 같은 값: 바탕·카드·선·본문 글·강조·상태 셋. 새로 넣는 값: 떠 있는 판·진한 선·격자, 글 2·3단(흐린 글 #9a9cab 대신), 자료 없음, 차트 8색, 진하기 5단. 빼는 값: 보조 #6f9bee. 변수 이름은 설명용이라 기존 `--color-*`·`--tone-*` 이름에 값만 넣어도 된다.

## 6. ECharts 5.5.1 로 만들 때

1. **테마 등록.** `echarts.registerTheme('kt', {...})` 를 공통 모듈에서 한 번, `echarts.init(el, 'kt')`(5.5.1 사본에 `registerTheme` 와 내장 `'dark'` 가 있음을 확인). 값은 `getComputedStyle(document.documentElement)` 로 §5 CSS 변수에서 읽어 CSS 를 하나의 원본으로 둔다. 테마에 넣을 것: `color` 8색, `backgroundColor:'transparent'`, 시스템 글꼴, 축(축선 숨김·`splitLine` `--grid`·눈금 글 `--text-3` 11px), 툴팁(바탕 `--raised`·테두리 `--line-strong`·글 `--text-1`), `animation:false`.
2. **십자선·확대 공유.** 같은 페이지 시간 차트는 `chart.group = 'server'` + `echarts.connect('server')`([API](https://echarts.apache.org/en/api.html#echarts.connect)). 5.5.1 소스에서 한 차트의 이벤트 동작(툴팁·dataZoom·범례 고르기)을 같은 group 의 다른 차트에 그대로 보내는 구조를 확인했다. 상세 판 차트는 group 을 주지 않아 뒤 카드가 따라 움직이지 않게 한다. 한 차트 안 위아래 두 칸은 `axisPointer.link: [{xAxisIndex: 'all'}]`([옵션](https://echarts.apache.org/en/option.html#axisPointer.link)).
3. **확대.** 상세 판은 `dataZoom: [{type:'inside'}, {type:'slider'}]`, 카드는 `inside` 만. Grafana 식 '끌어서 구간 확대' 는 `toolbox.feature.dataZoom` 을 숨긴 채 `dispatchAction({type:'takeGlobalCursor', key:'dataZoomSelect', dataZoomSelectActive:true})` 로 켠다(5.5.1 에 있음). `datazoom` 이벤트에서 새 구간을 읽어 서버에 다시 부른다. `filterMode` 의 권장값은 **(미확인)**.
4. **큰 자료.** 선 계열에 `sampling:'lttb'`(추세·극값 유지), `'minmax'` 는 5.5.0 부터([옵션](https://echarts.apache.org/en/option.html#series-line.sampling)). 5.5.1 소스상 직교 좌표에서 점이 10개를 넘고 기기 픽셀당 점이 1.5개 이상일 때만 솎는다. 30일 × 1분(43,200점)도 그려지지만 기간별 단위(5분·1시간)를 서버가 주는 게 먼저. `showSymbol:false`.
5. **툴팁 안전.** `formatter` 가 낸 글은 HTML 로 그대로 들어간다 → 피드 글자는 모두 `echarts.format.encodeHTML` 을 거친다([보안 지침](https://echarts.apache.org/handbook/en/best-practices/security)). 기본 서식은 보통 이스케이프하지만 예외가 있었다([CVE-2026-45249](https://security.apache.org/projects/echarts/) — Lines 계열 기본 툴팁, 6.1.0 에서 고침, 우리는 Lines 를 안 쓴다). `renderMode:'richText'` 는 캔버스에 그려 주입이 없다 — 그러면 CSP 의 `style-src 'unsafe-inline'` 을 뺄 수 있는지는 **(미확인, 브라우저에서 CSP 위반 0 확인 필요)**. 그 밖에 `confine:true`(좁은 화면), `appendTo`(5.5.0+, 카드 밖으로), `valueFormatter`(단위), `order:'valueDesc'`.
6. **색각 보조.** `aria: {enabled:true, decal:{show:true}}` 로 계열에 무늬를 더한다(ECharts 5, [옵션](https://echarts.apache.org/en/option.html#aria.decal)) — 머리의 '무늬' 토글로.
7. **상태 띠.** ECharts 에는 state timeline 이 없다 → `custom` 계열의 `renderItem` 으로 (거래소, 시작, 끝, 상태) 사각형을 그린다. 정상은 얇고 옅게, 끊김은 칸 높이 전체 `--bad`, 자료 없음은 `--none` 빗금.
8. **기준선·사건·이전 기간.** `markLine`(75·85%, 점선 + 이름표), `markArea`(경보·장애 구간 옅은 띠), 알림은 x 축 아래 작은 점 계열. 이전 기간은 시각에 기간 길이를 더해 같은 축에 `lineStyle:{type:'dashed'}`·`--text-3`·낮은 `z` 로 그리고, 툴팁에 '지금 · 이전 · 차(%)'.
9. **수명.** 상세 판을 닫으면 `dispose()`, 카드 크기 변화는 `ResizeObserver` → `resize()`. 주기 갱신은 `setOption` 으로 자료만, 계열 수가 바뀌면 `replaceMerge:['series']`. 시간 축은 브라우저 시간(KST)으로 그려진다(`useUTC` 기본 false).

## 7. 사람이 정할 것 · 확인 못 한 것

- **IP 대역 화면.** 지금 계약(035·038·039)은 가린 IP 를 메모리 안에서 나라·망 종류 찾기와 방문 세기에만 쓰고 응답·로그에 내지 않는다. 대역을 화면에 보이면 이 계약이 바뀌고(서버 스펙 필요 — 지금 짝 기록에는 대역이 남지 않는다), 처리방침 문장이 이 쓰임을 덮는지도 확인해야 한다. /24 하나가 집이나 작은 사무실 하나일 수도 있다. 제안: 데이터센터·봇 대역만 대역을 보이고 국내·해외 통신사 대역은 '통신사 대역 n개' 로 묶기, 짝 셋 미만 대역은 '(기타)'(039 의 묶기 규칙과 같게).
- **Clarity 추이.** 지금(040)은 마지막 성공 값만 둔다 → 날마다 값을 쌓는 저장이 필요하다(서버 스펙). 하루 10회 나눔도 다시 짜야 한다 — 예: 기본(4시간마다)을 하루 두 번으로 줄이고 남는 횟수를 Device·Channel·Browser·Country 차원에(미검토 제안).
- **(미확인)**: Stripe 비교선을 그리는 방식, Better Stack 공식 문서(표에서 뺌), ECharts `dataZoom.filterMode` 권장값과 테마 빌더 페이지 내용(JS 화면이라 글로 못 읽음), richText 툴팁의 CSP 효과. PostHog 는 GitHub 원문으로만 확인했다.
