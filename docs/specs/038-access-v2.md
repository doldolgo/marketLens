# 038 — access-v2

상태: TODO | 의존: **037 privacy-v2**(상수 `PRIVACY_V2_EFFECTIVE`) — `feat/037-privacy-v2` 위에 쌓고 037 머지 뒤 main 으로 옮긴다(PR base 는 늘 main, 037 보다 먼저 머지하지 않는다). main 에는 fix/036-admin-followup 이 있어야 한다. 계약을 쓰는 스펙(쓰는 계약은 §3 에 복사했다): 035(읽는 계약·부분 공통 규칙·파일 고르기 — 접속 요약의 창·집계·응답은 이 스펙이 넘겨받는다), 027(caddy 줄 모양·IP 가림), 029(관리자 nginx), 036(화면이 읽는 키), 002(탭 id), 033(clarity.js 캐시 규칙), 037(처리방침 문장·시행일). 뒤에 쌓는 스펙: 039(짝 기록에 나라·망 종류, 응답에 `geo`), 042(창 고르기 화면).

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
운영자가 관리자 화면에서 서버 접속 기록을 최근 24시간·7일·30일로 나눠 '언제·누가·어디서 들어왔나' 를 본다. 지금 요약(035)은 24시간뿐이고, 봇이 페이지의 절반을 넘는데 사람과 섞어 세며, 배포 때마다 WebSocket 502 가 5xx 칸을 빨갛게 만든다. 이 스펙이 끝나면 줄마다 방문자 종류(여덟)를 가르고, JS 가 돈 흔적으로 '확인된 방문자'(하한)와 '브라우저 모양 방문자'(상한)를 함께 세며, 유입 채널·기기·인앱을 방문자 기준으로 준다. 7일·30일은 처리방침 v2 시행일(037) 0시부터만 열린다.

## 2. 범위
- 만드는 것: api `GET /admin/access` 의 `window` 인자와 v2 응답(기능 폴더 `admin`), 회전 파일별 메모리 캐시, 줄 분류 표(종류·탐색 경로·운영자 흔적·채널·인앱·기기·OS·브라우저), 방문자-일 세기, 계약 테스트, `web/admin/admin.js` 한 줄(상위 표 비율 분모).
- 하지 않는 것: 화면 개편(042 — 이 스펙 뒤에도 036 화면은 인자 없이 불러 24시간을 그대로 그린다). 나라·망 종류(039 — 이 스펙의 응답에는 `geo` 가 없다). nginx·Caddyfile·compose 변경. 새 저장(Redis·디스크·로그 어디에도). 새 라이브러리(BLAKE2b 는 표준 `hashlib`). 운영자 IP 목록. 처리방침 문장(037).
- 처리방침: 이 스펙은 037 이 2절 서버 접속 기록 문단에 적는 사실대로만 동작한다 — (가) 최근 30일까지의 요약을 서버 메모리에서만 만들고 따로 저장하지 않으며 다시 시작하면 사라진다 (나) 같은 방문을 하루에 한 번만 세려고 IP 앞부분·브라우저 정보를 되돌릴 수 없는 값으로 바꿔(바꾸는 방식은 다시 시작할 때마다·날마다 달라진다) 메모리에서만 쓰고, 그 값에 그날의 시간대·들어온 경로·기기 종류·다시 온 방문인지를 붙여 최근 30일까지만 둔다 (다) 시행일 전 기록에는 쓰지 않는다. 시행일 전에는 지금 판의 '24시간 요약' 안에서만 센다(§3.2).
- 바꾸는 기존 것: 035 §1·§2·§3.1·§3.2·§3.4·§3.5·§4, 036 §3.2·§3.4, 027 §3.2 끝 줄(문장은 §6). 셋 다 이 레포 주인 담당이다. 경로·nginx·compose 가 그대로라 016·018·021 에는 고칠 문장이 없다.

## 3. 동작

### 3.1 요청·창
- `GET /admin/access?window=24h|7d|30d` — api 역할(collector 404, OpenAPI 는 api 에만 — 035 그대로). 관리자 nginx 의 `= /svc/api/admin/access` 는 `rewrite ^ /admin/access break;` 로 경로만 바꾸고 원래 쿼리를 뒤에 붙여 넘긴다(바꿀 글에 `?` 가 없으면 nginx 가 원 인자를 붙인다) — `nginx-admin.conf` 는 고치지 않는다.
- 창의 시(N): `24h` 24 · `7d` 168 · `30d` 720.
- 인자가 없거나, 위 셋과 글자가 정확히 같지 않거나(`7D`·`1y`·빈 값), 지금 고를 수 없는 창(게이트 전 `7d`·`30d`)이면 24시간 창으로 답한다 — 422·400 없음. 실제로 답한 창은 `window` 가 말한다.
- 공통 규칙(035 §3.1 복사): 늘 200 JSON·키 camelCase, `…At` 은 epoch ms, `…Ts` 와 `hourly`·`recent5xx`·`days` 의 `ts` 는 epoch 초. 응답은 부분 하나 `{state, code, fetchedAt, refreshSec, …값 키}` — `state` 는 `ok`·`unconfigured`·`error`·`pending`, `code` 는 ok 면 null·아니면 `no_file`·예외 이름, 오류 문장 없음. ok 가 아니면 값 키는 null — 단 `window`·`windows`·`gateAt` 은 상태와 무관하게 늘 싣는다(화면이 창 버튼을 그리게).
- 갱신: 창마다 따로 60초 칸(`refreshSec` 60). 요청이 왔을 때 그 창이 비었거나 60초가 지났으면 갱신 하나를 띄우고 3초까지 기다린다 — 끝나면 새 값, 아니면 직전 결과(없으면 `pending`)를 답하고 갱신은 뒤에서 마저 돈다. 요청이 없으면 파일을 읽지 않는다. 파일 읽기·세기는 `asyncio.to_thread`. 로그·예외는 035 그대로다(처리기 안 예외는 그 부분 `error`·500 없음, 실패는 `marketlens.admin` WARNING 에 부분 이름 `access`·`code` 만 10분 1줄, ERROR 없음). 갱신 전체(3초 뒤 뒤에서 마저 도는 부분 포함)의 모든 예외를 잡아 부분 이름과 예외 종류 이름만 남기고, 예외 문장·traceback 은 남기지 않는다 — 문장에 가린 IP·UA 가 실릴 수 있고 025 가 ERROR 를 Slack 으로 보낸다(잡히지 않은 태스크 예외도 ERROR 다).

### 3.2 게이트 — 처리방침 v2 시행일
- 한 곳: `server/app/core/config.py` 의 `PRIVACY_V2_EFFECTIVE = "YYYY-MM-DD"`(KST 날짜, 037 이 만든다). 게이트 시각 = 그날 00:00 Asia/Seoul(= 전날 15:00Z). 응답 `gateAt`(ms)에 늘 싣는다.
- 지금 < 게이트면 `windows` = `["24h"]`, 지금 ≥ 게이트면 `["24h","7d","30d"]` — 같은 프로세스가 시계만 지나면 재시작 없이 연다.
- `startTs` = 지금이 든 시의 시작 − (N−1)시간(N 은 §3.1). `7d`·`30d` 는 max(그 값, 게이트 초). `endTs` = 지금(초).
- **읽기 시작점** = min(24시간 창 시작, max(30일 창 시작, 게이트)). 이 앞 줄은 어떤 값에도 들지 않고, 이 앞에서 끝난 파일은 읽지도 캐시하지도 않는다. 게이트 전에는 24시간 창 시작과 같다 — 개정 전 기록은 개정 전 목적(24시간 요약) 안에서만 쓰인다. 게이트 뒤 첫 하루는 24시간 창이 게이트 앞 시간을 품지만 7일·30일 창에는 들지 않는다. 그래서 30일 창은 게이트 + 30일부터 꽉 찬다.
- KST 는 UTC+9 고정이다. 게이트가 KST 자정이라 게이트 앞뒤 줄이 같은 KST 날에 섞이지 않는다.

### 3.3 읽기·캐시
- 읽는 계약(027·035 복사): caddy 가 도메인 요청마다 JSON 한 줄을 `ACCESS_LOG_DIR`(serve 의 `logs/caddy`, api 에 읽기 전용)의 `access.log` 에 쓴다. 쓰는 필드 — `ts`(epoch 초 실수, 줄의 둘째 필드), `request.client_ip`(없으면 `request.remote_ip` — IPv4 /24·IPv6 /48 로 가려져 있다), `request.method`·`request.uri`(경로+쿼리, 검색어 키는 지워져 있다), `status`, `duration`(초), `referer`(출처 `스킴://호스트[:포트]` 또는 빈 값), `ua`. 폴링 다섯 경로와 canary 는 기록되지 않는다. `/api/ws/spreads` 는 연결이 끝날 때 101 한 줄. 하루 또는 50MiB 에서 회전해 gzip 된 회전 파일 `access-<UTC 시각>-<size|time>.log.gz` 를 같은 디렉터리에 90일·100개까지 둔다(압축 중엔 같은 이름 `.log` 가 잠깐 함께 있고 그때는 `.log` 하나만 읽는다). 회전 파일의 수정 시각은 마지막 줄 무렵이다.
- 파일 고르기: `access.log` 와 수정 시각이 읽기 시작점 뒤인 회전 파일 — 그중 최신부터 압축 크기 합 10MB 까지. 넘는 오래된 파일은 읽지도 캐시하지도 않고 `firstTs` 가 늦어진다. 몰리는 날엔 50MiB 마다 회전해 파일이 100개까지 쌓일 수 있어 첫 채움을 묶으려는 것이다(지금 30일치는 ≈0.7MB, 50MiB 회전 파일 하나는 ≈3MB). 디렉터리·파일이 없으면 `unconfigured`·`no_file`, `access.log` 읽기 실패는 `error`.
- **회전 파일 캐시** — 회전 파일은 압축이 끝나면 바뀌지 않으므로 파일마다 한 번 읽어 시(時) 버킷 집계·최근 5xx 20줄·짝 기록(§3.5)을 메모리에 둔다. 키는 확장자(`.log`·`.log.gz`)를 뗀 이름이다 — 압축 전후를 두 번 세지 않는다.
  - 읽은 때와 크기·수정 시각이 다르면 다시 만든다(압축이 끝나 `.log` 가 `.log.gz` 로 바뀔 때 한 번).
  - 목록에서 사라지거나(보관 기간 삭제), 수정 시각이 읽기 시작점 앞이거나, 압축 10MB 밖으로 밀려나면 같은 회차에 버린다 — 요약이 원본보다 오래 남지 않게.
  - 읽기 시작점이 그 파일 안으로 옮겨 오면(시작점은 한 시간에 한 번 움직인다) 그 파일만 다시 만든다 — 시작점 앞 줄이 짝의 '그날 첫 페이지 줄' 에 남지 않게.
  - 깨진 회전 파일(잘린 gz·틀린 머리·권한)은 035 그대로 읽은 데까지 세고 `skipped` 에 1 을 더하며 WARNING — 그 결과도 캐시하고, 크기·수정 시각이 바뀌기 전엔 다시 읽지 않는다.
- **`access.log`** 는 캐시하지 않고 통째로 다시 읽는다(이어 읽지 않는다 — 회전된 줄을 두 번 세지 않게). 마지막으로 읽은 지 60초가 지났을 때만 읽고, 그 안에 다른 창이 갱신하면 같은 결과를 나눠 쓴다.
- 창은 늘 읽는 범위 전체를 채운 캐시에서 만든다 — 게이트 뒤면 24시간 창만 불러도 30일치 파일을 채운다. 어느 창이든 짝의 날 속성이 같은 줄에서 나오게 하려는 것이다. 첫 채움(재시작 뒤·게이트 뒤 처음·1시간 비움 뒤)이 3초를 넘기면 `pending` 이고 뒤에서 마저 돈다. 파일 캐시 만들기는 프로세스에서 한 번에 하나다 — 두 창이 같은 파일을 기다리면 한 번만 읽는다.
- 줄은 035 처럼 흘려 읽고, 읽기 시작점 앞 줄은 JSON 을 풀기 전에 `ts` 만 보고 버린다. JSON 이 아니거나 필드가 빠진 줄은 `skipped`. 재시작하면 캐시·열쇠(§3.5)가 모두 사라지고 처음부터 읽는다. 저장소(Redis·디스크)에는 아무것도 쓰지 않는다.
- 마지막 접속 요청에서 1시간이 지나면 파일 캐시·짝 기록·열쇠를 통째로 버린다 — 화면을 오래 열지 않아도 요약이 창과 원본보다 오래 남지 않게(갱신 때마다 다시 거는 타이머 하나). 다음 요청은 첫 채움(3초 기다림·`pending`)으로 다시 채운다.

### 3.4 줄 분류
- **페이지 줄** = GET·상태 200 또는 304·쿼리 뗀 경로의 마지막 조각에 점이 없음(`/`·`/app/`·`/privacy` — 자산·`.php`·301 제외). 035 와 달리 304 를 넣는다 — `/` 는 no-cache 라 다시 온 브라우저가 304 를 받는다.
- **JS 신호 줄** = GET `/clarity.js`·`/app/clarity.js` 의 200·304(033 뒤 no-cache·no-store 라 JS 가 돈 페이지 보기마다 한 줄 — 동의와 무관하게 받는 로더다) 또는 `/api/ws/spreads` 의 101. `/landing/*`·`/assets/*` 같은 하위 자원은 캐시 때문에 신호로 쓰지 않는다.
- **탐색 줄** = 쿼리 뗀 경로(소문자)에 `wp-`·`wordpress`·`xmlrpc`·`.php`·`.env`·`.git`·`.aws`·`.ssh`·`cgi-bin`·`phpmyadmin`·`actuator`·`/admin`·`/login`·`/config`·`/vendor/`·`/boaform`·`/hnap1`·`/owa/`·`/autodiscover`·`/server-status`·`/solr`·`/console`·`.ini`·`.sql`·`.bak`·`/backup`·`/shell`·`/setup`·`/install`·`/debug`·`.ds_store` 중 하나가 든 줄. 우리 경로(`/`·`/app/`·`/privacy`·`/clarity.js`·`/app/clarity.js`·`/assets/`·`/landing/`·`/api/ws/spreads`·`/api/history/*`·`/robots.txt`·`/sitemap.xml`·`/favicon.ico`·`/fonts/`)와 `web/public` 아래 파일은 어느 낱말에도 걸리지 않는다.
- **운영자 흔적** = `referer` 의 호스트가 `localhost`·`*.localhost`·`*.test`·`127.0.0.0/8`·`[::1]`·사설 IPv4(`10/8`·`172.16/12`·`192.168/16`)·그 밖의 IP 글자 그대로(탄력 IP 를 직접 연 출처)·`clarity.microsoft.com`(Clarity 대시보드가 녹화를 그리며 우리 자산을 부른다) 중 하나. 운영자 IP 는 설정·코드에 적지 않는다.
- **종류** — 줄마다 하나, 위에서 먼저 맞은 것(UA 는 대소문자 무관 부분 일치):
  1. `operator` — 운영자 흔적 줄(UA 와 무관).
  2. `unknown` — UA 가 없거나 빈 값.
  3. `search` — `googlebot`·`google-site-verification`·`yeti`·`daumoa`·`bingbot`·`applebot`·`duckduckbot`·`baiduspider`·`yandex`.
  4. `ai` — `gptbot`·`chatgpt-user`·`oai-searchbot`·`claudebot`·`claude-user`·`claude-searchbot`·`perplexitybot`·`perplexity-user`·`bytespider`·`ccbot`·`amazonbot`·`meta-externalagent`.
  5. `preview` — `kakaotalk-scrap`·`slackbot`·`slack-imgproxy`·`telegrambot`·`twitterbot`·`facebookexternalhit`·`discordbot`·`whatsapp`·`linkedinbot`·`mastodon`.
  6. `tool` — `curl`·`wget`·`python`·`go-http`·`axios`·`okhttp`·`node-fetch`·`undici`·`java/`·`apache-httpclient`·`libwww`·`scrapy`·`headless`·`phantomjs`·`dalvik`·`postman`·`lighthouse`·`uptimerobot`.
  7. `scanner` — `bot`·`crawl`·`spider`·`slurp`·`scan`·`zgrab`·`masscan`·`nmap`·`nuclei`·`censys` 가 들었거나 UA 가 `http://`·`https://` 로 시작.
  8. `browser`(사람 브라우저 모양) — UA 가 `Mozilla/5.0` 으로 시작하고 `Chrome/`·`CriOS/`·`Firefox/`·`FxiOS/`·`Safari/`·`Edg`·`OPR/`·`SamsungBrowser/`·`Whale/`·`KAKAOTALK`·`NAVER(inapp`·`Instagram`·`FBAN`·`FBAV`·`Line/` 중 하나가 든 줄. 단 탐색 줄이면 `scanner`.
  9. 나머지(`Mozlila` 오타·브라우저 토큰 없는 `Mozilla/5.0 (Windows NT …)`·Mozilla 로 시작하지 않는 것)는 `scanner`.
- 위장 봇은 종류를 따로 두지 않는다 — JS 신호 없는 브라우저 모양 짝(`shaped` − `confirmed`)과 탐색한 브라우저 짝(`scanner`·`probes`)으로 드러난다(게이트 뒤에는 039 의 망 종류 `cloud` 도).

### 3.5 방문자-일
- **짝** = (가린 IP, UA 원문). 메모리에서는 BLAKE2b(digest 8바이트)로 바꾼 값만 들고, 응답·로그·Redis 어디에도 내지 않는다. 열쇠는 그 줄의 KST 날마다 다르다 — 프로세스가 뜰 때(또는 §3.3 의 1시간 비움 뒤) 만든 `os.urandom(16)` 에 KST 날짜를 섞은 값이다. 같은 짝도 날이 다르면 다른 값이라 날을 넘어 이을 수 없고, 같은 날을 여러 파일이 나눠도 같은 값이라 합칠 수 있다. IP 가 없는 줄은 짝을 만들지 않는다(종류·시간 칸에는 센다).
- **짝 기록** — (파일, KST 날)마다, UA 가 §3.4-8 의 브라우저 모양인 줄(그날 빼려고 운영자 흔적·탐색 줄도)의 짝을 처음 나온 순서로 1,000개까지 둔다. 넘는 짝은 기록하지 않고 `capped` 를 세우며 줄 세기는 그대로다. 짝 하나가 그날 갖는 것: 페이지 줄·JS 신호 줄·101 줄이 있었던 KST 시, 그날 첫 페이지 줄의 시각과 그 줄로 정한 채널·다시 온 여부, 탐색 줄·운영자 흔적 줄이 있었는지, UA 로 정한 기기·OS·브라우저·인앱. IP 는 남기지 않는다.
- 같은 날을 여러 파일이 나눠 가지면 해시로 합친다 — 시는 합집합, 첫 페이지 줄은 이른 쪽, 탐색·운영자는 하나라도 있으면.
- 창 W 에서 세는 짝 — 그날 탐색 줄·운영자 흔적 줄이 없는 짝만:
  - `shaped`(브라우저 모양, 상한): W 안 시에 페이지 줄이나 JS 신호 줄이 있다. 조사 실측으로 이 짝의 페이지 ≈80% 는 하위 자원을 하나도 받지 않은 위장 봇이었다.
  - `confirmed`(확인된 방문자, 하한): W 안 시에 JS 신호 줄이 있다. 광고 차단기가 clarity.js 를 막거나 JS 를 끈 사람은 빠진다.
  - `returning`(다시 온): confirmed 중 그날 첫 페이지 줄이 `/`·`/privacy` 의 304. 새로고침은 그 앞에 200 이 있어 걸리지 않는다. 랜딩이 바뀐 배포 날과 `/app/` 만 본 방문자는 빠진다.
  - `ws.pairs`: shaped 중 W 안 시에 101 줄이 있다.
- **방문자-일** = KST 날마다 따로 센 서로 다른 짝 수의 합(같은 짝이 이틀 오면 2). 날을 넘어 이어 붙이지 않는다.
- **채널** — 그날 첫 페이지 줄로, 위에서 먼저 맞은 것(운영자 흔적 짝은 이미 빠졌다). 출처 호스트가 그 이름과 같거나 `.<그 이름>` 으로 끝나면 맞고(`www.google.co.kr`·`m.search.naver.com` — `spacex.com` 은 `x.com` 이 아니다), `<끝>` 은 도메인 끝 부분(최상위 도메인 — `com`·`co.kr`·`de` 등)이다.
  1. 쿼리에 `utm_source` 가 있음 → `campaign`
  2. 출처가 자기 호스트(`kimptrack.com`·`www.`·`admin.`) → `internal`
  3. `ai` — `chatgpt.com`·`chat.openai.com`·`perplexity.ai`·`claude.ai`·`gemini.google.com`·`copilot.microsoft.com`
  4. `social` — `x.com`·`t.co`·`twitter.com`·`facebook.com`·`instagram.com`·`threads.net`·`youtube.com`·`reddit.com`·`kakao.com`·`band.us`·`blog.naver.com`·`cafe.naver.com`(`m.` 포함)·`dcinside.com`·`fmkorea.com`·`clien.net`·`ppomppu.co.kr`·`coinpan.com`·`t.me`
  5. `search` — `google.<끝>`·`naver.com`·`daum.net`·`bing.com`·`duckduckgo.com`·`yahoo.<끝>`·`baidu.com`·`yandex.<끝>`·`ecosia.org`
  6. 그 밖 외부 출처 → `referral`
  7. 출처 없음 + 인앱 UA → `inapp`
  8. 출처 없음 → `direct`
  9. 그날 페이지 줄이 없음(WS·clarity.js 만) → `unknown`
- 인앱·기기·OS·브라우저의 UA 토큰은 적힌 대로 대소문자를 가린다(§3.4 종류와 다르다). **인앱**(위에서 먼저): `KAKAOTALK` → `kakaotalk`, `NAVER(inapp` → `naver`, `Instagram` → `instagram`, `FBAN`·`FBAV` → `facebook`, `Line/` → `line`, `DaumApps` → `daum`, `BAND` → `band`, Android 웹뷰 표시 `; wv)` → `other`.
- **기기**: `iPad` 또는 `Mobile` 없는 `Android` → `tablet`, `Mobi`·`iPhone`·`Android` → `mobile`, 그 밖 `desktop`(iPadOS 의 Mac UA 는 desktop — 한계). **OS**: `iPhone`·`iPad`·`iPod` → `ios`, `Android` → `android`, `Windows` → `windows`, `Mac OS X`·`Macintosh` → `macos`, `CrOS` → `chromeos`, `Linux` → `linux`, 그 밖 `other`. **브라우저**: 인앱 → `inapp`, `Whale/` → `whale`, `SamsungBrowser/` → `samsung`, `Edg` → `edge`, `OPR/`·`Opera` → `opera`, `Firefox`·`FxiOS` → `firefox`, `Chrome`·`CriOS` → `chrome`, `Safari` → `safari`, 그 밖 `other`.

### 3.6 응답 — 값 키 (036 화면이 읽는 키는 이름·모양 그대로, 더하기만)
| 키 | 형 |
|---|---|
| `window` | 글자 |
| `windows` | 목록 |
| `gateAt` | ms |
| `startTs` | 초 |
| `endTs` | 초 |
| `firstTs` | 초 |
| `totals` | 객체 |
| `hourly` | 목록 |
| `status` | 객체 |
| `recent5xx` | 목록 |
| `ws` | 객체 |
| `classes` | 객체 |
| `visitors` | 객체 |
| `paths` | 목록 |
| `tabs` | 목록 |
| `referrers` | 목록 |
| `utmSources` | 목록 |
| `devices` | 목록 |
| `browsers` | 목록 |

- `firstTs` = 창 안에서 읽은 가장 이른 줄(없으면 null) — `startTs` 보다 늦으면 화면이 '기록 시작' 을 보인다.
- `totals` = `{requests, pages, humanPages, jsViews, probes, ws, skipped}` — `requests` 모든 줄, `pages` 페이지 줄(봇 포함), `humanPages` 종류 `browser` 의 페이지 줄, `jsViews` 종류 `browser` 의 `/clarity.js`·`/app/clarity.js` 200·304 줄(= JS 가 돈 페이지 보기, WS 101 은 넣지 않는다), `probes` 탐색 줄, `ws` 101 줄, `skipped` 창 안 시의 읽지 못한 줄 + 창과 겹친 파일의 `ts` 없는 줄·깨진 회전 파일(파일마다 1).
- `hourly` — 창의 시마다 `{ts, requests, pages, humanPages, jsViews, errors, wsErrors}`(`ts` 는 그 시의 시작, 24·168·720개 — 게이트로 잘리면 더 적다). `errors` = `/api/ws/spreads` 밖 5xx, `wsErrors` = `/api/ws/spreads` 의 5xx.
- `status` = `{"2xx","3xx","4xx","5xx","ws5xx"}` — 모든 줄, `"5xx"` 는 `/api/ws/spreads` 를 뺀 수이고 `"ws5xx"` 가 그 경로. 101 등 다섯 칸 밖은 세지 않는다. `recent5xx` = `{ts, path, status}` 20줄, 최신 앞, `/api/ws/spreads` 뺌, 쿼리 없는 경로 100자.
- `ws` = `{count, pairs, errors, durations}` — `count` 101 줄, `pairs` §3.5, `errors` = `status.ws5xx`, `durations` = `{lt10s, lt1m, lt10m, lt1h, ge1h}`(035 경계 그대로). 지속은 대시보드를 열어 둔 시간이지 머문 시간이 아니다.
- `classes` — 키 여덟 `browser`·`search`·`ai`·`preview`·`tool`·`scanner`·`operator`·`unknown`, 값 `{requests, pages}`. 합은 `totals.requests`·`totals.pages` 와 같다.
- `visitors` = `{confirmed, shaped, returning, capped, days, channels, devices, os, browsers, inApp}` — 수는 창 안 방문자-일의 합, `capped` 는 창과 겹친 어느 (파일, KST 날) 짝 기록이 1,000 에 닿았는지. `days` = 창과 겹친 KST 날마다 `{ts(그날 00:00 KST), confirmed, shaped, returning}`(0 인 날도, ≤31). 나머지는 `[[이름, confirmed, shaped], …]` — 이름은 §3.5 의 채널 아홉·기기 셋·OS 일곱·브라우저 아홉·인앱 여덟(인앱 짝만), shaped 내림차순·같으면 이름순, 0 행은 뺀다.
- 상위 목록 여섯 `paths`·`tabs`·`referrers`·`utmSources`·`devices`·`browsers` — 035 모양 `[[이름, 수], …]`(수 내림차순·같으면 이름순 20개) 그대로이되 **종류 `browser` 의 페이지 줄만** 센다. `paths` 쿼리 뺀 경로 100자, `tabs` 경로가 정확히 `/app/` 인 페이지의 `tab`(허용 여섯 `spread`·`history`·`gap`·`pp`·`health`·`flow`, 키 없으면 `spread`, 빈 값·그 밖 `(기타)`), `referrers` 출처를 `스킴://호스트[:포트]` 로 다시 만든 http(s) 만·자기 호스트 뺌·100자, `utmSources` 소문자 50자(빈 값 안 셈), `devices` `mobile`·`tablet`·`desktop`, `browsers` §3.5 이름.
- 상한: 시마다 목록 키는 목록마다 30 — 이미 있는 키는 세고 새 키가 넘치면 `(기타)`(035 의 창마다 5,000 을 바꾼다 — 봇의 무작위 경로가 메모리를 키우지 않게). 창의 상위 목록은 시마다 센 것을 더해 고르므로 꼬리는 근사값이다.
- IP·UA 원문·쿼리·짝 값은 응답에 없다. `geo` 는 039 가 더한다.
- 036 화면과의 관계: 인자 없는 호출은 24시간 창이라 036 화면이 그대로 돈다. 이름은 같고 뜻이 바뀌는 것 — `pages`(304 포함), `status["5xx"]`·`hourly.errors`·`recent5xx`(WS 경로 뺌), 상위 목록(사람 브라우저 모양만), `devices` 이름(`bot`·`unknown` 없음, `tablet` 있음). 그래서 `admin.js` 의 상위 표 비율 분모를 `totals.pages` → `totals.humanPages` 로 한 줄 고친다. 036 화면은 `ws5xx`·`wsErrors`·`ws.errors` 를 그리지 않으므로 042 전까지 배포 때의 WS 502 는 화면에 없다 — 응답 `status.ws5xx` 나 serve 의 caddy 로그로 본다(받아들인 공백, CloudWatch 5xx 경보도 WS 를 뺀다).

### 3.7 부담 (serve t4g.micro — 2026-10-02 조사 실측)
- 지금 양: 하루 ≈706줄·384KB(회전 gz ≈23KB). 종류·짝까지 가르는 세기의 줄당 비용 18.4µs(035 의 세기 15.2µs) → 30일 ≈21,000줄 첫 채움 ≈0.4초, 캐시가 찬 뒤 회차는 `access.log` 다시 읽기(≈13ms)와 창 합치기.
- 최악: 회전은 하루 또는 50MiB 중 먼저 닿는 쪽이라, 몰리는 날엔 50MiB(≈95,000줄 — serve 한 파일 ≈1.8초) 회전 파일이 여럿 생겨 100개까지 쌓인다. 그래서 캐시하는 회전 파일을 압축 10MB(§3.3 — 50MiB 파일 셋쯤, ≈30만 줄 ≈6초)에서 끊는다. 첫 채움 동안은 `pending`. 캐시가 없으면 60초마다 그만큼이라 캐시가 꼭 필요하다.
- api 는 uvicorn 워커 하나라 to_thread 의 세기가 공개 `/api/landing`·WS 허브와 GIL 을 나눠 쓴다 — 관리자 화면을 열 때만 일어난다. 세기는 5,000줄마다 다른 스레드에 차례를 넘긴다(0초 잠들기) — 첫 채움 동안 공개 응답이 오래 기다리지 않게. 그래도 조금 늦을 수 있다(받아들인 위험).
- 메모리: 24시간 회전은 KST 자정과 맞지 않아 (파일, KST 날) 짝 기록이 날마다 둘이다. 시마다 목록(목록 6 × 키 31 × 720시) ≈13MB + 짝 기록 62 × 1,000 ≈16MB → 최악 ≈29MB(조사 로컬 tracemalloc, 창 조립 때의 잠깐 사본 뺌). api 에는 `mem_limit` 이 없고 serve 가용은 ≈360~420MB 다.

### 3.8 엣지
- 게이트 전 `7d`·`30d` 요청 → 24시간 창·`windows ["24h"]`, 24시간 창 밖 파일은 열지 않는다.
- 도는 중 게이트를 지남 → 다음 갱신부터 `windows` 셋, 7일·30일 `startTs` = 게이트, `hourly` 는 게이트부터. 기록이 창보다 짧으면(배포·게이트 직후) `firstTs` 가 `startTs` 보다 늦다.
- 회전 중 읽기: 그 회차는 읽은 만큼, 다음 회차가 새 회전 파일(새 키)을 읽고 `access.log` 를 통째로 바꾼다 — 같은 줄을 두 번 세지 않는다.
- 짝이 1,000 넘음(위장 브라우저 폭주) → 그 (파일, 날)의 새 짝은 기록하지 않고 `capped` true — 방문자 수는 하한이 된다.
- 회전 파일이 압축 10MB 를 넘게 쌓임(몰리는 날) → 오래된 파일은 읽지 않아 `firstTs` 가 `startTs` 보다 늦다. 관리자 화면을 1시간 넘게 안 엶 → 캐시·열쇠가 비고 다음 요청이 다시 채운다(`pending` 일 수 있다).
- 운영자가 보통 브라우저로 연 방문(출처 없음·자기 호스트) → 방문자로 센다(IP 목록이 없어 받아들인 한계). HeadlessChrome·curl 같은 운영 도구는 `tool` 이다.
- 로그 디렉터리 없음 → `unconfigured`·`no_file`(값 키 null, `window`·`windows`·`gateAt` 은 실림). 접속 요약은 Redis 를 쓰지 않아 Redis 불달과 무관하다.

## 4. 검증
**PR 안 — 실행 세션(완료 조건)**. 시작 전에 037 브랜치(또는 037 이 든 main) 위인지 본다 — 아니면 멈추고 묻는다. 네트워크 없음. 가짜 자료는 027 줄 모양을 만드는 도우미(035 의 `access_fakes.py` 를 넓힌다)로 테스트가 만든 디렉터리에 둔다 — 회전 파일 이름 `access-<UTC>-time.log.gz`, 수정 시각 = 마지막 줄 `ts`. 시계와 `PRIVACY_V2_EFFECTIVE` 는 테스트가 바꿔 끼운다.
- 창·게이트: 인자 없음·`24h`·`7d`·`30d`·`7D`·`1y`·빈 값 → 게이트 전엔 모두 `window` 24h·`windows ["24h"]`, 게이트 뒤엔 `7d`·`30d` 만 그 창 / 상수 `"2026-10-12"` → `gateAt` = 2026-10-11T15:00Z(ms) / `startTs`·`hourly` 길이(24h 24, 게이트 이튿날 7d 는 게이트부터, 게이트 + 10일 7d 168, 게이트 + 40일 30d 720) / 시계를 게이트 앞→뒤로 옮기면 같은 앱이 셋을 연다 / 게이트 전: 24시간 창 밖 줄(같은 `access.log` 안)은 어느 값에도 없고 창 밖 회전 파일은 열지 않는다(여는 함수를 감시) / 게이트 뒤: 게이트 앞 줄은 7d·30d 의 어떤 값(`totals`·`hourly`·`visitors`·상위 목록)에도 없고 24h 창 안이면 든다 / `unconfigured`·`pending` 에도 `window`·`windows`·`gateAt` 이 있다.
- 캐시: 둘째 회차에 회전 파일을 다시 열지 않음 / `access.log` 는 60초 지나서만 다시, 60초 안 다른 창 갱신은 다시 읽지 않음 / 사라진 파일의 수가 빠짐 / `.log`·`.log.gz` 짝과 `.log` → `.log.gz` 바뀜 → 두 번 세지 않음 / 크기 바뀐 회전 파일 다시 읽음 / 읽기 시작점이 파일 안으로 옮겨 오면 시작점 앞 줄이 채널·`returning`·`firstTs` 에 남지 않음 / 동시에 온 두 창 요청에 회전 파일을 한 번만 엶 / 느린 가짜 읽기로 첫 채움이 3초를 넘기면 `pending`, 끝난 뒤 다음 요청은 ok / 회전 파일 압축 합이 10MB 를 넘으면 오래된 파일은 열지 않고 캐시에서도 빠짐 / 요청 없이 시계를 1시간 넘기면 파일 캐시·짝 기록이 비고 열쇠가 바뀜.
- 종류: 단계마다 대표 UA 하나 이상 — `Googlebot`·`Yeti`·`Daumoa`·`google-site-verification` / `GPTBot`·`ClaudeBot`·`Claude-User`·`Bytespider`(spider 보다 ai)·`meta-externalagent` / `kakaotalk-scrap`·`facebookexternalhit`·`Slackbot` / `curl`·`python-requests`·`Go-http-client`·`HeadlessChrome`·`Dalvik` / `zgrab`·`SemrushBot`·`https://` 로 시작하는 UA·`Mozlila`·토큰 없는 `Mozilla/5.0 (Windows NT 10.0; Win64; x64)` / Chrome·iPhone Safari·Whale·SamsungBrowser·KAKAOTALK·`NAVER(inapp`·`DaumApps`(search 에 안 걸림)·Instagram·FBAN 은 browser / 빈 UA·UA 키 없음 → unknown / 출처 `localhost` + curl → operator. `classes` 의 requests 합 = `totals.requests`, pages 합 = `totals.pages`.
- 탐색: 낱말마다 걸리는 경로 하나 / 우리 경로 목록과 `web/public` 아래 모든 파일 경로가 안 걸림 / 브라우저 UA 의 `/wp-login.php` 줄 → `scanner`·`probes` +1, 그 짝은 그날 `visitors` 에서 빠지고 다른 날은 셈.
- 운영자: 출처 `localhost`·`kimptrack.localhost`·`a.test`·`127.0.0.1`·`[::1]`·`10.1.2.3`·`172.20.0.1`·`192.168.0.1`·`3.34.104.16`·`clarity.microsoft.com` → `operator`, 그 짝은 그날 방문자·채널에서 빠짐 / `example.com`·`localhost.example.com` 은 아님.
- 페이지·신호: `/` 304 는 페이지 / 자산·`.php`·301 은 아님 / `jsViews` 는 브라우저 종류의 clarity.js 두 경로 200·304 만(WS 101·curl 의 clarity.js·clarity.js 404 는 아님).
- 방문자: 페이지만 → shaped 1·confirmed 0 / clarity.js 함께 → confirmed 1 / 하루 페이지 열 번 → 1 / 같은 짝 이틀 → 2·`days` 둘 / 같은 날을 두 파일이 나눔 → 1이고 채널은 이른 파일의 첫 페이지 줄 / 첫 페이지 `/` 304 → returning, `/` 200 뒤 304 → 아님, 첫 페이지 `/app/` → 아님 / 24시간 창 앞 시의 JS 신호만 있는 짝 → 그 창에서 confirmed 아님(7일 창은 셈) / `ws.pairs` / 1,001번째 짝 → `capped` true·`totals.requests` 는 다 셈 / IP 칸 없는 줄 → 짝 없음 / 열쇠를 고정하면 같은 짝도 다른 KST 날이면 해시가 다르고 같은 날 두 파일이면 같다.
- 채널: 단계마다 하나 — `utm_source` + google 출처 → campaign, 자기 호스트 → internal, `chatgpt.com` → ai, `m.blog.naver.com` → social(search 보다 먼저), `m.search.naver.com`·`www.google.co.kr` → search, `example.com` → referral, 출처 없음 + KAKAOTALK → inapp, 출처 없음 → direct, 그날 WS 만 → unknown.
- 기기·OS·브라우저·인앱: iPad → tablet·ios, `Mobile` 없는 Android → tablet, iPhone → mobile·ios, Mac → desktop·macos, CrOS → chromeos, Whale → whale, `Edg/` → edge, 인앱 → inapp, `; wv)` → 인앱 other / 목록 정렬·0 행 빠짐.
- 5xx: `/api/ws/spreads` 502 → `ws5xx`·`hourly.wsErrors`·`ws.errors` 에만, `/` 500 → `"5xx"`·`errors`·`recent5xx`.
- 상위 목록: 봇·운영자·탐색 페이지 줄은 여섯 목록에 없음 / `tabs`·`utmSources`·`referrers` 의 035 규칙 그대로 / 한 시에 새 경로 31개째부터 `(기타)`.
- 개인정보: 응답 바이트와 `caplog` 에 가린 IP·UA 원문·쿼리·짝 해시(테스트가 열쇠를 고정해 계산한 값)가 없다 / 파서에 문장에 가린 IP·UA 가 든 예외를 넣으면(3초 뒤 뒤에서 마저 도는 갱신 포함) `caplog` 와 025 Slack 가짜 받는 곳에 그 글자가 없고 ERROR 도 없다.
- 035 회귀: 파일 없음 `unconfigured`·`no_file` / `access.log` 읽기 실패 `error` / 깨진 회전 gz 읽은 데까지·`skipped` +1 / 44MB 한 파일 흘려 읽기 tracemalloc 최고 8MB 이하(035 단언 유지). 035 테스트 중 이 스펙이 바꾼 동작(304·`devices` 이름·5xx·상위 목록·키 5,000)은 이 계약으로 고친다.
- 화면: `test_admin.py` 정적 단언에 `admin.js` 의 상위 표 분모가 `totals.humanPages`.
- 성능 — pytest 밖 임시 스크립트(레포에 넣지 않는다)로 재고, 로컬 값과 serve 환산(로컬 × 6 — 조사에서 잰 Mac 대 t4g.micro 비)을 §5 에 적는다. 넘으면 멈추고 묻는다.
  1. 지금 규모 30일(하루 700줄 — 브라우저 모양 짝 150·그중 JS 신호 10, tool·scanner·탐색 줄 섞음, 회전 gz 30개 + `access.log`): 첫 채움 로컬 ≤ 0.1초(serve ≈0.6초 — §3.7 의 ≈0.4초), 채운 뒤 창 셋을 차례로 부른 한 회차 ≤ 0.05초.
  2. 50MiB 회전 파일 하나(≈95,000줄, 무작위 경로·UA): 읽기 로컬 ≤ 0.4초(serve ≈2.4초 — §3.7 의 ≈1.8초).
  3. 최악 30일(날마다 회전 파일 둘·시마다 목록마다 새 키 31·(파일, 날)마다 짝 1,000): 캐시 tracemalloc ≤ 40MB.
  4. 2 의 읽기 동안 같은 이벤트 루프의 지연(10ms 잠들기가 넘친 시간) 최댓값 ≤ 100ms — 5,000줄마다 차례 넘기기의 효과를 같은 측정으로 본다.
- nginx: 로컬 Docker 로 web 이미지 `nginx -t`, 가짜 api(받은 경로·쿼리를 되돌림)에 `/svc/api/admin/access?window=7d` → 백엔드가 `/admin/access?window=7d` 를 받는다(035 §5 의 방법). 설정은 고치지 않는다.
- 기존 스펙 재검증: `cd server && ruff check . && ruff format --check . && pytest -q`, `cd web && npm run lint && npm run build`. 커밋마다 diff 300줄 이하(분류 표 → 파일 캐시 → 방문자-일 → 응답·창 → 테스트 → 문서).

**배포 뒤 — 사람(완료 조건 아님, status.md 비고 "038 운영 확인 대기")**: 첫 회전 파일이 생긴 뒤 `/svc/api/admin/access` 의 24시간 수가 serve 의 caddy 로그로 따로 센 값과 같은지(두 번 세지 않음) / 게이트 뒤 `windows` 셋·7일 창이 게이트부터 차는지.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
(실행 후 기록)
```

## 6. 갱신할 문서
**이 PR 이 고치는 문서**
- `docs/context/status.md` — admin 행 server 칸의 "`/admin/access`(caddy 로그 24시간 요약·탭별 진입)" → "`/admin/access`(caddy 로그 요약 `window` 24h·7d·30d — 7d·30d 는 처리방침 v2 시행일부터, 방문자 종류 여덟·확인/브라우저 모양 방문자-일·유입 채널·기기·인앱, 회전 파일별 메모리 캐시 — 038)", 비고 끝에 "· 038 운영 확인 대기(첫 회전 파일 뒤 수 대조·게이트 뒤 7일 창)". 알려진 빚에 넷: `(038) 확인된 방문자는 하한(광고 차단기·JS 끔은 빠진다), 다시 온은 랜딩이 바뀐 배포 날과 /app/ 만 본 방문자를 놓치고, 운영자가 보통 브라우저로 연 방문은 방문자로 센다(IP 목록 없음)`, `(038) 회전 파일별 캐시는 운영 회전 파일로 아직 확인하지 못했다(첫 시간 회전 미관측) — 이름 규칙은 035 로컬 caddy 확인에 기댄다`, `(038) 30일 첫 채움(최악 — 압축 10MB 상한까지 ≈6초)이 api 워커 하나의 GIL 을 공개 응답과 나눠 쓴다 — 관리자 화면을 열 때만, 5,000줄마다 차례를 넘긴다`, `(038) 042 전까지 WS 5xx(배포 때 502)는 관리자 화면에 없다 — 응답 status.ws5xx·serve caddy 로그로 본다`.
- `CLAUDE.md` — 스펙 인덱스 038 행 상태 → DONE. 035 행 범위의 "`/admin/access`(caddy 로그 24시간 요약·탭별 진입·최근 5xx)" → "`/admin/access`(caddy 로그 요약 — 창·분류·방문자는 038)" — 실행 세션은 인덱스의 상태만 고치지만(CLAUDE.md §5), 038 뒤 035 행 범위가 거짓이 되므로 설계 세션이 허락한 예외다.
- `docs/context/architecture.md` — '현재 구조' admin 항목의 035 접속 부분(`access.py`·`access_tally.py` 설명)을 038 모듈로 바꾼다(창·게이트, 회전 파일 캐시, 줄 분류 표, 방문자-일과 짝 기록, 창 조립)과 테스트 파일. 원칙·데이터 흐름 문장은 그대로다.
- `docs/context/product.md` — 용어 절에 "**방문자-일·확인된 방문자**(관리자 접속 요약, 038): (가린 IP, 브라우저 정보) 짝을 KST 하루마다 따로 센 수 — 같은 사람이 이틀 오면 2. 확인 = 그날 JS 가 돈 흔적(`/clarity.js`·대시보드 WS)이 있는 짝(하한), 브라우저 모양 = 페이지를 연 짝(상한, 위장 봇 포함)." 기능 목록 admin 행 "접속 요약" → "접속 요약(최근 30일까지 — 방문자 종류·확인된 방문자·유입 채널)".
- `docs/context/dev-setup.md` — '검증용 스모크' 의 `curl -s localhost:8001/admin/access` 줄에 "`?window=30d` 도 같다 — 로컬은 `unconfigured` 이고 `window`·`windows`·`gateAt` 은 실린다".
- `docs/specs/035-monitoring-visits.md`:
  - §1 "caddy 접속 로그의 24시간 요약(어디서 들어와 무엇을 여는가)" → "caddy 접속 로그의 요약(창·분류는 038)".
  - §2 '처리방침(032)' 줄의 "접속 요약은 caddy 기록을 읽어 메모리에서 셀 뿐이고(032 1절이 "api 가 읽는다" 를 적는다)" → "접속 요약은 caddy 기록을 읽어 메모리에서 셀 뿐이고(창은 처리방침 v2 시행일 전 24시간, 뒤 최근 30일까지 — 038 §3.2, 방침 문장은 037)".
  - §3.1 "예외는 Clarity(§3.3)." → "예외는 Clarity(§3.3)와 접속 요약의 `window`·`windows`·`gateAt`(038 — 늘 싣는다)." 주기 표 아래 문장 끝에 "access 는 창(038 — 24h·7d·30d)마다 따로 60초 칸이고 `access.log` 다시 읽기는 창 셋이 나눠 쓴다".
  - §3.2 는 읽는 계약·compose·읽는 범위(받아들인 위험 — "요약에는 24시간만, IP 는 필요 없다" → "요약에는 최근 30일까지만. IP 는 방문을 세는 되돌릴 수 없는 값(038)과 나라·망 종류 찾기(039)에만 메모리에서 쓰고 응답·로그에는 내지 않는다") 세 줄만 남기고, 창·페이지 판정·응답·IP 줄은 "창·줄 분류·집계·응답은 038 이 정한다(`GET /admin/access?window=…`)" 한 줄로.
  - §3.4 serve 문장의 접속 로그 부분 → "접속 요약의 부담은 038 §3.7"(Clarity 문장은 남긴다). §3.5 의 접속 엣지 넷(읽는 중 회전·파일 없음·기록 짧음·깨진 회전 파일) → "접속 요약의 엣지는 038 §3.8". §4 '접속 요약:' 줄 → "접속 요약은 038 §4". §5·§7 기록은 그대로 둔다.
- `docs/specs/036-admin-v2.md`:
  - §3.2 느린 묶음 표의 `/svc/api/admin/access` 행 `035` → `038`. 접속 피드 복사 줄 → "`/svc/api/admin/access` = 부분 하나(60초, 038) — 인자 없이 부르면 24시간 창. `window`·`windows`·`gateAt`(늘 실림)·`startTs`·`endTs`·`firstTs`·`totals{requests, pages, humanPages, jsViews, probes, ws, skipped}`·`hourly[{ts, requests, pages, humanPages, jsViews, errors, wsErrors}]`(`errors` = `/api/ws/spreads` 밖 5xx)·`status{"2xx","3xx","4xx","5xx","ws5xx"}`·`recent5xx[{ts, path, status}]` 20줄(WS 경로 뺌)·`ws{count, pairs, errors, durations}`·`classes`(종류 여덟 `{requests, pages}`)·`visitors`(방문자-일 `confirmed`·`shaped`·`returning`·`capped`·`days`·`channels`·`devices`·`os`·`browsers`·`inApp`)·상위 목록 여섯(사람 브라우저 모양 페이지 줄만 — `devices` 는 `mobile`·`tablet`·`desktop`). IP·UA 원문·짝 값 없음, 폴링·canary 제외."
  - §3.4 접속 '서버 기록 24시간' 의 표 여섯 "이름·수·비율 막대, 상위 10" → "이름·수·비율 막대(비율 = 수 ÷ `totals.humanPages`), 상위 10".
- `docs/specs/027-observability.md` — §3.2 끝 줄 "api 가 이 디렉터리를 읽기 전용으로 읽어 관리자 페이지에 24시간 요약을 준다(035) — 새 저장은 없다." → "api 가 이 디렉터리를 읽기 전용으로 읽어 관리자 페이지에 최근 30일까지의 요약을 준다(035·038 — 처리방침 v2 시행일 전에는 24시간) — 새 저장은 없다(메모리에서만)."

040(Clarity 요약 v2)과 나란히 고치는 곳 — 035 §2 처리방침 줄·§3.1 주기 표와 그 아래 문장·§3.5·§4, 036 §3.2 느린 묶음 표, CLAUDE.md 035 행 — 은 권장 머지 순서(040 → 038)대로 늦게 머지하는 쪽이 main 을 받아 두 고침을 합친다.

**담당자에게 제안**: 없다 — 경로·nginx·compose 가 그대로라 016·018·021 의 문장이 맞다.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
- 남은 빚:
