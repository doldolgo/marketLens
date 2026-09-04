# conventions.md — 규칙

## 코드
- Python 은 ruff 로 lint+format(설정은 server 의 pyproject 에 고정). 줄 길이 위반(E501)은 잡지 않는다 — 한국어 메시지가 길어지기 때문.
- pytest(async 테스트 자동 인식).
- Python 타입힌트 필수.
- 주석은 한국어로 **왜** 를 쓴다.
- 거래소 커넥터는 서로 코드 공유 금지. 각 거래소 quirk 가 섞이면 디버깅 불가.
- TypeScript: oxlint, 인라인 style 객체 + CSS 변수(토큰의 진실은 `docs/design/theme.css`, 화면 구조의 진실은 `docs/design/reference/` — 웹은 `web/src/shared/` 복사본을 쓴다). 라이브러리 추가는 스펙에 명시된 경우만.
- 기능 간 import 금지. `core/`·`shared/` 만 공유.
- 추측성 코드 금지: 스펙에 없는 옵션·유연성·에러 핸들링을 넣지 않는다.

## 테스트
- 모든 BE 기능은 `features/<name>/tests/` 에 테스트. 네트워크 없음 — 거래소 호출은 fake 로 대체.
- 테스트는 **스펙 §4 의 동작 목록을 보고 실행 세션이 직접 쓴다.**
- 테스트 대상은 공개 동작(HTTP 응답·계산 결과)이다. private 함수를 직접 단언하지 않는다.
- FE 테스트 러너 도입은 별도 스펙으로.

## Git
- 브랜치: `feat/<spec-name>`, `fix/…`, `chore/…`. main 직접 push 금지.
- 커밋: Conventional Commits, **영어**, 제목에 스펙 번호. 예) `feat(spreads): add spreads feature (spec 003)`
- 커밋 1개의 diff 는 **300줄을 넘기지 않는다**(문서 포함). 넘으면 논리 단위로 쪼갠다 — 리뷰가 한 호흡에 끝나는 크기.
- 커밋·PR 에 도구 서명을 넣지 않는다(Co-Authored-By, "Generated with …" 류 푸터 금지).
- PR: 제목·본문 **한글**. 본문 3줄: 무엇을 / 왜 / 테스트.
- 문서 변경은 코드와 **같은 PR** 에.

## CI/배포
- PR 은 CI(`server`·`web` 두 check) 통과가 필수다. main 머지 = EC2 자동 배포.

## 스펙 완료 조건 (실행 세션 체크리스트)
1. 스펙 §4 의 검증 조건이 모두 통과 (pytest / build / lint / curl 스모크). 실제로 돌린 명령은 스펙 §5 에 기록
2. 스펙의 "갱신할 문서" 항목을 모두 수정 (`docs/context/status.md` 는 항상 포함)
3. `CLAUDE.md` 스펙 인덱스 상태를 DONE 으로
4. 보고서 작성: 스펙 파일 끝 `§7 실행 보고` 에 — 소요 파일 목록 / 스펙에 없어서 추측한 사소한 지점 / 실행 중 함께 고친 절 / 남은 빚
5. 커밋 (push 는 사람이)
