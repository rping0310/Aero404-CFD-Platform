"""Background queue polling and lightweight server health collection."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from typing import Any

from .queue_manager import JobQueueManager
from .real_runner import RealRunner
from ..config import settings


class JobMonitor:
    def __init__(self, manager: JobQueueManager, runner: RealRunner):
        self.manager = manager
        self.runner = runner
        self.server_metrics: dict[str, dict[str, Any]] = {}
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            await asyncio.to_thread(self.manager.poll)
            await self.refresh_metrics()
            interval = max(1, int(settings.get_app_settings().get("status_poll_interval_sec", 10)))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()

    async def refresh_metrics(self) -> None:
        results = await asyncio.gather(*[
            asyncio.to_thread(self._collect_server, server)
            for server in settings.get_enabled_servers()
        ], return_exceptions=True)
        for server, result in zip(settings.get_enabled_servers(), results):
            name = server["name"]
            if isinstance(result, Exception):
                self.server_metrics[name] = {"cpu_percent": None, "online": False, "error": str(result), "updated_at": time.time()}
            else:
                self.server_metrics[name] = result

    def _collect_server(self, server: dict) -> dict[str, Any]:
        if self.runner._is_local(server):
            cpu = self._local_cpu_percent()
        else:
            command = "LANG=C mpstat 1 1 2>/dev/null | awk '/Average:.*all/{print 100-$NF}'"
            result = self.runner._ssh(server, command)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "SSH/mpstat failed")
            cpu = float(result.stdout.strip()) if result.stdout.strip() else None
        return {"cpu_percent": cpu, "online": True, "error": None, "updated_at": time.time()}

    @staticmethod
    def _local_cpu_percent() -> float | None:
        if os.path.exists("/proc/stat"):
            def sample() -> tuple[int, int]:
                with open("/proc/stat", encoding="utf-8") as handle:
                    values = [int(v) for v in handle.readline().split()[1:]]
                idle = values[3] + (values[4] if len(values) > 4 else 0)
                return idle, sum(values)
            idle1, total1 = sample()
            time.sleep(0.1)
            idle2, total2 = sample()
            delta = total2 - total1
            return round(100 * (1 - (idle2 - idle1) / delta), 1) if delta else 0.0
        try:
            return round(min(100.0, os.getloadavg()[0] / (os.cpu_count() or 1) * 100), 1)
        except (AttributeError, OSError):
            return None
