# Microsoft Clarity — 대시보드 설정·켜고 끄기·삭제 요청·Data Export 토큰 (스펙 033·035·040, 사람용)

랜딩(`/`)과 대시보드(`/app/`)의 화면 이용을 Microsoft Clarity 로 본다. **동의 방식**이다 — 화면 아래 동의 안내 띠나 처리방침의 동의 관리(`/privacy#consent`)에서 수집·이용·Microsoft 제공·미국 이전 세 칸에 모두 동의해 저장한 방문자만 Clarity 태그를 받는다. 설정은 레포의 `web/public/clarity.js` 머리 세 값(프로젝트 ID·켤 페이지·안내 판)이 전부이고, 나머지는 Clarity 대시보드에서 사람이 한다.

## 1. 켜는 순서
1. 032 처리방침이 게시돼 있다 — `https://kimptrack.com/privacy` 가 열리고 '화면 분석 동의 관리' 절이 있다. 032 §7 의 법률 확인을 끝냈다.
2. 아래 2절의 대시보드 설정을 한다.
3. ID 가 든 PR 을 머지·배포한다. 지금 ID 는 033 PR 에 들어 있다(032 가 같은 줄기에서 먼저 머지되므로) — **033 PR 의 머지가 곧 켜기다.** ID 가 있는데 방침이 게시 전 모양(자리표시자 `〔`, 동의 방식의 국외 이전 근거 없음)이면 CI 의 `test_clarity.py` 가 막는다.
4. 3절의 확인을 한다.

## 2. Clarity 대시보드 설정 (Settings)
- **Setup → Advanced**: Cookies **끔**(동의 모드 — 사이트가 `analytics_Storage: granted` 를 보낸 방문자에게만 1차 쿠키 `_clck`·`_clsk` 가 생긴다), Bot detection **켬**.
- **Masking**: Balanced(기본 — 숫자·이메일을 가린다). 요소 규칙은 두지 않는다 — 가림 해제는 사이트 속성(대시보드 `#root`·랜딩 `<body>` 의 `data-clarity-unmask`)으로만 정해 레포에 남긴다. 입력칸은 어느 모드에서도 가려진다.
- **IP blocking**: 운영자 고정 IPv4(CIDR 가능). IPv6·모바일·VPN 은 막을 수 없다. 넣을 목록은 사람이 정한다.
- **Team**: 녹화를 보는 사람은 개인정보를 다루는 사람이다 — 관리자·구성원을 최소로. 범위는 사람이 정한다.
- GA·GTM 연동은 켜지 않는다.
- 선택: Clarity 지원 메일로 URL 매개변수 `s.q`·`g.q`·`p.q` 가림을 요청한다(033 부터 검색어는 URL 에 안 실린다 — 옛 링크 대비, 페이지 주소에만 적용된다).
- Data Export API 토큰은 6절(035).

## 3. 확인 (배포 뒤)
1. 새 크롬 프로필로 `https://kimptrack.com` — 화면 아래에 띠가 뜨고, 개발자 도구 네트워크에 `clarity.ms` 요청이 0건이다.
2. 띠의 세 칸을 모두 체크하고 [선택한 대로 저장] — `www.clarity.ms/tag/…`·`scripts.clarity.ms`·`*.clarity.ms/collect` 요청이 생기고 `c.clarity.ms` 는 0건, 쿠키는 `_clck`·`_clsk` 둘(도메인 `.kimptrack.com`). 콘솔에서 `clarity('metadata', (m) => console.log(m), false, true, true)` 의 동의 상태가 ad DENIED·analytics GRANTED.
3. 2시간 안에 Clarity 녹화 목록에 그 세션이 보인다 — 숫자는 보이고 입력칸은 가려져 있다. 대시보드에서 탭을 두 번 바꾸면 같은 세션에 페이지가 셋이고 태그 `tab` 이 붙는다. 필터를 여러 번 바꿔도 페이지가 늘지 않는다. 이벤트 `tab_<id>`·`pivot_history` 가 보인다.
4. GPC 를 보내는 브라우저(Brave)·[모두 거부]·`www.kimptrack.com` 에서는 `clarity.ms` 요청이 0건이다(GPC·거부는 띠도 없다).
5. 실제 휴대폰에서 띠가 화면 절반을 넘지 않고, 띠 밖 페이지를 스크롤·누를 수 있다. VoiceOver 로 띠의 세 칸을 체크하고 '내용 보기'를 펼치고 버튼을 누를 수 있다.
6. 같은 기기에서 스프레드 탭 60초 Performance 기록(동의·거부 각각)의 스크립트 시간과 1시간 전송량을 033 §7 에 적는다. 동의 쪽이 1.5배를 넘으면 `PAGES` 에서 `"app"` 을 빼는 PR 을 낸다(기준값은 사람이 정한다). 띠가 대시보드 부하를 늘리지 않는지도 잰다 — 띠 있음(값 없음)·없음(`denied`) 각 3회, 스프레드 탭 30초 Performance 기록의 스크립트 시간 중앙값을 033 §7 에 적는다(차이 5% 안이 목표).

## 4. 끄기·되돌리기
- 전부 끄기: `clarity.js` 의 `CLARITY_ID` 를 `""` 로 바꾸는 PR. 배포 뒤 다음 페이지 로드부터 띠도 태그도 없다(`/clarity.js` 는 nginx 가 매번 다시 확인하게 한다 — `no-cache`, 대시보드의 `/app/clarity.js` 는 `no-store`).
- 대시보드만 끄기: `PAGES` 에서 `"app"` 을 뺀다.
- 받는 자·항목·목적·보유 기간이 바뀌면: 같은 PR 에서 032 방침의 알릴 사항과 안내 판 세 곳(`clarity.js` 의 `NOTICE_VERSION`·`privacy.html` 스크립트의 `VERSION`·방침의 보이는 판 글자)을 함께 올린다. 예전 판의 동의는 '정하지 않음' 이 되어 띠가 다시 묻는다. 문장만 다듬으면 판을 올리지 않는다.
- Clarity 프로젝트 삭제는 끄는 방법으로 쓰지 않는다 — 데이터가 전부 사라지고 되돌릴 수 없다. 삭제 요청(5절)에만 쓴다.

## 5. 삭제 요청 — 프로젝트 기록 통째 삭제
Clarity 는 한 사람분만 지울 수 없다. 철회한 방문자가 이미 보낸 기록을 바로 지우기를 원하거나 Clarity 기록 삭제를 요청하면(032 방침 3절·8절의 약속 — 개인정보 보호법 제37조 제3항·제36조) 운영자가 다음 순서로 한다. 새 프로젝트를 먼저 켜고 옛 것을 지우므로, 그 사이 동의한 방문자의 기록이 갈 곳 없는 시간이 없다.
1. 받은 메일에 접수를 답한다.
2. 새 Clarity 프로젝트를 만들고 2절의 설정(쿠키·봇·가림·IP 차단·팀)을 그대로 한다.
3. `clarity.js` 의 `CLARITY_ID` 를 새 ID 로 바꾸는 PR 을 머지·배포한다. 안내 판은 올리지 않는다(받는 자·항목·목적·보유 기간이 같다).
4. 배포 뒤(3절 1·2번으로 새 ID 의 태그가 나가는지 확인) 옛 프로젝트를 지운다 — 그 프로젝트의 모든 방문자 녹화·히트맵이 함께 사라지고 되돌릴 수 없다. 남길 숫자가 있으면 개인정보 없는 집계만 지우기 전에 따로 적는다.
5. 035 의 Data Export 토큰이 있으면 새 프로젝트에서 다시 만들어 바꾸고 옛 집계를 지운다 — `DEL admin:clarity admin:clarity:pages`(api 를 다시 만든 뒤라 둘 다 곧바로 부른다 — 그날 한도를 2회 더 쓴다).
6. 요청자에게 결과를 지체 없이 알린다. Microsoft 가 자기 목적에 쓰는 정보는 이것으로 지워지지 않으며 Microsoft 개인정보 문의로 따로 요청할 수 있다고 함께 적는다. 주고받은 메일은 문의 메일로 보관한다(032 — 답변을 마친 날부터 1년).

## 6. Data Export 토큰(035)
관리자 페이지의 Clarity 요약은 serve 의 api 가 Clarity Data Export API(`project-live-insights` — 기본 요약은 부른 때 직전 24시간, 페이지×기기 묶음은 직전 72시간)를 불러 만든다. 토큰이 없으면 그 부분은 '연결 안 됨'(`unconfigured`)이고 아무것도 부르지 않는다. **토큰 값은 레포·이 문서·채팅·이슈에 적지 않는다.**
- **한도**: 프로젝트당 하루 10회(넘으면 429). api 는 기본 호출을 4시간, 페이지×기기 호출을 12시간 띄운다(성공·실패 모두 센다 — 어떤 24시간에도 6 + 2 = 8회라 사람 몫 2회가 남는다). 둘이 같이 때가 되면 기본 먼저, 기본이 401·403·429 면 페이지는 미룬다. 마지막 시도·결과·마지막 성공 값은 Redis `admin:clarity`(기본)·`admin:clarity:pages`(페이지 — 주소 없이 묶은 수만)에 있어 api 를 다시 띄워도(배포) 한도를 쓰지 않는다. 관리자 페이지를 열 때만 부른다.
1. 언제: 032 처리방침 게시·033 설치(1절) 뒤.
2. 발급: Clarity 프로젝트 → Settings → Data Export → 새 토큰(프로젝트 관리자만 만들 수 있다). 이름은 용도를 알게(예: 관리자 요약).
3. 넣기: serve 박스의 `server/.env` 에 `CLARITY_API_TOKEN=<토큰>` 한 줄을 편집기로 넣는다(셸 기록에 남기지 않게). collect 의 `server/.env` 에는 넣지 않는다 — api 만 쓴다.
4. api 다시 만들기(serve): `docker compose --profile serve --env-file .env --env-file server/.env up -d --force-recreate api`.
5. 확인: 관리자 페이지(036 전이면 serve 에서 `docker exec marketlens-caddy wget -qO- http://web:8081/svc/api/admin/clarity`)의 `state` 가 `ok`, `traffic.sessions`·`botSessions` 가 숫자 — 세션이 0 이면 `users`·`pagesPerSession` 은 null 일 수 있다(2026-10-02 첫 응답). 첫 응답(세션 0)의 `metrics[].name` 목록과 행의 **키 이름**(값은 옮기지 않는다)은 035 §7 에 옮겨 두었다 — 이름이 그 목록과 다르면 035 §7 에 옮긴다. 정규화 키는 세션이 있는 응답을 본 뒤 035·036 에 함께 더한다. `pages.state` 도 `ok` 인지 본다(첫 요청은 `pending` — 60초 뒤). 동의한 세션이 생긴 뒤 처음 본 응답으로 040 §4 '배포 뒤' 확인을 한다.
- **상태 읽기**: `denied`·`http_401` = 토큰이 없거나 틀리거나 만료(교체), `denied`·`http_403` = 권한 없음, `error`·`http_429` = 오늘 한도를 다 썼다(값은 마지막 성공 그대로, `nextAt` 에 다시 부르는 시각), `error`·`redis` = data 박스 Redis 에 못 닿아 부르지 않았다. 값은 마지막 성공에서 7일이 지나면 비고 `fetchedAt` 이 경과를 말한다. `pages` 는 따로 읽는다 — 기본과 같은 `denied`·`http_429` 면 기본 때문에 미룬 것이고, `error`·`bad_data` 면 차원 키가 040 의 짐작과 달라 묶지 못했다(040 을 고친다).
- **바로 부르기**(토큰을 바꿨거나 간격을 기다리지 않을 때) 키 **하나만**, **하루 한 번** 지운다 — 기본 요약은 data 박스에서 `docker exec marketlens-redis redis-cli DEL admin:clarity`, 페이지×기기 묶음은 `… DEL admin:clarity:pages`. 다음 관리자 페이지 요청이 곧바로 부른다(하루 10회 중 1회). api 는 키마다 24시간에 한 번만 곧바로 부르고, 그 안에 다시 지우면 기록을 되살리고 간격을 따른다 — 둘 다 지우면 사람 몫 2회를 다 쓴다.
- **교체**(관리자 이탈 — Clarity 권장 — 또는 유출 의심): 새 토큰 발급(2) → serve `server/.env` 값 교체(3) → api 다시 만들기(4) → Data Export 화면에서 옛 토큰 삭제 → 바로 확인하려면 '바로 부르기'. 두 간격은 Redis 값이라 교체해도 유지된다.
- **끄기**: serve `server/.env` 에서 `CLARITY_API_TOKEN` 줄을 지우고 api 다시 만들기 → '연결 안 됨'. 남은 집계(최대 7일치 숫자와 쿼리를 뗀 페이지 주소·출처, 페이지 종류×기기 묶음)는 data 박스에서 `DEL admin:clarity admin:clarity:pages` 로 지운다 — 토큰이 없으면 api 가 그 키를 읽지도 지우지도 않는다.
