"""Read Codex plan quotas without creating a thread or running a model.

Official protocol: https://learn.chatgpt.com/docs/app-server
The only RPC requests sent are initialize and account/rateLimits/read.
Credentials are managed by Codex; this module never opens auth.json.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import queue
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime


class _ReadError(Exception):
    """Only fixed, credential-free messages may be used here."""


def _empty(source: str, fetched_at: float, error: str | None = None) -> dict:
    return dict(status="unavailable", fetched_at=fetched_at, source=source,
                five_hour=None, weekly=None, error=error)


def _number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value if math.isfinite(value) else None
    return None


def _windows(bucket: dict) -> dict:
    out = dict(five_hour=None, weekly=None)
    if not isinstance(bucket, dict):
        return out
    # primary is not necessarily 5h: use the actual window duration.
    for key in ("primary", "secondary"):
        item = bucket.get(key)
        if not isinstance(item, dict):
            continue
        minutes = item.get("windowDurationMins", item.get("window_minutes"))
        label = {300: "five_hour", 10080: "weekly"}.get(minutes) if isinstance(minutes, (int, float)) else None
        used = _number(item.get("usedPercent", item.get("used_percent")))
        reset = _number(item.get("resetsAt", item.get("resets_at")))
        if label and used is not None and 0 <= used <= 100:
            out[label] = dict(used_percent=used, remaining_percent=100-used,
                              reset_at=reset, window_minutes=int(minutes))
    return out


def _api_windows(result: dict) -> dict:
    # Prefer the explicitly named Codex bucket. Never merge other model buckets.
    mapping = result.get("rateLimitsByLimitId")
    if isinstance(mapping, dict) and isinstance(mapping.get("codex"), dict):
        return _windows(mapping["codex"])
    bucket = result.get("rateLimits")
    if isinstance(bucket, dict) and bucket.get("limitId") in (None, "codex"):
        return _windows(bucket)
    return dict(five_hour=None, weekly=None)


def _codex_exe() -> str:
    # Store installs move on update; discover instead of pinning a version hash.
    local = Path(os.environ.get("LOCALAPPDATA", Path.home()/"AppData"/"Local"))
    candidates = list((local/"OpenAI"/"Codex"/"bin").glob("*/codex.exe"))
    if candidates:
        return str(max(candidates, key=lambda p: p.stat().st_mtime))
    found = shutil.which("codex.exe") or shutil.which("codex")
    if found:
        return found
    raise _ReadError("Codex executable not found")


def _environment() -> dict:
    env = os.environ.copy()
    # CLI does not always use Windows' system proxy. Use the local proxy only
    # when it is listening and the caller has not already chosen one.
    if not any(env.get(k) for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")):
        try:
            with socket.create_connection(("127.0.0.1", 7897), timeout=0.15):
                env["HTTPS_PROXY"] = "http://127.0.0.1:7897"
        except OSError:
            pass
    return env


def _live_read(timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    proc = None
    reader = None
    inbox = queue.Queue(maxsize=128)
    try:
        proc = subprocess.Popen(
            [_codex_exe(), "app-server", "--stdio"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=_environment(), cwd=str(Path(__file__).parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        def read_lines():
            try:
                for line in proc.stdout:
                    # No raw protocol output is printed or persisted.
                    try:
                        message = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if isinstance(message, dict) and message.get("id") in (1, 2):
                        try:
                            inbox.put_nowait(message)
                        except queue.Full:
                            pass
            finally:
                try:
                    inbox.put_nowait(None)
                except queue.Full:
                    pass

        reader = threading.Thread(target=read_lines, daemon=True, name="hud-quota-read")
        reader.start()

        def send(message):
            proc.stdin.write((json.dumps(message)+"\n").encode("utf-8"))
            proc.stdin.flush()

        def receive(request_id):
            while True:
                left = deadline-time.monotonic()
                if left <= 0:
                    raise _ReadError("Codex quota read timed out")
                try:
                    response = inbox.get(timeout=left)
                except queue.Empty:
                    raise _ReadError("Codex quota read timed out") from None
                if response is None:
                    raise _ReadError("Codex app-server exited before replying")
                if response.get("id") == request_id:
                    if "error" in response:
                        # Do not expose the server's free-form error message.
                        raise _ReadError("Codex quota request rejected")
                    result = response.get("result")
                    if not isinstance(result, dict):
                        raise _ReadError("Invalid Codex quota response")
                    return result

        send(dict(id=1, method="initialize", params=dict(clientInfo=dict(
            name="local_usage_hud", title="Local Usage HUD", version="1.0"))))
        receive(1)
        send(dict(method="initialized", params={}))
        send(dict(id=2, method="account/rateLimits/read"))
        return receive(2)
    finally:
        if proc is not None:
            # EOF lets the server shut down cleanly; enforce bounded cleanup.
            if proc.stdin:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            try:
                proc.wait(timeout=0.4)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1)
            if reader:
                reader.join(timeout=0.5)
            if proc.stdout:
                proc.stdout.close()


def _timestamp(value):
    numeric = _number(value)
    if numeric is not None:
        return float(numeric)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return None


def _session_fallback(error: str, deadline: float) -> dict:
    """Read only bounded tails of local rollout files; always mark historical."""
    home = Path(os.environ.get("CODEX_HOME", Path.home()/".codex"))
    best = None
    paths = []
    for folder in (home/"sessions", home/"archived_sessions"):
        if folder.is_dir():
            for path in folder.rglob("*.jsonl"):
                if time.monotonic() >= deadline:
                    break
                try:
                    paths.append((path.stat().st_mtime, path))
                except OSError:
                    pass
    for _, path in sorted(paths, reverse=True)[:48]:
        if time.monotonic() >= deadline:
            break
        try:
            with path.open("rb") as stream:
                size = stream.seek(0, 2)
                stream.seek(max(0, size-1024*1024))
                lines = stream.read().splitlines()
            for line in reversed(lines):
                if b'"rate_limits"' not in line or b'"token_count"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(event, dict):
                    continue
                payload = event.get("payload", {})
                if not isinstance(payload, dict):
                    continue
                if event.get("type") != "event_msg" or payload.get("type") != "token_count":
                    continue
                stamp = _timestamp(event.get("timestamp"))
                bucket = payload.get("rate_limits")
                if stamp is None or not isinstance(bucket, dict):
                    continue
                if bucket.get("limit_id") not in (None, "codex"):
                    continue
                windows = _windows(bucket)
                if not any(windows.values()):
                    continue
                if best is None or stamp > best[0]:
                    best = (stamp, windows)
                break
        except OSError:
            continue
    if best:
        result = _empty("codex_session_token_count", best[0], error)
        result.update(best[1], status="stale")
        return result
    return _empty("unavailable", time.time(), error)


def read_usage(timeout: float = 15) -> dict:
    """Return safe quotas; fetched_at is the observation time in Unix seconds.

    Live reads are 'ok'. Rollout snapshots are always 'stale', even when recent;
    expired snapshots are never assumed to reset to zero. Missing windows stay
    null. timeout is capped at 15s including reserved process cleanup/fallback.
    """
    start = time.monotonic()
    timeout = max(0.1, min(float(timeout), 15.0))
    deadline = start + timeout
    try:
        windows = _api_windows(_live_read(max(0.05, timeout-2.5)))
        if any(windows.values()):
            result = _empty("codex_app_server", time.time())
            result.update(windows, status="ok")
            return result
        error = "Codex returned no supported quota windows"
    except _ReadError as exc:
        error = str(exc)
    except Exception:
        # Exception text may contain URLs or credentials; never pass it through.
        error = "Codex quota read failed"
    try:
        return _session_fallback(error, deadline)
    except Exception:
        return _empty("unavailable", time.time(), error)


if __name__ == "__main__":
    print(json.dumps(read_usage(), ensure_ascii=False))
