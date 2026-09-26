# 외부 uptime 감시 등록 (스펙 025 §3.6)

박스가 통째로 죽으면 앱 안의 Slack 알림은 아무 소리도 못 낸다. 그래서 **박스 밖**에서 `/health` 를 주기적으로 찔러 주는 서비스가 하나 필요하다. 사람이 한 번 등록하면 끝이다.

## 왜 `/health` 하나로 되는가
`https://kimptrack.com/api/health` 는 serve 박스의 api 가 답하지만, 그 답은 collect 박스의 수집기가 매초 Redis 에 쓰는 심장박동(`collect:heartbeat`, TTL 30초)을 읽은 결과다. 그래서 이 URL 하나가 다음을 전부 덮는다.
- serve 박스·caddy·api 컨테이너가 죽음 → 응답 없음
- data 박스(Redis)가 죽음 → `503 redis_down`
- collect 박스·수집기가 죽거나 틱 루프가 30초 넘게 멈춤 → `503 stale`
- 정상 → `200 {"status":"ok", …, "lastTickAt": <ms>}`

거래소 하나가 끊기는 것은 여기서 안 잡힌다(틱은 계속 돈다) — 그건 수집기가 직접 Slack 으로 보낸다(60초 넘게 끊길 때 발생·복구 짝).

## 등록 절차
1. Slack 에서 Incoming Webhook 을 만든다(앱 → Incoming Webhooks → 채널 선택). 그 URL 을 collect·serve 박스의 `~/marketlens/server/.env` 에 `SLACK_WEBHOOK_URL=` 로 넣고 컨테이너를 다시 띄운다. 채널에 `[collector] 🟢 collector 기동` 과 `[api] 🟢 api 기동` 이 오면 앱 쪽은 끝이다.
2. 무료 uptime 서비스 하나를 고른다 — UptimeRobot 또는 Better Stack. 둘 다 Slack 연동이 내장돼 있다. 무료 티어의 확인 간격·모니터 수는 가입 시점에 서비스 페이지에서 확인한다(여기 숫자를 박지 않는다 — 바뀐다).
3. HTTP(S) 모니터를 하나 만든다.
   - URL: `https://kimptrack.com/api/health`
   - 방식: GET, 기대 = HTTP 200 (503 은 다운으로 취급되어야 한다 — 수집 정체가 곧 장애다)
   - 간격: 무료 티어의 가장 짧은 값
   - 알림: 1번과 같은 Slack 채널
4. 등록 직후 테스트 알림을 한 번 보내 채널에 오는지 본다.

## 알아둘 것
- **배포 중 1~2분 다운 알림은 정상이다.** collect 박스는 배포 때 이미지를 다시 빌드해서 수집이 잠깐 멈추고(021), 그 30초 뒤부터 `/health` 가 `stale` 이 된다. 곧 복구 알림이 따라온다.
- 알림이 너무 자주 오면 먼저 `docker logs marketlens-server` 로 `stale` 원인(거래소 끊김인지, 틱 루프 예외인지)을 본다. 앱의 Slack 메시지에 원인이 같이 와 있을 것이다.
- 후속 후보: CloudWatch Agent 로 디스크 80%·메모리 알람 → SNS(스펙 025 범위 밖).
