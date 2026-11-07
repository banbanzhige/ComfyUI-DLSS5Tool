"""Cancellable, bounded-lifetime native sessions and owned output copies."""

from dataclasses import asdict
import json
from multiprocessing import shared_memory
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

import numpy as np

from .paths import ROOT, checked_path, verify_identity
from ..core.contracts import Adapter


_execution_lock = threading.Lock()


class Worker:
    def __init__(self, cancel=None):
        self.cancel = cancel or (lambda: None)
        self.replies = queue.Queue()
        env = os.environ.copy()
        env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", DLSS5TOOL_PARENT_PID=str(os.getpid()))
        self.process = subprocess.Popen(
            [sys.executable, "-B", str(Path(__file__).with_name("worker.py"))],
            cwd=str(ROOT), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    self.replies.put(json.loads(line))
                except ValueError:
                    self.replies.put({"ok": False, "error": "Invalid native worker response"})
        finally:
            self.replies.put(None)

    def request(self, message, *, timeout=120, allow_cancel=True):
        if allow_cancel:
            self.cancel()
        if self.process.poll() is not None:
            raise RuntimeError(f"DLSS host exited ({self.process.returncode})")
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            if allow_cancel:
                self.cancel()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("DLSS host timed out")
            try:
                reply = self.replies.get(timeout=min(.1, remaining))
            except queue.Empty:
                continue
            if reply is None:
                raise RuntimeError(f"DLSS host crashed or closed ({self.process.poll()})")
            if not reply.get("ok"):
                raise RuntimeError(reply.get("error", "DLSS host failed") + "\n" + reply.get("log", ""))
            return reply["payload"]

    def close(self):
        if self.process.poll() is None:
            try:
                self.request({"op": "close"}, timeout=2, allow_cancel=False)
                self.process.wait(timeout=2)
            except (OSError, RuntimeError, TimeoutError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                stream.close()
        self.reader.join(timeout=1)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def inventory(host, cancel=None):
    verify_identity(host)
    with Worker(cancel) as worker:
        response = worker.request({"op": "inventory", "host_path": host.path}, timeout=20)
    return tuple(Adapter(r["id"], r["name"], r["dedicated_video_memory"],
                         r["luid_high"], r["luid_low"], r.get("compute_capability", 0)) for r in response["adapters"])


def tile_plan(adapter):
    # Reserve capacity for ComfyUI's models. No resolution reduction or RGBA8
    # fallback. Native tiling keeps its 128px context and overlap blending.
    gib = adapter.memory / 1024**3
    return (4096, 2048) if gib >= 20 else (2048, 2048) if gib >= 10 else (1024, 1024)


class ProcessLive:
    def __init__(self, model, width, height, profile, options, log_directory, cancel=None):
        self.worker = None
        self.memories = []
        self.input = self.output = None
        self.metadata = None
        self.locked = False
        self.cancel = cancel or (lambda: None)
        verify_identity(model.runtime)
        verify_identity(model.host)
        adapters = inventory(model.host, cancel)
        adapter = next((a for a in adapters if a.id == model.adapter.id), None)
        if adapter is None:
            raise RuntimeError("Selected GPU is no longer available; run Model Loader again")
        self.log_directory = checked_path(log_directory, exists=False)
        self.log_directory.mkdir(parents=True, exist_ok=True)
        self.log_path = checked_path(self.log_directory / f"host-{uuid.uuid4().hex}.log", exists=False)
        try:
            while not _execution_lock.acquire(timeout=.1):
                self.cancel()
            self.locked = True
            self.cancel()
            for _ in range(2):
                self.memories.append(shared_memory.SharedMemory(create=True, size=height * width * 8))
            self.input = np.ndarray((height, width, 4), np.float16, buffer=self.memories[0].buf)
            self.output = np.ndarray((height, width, 4), np.float16, buffer=self.memories[1].buf)
            self.worker = Worker(cancel)
            spec = {"host": asdict(model.host), "runtime": asdict(model.runtime),
                    "adapter": asdict(adapter), "width": width, "height": height,
                    "profile": profile, "options": options, "tile": tile_plan(adapter),
                    "input_name": self.memories[0].name, "output_name": self.memories[1].name,
                    "log_path": str(self.log_path)}
            self.metadata = self.worker.request({"op": "open", "spec": spec})
        except BaseException:
            self.close()
            raise

    def process(self, rgb):
        if rgb.shape != (*self.input.shape[:2], 3) or not np.isfinite(rgb).all():
            raise ValueError("Native input must be finite RGB matching the configured size")
        if np.max(np.abs(rgb)) > np.finfo(np.float16).max:
            raise ValueError("Input exceeds the finite RGBA16F range")
        self.input[..., :3] = rgb
        self.input[..., 3] = 1
        self.worker.request({"op": "process"})  # Every still image resets history.
        return self.output[..., :3].astype(np.float32).copy()

    def close(self):
        try:
            if self.worker is not None:
                self.worker.close()
        finally:
            self.worker = None
            self.input = self.output = None
            try:
                for memory in self.memories:
                    try:
                        memory.close()
                    finally:
                        memory.unlink()
            finally:
                self.memories.clear()
                if self.locked:
                    self.locked = False
                    _execution_lock.release()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
