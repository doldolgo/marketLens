# 038 — access-v2

상태: DONE | 의존: **037 privacy-v2**(상수 `PRIVACY_V2_EFFECTIVE`) — `feat/037-privacy-v2` 위에 쌓고 037 머지 뒤 main 으로 옮긴다(PR base 는 늘 main, 037 보다 먼저 머지하지 않는다). main 에는 fix/036-admin-followup 이 있어야 한다. 계약을 쓰는 스펙(쓰는 계약은 §3 에 복사했다): 035(읽는 계약·부분 공통 규칙·파일 고르기 — 접속 요약의 창·집계·응답은 이 스펙이 넘겨받는다), 027(caddy 줄 모양·IP 가림), 029(관리자 nginx), 036(화면이 읽는 키), 002(탭 id), 033(clarity.js 캐시 규칙), 037(처리방침 문장·시행일). 뒤에 쌓는 스펙: 039(짝 기록에 나라·망 종류, 응답에 `geo`), 042(창 고르기 화면).

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
운영자가 관리자 화면에서 서버 접속 기록을 최근 24시간·7일·30일로 나눠 '언제·누가·어디서 들어왔나' 를 본다. 지금 요약(035)은 24시간뿐이고, 봇이 페이지의 절반을 넘는데 사람과 섞어 세며, 배포 때마다 WebSocket 502 가 5xx 칸을 빨갛게 만든다. 이 스펙이 끝나면 줄마다 방문자 종류(여덟)를 가르고, JS 가 돈 흔적으로 '확인된 방문자'(하한)와 '브라우저 모양 방문자'(상한)를 함께 세며, 유입 채널·기기·인앱을 방문자 기준으로 준다. 7일·30일 창과 짝을 쓰는 방문자 세기는 처리방침 v2 시행일(037) 0시부터만 열린다 — 그 전에는 035 처럼 줄 단위로만 센다.

## 2. 범위
- 만드는 것: api `GET /admin/access` 의 `window` 인자와 v2 응답(기능 폴더 `admin`), 회전 파일별 메모리 캐시, 줄 분류 표(종류·탐색 경로·운영자 흔적·채널·인앱·기기·OS·브라우저), 날마다 센 방문자, 계약 테스트, `web/admin/admin.js` 한 줄(상위 표 비율 분모).
- 하지 않는 것: 화면 개편(042 — 이 스펙 뒤에도 036 화면은 인자 없이 불러 24시간을 그대로 그린다). 나라·망 종류(039 — 이 스펙의 응답에는 `geo` 가 없다). nginx·Caddyfile·compose 변경. 새 저장(Redis·디스크·로그 어디에도). 새 라이브러리(BLAKE2b 는 표준 `hashlib`). 운영자 IP 목록. 처리방침 문장(037).
- 처리방침: 이 스펙은 037 이 2절 서버 접속 기록 문단에 적는 사실대로만 동작한다 — (가) 최근 30일까지의 요약을 서버 메모리에서만 만들고 따로 저장하지 않으며 다시 시작하면 사라진다 (나) 같은 방문을 하루에 한 번만 세려고 IP 앞부분·브라우저 정보를 되돌릴 수 없는 값으로 바꿔(바꾸는 방식은 다시 시작할 때마다·날마다 달라진다) 메모리에서만 쓰고, 그 값에 그날 페이지를 연 시각과 시간대·들어온 길·기기·운영체제·브라우저 종류(앱 안 브라우저면 어느 앱인지)·페이지의 스크립트를 받았는지(화면 분석 동의와 상관없이 페이지가 부르는 파일의 요청으로 판단)·대시보드에 실시간 연결을 했는지·다시 온 방문인지·보안 허점을 찾는 요청이나 운영자가 연 흔적이 있었는지를 붙여 최근 30일까지만 둔다 — §3.5 의 짝 기록이 이것뿐이다 (다) 시행일 전 기록은 24시간 요약에만 쓰고 짝을 쓰는 처리에는 쓰지 않는다. 그래서 시행일 전에는 짝을 하나도 만들지 않고, 지금 판의 24시간 요약(보안·이용 통계) 안의 줄 단위 집계만 한다(§3.2). UA 로 봇 종류를 나누는 것은 그 안이다 — 037 대조표가 '풀어 적음' 으로 밝힌다.
- 바꾸는 기존 것: 035 §1·§2·§3.1·§3.2·§3.4·§3.5·§4, 036 §3.2·§3.4, 027 §3.2 끝 줄(문장은 §6). 셋 다 이 레포 주인 담당이다. 경로·nginx·compose 가 그대로라 016·018·021 에는 고칠 문장이 없다.

## 3. 동작

### 3.1 요청·창
- `GET /admin/access?window=24h|7d|30d` — api 역할(collector 404, OpenAPI 는 api 에만 — 035 그대로). 관리자 nginx 의 `= /svc/api/admin/access` 는 `rewrite ^ /admin/access break;` 로 경로만 바꾸고 원래 쿼리를 뒤에 붙여 넘긴다(바꿀 글에 `?` 가 없으면 nginx 가 원 인자를 붙인다) — `nginx-admin.conf` 는 고치지 않는다.
- 창의 시(N): `24h` 24 · `7d` 168 · `30d` 720.
- 인자가 없거나, 위 셋과 글자가 정확히 같지 않거나(`7D`·`1y`·빈 값), 지금 고를 수 없는 창(게이트 전 `7d`·`30d`)이면 24시간 창으로 답한다 — 422·400 없음. 실제로 답한 창은 `window` 가 말한다.
- 공통 규칙(035 §3.1 복사): 늘 200 JSON·키 camelCase, `…At` 은 epoch ms, `…Ts` 와 `hourly`·`recent5xx`·`days` 의 `ts` 는 epoch 초. 응답은 부분 하나 `{state, code, fetchedAt, refreshSec, …값 키}` — `state` 는 `ok`·`unconfigured`·`error`·`pending`, `code` 는 ok 면 null·아니면 `no_file`·예외 이름(하위 부분 `visitors` 는 `before_gate` 도), 오류 문장 없음. ok 가 아니면 값 키는 null — 단 `window`·`windows`·`gateAt` 은 상태와 무관하게 늘 싣는다(화면이 창 버튼을 그리게).
- 갱신: 창마다 따로 60초 칸(`refreshSec` 60). 요청이 왔을 때 그 창이 비었거나 60초가 지났으면 갱신 하나를 띄우고 3초까지 기다린다 — 끝나면 새 값, 아니면 직전 결과(없으면 `pending`)를 답하고 갱신은 뒤에서 마저 돈다. 요청이 없으면 파일을 읽지 않는다. 파일 읽기·세기는 `asyncio.to_thread`. 로그·예외는 035 그대로다(처리기 안 예외는 그 부분 `error`·500 없음, 실패는 `marketlens.admin` WARNING 에 부분 이름 `access`·`code` 만 10분 1줄, ERROR 없음). 갱신 전체(3초 뒤 뒤에서 마저 도는 부분 포함)의 모든 예외를 잡아 부분 이름과 예외 종류 이름만 남기고, 예외 문장·traceback 은 남기지 않는다 — 문장에 가린 IP·UA 가 실릴 수 있고 025 가 ERROR 를 Slack 으로 보낸다(잡히지 않은 태스크 예외도 ERROR 다).

### 3.2 게이트 — 처리방침 v2 시행일
- 한 곳: `server/app/core/config.py` 의 `PRIVACY_V2_EFFECTIVE = "YYYY-MM-DD"`(KST 날짜, 037 이 만든다). 게이트 시각 = 그날 00:00 Asia/Seoul(= 전날 15:00Z). 응답 `gateAt`(ms)에 늘 싣는다.
- 지금 < 게이트면 `windows` = `["24h"]`, 지금 ≥ 게이트면 `["24h","7d","30d"]` — 같은 프로세스가 시계만 지나면 재시작 없이 연다.
- `startTs` = 지금이 든 시의 시작 − (N−1)시간(N 은 §3.1). `7d`·`30d` 는 max(그 값, 게이트 초). `endTs` = 지금(초).
- **읽기 시작점** = min(24시간 창 시작, max(30일 창 시작, 게이트)). 이 앞 줄은 어떤 값에도 들지 않고, 이 앞에서 끝난 파일은 읽지도 캐시하지도 않는다. 게이트 전에는 24시간 창 시작과 같다 — 개정 전 기록은 개정 전 목적(24시간 요약) 안에서만 쓰인다. 게이트 뒤 첫 하루는 24시간 창이 게이트 앞 시간을 품지만 7일·30일 창에는 들지 않는다. 그래서 30일 창은 게이트 + 30일부터 꽉 찬다.
- **짝 게이트**: (가린 IP, UA) 짝을 쓰는 처리 — 짝 해시·짝 기록·`visitors` 전부(확인·모양·다시 온·채널·기기·OS·브라우저·인앱)·`ws.pairs`, 039 의 나라·망 종류 — 는 KST 날짜가 게이트 이상인 줄에만 한다. 게이트 전 날의 줄은 해시하지도 짝 기록에 넣지도 않고, 줄 단위 집계(종류·경로·출처·상태·시간 칸·5xx 나눔·상위 목록)에만 든다. 그래서 게이트 전에는 `visitors` 가 `before_gate`·`ws.pairs` 가 null 이고(§3.6), 게이트 뒤 24시간 창이 게이트 앞 시간을 품어도 그 줄은 짝 값에 들지 않는다.
- KST 는 UTC+9 고정이다. 게이트가 KST 자정이라 게이트 앞뒤 줄이 같은 KST 날에 섞이지 않는다.

### 3.3 읽기·캐시
- 읽는 계약(027·035 복사): caddy 가 도메인 요청마다 JSON 한 줄을 `ACCESS_LOG_DIR`(serve 의 `logs/caddy`, api 에 읽기 전용)의 `access.log` 에 쓴다. 쓰는 필드 — `ts`(epoch 초 실수, 줄의 둘째 필드), `request.client_ip`(없으면 `request.remote_ip` — IPv4 /24·IPv6 /48 로 가려져 있다), `request.method`·`request.uri`(경로+쿼리, 검색어 키는 지워져 있다), `status`, `duration`(초), `referer`(출처 `스킴://호스트[:포트]` 또는 빈 값), `ua`. 폴링 다섯 경로와 canary 는 기록되지 않는다. `/api/ws/spreads` 는 연결이 끝날 때 101 한 줄. 하루 또는 50MiB 에서 회전해 gzip 된 회전 파일 `access-<UTC 시각>-<size|time>.log.gz` 를 같은 디렉터리에 90일·100개까지 둔다(압축 중엔 같은 이름 `.log` 가 잠깐 함께 있고 그때는 `.log` 하나만 읽는다). 회전 파일의 수정 시각은 마지막 줄 무렵이다.
- 파일 고르기: `access.log` 와 수정 시각이 읽기 시작점 뒤인 회전 파일 — 회전 파일은 최신부터 압축 풀린 크기를 더해 100MiB(104,857,600바이트)를 넘는 첫 파일에서 멈춘다. `.gz` 는 gzip 꼬리의 ISIZE(마지막 4바이트)로, `.log`(압축 전·압축 중 — 그때는 `.log` 만 읽는다)는 파일 크기로 센다. 넘는 오래된 파일은 읽지도 캐시하지도 않고 `firstTs` 가 늦어진다. 몰리는 날엔 50MiB 마다 회전해 파일이 100개까지 쌓일 수 있어 첫 채움을 줄 수로 묶으려는 것이다(압축 크기로 세면 잘 눌리는 몰림이 빠져나간다 — 지금 하루 ≈400KB 라 30일 ≈12MB 로 늘 다 든다). 꼬리가 4바이트 미만이거나 ISIZE 가 100MiB 를 넘는 `.gz` 는 깨진 파일로 보고 0 으로 센다(50MiB 에서 회전하는 파일은 그럴 수 없고 잘린 gz 의 꼬리는 아무 값이다 — 읽으면 아래 깨진 회전 파일 규칙대로). 디렉터리·파일이 없으면 `unconfigured`·`no_file`, `access.log` 읽기 실패는 `error`.
- **회전 파일 캐시** — 회전 파일은 압축이 끝나면 바뀌지 않으므로 파일마다 한 번 읽어 시(時) 버킷 집계·최근 5xx 20줄·짝 기록(§3.5 — 게이트 뒤 KST 날만)을 메모리에 둔다. 키는 확장자(`.log`·`.log.gz`)를 뗀 이름이다 — 압축 전후를 두 번 세지 않는다.
  - 읽은 때와 크기·수정 시각이 다르면 다시 만든다(압축이 끝나 `.log` 가 `.log.gz` 로 바뀔 때 한 번).
  - 목록에서 사라지거나(보관 기간 삭제), 수정 시각이 읽기 시작점 앞이거나, 100MiB 밖으로 밀려나면 같은 회차에 버린다 — 요약이 원본보다 오래 남지 않게.
  - 읽기 시작점이 그 파일 안으로 옮겨 오면(시작점은 한 시간에 한 번 움직인다) 그 파일만 다시 만든다 — 시작점 앞 줄이 짝의 '그날 첫 페이지 줄' 에 남지 않게.
  - 깨진 회전 파일(잘린 gz·틀린 머리·권한)은 035 그대로 읽은 데까지 세고 `skipped` 에 1 을 더하며 WARNING — 그 결과도 캐시하고, 크기·수정 시각이 바뀌기 전엔 다시 읽지 않는다.
- **`access.log`** 는 캐시하지 않고 통째로 다시 읽는다(이어 읽지 않는다 — 회전된 줄을 두 번 세지 않게). 마지막으로 읽은 지 60초가 지났을 때만 읽고, 그 안에 다른 창이 갱신하면 같은 결과를 나눠 쓴다.
- 창은 늘 읽는 범위 전체를 채운 캐시에서 만든다 — 게이트 뒤면 24시간 창만 불러도 30일치 파일을 채운다. 어느 창이든 짝의 날 속성이 같은 줄에서 나오게 하려는 것이다. 첫 채움(재시작 뒤·게이트 뒤 처음·1시간 비움 뒤)이 3초를 넘기면 `pending` 이고 뒤에서 마저 돈다. 파일 캐시 만들기는 프로세스에서 한 번에 하나다 — 두 창이 같은 파일을 기다리면 한 번만 읽는다.
- 줄은 035 처럼 흘려 읽고, 읽기 시작점 앞 줄은 JSON 을 풀기 전에 `ts` 만 보고 버린다. JSON 이 아니거나 필드가 빠지거나 모양이 틀린 줄(`ts` 가 날짜로 바꿀 수 있는 0 이상의 수가 아님·`status` 가 정수가 아님·`duration` 이 유한한 수가 아님 — 실수로 바꿀 수 없는 큰 정수·NaN·무한)은 `skipped` — 한 줄이 요약 전체를 `error` 로 만들지 않는다. 재시작하면 캐시·열쇠(§3.5)가 모두 사라지고 처음부터 읽는다. 저장소(Redis·디스크)에는 아무것도 쓰지 않는다.
- 마지막 접속 요청에서 1시간이 지나면 파일 캐시·짝 기록·열쇠를 통째로 버린다 — 화면을 오래 열지 않아도 요약이 창과 원본보다 오래 남지 않게(갱신 때마다 다시 거는 타이머 하나). 다음 요청은 첫 채움(3초 기다림·`pending`)으로 다시 채운다.

### 3.4 줄 분류
- **페이지 줄** = GET·상태 200 또는 304·쿼리 뗀 경로의 마지막 조각에 점이 없음(`/`·`/app/`·`/privacy` — 자산·`.php`·301 제외). 035 와 달리 304 를 넣는다 — `/` 는 no-cache 라 다시 온 브라우저가 304 를 받는다.
- **JS 신호 줄** = GET `/clarity.js`·`/app/clarity.js` 의 200·304(033 뒤 no-cache·no-store 라 JS 가 돈 페이지 보기마다 한 줄 — 동의와 무관하게 받는 로더다) 또는 `/api/ws/spreads` 의 101. `/landing/*`·`/assets/*` 같은 하위 자원은 캐시 때문에 신호로 쓰지 않는다.
- **탐색 줄** = 쿼리 뗀 경로(소문자)에 `wp-`·`wordpress`·`xmlrpc`·`.php`·`.env`·`.git`·`.aws`·`.ssh`·`cgi-bin`·`phpmyadmin`·`actuator`·`/admin`·`/login`·`/config`·`/vendor/`·`/boaform`·`/hnap1`·`/owa/`·`/autodiscover`·`/server-status`·`/solr`·`/console`·`.ini`·`.sql`·`.bak`·`/backup`·`/shell`·`/setup`·`/install`·`/debug`·`.ds_store` 중 하나가 든 줄. 우리 경로(`/`·`/app/`·`/privacy`·`/clarity.js`·`/app/clarity.js`·`/assets/`·`/landing/`·`/api/ws/spreads`·`/api/history/*`·`/robots.txt`·`/sitemap.xml`·`/favicon.ico`·`/fonts/`)와 `web/public` 아래 파일은 어느 낱말에도 걸리지 않는다.
- **운영자 흔적** = `referer` 의 호스트가 `localhost`·`*.localhost`·`*.test`·`127.0.0.0/8`·`[::1]`·사설 IPv4(`10/8`·`172.16/12`·`192.168/16`)·그 밖의 IP 글자 그대로(탄력 IP 를 직접 연 출처)·`clarity.microsoft.com`(Clarity 대시보드가 녹화를 그리며 우리 자산을 부른다) 중 하나. 운영자 IP 는 설정·코드에 적지 않는다.
- **종류** — 줄마다 하나, 위에서 먼저 맞은 것(UA 는 앞 1,024자로 판정 — 대소문자 무관 부분 일치. 줄마다 다른 긴 UA 의 판정 비용을 묶는다):
  1. `operator` — 운영자 흔적 줄(UA 와 무관).
  2. `unknown` — UA 가 없거나 빈 값.
  3. `search` — `googlebot`·`google-site-verification`·`yeti`·`daumoa`·`bingbot`·`applebot`·`duckduckbot`·`baiduspider`·`yandex`.
  4. `ai` — `gptbot`·`chatgpt-user`·`oai-searchbot`·`claudebot`·`claude-user`·`claude-searchbot`·`perplexitybot`·`perplexity-user`·`bytespider`·`ccbot`·`amazonbot`·`meta-externalagent`.
  5. `preview` — `kakaotalk-scrap`·`slackbot`·`slack-imgproxy`·`telegrambot`·`twitterbot`·`facebookexternalhit`·`discordbot`·`whatsapp`·`linkedinbot`·`mastodon`.
  6. `tool` — `curl`·`wget`·`python`·`go-http`·`axios`·`okhttp`·`node-fetch`·`undici`·`java/`·`apache-httpclient`·`libwww`·`scrapy`·`headless`·`phantomjs`·`dalvik`·`postman`·`lighthouse`·`uptimerobot`.
  7. `scanner` — `bot`·`crawl`·`spider`·`slurp`·`scan`·`zgrab`·`masscan`·`nmap`·`nuclei`·`censys` 가 들었거나 UA 가 `http://`·`https://` 로 시작.
  8. `browser`(사람 브라우저 모양) — UA 가 `Mozilla/5.0` 으로 시작하고 `Chrome/`·`CriOS/`·`Firefox/`·`FxiOS/`·`Safari/`·`Edg`·`OPR/`·`SamsungBrowser/`·`Whale/`·`KAKAOTALK`·`NAVER(inapp`·`Instagram`·`FBAN`·`FBAV`·`Line/` 중 하나가 든 줄. 단 탐색 줄이면 `scanner`.
  9. 나머지(`Mozlila` 오타·브라우저 토큰 없는 `Mozilla/5.0 (Windows NT …)`·Mozilla 로 시작하지 않는 것)는 `scanner`.
- 위장 봇은 종류를 따로 두지 않는다 — JS 신호 없는 브라우저 모양 짝(`shaped` − `confirmed`)과 탐색한 브라우저 줄(`scanner`·`probes`)로 드러난다. 짝 값(앞의 것과 039 의 망 종류 `cloud`)은 게이트 뒤에만 있고, 게이트 전에는 줄 단위 `scanner`·`probes` 로만 보인다.

### 3.5 날마다 센 방문자 (게이트 뒤 KST 날만 — §3.2 짝 게이트)
- **짝** = (가린 IP, UA 원문 전체 — 판정의 1,024자와 무관). 메모리에서는 BLAKE2b(digest 8바이트)로 바꾼 값만 들고, 응답·로그·Redis 어디에도 내지 않는다. 열쇠는 그 줄의 KST 날마다 다르다 — 처음 짝을 만들 때(또는 §3.3 의 1시간 비움 뒤) 만든 `os.urandom(16)` 에 KST 날짜를 섞은 값이다. 같은 짝도 날이 다르면 다른 값이라 날을 넘어 이을 수 없고, 같은 날을 여러 파일이 나눠도 같은 값이라 합칠 수 있다. IP 가 없는 줄과 게이트 전 날의 줄은 짝을 만들지 않는다(종류·시간 칸에는 센다).
- **짝 기록** — (파일, KST 날)마다, UA 가 §3.4-8 의 브라우저 모양인 줄(그날 빼려고 운영자 흔적·탐색 줄도)의 짝을 처음 나온 순서로 1,000개까지 둔다. 넘는 짝은 기록하지 않고 `capped` 를 세우며 줄 세기는 그대로다. 짝 하나가 그날 갖는 것: 페이지 줄·JS 신호 줄·101 줄이 있었던 KST 시, 그날 첫 페이지 줄의 시각과 그 줄로 정한 채널·다시 온 여부, 탐색 줄·운영자 흔적 줄이 있었는지, UA 로 정한 기기·OS·브라우저·인앱. IP 는 남기지 않는다.
- 같은 날을 여러 파일이 나눠 가지면 해시로 합친다 — 시는 합집합, 첫 페이지 줄은 이른 쪽, 탐색·운영자는 하나라도 있으면.
- 창 W 에서 세는 짝 — 그날 탐색 줄·운영자 흔적 줄이 없는 짝만:
  - `shaped`(브라우저 모양, 상한): W 안 시에 페이지 줄이나 JS 신호 줄이 있다. 조사 실측으로 이 짝의 페이지 ≈80% 는 하위 자원을 하나도 받지 않은 위장 봇이었다.
  - `confirmed`(확인된 방문자, 하한): W 안 시에 JS 신호 줄이 있다. 광고 차단기가 clarity.js 를 막거나 JS 를 끈 사람은 빠진다.
  - `returning`(다시 온): confirmed 중 그날 첫 페이지 줄이 `/`·`/privacy` 의 304. 새로고침은 그 앞에 200 이 있어 걸리지 않는다. 랜딩이 바뀐 배포 날과 `/app/` 만 본 방문자는 빠진다.
  - `ws.pairs`: shaped 중 W 안 시에 101 줄이 있다.
- **날마다 센 방문자**(줄여 '방문자(날마다 셈)') = KST 날마다 따로 센 서로 다른 짝 수의 합(같은 짝이 이틀 오면 2). 날을 넘어 이어 붙이지 않는다. 24시간 창의 수는 오늘·어제(KST)를 따로 세어 더한 수 — 사람 수가 아니다. 응답 키(`confirmed`·`shaped` 등)는 이 낱말과 무관하게 그대로다.
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
- 인앱·기기·OS·브라우저도 UA 앞 1,024자로 판정하되 토큰은 적힌 대로 대소문자를 가린다(§3.4 종류와 다르다). **인앱**(위에서 먼저): `KAKAOTALK` → `kakaotalk`, `NAVER(inapp` → `naver`, `Instagram` → `instagram`, `FBAN`·`FBAV` → `facebook`, `Line/` → `line`, `DaumApps` → `daum`, `BAND` → `band`, Android 웹뷰 표시 `; wv)` → `other`.
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
| `visitors` | 부분 |
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
- `ws` = `{count, pairs, errors, durations}` — `count` 101 줄, `pairs` §3.5(`visitors` 가 `ok` 가 아니면 null), `errors` = `status.ws5xx`, `durations` = `{lt10s, lt1m, lt10m, lt1h, ge1h}`(035 경계 그대로). 지속은 대시보드를 열어 둔 시간이지 머문 시간이 아니다.
- `classes` — 키 여덟 `browser`·`search`·`ai`·`preview`·`tool`·`scanner`·`operator`·`unknown`, 값 `{requests, pages}`. 합은 `totals.requests`·`totals.pages` 와 같다.
- `visitors` = 하위 부분 — 바깥 부분 안의 `{state, code, …}` 객체로, 자기 상태를 갖고 바깥 부분을 error 로 만들지 않는다(이 스펙이 정한다. 034 알림의 `slack`·`alarms` 처럼 상태를 따로 갖는 꼴이다) `{state, code, sinceTs, confirmed, shaped, returning, capped, days, channels, devices, os, browsers, inApp}`. 게이트 전에는 `state` `unconfigured`·`code` `before_gate` 이고 나머지 열한 키는 null 이다. 게이트 뒤에는 `ok`·code null, `sinceTs`(초 — 짝을 세기 시작한 때) = max(`startTs`, 게이트 초), 수는 sinceTs 뒤 창 안 날마다 센 방문자의 합, `capped` 는 창과 겹친 어느 (파일, KST 날) 짝 기록이 1,000 에 닿았는지. `days` = 창과 겹치고 게이트 뒤인 KST 날마다 `{ts(그날 00:00 KST), confirmed, shaped, returning}`(0 인 날도, ≤31). 나머지는 `[[이름, confirmed, shaped], …]` — 이름은 §3.5 의 채널 아홉·기기 셋·OS 일곱·브라우저 아홉·인앱 여덟(인앱 짝만), shaped 내림차순·같으면 이름순, 0 행은 뺀다.
- 상위 목록 여섯 `paths`·`tabs`·`referrers`·`utmSources`·`devices`·`browsers` — 035 모양 `[[이름, 수], …]`(수 내림차순·같으면 이름순 20개) 그대로이되 **종류 `browser` 의 페이지 줄만** 센다. `paths` 쿼리 뺀 경로 100자, `tabs` 경로가 정확히 `/app/` 인 페이지의 `tab`(허용 여섯 `spread`·`history`·`gap`·`pp`·`health`·`flow`, 키 없으면 `spread`, 빈 값·그 밖 `(기타)`), `referrers` 출처를 `스킴://호스트[:포트]` 로 다시 만든 http(s) 만·자기 호스트 뺌·100자, `utmSources` 소문자 50자(빈 값 안 셈), `devices` `mobile`·`tablet`·`desktop`, `browsers` §3.5 이름.
- 상한: 시마다 목록 키는 목록마다 30 — 이미 있는 키는 세고 새 키가 넘치면 `(기타)`(035 의 창마다 5,000 을 바꾼다 — 봇의 무작위 경로가 메모리를 키우지 않게). 창의 상위 목록은 시마다 센 것을 더해 고르므로 꼬리는 근사값이다.
- IP·UA 원문·쿼리·짝 값은 응답에 없다. `geo` 는 039 가 더한다(같은 짝 게이트 — 게이트 전 `before_gate`).
- 036 화면과의 관계: 인자 없는 호출은 24시간 창이라 036 화면이 그대로 돈다(`visitors`·`ws.pairs` 는 읽지 않아 게이트 전 null 이어도 같다). 이름은 같고 뜻이 바뀌는 것 — `pages`(304 포함), `status["5xx"]`·`hourly.errors`·`recent5xx`(WS 경로 뺌), 상위 목록(사람 브라우저 모양만), `devices` 이름(`bot`·`unknown` 없음, `tablet` 있음). 그래서 `admin.js` 의 상위 표 비율 분모를 `totals.pages` → `totals.humanPages` 로 한 줄 고친다. 036 화면은 `ws5xx`·`wsErrors`·`ws.errors` 를 그리지 않으므로 042 전까지 배포 때의 WS 502 는 화면에 없다 — 응답 `status.ws5xx` 나 serve 의 caddy 로그로 본다(받아들인 공백, CloudWatch 5xx 경보도 WS 를 뺀다).

### 3.7 부담 (serve t4g.micro — 2026-10-02 조사 실측)
- 지금 양: 하루 ≈706줄·384KB(회전 gz ≈23KB). 종류·짝까지 가르는 세기의 줄당 비용 18.4µs(035 의 세기 15.2µs) → 30일 ≈21,000줄 첫 채움 ≈0.4초, 캐시가 찬 뒤 회차는 `access.log` 다시 읽기(≈13ms)와 창 합치기.
- 최악: 회전은 하루 또는 50MiB 중 먼저 닿는 쪽이라, 몰리는 날엔 50MiB(≈9만~9.5만 줄 — serve 한 파일 ≈2.3초, UA 가 수백 가지로 몰린 꼴) 회전 파일이 여럿 생겨 100개까지 쌓인다. 그래서 캐시하는 회전 파일을 압축 풀린 100MiB(§3.3 — 50MiB 파일 둘·≈19만 줄)에서 끊는다 — 첫 채움 serve ≈4.7초, 모든 줄 UA 가 다른 극단은 ≈7.2초(회전 파일은 한 번 읽어 캐시하므로 처음 한 번뿐). 첫 채움 동안은 `pending`. 캐시가 없으면 60초마다 그만큼이라 캐시가 꼭 필요하다. ISIZE 는 2^32 의 나머지라 4GiB 를 넘는 파일은 작게 세지만, caddy 가 50MiB 를 넘기 전에 회전하므로 그런 회전 파일은 없다.
- 몰리는 날의 `access.log`: 캐시하지 않고 60초마다 통째로 다시 읽으므로(§3.3) 50MiB 까지 자란 `access.log` 는 관리자 화면을 띄워 두는 동안 serve 에서 분당 ≈2.3초를 쓰고, 첫 채움에도 그만큼 더해진다. 이어 읽기는 후속이다 — 지금 하루 ≈400KB 라 ≈13ms.
- 긴 UA: 종류·특성 판정은 UA 앞 1,024자만 본다(§3.4·§3.5) — 줄마다 다른 3,000자 UA 로 찬 50MiB 파일(≈1.5만 줄)도 serve ≈4.6초다(자르기 전 ≈10.6초).
- api 는 uvicorn 워커 하나라 to_thread 의 세기가 공개 `/api/landing`·WS 허브와 GIL 을 나눠 쓴다 — 관리자 화면을 열 때만 일어난다. 따로 차례를 넘기지 않는다 — 인터프리터가 5ms 마다 GIL 을 넘겨 루프 지연은 측정상 ≤30ms(대개 10~20ms, §5 — 5,000줄마다 0초 잠들기는 같은 측정에서 차이가 없었다). 그래도 조금 늦을 수 있다(받아들인 위험).
- 메모리: 24시간 회전은 KST 자정과 맞지 않아 (파일, KST 날) 짝 기록이 날마다 둘이다. 시마다 목록(목록 6 × 키 31 × 720시) ≈13MB + 짝 기록 62 × 1,000 ≈16MB → 최악 ≈29MB(조사 로컬 tracemalloc, 창 조립 때의 잠깐 사본 뺌). api 에는 `mem_limit` 이 없고 serve 가용은 ≈360~420MB 다.

### 3.8 엣지
- 게이트 전 `7d`·`30d` 요청 → 24시간 창·`windows ["24h"]`, 24시간 창 밖 파일은 열지 않는다. 짝 해시는 한 번도 계산하지 않고 `visitors` 는 `before_gate`·`ws.pairs` null, 줄 단위 값은 그대로 찬다.
- 도는 중 게이트를 지남 → 다음 갱신부터 `windows` 셋, 7일·30일 `startTs` = 게이트, `hourly` 는 게이트부터, `visitors` `ok`·`sinceTs` = 게이트(24시간 창은 게이트 앞 시간의 줄 단위 값을 품고 짝 값은 게이트부터). 기록이 창보다 짧으면(배포·게이트 직후) `firstTs` 가 `startTs` 보다 늦다.
- 회전 중 읽기: 그 회차는 읽은 만큼, 다음 회차가 새 회전 파일(새 키)을 읽고 `access.log` 를 통째로 바꾼다 — 같은 줄을 두 번 세지 않는다.
- 짝이 1,000 넘음(위장 브라우저 폭주) → 그 (파일, 날)의 새 짝은 기록하지 않고 `capped` true — 방문자 수는 하한이 된다.
- 회전 파일이 압축 풀린 100MiB 를 넘게 쌓임(몰리는 날) → 오래된 파일은 읽지 않아 `firstTs` 가 `startTs` 보다 늦다. 잘린 회전 gz 는 ISIZE 자리가 아무 값이라 100MiB 를 넘으면 0 으로 세어(§3.3) 오래된 파일을 막지 않는다. 관리자 화면을 1시간 넘게 안 엶 → 캐시·열쇠가 비고 다음 요청이 다시 채운다(`pending` 일 수 있다).
- 운영자가 보통 브라우저로 연 방문(출처 없음·자기 호스트) → 방문자로 센다(IP 목록이 없어 받아들인 한계). HeadlessChrome·curl 같은 운영 도구는 `tool` 이다.
- 로그 디렉터리 없음 → `unconfigured`·`no_file`(값 키 null, `window`·`windows`·`gateAt` 은 실림). 접속 요약은 Redis 를 쓰지 않아 Redis 불달과 무관하다.

## 4. 검증
**PR 안 — 실행 세션(완료 조건)**. 시작 전에 037 브랜치(또는 037 이 든 main) 위인지 본다 — 아니면 멈추고 묻는다. 네트워크 없음. 가짜 자료는 027 줄 모양을 만드는 도우미(035 의 `access_fakes.py` 를 넓힌다)로 테스트가 만든 디렉터리에 둔다 — 회전 파일 이름 `access-<UTC>-time.log.gz`, 수정 시각 = 마지막 줄 `ts`. 시계와 `PRIVACY_V2_EFFECTIVE` 는 테스트가 바꿔 끼운다.
- 창·게이트: 인자 없음·`24h`·`7d`·`30d`·`7D`·`1y`·빈 값 → 게이트 전엔 모두 `window` 24h·`windows ["24h"]`, 게이트 뒤엔 `7d`·`30d` 만 그 창 / 상수 `"2026-10-12"` → `gateAt` = 2026-10-11T15:00Z(ms) / `startTs`·`hourly` 길이(24h 24, 게이트 이튿날 7d 는 게이트부터, 게이트 + 10일 7d 168, 게이트 + 40일 30d 720) / 시계를 게이트 앞→뒤로 옮기면 같은 앱이 셋을 연다 / 게이트 전: 24시간 창 밖 줄(같은 `access.log` 안)은 어느 값에도 없고 창 밖 회전 파일은 열지 않는다(여는 함수를 감시) / 게이트 뒤: 게이트 앞 줄은 7d·30d 의 어떤 값(`totals`·`hourly`·`visitors`·상위 목록)에도 없고 24h 창 안이면 든다 / `unconfigured`·`pending` 에도 `window`·`windows`·`gateAt` 이 있다.
- 짝 게이트: 게이트 전 → `visitors` = `unconfigured`·`before_gate`·나머지 열한 키 null, `ws.pairs` null, 짝 해시 계산 0(해시 함수를 감시 — `window=7d` 를 청해도), `classes`·`totals`·`hourly`·`status`·상위 목록은 값이 있다 / 24h 창이 게이트를 걸침 → 게이트 앞 줄은 `totals`·`hourly`·`classes` 에 들고 `visitors`·`ws.pairs` 에는 없다(같은 IP·UA 가 게이트 앞뒤에 오면 게이트 뒤 날로만 1), `visitors.sinceTs` = 게이트 / 접속 `unconfigured` → `visitors` null.
- 캐시: 둘째 회차에 회전 파일을 다시 열지 않음 / `access.log` 는 60초 지나서만 다시, 60초 안 다른 창 갱신은 다시 읽지 않음 / 사라진 파일의 수가 빠짐 / `.log`·`.log.gz` 짝과 `.log` → `.log.gz` 바뀜 → 두 번 세지 않음 / 크기 바뀐 회전 파일 다시 읽음 / 읽기 시작점이 파일 안으로 옮겨 오면 시작점 앞 줄이 채널·`returning`·`firstTs` 에 남지 않음 / 동시에 온 두 창 요청에 회전 파일을 한 번만 엶 / 느린 가짜 읽기로 첫 채움이 3초를 넘기면 `pending`, 끝난 뒤 다음 요청은 ok / 회전 파일의 압축 풀린 크기 합이 100MiB 를 넘으면 오래된 파일은 열지 않고 캐시에서도 빠짐(압축 크기 합은 예산 안인 잘 눌리는 파일로 — 압축 크기로 세지 않음) / 압축 전·압축 중 회전 `.log` 는 파일 크기로(덜 쓴 `.gz` 의 꼬리가 아니라) / `.gz` 는 ISIZE, 꼬리 4바이트 미만·ISIZE 100MiB 넘음은 0 / 요청 없이 시계를 1시간 넘기면 파일 캐시·짝 기록이 비고 열쇠가 바뀜.
- 종류: 단계마다 대표 UA 하나 이상 — `Googlebot`·`Yeti`·`Daumoa`·`google-site-verification` / `GPTBot`·`ClaudeBot`·`Claude-User`·`Bytespider`(spider 보다 ai)·`meta-externalagent` / `kakaotalk-scrap`·`facebookexternalhit`·`Slackbot` / `curl`·`python-requests`·`Go-http-client`·`HeadlessChrome`·`Dalvik` / `zgrab`·`SemrushBot`·`https://` 로 시작하는 UA·`Mozlila`·토큰 없는 `Mozilla/5.0 (Windows NT 10.0; Win64; x64)` / Chrome·iPhone Safari·Whale·SamsungBrowser·KAKAOTALK·`NAVER(inapp`·`DaumApps`(search 에 안 걸림)·Instagram·FBAN 은 browser / 빈 UA·UA 키 없음 → unknown / 출처 `localhost` + curl → operator / 앞 1,024자 뒤의 `Googlebot` 은 browser, 안이면 search. `classes` 의 requests 합 = `totals.requests`, pages 합 = `totals.pages`.
- 탐색: 낱말마다 걸리는 경로 하나 / 우리 경로 목록과 `web/public` 아래 모든 파일 경로가 안 걸림 / 브라우저 UA 의 `/wp-login.php` 줄 → `scanner`·`probes` +1, 그 짝은 그날 `visitors` 에서 빠지고 다른 날은 셈.
- 운영자: 출처 `localhost`·`kimptrack.localhost`·`a.test`·`127.0.0.1`·`[::1]`·`10.1.2.3`·`172.20.0.1`·`192.168.0.1`·`203.0.113.7`(문서용 주소 — IP 글자 그대로인 출처)·`clarity.microsoft.com` → `operator`, 그 짝은 그날 방문자·채널에서 빠짐 / `example.com`·`localhost.example.com` 은 아님.
- 페이지·신호: `/` 304 는 페이지 / 자산·`.php`·301 은 아님 / `jsViews` 는 브라우저 종류의 clarity.js 두 경로 200·304 만(WS 101·curl 의 clarity.js·clarity.js 404 는 아님).
- 방문자(시계·줄 모두 게이트 뒤): 페이지만 → shaped 1·confirmed 0 / clarity.js 함께 → confirmed 1 / 하루 페이지 열 번 → 1 / 같은 짝 이틀 → 2·`days` 둘 / 같은 날을 두 파일이 나눔 → 1이고 채널은 이른 파일의 첫 페이지 줄 / 첫 페이지 `/` 304 → returning, `/` 200 뒤 304 → 아님, 첫 페이지 `/app/` → 아님 / 24시간 창 앞 시의 JS 신호만 있는 짝 → 그 창에서 confirmed 아님(7일 창은 셈) / `ws.pairs` / 1,001번째 짝 → `capped` true·`totals.requests` 는 다 셈 / IP 칸 없는 줄 → 짝 없음 / 열쇠를 고정하면 같은 짝도 다른 KST 날이면 해시가 다르고 같은 날 두 파일이면 같다.
- 채널: 단계마다 하나 — `utm_source` + google 출처 → campaign, 자기 호스트 → internal, `chatgpt.com` → ai, `m.blog.naver.com` → social(search 보다 먼저), `m.search.naver.com`·`www.google.co.kr` → search, `example.com` → referral, 출처 없음 + KAKAOTALK → inapp, 출처 없음 → direct, 그날 WS 만 → unknown.
- 기기·OS·브라우저·인앱: iPad → tablet·ios, `Mobile` 없는 Android → tablet, iPhone → mobile·ios, Mac → desktop·macos, CrOS → chromeos, Whale → whale, `Edg/` → edge, 인앱 → inapp, `; wv)` → 인앱 other / 목록 정렬·0 행 빠짐 / 앞 1,024자 뒤의 `KAKAOTALK` 은 인앱이 아니고, 그 뒤만 다른 두 UA 는 두 짝(짝은 UA 전체).
- 5xx: `/api/ws/spreads` 502 → `ws5xx`·`hourly.wsErrors`·`ws.errors` 에만, `/` 500 → `"5xx"`·`errors`·`recent5xx` / 5xx 밖 상태(600·999·0·큰 정수)는 `requests` 에만.
- 상위 목록: 봇·운영자·탐색 페이지 줄은 여섯 목록에 없음 / `tabs`·`utmSources`·`referrers` 의 035 규칙 그대로 / 한 시에 새 경로 31개째부터 `(기타)`.
- 개인정보: 응답 바이트와 `caplog` 에 가린 IP·UA 원문·쿼리·짝 해시(테스트가 열쇠를 고정해 계산한 값)가 없다 / 파서에 문장에 가린 IP·UA 가 든 예외를 넣으면(3초 뒤 뒤에서 마저 도는 갱신 포함) `caplog` 와 025 Slack 가짜 받는 곳에 그 글자가 없고 ERROR 도 없다.
- 035 회귀: 파일 없음 `unconfigured`·`no_file` / `access.log` 읽기 실패 `error` / 깨진 회전 gz 읽은 데까지·`skipped` +1 / `ts` 가 시각이 아니거나 `duration` 이 유한한 수가 아닌 줄(실수로 못 바꾸는 큰 정수·풀 수 없는 긴 정수·NaN·무한)은 그 줄만 `skipped` / 44MB 한 파일 흘려 읽기 tracemalloc 최고 8MB 이하(035 단언 유지). 035 테스트 중 이 스펙이 바꾼 동작(304·`devices` 이름·5xx·상위 목록·키 5,000)은 이 계약으로 고친다.
- 화면: `test_admin.py` 정적 단언 — `admin.js` 의 상위 표 비율 분모가 `totals.humanPages` 다(단언 글자 꼴은 실행 세션이 정하고 §7 에 적는다. 042 가 화면을 다시 쓰며 이 단언을 자기 분모 단언으로 바꾼다).
- 성능 — pytest 밖 임시 스크립트(레포에 넣지 않는다)로 재고, 로컬 값과 serve 환산(로컬 × 6 — 조사에서 잰 Mac 대 t4g.micro 비)을 §5 에 적는다. 넘으면 멈추고 묻는다.
  1. 지금 규모 30일(시계는 게이트 + 30일 뒤 — 짝 처리가 모든 날에 돈다. 하루 700줄 — 브라우저 모양 짝 150·그중 JS 신호 10, tool·scanner·탐색 줄 섞음, 회전 gz 30개 + `access.log`): 첫 채움 로컬 ≤ 0.1초(serve ≈0.6초 — §3.7 의 ≈0.4초), 채운 뒤 창 셋을 차례로 부른 한 회차 ≤ 0.05초.
  2. 50MiB 회전 파일 하나(≈95,000줄, 무작위 경로, UA 는 수백 가지 무리에서 무작위 — 실제 몰림 꼴): 읽기 로컬 ≤ 0.4초(serve ≈2.4초). 모든 줄 UA 가 다른 극단과 줄마다 다른 3,000자 UA 도 재어 §5 에 참고로 적는다(한도 밖 — 설계 세션 결정 2026-10-02). 회전 파일 50MiB 둘(= 100MiB)의 첫 채움과 50MiB `access.log` 의 60초 회차도 재어 §3.7·§5 에.
  3. 최악 30일(날마다 회전 파일 둘·시마다 목록마다 새 키 31·(파일, 날)마다 짝 1,000): 캐시 tracemalloc ≤ 40MB.
  4. 2 의 읽기 동안 같은 이벤트 루프의 지연(10ms 잠들기가 넘친 시간) 최댓값 ≤ 100ms — 따로 차례를 넘기지 않고 인터프리터가 5ms 마다 GIL 을 넘기는 것으로 충분한지 본다.
- nginx: 설정은 고치지 않는다. 근거는 `test_admin.py` 의 기존 단언(`= /svc/api/admin/access` 의 rewrite 가 `^ /admin/access break` — 바꿀 글에 `?` 가 없어 nginx 가 원 인자를 붙인다)이고 그대로 통과한다. 실제 넘김(`/svc/api/admin/access?window=7d` → 백엔드 `/admin/access?window=7d`, 035 §5 의 방법)은 실행 세션의 샌드박스가 Docker 를 띄울 수 없어 설계 세션이 머지 전 로컬 Docker 로 한 번 보고 §5 에 적는다.
- 기존 스펙 재검증: `cd server && ruff check . && ruff format --check . && pytest -q`, `cd web && npm run lint && npm run build`. 커밋마다 diff 300줄 이하(분류 표 → 파일 캐시 → 방문자 세기 → 응답·창 → 테스트 → 문서).

**배포 뒤 — 사람(완료 조건 아님, status.md 비고 "038 운영 확인 대기")**: 첫 회전 파일이 생긴 뒤 `/svc/api/admin/access` 의 24시간 수가 serve 의 caddy 로그로 따로 센 값과 같은지(두 번 세지 않음) / 게이트 뒤 `windows` 셋·7일 창이 게이트부터 차는지.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
git log --oneline -1 ced71a0   # 의존 확인 — 037 브랜치 위(PRIVACY_V2_EFFECTIVE = "2026-10-11")
cd server && uv venv -p 3.12 .venv && uv pip install -p .venv -e ".[dev]"   # 추적 중인 egg-info 변경은 git checkout 으로 되돌림
cd web && npm ci
cd server && ruff check . && ruff format --check . && pytest -q
#   1342 passed · 6 failed — 6건 모두 app/features/spreads/tests/test_gauge.py 의 UDP bind PermissionError(샌드박스), 그 밖 실패 0
cd server && pytest -q app/features/admin tests/test_admin.py tests/test_role.py   # 252 passed
cd web && npm run lint && npm run build   # oxlint 경고·오류 0 · build ok
# 커밋마다 — git archive <커밋> server 를 레포 밖에 풀어 같은 venv 로 ruff check·pytest(그 커밋 안의 app 을 읽는지 확인)
# 성능(§4) — 레포 밖 임시 스크립트, 로컬 macOS 26.6 arm64·Python 3.12.13, 여러 번 중 최솟값, serve 환산 = 로컬 × 6
#   1. 지금 규모 30일(시계 = 게이트 + 31일, 하루 700줄 — 브라우저 모양 짝 150·그중 JS 신호 10, 도구·스캐너·탐색 섞음, 회전 gz 30 + access.log)
#      첫 채움 0.094초(serve ≈0.56초) · 채운 뒤 창 셋 한 회차(access.log 다시 읽기 포함) 0.0083초(serve ≈0.05초) — requests 20,987·shaped 4,497·confirmed 300
#   2. 50MiB 회전 파일 하나(무작위 경로·IP, 실제 줄처럼 tls 필드 포함)
#      UA 를 310개 무리(브라우저 250·도구/봇 60)에서 무작위로: 90,175줄·gz 3.5MB → 0.391~0.399초(serve ≈2.35~2.39초) — 한도 0.4초 안
#      모든 줄 UA 가 다름(절반 브라우저 모양): 95,624줄·gz 4.0MB → 0.607~0.609초(serve ≈3.64초) — 한도를 넘는다(§7 질문)
#   3. 최악 30일(날마다 회전 파일 둘·시마다 목록마다 새 키 31·(파일, 날)마다 짝 1,000 — 파일 60): 캐시 tracemalloc 남은 23.8MB·최고 25.7MB(한도 40MB)
#   4. 2 의 읽기 동안 같은 루프의 10ms 잠들기 넘침 최댓값: 무리 7.6~13.4ms · 모두 다름 10.7~17.4ms(한도 100ms)
# nginx — 설정 안 고침. test_admin.py 의 `= /svc/api/admin/access` rewrite(^ /admin/access break) 단언 그대로 통과.
#   실제 넘김(/svc/api/admin/access?window=7d → /admin/access?window=7d)은 샌드박스가 Docker 를 못 띄워 설계 세션 몫
# 검토 반영(2026-10-02) 뒤 다시 — ruff check·format --check 통과 · pytest 1347 passed · 6 failed(같은 test_gauge UDP bind) · web lint·build ok
#   새 테스트 다섯은 고치기 전 커밋(git archive HEAD)에서 실패 확인 — 긴 UA 최고 19.2MB · 회전 뒤 40줄(20) · 깨진 gz 메모 남음 · ts NaN 요약 전체 error
#   성능 1·3·4 는 위와 같은 범위, 2(무리)는 고치기 전·뒤를 번갈아 재어 최솟값 0.401~0.406초 → 0.399~0.400초(오늘 기계 — 차이 없음)
```

## 6. 갱신할 문서
**이 PR 이 고치는 문서**
- `docs/context/status.md` — admin 행 server 칸의 "`/admin/access`(caddy 로그 24시간 요약·탭별 진입)" → "`/admin/access`(caddy 로그 요약 `window` 24h·7d·30d — 7d·30d 는 처리방침 v2 시행일부터, 방문자 종류 여덟·날마다 센 확인/브라우저 모양 방문자·유입 채널·기기·인앱(짝을 쓰는 값은 시행일 뒤 KST 날만 — 그 전 `visitors` 는 `before_gate`), 회전 파일별 메모리 캐시 — 038)", 비고 끝에 "· 038 운영 확인 대기(첫 회전 파일 뒤 수 대조·게이트 뒤 7일 창)". 알려진 빚에 넷: `(038) 확인된 방문자는 하한(광고 차단기·JS 끔은 빠진다), 다시 온은 랜딩이 바뀐 배포 날과 /app/ 만 본 방문자를 놓치고, 운영자가 보통 브라우저로 연 방문은 방문자로 센다(IP 목록 없음)`, `(038) 회전 파일별 캐시는 운영 회전 파일로 아직 확인하지 못했다(첫 시간 회전 미관측) — 이름 규칙은 035 로컬 caddy 확인에 기댄다`, `(038) 30일 첫 채움(최악 — 압축 10MB 상한까지 ≈6초)이 api 워커 하나의 GIL 을 공개 응답과 나눠 쓴다 — 관리자 화면을 열 때만, 5,000줄마다 차례를 넘긴다`, `(038) 042 전까지 WS 5xx(배포 때 502)는 관리자 화면에 없다 — 응답 status.ws5xx·serve caddy 로그로 본다`.
- `CLAUDE.md` — 스펙 인덱스 038 행 상태 → DONE. 035 행 범위의 "`/admin/access`(caddy 로그 24시간 요약·탭별 진입·최근 5xx)" → "`/admin/access`(caddy 로그 요약 — 창·분류·방문자는 038)" — 실행 세션은 인덱스의 상태만 고치지만(CLAUDE.md §5), 038 뒤 035 행 범위가 거짓이 되므로 설계 세션이 허락한 예외다.
- `docs/context/architecture.md` — '현재 구조' admin 항목의 035 접속 부분(`access.py`·`access_tally.py` 설명)을 038 모듈로 바꾼다(창·게이트, 회전 파일 캐시, 줄 분류 표, 날마다 센 방문자와 짝 기록·짝 게이트, 창 조립)과 테스트 파일. 원칙·데이터 흐름 문장은 그대로다.
- `docs/context/product.md` — 용어 절에 "**날마다 센 방문자·확인된 방문자**(관리자 접속 요약, 038): (가린 IP, 브라우저 정보) 짝을 KST 하루마다 따로 세어 더한 수 — 같은 사람이 이틀 오면 2, 24시간 창은 오늘·어제(KST)를 따로 세어 더한 수라 사람 수가 아니다. 확인 = 그날 JS 가 돈 흔적(`/clarity.js`·대시보드 WS)이 있는 짝(하한), 브라우저 모양 = 페이지를 연 짝(상한, 위장 봇 포함). 처리방침 v2 시행일 뒤 KST 날만 센다." 기능 목록 admin 행 "접속 요약" → "접속 요약(최근 30일까지 — 방문자 종류·확인된 방문자·유입 채널)".
- `docs/context/dev-setup.md` — '검증용 스모크' 의 `curl -s localhost:8001/admin/access` 줄에 "`?window=30d` 도 같다 — 로컬은 `unconfigured` 이고 `window`·`windows`·`gateAt` 은 실린다".
- `docs/specs/035-monitoring-visits.md`:
  - §1 "caddy 접속 로그의 24시간 요약(어디서 들어와 무엇을 여는가)" → "caddy 접속 로그의 요약(창·분류는 038)".
  - §2 '처리방침(032)' 줄의 "접속 요약은 caddy 기록을 읽어 메모리에서 셀 뿐이고(032 1절이 "api 가 읽는다" 를 적는다)" → "접속 요약은 caddy 기록을 읽어 메모리에서 셀 뿐이고(창은 처리방침 v2 시행일 전 24시간, 뒤 최근 30일까지 — 038 §3.2, 방침 문장은 037)".
  - §3.1 "예외는 Clarity(§3.3)." → "예외는 Clarity(§3.3)와 접속 요약의 `window`·`windows`·`gateAt`(038 — 늘 싣는다)." 주기 표 아래 문장 끝에 "access 는 창(038 — 24h·7d·30d)마다 따로 60초 칸이고 `access.log` 다시 읽기는 창 셋이 나눠 쓴다".
  - §3.2 는 읽는 계약·compose·읽는 범위(받아들인 위험 — "요약에는 24시간만, IP 는 필요 없다" → "요약에는 최근 30일까지만. IP 는 방문을 세는 되돌릴 수 없는 값(038)과 나라·망 종류 찾기(039)에만 — 처리방침 v2 시행일 뒤 KST 날의 줄만 — 메모리에서 쓰고 응답·로그에는 내지 않는다") 세 줄만 남기고, 창·페이지 판정·응답·IP 줄은 "창·줄 분류·집계·응답은 038 이 정한다(`GET /admin/access?window=…`)" 한 줄로.
  - §3.4 serve 문장의 접속 로그 부분 → "접속 요약의 부담은 038 §3.7"(Clarity 문장은 남긴다). §3.5 의 접속 엣지 넷(읽는 중 회전·파일 없음·기록 짧음·깨진 회전 파일) → "접속 요약의 엣지는 038 §3.8". §4 '접속 요약:' 줄 → "접속 요약은 038 §4". §5·§7 기록은 그대로 둔다.
- `docs/specs/036-admin-v2.md`:
  - §3.2 느린 묶음 표의 `/svc/api/admin/access` 행 `035` → `038`. 접속 피드 복사 줄 → "`/svc/api/admin/access` = 부분 하나(60초, 038) — 인자 없이 부르면 24시간 창. `window`·`windows`·`gateAt`(늘 실림)·`startTs`·`endTs`·`firstTs`·`totals{requests, pages, humanPages, jsViews, probes, ws, skipped}`·`hourly[{ts, requests, pages, humanPages, jsViews, errors, wsErrors}]`(`errors` = `/api/ws/spreads` 밖 5xx)·`status{"2xx","3xx","4xx","5xx","ws5xx"}`·`recent5xx[{ts, path, status}]` 20줄(WS 경로 뺌)·`ws{count, pairs, errors, durations}`·`classes`(종류 여덟 `{requests, pages}`)·`visitors`(하위 부분 — 날마다 센 방문자 `state`·`code`·`sinceTs`·`confirmed`·`shaped`·`returning`·`capped`·`days`·`channels`·`devices`·`os`·`browsers`·`inApp`, 처리방침 v2 시행일 전 `unconfigured`·`before_gate`·값 null — 그때 `ws.pairs` 도 null)·상위 목록 여섯(사람 브라우저 모양 페이지 줄만 — `devices` 는 `mobile`·`tablet`·`desktop`). IP·UA 원문·짝 값 없음, 폴링·canary 제외."
  - §3.4 접속 '서버 기록 24시간' 의 표 여섯 "이름·수·비율 막대, 상위 10" → "이름·수·비율 막대(비율 = 수 ÷ `totals.humanPages`), 상위 10".
- `docs/specs/027-observability.md` — §3.2 끝 줄 "api 가 이 디렉터리를 읽기 전용으로 읽어 관리자 페이지에 24시간 요약을 준다(035) — 새 저장은 없다." → "api 가 이 디렉터리를 읽기 전용으로 읽어 관리자 페이지에 최근 30일까지의 요약을 준다(035·038 — 처리방침 v2 시행일 전에는 24시간) — 새 저장은 없다(메모리에서만)."

040(Clarity 요약 v2)과 나란히 고치는 곳 — 035 §2 처리방침 줄·§3.1 주기 표와 그 아래 문장·§3.4 serve 부담 문장·§3.5·§4, 036 §3.2 느린 묶음 표, CLAUDE.md 035 행 — 은 권장 머지 순서(040 → 038)대로 늦게 머지하는 쪽이 main 을 받아 두 고침을 합친다.

**담당자에게 제안**: 없다 — 경로·nginx·compose 가 그대로라 016·018·021 의 문장이 맞다.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - server `app/features/admin/`: `access_cache.py`(창·게이트·파일 고르기·회전 파일 캐시·`access.log` 60초·창 조립), `access_hours.py`(파일 하나의 세기 — 시 버킷·최근 5xx·짝 기록), `access_classes.py`(줄 풀기·탐색·운영자 흔적·종류), `access_traits.py`(채널·인앱·기기·OS·브라우저), `access_pairs.py`(열쇠·BLAKE2b·짝 기록·날마다 센 방문자), `visits.py`(창마다 칸·1시간 비움·늘 싣는 셋), `router.py`(`window` 인자). 지움: 035 의 `access.py`·`access_tally.py`.
  - 테스트: `tests/access_fakes.py`(넓힘), 새 `test_access_classes.py`·`test_access_visitors.py`·`test_access_window.py`·`test_access_cache.py`·`test_access_privacy.py`, 035 계약을 이 규칙으로 고친 `test_access_summary.py`·`test_access_files.py`·`test_visits.py`, `server/tests/test_admin.py`(분모 단언).
  - web: `web/admin/admin.js` 한 줄(상위 표 비율 분모 `totals.humanPages`).
  - 문서: context 넷(status·architecture·product·dev-setup), 스펙 027·035·036, `CLAUDE.md` 인덱스(035 행 범위·038 상태).
- 추측한 지점 (묻지 않고 정한 사소한 것):
  - 압축 10MB(10,000,000바이트)는 최신부터 더해 넘는 첫 파일에서 멈춘다. 압축 중인 회전 `.log` 는 0 으로 센다 — 잠깐뿐이고 다음 회차가 `.gz` 크기로 센다(압축 전 크기로 세면 그 몇 초 동안 오래된 파일이 밀려났다 다시 읽힌다).
  - `access.log` 는 60초 규칙에 더해 읽기 시작점이 바뀌었을 때(시작점 앞 줄이 짝의 첫 페이지 줄에 남지 않게 — 시작점은 한 시간에 한 번 움직인다)와 회전을 본 회차(새 회전 파일 키가 목록에 나타남·`access.log` inode 바뀜·크기 줄어듦)에도 다시 읽는다 — 회전 직후 60초 안 다른 창 갱신이 옛 `access.log` 결과와 새 회전 파일을 함께 세지 않게(§3.8).
  - 1시간 비움 타이머는 접속 요청마다 다시 건다('마지막 접속 요청' — 60초 칸 안의 요청 포함). 비울 때 창 칸 셋도 새로 만들어 다음 요청이 첫 채움(`pending`)을 본다. 원본 파일이 모두 사라진 회차(`no_file`)에도 캐시를 비운다.
  - 짝 기록 대상은 UA 만으로 정한 종류가 `browser` 인 줄(§3.4 의 2~8 — 운영자·탐색 덮어쓰기 전). HeadlessChrome 처럼 브라우저 토큰이 있어도 앞 종류에 걸리면 짝이 아니다. 빈 UA 만 `unknown`(공백뿐인 UA 는 scanner).
  - 열쇠 = `os.urandom(16)` 뒤에 그 줄의 KST 날짜 글자(`YYYY-MM-DD`)를 붙인 BLAKE2b `key`, 해시 입력 = `IP\0UA`(UTF-8 — 짝 없는 서로게이트도 바이트로).
  - 채널 `campaign` 은 쿼리에 `utm_source` 키가 있으면(빈 값 포함). `utmSources` 목록은 035 대로 빈 값을 세지 않는다. 출처가 http(s) 가 아니거나 모양이 아니면 '출처 없음'. `<끝>` = 그 마디 뒤가 한 마디, 또는 `co`·`com`·`ne`·`or`·`net`·`org`·`ac`·`go`·`gov`·`edu` + 나라 두 글자(`www.google.co.kr` 는 search, `mail.google.com` 도 search).
  - JS 신호의 101 은 메서드를 보지 않는다(035 의 WS 세기와 같다). 시마다 목록 30 = 진짜 키 30 + `(기타)`.
  - `skipped` 의 '창과 겹친 파일' = 회전 파일은 수정 시각 ≥ `startTs`, `access.log` 는 늘.
  - 지금 고를 수 없는 창은 캐시(`AccessLog.summary`) 안에서도 24h 로 바꾼다 — 라우터·피드와 같은 함수.
  - `ts` 가 0 이상 9999-12-30T00:00Z 미만의 수가 아니면(NaN·무한·음수·먼 미래 — 손상·손으로 고친 줄) `ts` 없는 줄로 `skipped` — 한 줄이 `int()`·KST 날짜 바꾸기에서 요약 전체를 `error` 로 만들지 않게(§3.3).
  - 화면 단언 꼴: `test_admin.py::test_screen_top_table_share_is_over_human_pages` — `fillAccess` 본문에 `const pages = num(totals.humanPages) ?? 0;`·`topTable(a[k], label, pages)` 가 있고 `num(totals.pages) ?? 0` 이 없다.
  - 성능 때문에 줄마다 도는 판정(페이지·JS 신호·종류 덮어쓰기)은 `access_hours.py` 에 풀어 썼고, UA 종류 3~7 의 낱말은 정규식 대신 부분 문자열로 찾는다(정규식 대안 ≈3.8µs → ≈1.6µs/UA). UA·출처·특성 판정 메모(4,096 — 512자 넘는 글자는 넣지 않는다)는 그 파일을 읽는 동안만 두고 버린다(깨진 파일로 예외가 나도 — 결과째 캐시되므로). 줄 풀기는 `raw_decode` 로 평범한 튜플(json.loads 와 같이 뒤에 공백 밖 글자가 있으면 `skipped`).
- 실행 중 함께 고친 스펙 절: 없음(027·035·036 은 §6 대로).
- 검토 반영(2026-10-02): 깨진 회전 파일도 판정 메모·날 열쇠를 버림(UA·출처 원문이 캐시에 남던 것), 회전 직후 60초 안 다른 창이 같은 줄을 두 번 세던 것, 긴 UA 가 판정 메모를 파일 크기만큼 키우던 것(44MB·UA ≈1만 자 최고 42MB → 0.3MB), `ts` NaN·무한 줄 하나가 요약 전체를 error 로 만들던 것 — 테스트 다섯(`test_access_privacy`·`test_access_cache`·`test_access_files`).
- 남은 빚:
  - §4 성능 2 는 설계 세션이 (가)로 정했다(2026-10-02): 기준 파일은 UA 를 수백 가지 무리에서 고른 꼴(실제 몰림 — 판정 메모가 맞는다), 모든 줄 UA 가 다른 극단(로컬 0.61초·serve ≈3.6초)은 참고 값(§3.7·§5) — 회전 파일은 한 번 읽어 캐시하므로 극단도 처음 한 번뿐이다.
  - 실제 nginx 넘김(Docker)·브라우저 확인은 설계 세션 몫(샌드박스). 운영 확인은 §4 '배포 뒤'.
  - status 빚 넷(§6).
  - 긴 UA 판정 비용(검토 2026-10-02 — 사람 결정): 종류 낱말 찾기가 UA 길이에 비례해(≈300자 ≈26µs, 3,000자 ≈210µs) 긴 UA 가 줄마다 다른 50MiB 회전 파일은 로컬 ≈3.3초(serve ≈20초 — §5 의 극단 ≈3.6초 밖)다. 판정을 UA 앞 512자로 자르면 줄지만 §3.4·§3.5 동작(그 뒤 토큰)이 바뀐다. 처음 한 번 뒤엔 캐시된다.
