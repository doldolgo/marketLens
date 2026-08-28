# web 이미지 — 멀티스테이지: Node 22 로 빌드 → nginx 가 정적 파일 서빙 (스펙 007 §3)
FROM node:22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
# /api/ → server:8000/ 프록시 + SPA fallback (nginx.conf 참고)
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
