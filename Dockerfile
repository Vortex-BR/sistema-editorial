FROM node:22-alpine AS frontend-build

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ARG VITE_API_URL=/api/v1
ENV VITE_API_URL=${VITE_API_URL}
RUN npm run build

FROM python:3.12-slim AS backend-dependencies

WORKDIR /build
COPY backend/requirements-runtime.txt ./requirements-runtime.txt
RUN python -m pip install --no-cache-dir \
        --prefix=/install -r requirements-runtime.txt

FROM python:3.12-slim

WORKDIR /app/backend
ARG GIT_SHA=
ARG APP_COMMIT_SHA=
ARG APP_BUILD_VERSION=
ARG APP_SOURCE_DIGEST=
ARG APP_IMAGE_SOURCE=https://github.com/Vortex-BR/seo-docker
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/var/lib/seo \
    TMPDIR=/var/lib/seo/tmp \
    SKILLS_PATH=/app/skills/default \
    APP_COMMIT_SHA=${APP_COMMIT_SHA:-${GIT_SHA}} \
    APP_BUILD_VERSION=${APP_BUILD_VERSION:-easypanel-${GIT_SHA}} \
    APP_SOURCE_DIGEST=${APP_SOURCE_DIGEST:-${GIT_SHA}}
LABEL org.opencontainers.image.revision=${APP_COMMIT_SHA} \
    org.opencontainers.image.version=${APP_BUILD_VERSION} \
    org.opencontainers.image.source=${APP_IMAGE_SOURCE}

# GitHub Actions supplies the three APP_* values. EasyPanel source builds
# supply GIT_SHA, which becomes the immutable revision and source identifier.
# Refuse every build that has neither form of production identity.
RUN python -c "import os,re,sys; values={name: os.environ.get(name, '').strip() for name in ('APP_COMMIT_SHA','APP_BUILD_VERSION','APP_SOURCE_DIGEST')}; invalid=[name for name in ('APP_COMMIT_SHA','APP_SOURCE_DIGEST') if re.fullmatch(r'[0-9a-f]{40}', values[name]) is None]; invalid += ['APP_BUILD_VERSION'] if values['APP_BUILD_VERSION'].lower() in {'', 'development', 'unversioned', 'easypanel-'} else []; sys.exit('Production image build arguments are invalid: ' + ', '.join(invalid)) if invalid else None"

RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx supervisor \
    && groupadd --system --gid 10001 seo \
    && useradd --system --uid 10001 --gid seo --home-dir /var/lib/seo \
        --shell /usr/sbin/nologin seo \
    && mkdir -p /var/lib/seo/run /var/lib/seo/log /var/lib/seo/tmp \
        /var/lib/seo/celery /var/lib/seo/nginx/client_temp \
        /var/lib/seo/nginx/proxy_temp /var/lib/seo/nginx/fastcgi_temp \
        /var/lib/seo/nginx/uwsgi_temp /var/lib/seo/nginx/scgi_temp \
    && chown -R 10001:10001 /var/lib/seo \
    && chmod 0750 /var/lib/seo /var/lib/seo/run /var/lib/seo/log \
        /var/lib/seo/tmp /var/lib/seo/celery /var/lib/seo/nginx \
        /var/lib/seo/nginx/client_temp /var/lib/seo/nginx/proxy_temp \
        /var/lib/seo/nginx/fastcgi_temp /var/lib/seo/nginx/uwsgi_temp \
        /var/lib/seo/nginx/scgi_temp \
    && rm -rf /var/lib/apt/lists/*

COPY --from=backend-dependencies /install/ /usr/local/

COPY --chown=root:seo backend/alembic.ini ./alembic.ini
COPY --chown=root:seo backend/alembic/ ./alembic/
COPY --chown=root:seo backend/app/ ./app/
COPY --chown=root:seo skills/ /app/skills/
COPY --chown=root:seo --from=frontend-build /frontend/dist/ /usr/share/nginx/html/
COPY --chown=root:seo deploy/easypanel/nginx.conf /etc/nginx/nginx.conf
COPY --chown=root:seo deploy/easypanel/supervisord.conf /etc/supervisor/seo-supervisord.conf
COPY --chown=root:seo deploy/easypanel/entrypoint.sh /usr/local/bin/seo-entrypoint
RUN python -c "import json; from pathlib import Path; Path('/app/build-info.json').write_text(json.dumps({'commit_sha':'${APP_COMMIT_SHA}','build_version':'${APP_BUILD_VERSION}','source_digest':'${APP_SOURCE_DIGEST}'}, sort_keys=True), encoding='utf-8')" \
    && chmod 0440 /app/build-info.json \
    && chown root:seo /app/build-info.json \
    && chmod 0550 /usr/local/bin/seo-entrypoint \
    && chmod 0640 /etc/nginx/nginx.conf /etc/supervisor/seo-supervisord.conf \
    && chown root:seo /app/backend /app/skills /usr/share/nginx/html \
    && chmod -R u=rwX,g=rX,o= /app/backend /app/skills /usr/share/nginx/html \
    && rm -f /etc/nginx/sites-enabled/default

USER 10001:10001

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/v1/health', timeout=3)" || exit 1

ENTRYPOINT ["/usr/local/bin/seo-entrypoint"]
