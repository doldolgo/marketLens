# web 이미지 — 멀티스테이지: Node 22 로 빌드 → nginx 가 정적 파일 서빙 (스펙 007 §3)
FROM node:22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
# /api 허용 목록 프록시(028) + SPA fallback (nginx.conf 참고)
# 021 — conf.d 가 아니라 templates 에 둔다: 이미지 entrypoint(20-envsubst-on-templates.sh)가 기동 시
# COLLECT_HOST 를 채워 /etc/nginx/conf.d/default.conf 로 써 준다. 변수 없이 conf.d 에 두면 ${COLLECT_HOST} 가 문자 그대로 남는다.
COPY nginx.conf /etc/nginx/templates/default.conf.template
# 029 — 관리자 server(:8081, 게시 안 함). 같은 envsubst 규칙(COLLECT_HOST 하나)으로 conf.d/admin.conf 가 된다
COPY nginx-admin.conf /etc/nginx/templates/admin.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
# 029 — 관리자 화면(정적 셋, 빌드 없음). 공개 root(/usr/share/nginx/html) 밖이라 공개 server 로는 받아 갈 수 없다
COPY admin /usr/share/nginx/admin
# 029 — 관리자 접속 기록 디렉터리. compose 가 호스트 ./logs/admin 을 바인드하지만, 바인드가 없어도 nginx 가 뜨게
# 이미지가 만든다(열지 못하면 공개 server 까지 내려간다). /var/log/nginx 는 stdout·stderr 링크라 건드리지 않는다
RUN mkdir -p /var/log/nginx-admin
EXPOSE 80
