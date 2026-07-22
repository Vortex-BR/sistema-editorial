#!/bin/sh
set -eu

python -m app.startup --settings-only

printf '%s\n' '{"event":"startup.replica_policy","deployment_mode":"all-in-one","supported_app_replicas":1,"beat_replicas":1,"horizontal_scaling_supported":false,"message":"API, Worker, Celery Beat and Nginx share this container; keep exactly one App replica"}'

echo "Applying database migrations..."
alembic upgrade head

echo "Validating production startup requirements..."
python -m app.startup

echo "Starting Nginx, API, Worker and Celery Beat..."
exec /usr/bin/supervisord -c /etc/supervisor/seo-supervisord.conf
