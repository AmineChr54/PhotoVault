"""GUI-side client for the AI worker (numpy-free). Starts data\\venv python -m photovault.ai.serve lazily."""
import itertools
import json
import os
import subprocess
import sys
import threading

from . import paths

_ids = itertools.count(1)


class AiClient:
    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self.pending = {}
        self.events = []          # progress events from background indexing
        self.ready = threading.Event()
        self.last_error = ""

    @property
    def available(self) -> bool:
        return paths.VENV_PY.exists() and not paths.SETUP_MARKER.exists()

    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        if not self.available:
            raise RuntimeError(f"AI environment not found: {paths.VENV_PY}")
        env = dict(os.environ)
        src_root = str(paths.PROJECT)
        env.update(PYTHONPATH=src_root, PHOTOVAULT_AI_DATA=str(paths.DATA), HF_HOME=str(paths.DATA / "hf"), PYTHONUNBUFFERED="1")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.ready.clear()
        self.proc = subprocess.Popen([str(paths.VENV_PY), "-m", "photovault.ai.serve"], cwd=src_root, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, bufsize=1, creationflags=flags)
        threading.Thread(target=self._reader, daemon=True).start()
        if not self.ready.wait(120):
            raise RuntimeError("AI worker did not start")

    def _reader(self):
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("event") == "ready":
                self.ready.set()
            elif "id" in msg and msg["id"] in self.pending:
                slot = self.pending.pop(msg["id"])
                slot["res"] = msg
                slot["ev"].set()
            elif "event" in msg:
                self.events.append(msg)
                del self.events[:-50]

    def request(self, cmd, timeout=600, **kw):
        self.start()
        rid = next(_ids)
        slot = {"ev": threading.Event(), "res": None}
        self.pending[rid] = slot
        with self.lock:
            self.proc.stdin.write(json.dumps({"id": rid, "cmd": cmd, **kw}) + "\n")
            self.proc.stdin.flush()
        if not slot["ev"].wait(timeout):
            self.pending.pop(rid, None)
            raise TimeoutError(cmd)
        res = slot["res"]
        if "error" in res:
            raise RuntimeError(res["error"])
        return res

    def last_progress(self):
        for e in reversed(self.events):
            if e.get("event") in ("progress", "start", "done", "index_finished", "index_error", "listing"):
                return e
        return None

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(5)
            except Exception:
                self.proc.kill()
