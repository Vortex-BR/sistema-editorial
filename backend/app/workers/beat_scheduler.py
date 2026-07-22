from __future__ import annotations

import time

from celery.beat import PersistentScheduler

from app.core.config import settings
from app.services.operational_heartbeat import record_beat_heartbeat


class OperationalHeartbeatScheduler(PersistentScheduler):
    def __init__(self, *args, **kwargs):
        self._next_operational_heartbeat = 0.0
        super().__init__(*args, **kwargs)

    def tick(self, *args, **kwargs) -> float:
        now = time.monotonic()
        if now >= self._next_operational_heartbeat:
            record_beat_heartbeat()
            self._next_operational_heartbeat = (
                now + settings.operational_heartbeat_interval_seconds
            )
        scheduler_delay = max(0.0, float(super().tick(*args, **kwargs)))
        heartbeat_delay = max(0.0, self._next_operational_heartbeat - now)
        return min(scheduler_delay, heartbeat_delay)
