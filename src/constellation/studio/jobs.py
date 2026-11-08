"""Background jobs (builds, exports, publishes) with a log the page can poll."""

from __future__ import annotations

import asyncio
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Job:
    id: str
    kind: str
    site: str
    status: str = "running"  # running | done | failed
    started: float = field(default_factory=time.time)
    finished: float | None = None
    log: list[str] = field(default_factory=list)
    result: Any = None
    error: str = ""

    def say(self, line: str) -> None:
        self.log.append(line)


class Jobs:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._running_per_site: set[tuple[str, str]] = set()

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def for_site(self, site: str, limit: int = 10) -> list[Job]:
        jobs = [j for j in self._jobs.values() if j.site == site]
        return sorted(jobs, key=lambda j: j.started, reverse=True)[:limit]

    def busy(self, site: str, kind: str) -> bool:
        return (site, kind) in self._running_per_site

    async def start(self, kind: str, site: str, fn: Callable[[Job], Any]) -> Job:
        """Run ``fn(job)`` in a worker thread. One job of a kind per site at a time."""
        with self._lock:
            if (site, kind) in self._running_per_site:
                raise RuntimeError(f"a {kind} job is already running for {site}")
            job = Job(id=uuid.uuid4().hex[:10], kind=kind, site=site)
            self._jobs[job.id] = job
            self._running_per_site.add((site, kind))

        async def runner() -> None:
            try:
                job.result = await asyncio.to_thread(fn, job)
                job.status = "done"
            except Exception as e:  # noqa: BLE001
                job.status = "failed"
                job.error = str(e)
                job.say("error: " + "".join(traceback.format_exception_only(type(e), e)).strip())
            finally:
                job.finished = time.time()
                with self._lock:
                    self._running_per_site.discard((site, kind))

        asyncio.get_running_loop().create_task(runner())
        return job
