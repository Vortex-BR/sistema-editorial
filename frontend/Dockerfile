FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_URL=http://localhost:8000/api/v1
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build
FROM nginx:1.30.4-alpine-slim
RUN addgroup -S -g 10001 seo \
    && adduser -S -D -H -u 10001 -G seo -s /sbin/nologin seo \
    && mkdir -p /var/lib/seo/nginx/client_temp \
        /var/lib/seo/nginx/proxy_temp /var/lib/seo/nginx/fastcgi_temp \
        /var/lib/seo/nginx/uwsgi_temp /var/lib/seo/nginx/scgi_temp \
        /var/lib/seo/run \
    && chown -R 10001:10001 /var/lib/seo \
    && chmod -R 0750 /var/lib/seo
COPY --chown=root:seo nginx.conf /etc/nginx/nginx.conf
COPY --chown=root:seo --from=build /app/dist /usr/share/nginx/html
RUN chmod 0640 /etc/nginx/nginx.conf \
    && chown root:seo /usr/share/nginx/html \
    && chmod -R u=rwX,g=rX,o= /usr/share/nginx/html
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["/usr/sbin/nginx"]
CMD ["-g", "daemon off;"]
