# web 이미지 — 멀티스테이지: Node 22 로 빌드 → nginx 가 정적 파일 서빙 (스펙 007 §3)
FROM node:22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
# /api/ → ${COLLECT_HOST}:8000/ 프록시 + SPA fallback (nginx.conf 참고)
# 021 — conf.d 가 아니라 templates 에 둔다: 이미지 entrypoint(20-envsubst-on-templates.sh)가 기동 시
# COLLECT_HOST 를 채워 /etc/nginx/conf.d/default.conf 로 써 준다. 변수 없이 conf.d 에 두면 ${COLLECT_HOST} 가 문자 그대로 남는다.
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
