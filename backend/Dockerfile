FROM python:3.12-slim
WORKDIR /app
ARG APP_COMMIT_SHA=unversioned
ARG APP_BUILD_VERSION=development
ARG APP_SOURCE_DIGEST=unversioned
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HOME=/var/lib/seo \
    TMPDIR=/var/lib/seo/tmp \
    APP_COMMIT_SHA=${APP_COMMIT_SHA} \
    APP_BUILD_VERSION=${APP_BUILD_VERSION} \
    APP_SOURCE_DIGEST=${APP_SOURCE_DIGEST}
RUN groupadd --system --gid 10001 seo \
    && useradd --system --uid 10001 --gid seo --home-dir /var/lib/seo \
        --shell /usr/sbin/nologin seo \
    && mkdir -p /var/lib/seo/tmp /var/lib/seo/celery \
    && chown -R 10001:10001 /var/lib/seo \
    && chmod 0750 /var/lib/seo /var/lib/seo/tmp /var/lib/seo/celery
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=root:seo . .
RUN python -c "import json; from pathlib import Path; Path('/app/build-info.json').write_text(json.dumps({'commit_sha':'${APP_COMMIT_SHA}','build_version':'${APP_BUILD_VERSION}','source_digest':'${APP_SOURCE_DIGEST}'}, sort_keys=True), encoding='utf-8')" \
    && chown root:seo /app \
    && chmod -R u=rwX,g=rX,o= /app \
    && chmod 0440 /app/build-info.json
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
