"""任务状态：一个任务一个线程，前端用 SSE 看进度、刷新页面也能接管。"""
from __future__ import annotations

import threading
import time
import uuid

_lock = threading.RLock()
JOBS: dict[str, "Job"] = {}
MAX_JOBS = 40


class Job:
    def __init__(self, params: dict):
        self.id = uuid.uuid4().hex[:12]
        self.params = params or {}
        self.status = "pending"          # pending running done error cancelled
        self.stage = "排队中"
        self.progress = 0.0
        self.message = ""
        self.logs: list[str] = []
        self.result: dict = {}
        self.error = ""
        self.created = time.time()
        self.updated = time.time()
        self.cancel_evt = threading.Event()
        self._ver = 0

    # ---- 状态更新
    def _touch(self):
        self.updated = time.time()
        self._ver += 1

    def log(self, msg: str):
        with _lock:
            stamp = time.strftime("%H:%M:%S")
            self.logs.append(f"[{stamp}] {msg}")
            if len(self.logs) > 300:
                self.logs = self.logs[-300:]
            self.message = msg
            self._touch()

    def stage_to(self, stage: str, progress: float | None = None, message: str = ""):
        with _lock:
            self.stage = stage
            if progress is not None:
                self.progress = max(0.0, min(1.0, progress))
            if message:
                self.message = message
            self._touch()

    def set_progress(self, progress: float, message: str = ""):
        with _lock:
            self.progress = max(0.0, min(1.0, progress))
            if message:
                self.message = message
            self._touch()

    def cancel(self):
        self.cancel_evt.set()
        with _lock:
            self.status = "cancelled"
            self.stage = "已取消"
            self._touch()

    def check_cancel(self):
        if self.cancel_evt.is_set():
            raise Cancelled()

    def finish(self, result: dict):
        with _lock:
            self.status = "done"
            self.stage = "完成"
            self.progress = 1.0
            self.result = result
            self._touch()

    def fail(self, err: str):
        with _lock:
            self.status = "error"
            self.stage = "出错"
            self.error = err
            self._touch()

    # ---- 给前端
    def snapshot(self) -> dict:
        with _lock:
            return {
                "id": self.id,
                "status": self.status,
                "stage": self.stage,
                "progress": round(self.progress, 4),
                "message": self.message,
                "logs": self.logs[-60:],
                "result": self.result,
                "error": self.error,
                "ver": self._ver,
            }


class Cancelled(Exception):
    pass


def create(params: dict) -> Job:
    job = Job(params)
    with _lock:
        JOBS[job.id] = job
        if len(JOBS) > MAX_JOBS:
            for k in sorted(JOBS, key=lambda k: JOBS[k].created)[:len(JOBS) - MAX_JOBS]:
                if JOBS[k].status in ("done", "error", "cancelled"):
                    JOBS.pop(k, None)
    return job


def get(jid: str) -> Job | None:
    with _lock:
        return JOBS.get(jid)
