# Redis 비밀번호 켜기·확인·되돌리기 (스펙 021 §3.7)

data 박스의 Redis(6379)는 값을 넣기 전까지 보안그룹(collect·serve 그룹만)이 유일한 벽이다. 코드와 compose 는 이미 배포돼 있고, 사람이 `REDIS_PASSWORD` 를 **세 박스 `server/.env` 에 같은 값으로** 넣으면 켜진다. 비어 있으면 지금처럼 인증 없이 돈다.

- data: compose 가 `--requirepass "${REDIS_PASSWORD:-}"` 로 Redis 를 띄운다. 빈 값은 '비밀번호 없음'이다.
- collect(`server`)·serve(`api`): 값이 있으면 접속마다 `AUTH default <값>` 을 보낸다(구독 연결 포함).

## 순서: collect·serve 먼저, data 마지막
- 비밀번호가 없는 Redis 도 `AUTH default <값>` 은 받아 준다(redis 7.4 실측). 그래서 클라이언트에 먼저 넣으면 어느 순간에도 거절이 없다.
- data 에 먼저 넣으면 클라이언트가 다시 뜰 때까지 틱 인계·표 발행·대시보드 표가 멈춘다. **data 에만 값을 넣어 둔 채로 두지 않는다** — 배포 워크플로가 data → collect → serve 순서로 `up` 하므로, 아무 PR 이나 머지되면 그 배포가 data 를 먼저 켜고 collect 빌드(1~2분) 동안 끊긴다.
- Redis 를 다시 만들면 1~2초 끊긴다(운영 AOF 적재 0.1초 — 2026-10-13 로그). 그 사이 인계된 틱 1~2개는 버려지고(009) 허브는 1초 뒤 다시 구독한다.

## 0. 준비
- [ ] 값을 만든다 — 로컬에서 `openssl rand -hex 32`. 16진 글자만이라 compose 치환·따옴표 문제가 없다. 값은 문서·채팅·커밋·셸 명령 줄에 적지 않는다.
- [ ] data 박스에서 지금 상태를 본다.

```bash
ssh ml-data
docker exec marketlens-redis redis-cli ping              # PONG — 아직 비밀번호 없음
docker exec marketlens-redis redis-cli INFO errorstats   # NOAUTH·WRONGPASS 줄이 있으면 숫자를 적어 둔다(없는 게 보통)
```

## 1. collect — 값 넣고 server 다시 띄우기
수집이 수십 초 멈춘다(배포 때와 같고, 빌드가 없어 더 짧다).

```bash
ssh ml-collect
cd ~/marketlens
nano server/.env    # REDIS_PASSWORD=<값> 한 줄
docker compose --profile collect --env-file .env --env-file server/.env up -d --force-recreate server
```
- [ ] 값이 들어갔다(값은 찍지 않는다):
  `docker exec marketlens-server python -c "from app.core.config import get_settings; print(bool(get_settings().redis_password))"` → `True`
- [ ] Redis 오류가 없다: `docker logs --since 3m marketlens-server 2>&1 | grep -i -E "redis|noauth|wrongpass"` → 아무것도 안 나온다
- [ ] `curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/health` → `200`(첫 틱 뒤)

## 2. serve — 값 넣고 api 다시 띄우기
대시보드 웹소켓이 한 번 끊겼다가 브라우저가 알아서 다시 붙는다.

```bash
ssh ml-serve
cd ~/marketlens
nano server/.env    # 1 과 같은 값
docker compose --profile serve --env-file .env --env-file server/.env up -d --force-recreate api
```
- [ ] `docker exec marketlens-api python -c "from app.core.config import get_settings; print(bool(get_settings().redis_password))"` → `True`
- [ ] `docker exec marketlens-web wget -qO- http://api:8000/admin/status | grep -o '"redis":"[a-z]*"'` → `"redis":"ok"`

1·2 중 하나라도 `True` 가 아니면 여기서 멈춘다.

## 3. data — 켜기 (둘 중 하나)

### 3-A. 다시 만들기 (명령 하나, 1~2초 끊김)
```bash
ssh ml-data
cd ~/marketlens
nano server/.env    # 1·2 와 같은 값
docker compose --profile data --env-file .env --env-file server/.env up -d redis
```

### 3-B. 끊김 없이 지금 켜기 (ACL)
돌고 있는 Redis 에 비밀번호를 건다. 이미 붙어 있는 연결은 그대로 이어지고 새 연결부터 비밀번호가 필요하다(로컬 실측).

```bash
ssh ml-data
cd ~/marketlens
nano server/.env    # 같은 값 — 안 넣으면 다음 재기동·배포 때 비밀번호가 풀린다
docker exec -it marketlens-redis redis-cli
```
redis-cli 안에서 `ACL SETUSER default on >값`(`>` 바로 뒤에 값, 띄우지 않는다) → `OK`, 그리고 `exit`. 호스트 셸 기록에 값이 남지 않게 redis-cli 안에서 친다.

`server/.env` 가 바뀌어 compose 의 redis 명령이 달라졌으므로 **다음 배포(아무 PR 머지)가 redis 를 다시 만든다** — 그때 1~2초 끊기고 같은 값으로 뜬다. 클라이언트는 이미 값을 들고 있어 거절은 없다.

## 4. 확인
```bash
# data
docker exec marketlens-redis redis-cli ping    # NOAUTH Authentication required. = 켜짐
docker exec -it marketlens-redis redis-cli --user default --askpass INFO errorstats
#   값 입력 → errorstat_NOAUTH(값 없이 온 접속)·errorstat_WRONGPASS(틀린 값)가 0(3-A, 재시작으로 0부터) 또는 0 에 적은 숫자(3-B)에서
#   몇 분 두고 다시 봐도 늘지 않는다
docker exec -it marketlens-redis redis-cli --user default --askpass INFO clients | grep connected_clients   # 켜기 전과 비슷(9 안팎)
# collect
docker logs --since 5m marketlens-server 2>&1 | grep -i -E "redis|noauth|wrongpass"   # 없음
# serve
docker exec marketlens-web wget -qO- http://api:8000/admin/status | grep -o '"redis":"[a-z]*"'   # "redis":"ok"
curl -s -o /dev/null -w '%{http_code}\n' https://kimptrack.com/api/health   # 200
```
- [ ] 브라우저 `https://kimptrack.com/app/` 스프레드 표가 매초 갱신되고, 관리자 화면 개요의 Redis 가 정상이다.

## 되돌리기 — 켤 때와 거꾸로(data 먼저)

### 빨리 (재기동 없이)
```bash
ssh ml-data
docker exec -it marketlens-redis redis-cli --user default --askpass ACL SETUSER default nopass   # 값 입력 → OK, 곧바로 비밀번호 없음
```
클라이언트는 값이 남아 있어도 붙는다(비밀번호 없는 Redis 가 `AUTH default <값>` 을 받는다). 이어서 data 의 `server/.env` 에서 `REDIS_PASSWORD` 값을 비운다 — 안 비우면 다음 배포가 다시 켠다. 비운 compose 명령도 다음 배포 때 redis 를 한 번 다시 만든다(1~2초).

### 끝까지
1. data `server/.env` 의 값을 비우고 `docker compose --profile data --env-file .env --env-file server/.env up -d redis`
2. (골라서) collect·serve 의 값도 비우고 1·2 의 `--force-recreate` 로 다시 띄운다 — 반드시 1 **뒤에**.

## 값 바꾸기 (끊김 없이)
Redis 사용자는 비밀번호를 여럿 가질 수 있다 — 옛 값과 새 값을 잠깐 함께 받게 해 두고 바꾼다.
1. data: `docker exec -it marketlens-redis redis-cli --user default --askpass`(옛 값 입력) → `ACL SETUSER default >새값` — 옛 값도 계속 받는다
2. collect·serve: `server/.env` 를 새 값으로 바꾸고 1·2 처럼 다시 띄워 `True`·`"redis":"ok"` 확인
3. data: `server/.env` 를 새 값으로 바꾸고 3-A — 다시 만든 Redis 는 새 값 하나만 받는다
4. 4 의 확인

## 알아둘 것
- 값을 넣은 뒤 박스를 새로 만들면(ec2-setup.md) 그 박스 `server/.env` 에도 같은 값을 넣는다.
- 값은 `docker inspect marketlens-redis` 의 명령 줄(Cmd)에 보이고 `ps` 에는 안 보인다(Redis 프로세스 제목). 박스에 들어올 수 있는 사람은 `server/.env` 도 읽으므로 받아들였다.
- 로컬 dev compose 도 같은 키를 읽는다 — 로컬 `server/.env` 는 비워 둔다. 넣었다면 `redis-cli` 에 `--user default --askpass` 를 붙인다.
