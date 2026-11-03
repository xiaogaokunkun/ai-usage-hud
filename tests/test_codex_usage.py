"""Offline contract checks; --live also verifies the spawned process exits."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1]/"hud_codex_usage.py"
spec = importlib.util.spec_from_file_location("hud_codex_usage", MODULE)
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)


class QuotaTests(unittest.TestCase):
    def test_actual_duration_determines_label(self):
        windows = usage._windows({
            "primary": {"usedPercent": 25, "windowDurationMins": 10080, "resetsAt": 99},
            "secondary": {"usedPercent": 62, "windowDurationMins": 300, "resetsAt": 88}})
        self.assertEqual(windows["five_hour"]["remaining_percent"], 38)
        self.assertEqual(windows["weekly"]["used_percent"], 25)

    def test_unknown_or_invalid_is_missing(self):
        self.assertEqual(usage._windows({"primary": {
            "usedPercent": 25, "windowDurationMins": 15}}),
            {"five_hour": None, "weekly": None})
        self.assertIsNone(usage._windows({"primary": {
            "usedPercent": float("nan"), "windowDurationMins": 300}})["five_hour"])

    def test_does_not_merge_other_buckets(self):
        result = {"rateLimitsByLimitId": {
            "codex": {"primary": {"usedPercent": 10, "windowDurationMins": 300}},
            "other": {"secondary": {"usedPercent": 80, "windowDurationMins": 10080}}}}
        self.assertIsNone(usage._api_windows(result)["weekly"])

    def test_latest_snapshot_always_stale_and_timestamp_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            sessions = Path(folder)/"sessions"
            sessions.mkdir()
            def event(stamp, used):
                return json.dumps(dict(timestamp=stamp, type="event_msg", payload=dict(
                    type="token_count", rate_limits=dict(primary=dict(
                        used_percent=used, window_minutes=300, resets_at=123)))))
            (sessions/"a.jsonl").write_text(event("2026-11-01T00:00:00Z", 99)+"\n")
            (sessions/"b.jsonl").write_text(event("2026-11-02T00:00:00Z", 44)+"\n"+
                                          '{"rate_limits": 1, "token_count": 1, "payload": null}\n')
            with patch.dict(os.environ, {"CODEX_HOME": folder}), patch.object(
                    usage, "_live_read", side_effect=RuntimeError("secret-must-not-escape")):
                result = usage.read_usage()
            self.assertEqual(result["status"], "stale")
            self.assertEqual(result["five_hour"]["used_percent"], 44)
            self.assertEqual(result["fetched_at"], 1793577600)
            self.assertNotIn("secret", json.dumps(result))

    def test_no_snapshot_unavailable(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"CODEX_HOME": folder}), patch.object(
                usage, "_live_read", side_effect=RuntimeError("secret-must-not-escape")):
            result = usage.read_usage()
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["five_hour"])

    def test_transport_only_sends_allowlisted_requests_and_reaps(self):
        captured = []
        class Input(io.BytesIO):
            def close(self):
                captured.extend(json.loads(line) for line in self.getvalue().splitlines())
                super().close()
        class Process:
            def __init__(self):
                self.stdin = Input()
                self.stdout = io.BytesIO(b'{"id":1,"result":{}}\n{"id":2,"result":{"rateLimits":{}}}\n')
                self.reaped = False
            def wait(self, timeout):
                self.reaped = True
                return 0
        proc = Process()
        with patch.object(usage, "_codex_exe", return_value="codex"), patch.object(
                usage, "_environment", return_value={}), patch.object(usage.subprocess, "Popen", return_value=proc):
            usage._live_read(1)
        self.assertTrue(proc.reaped)
        self.assertTrue(proc.stdout.closed)
        self.assertEqual([x["method"] for x in captured], ["initialize", "initialized", "account/rateLimits/read"])


def live_check():
    original = usage.subprocess.Popen
    spawned = []
    def tracked(*args, **kwargs):
        proc = original(*args, **kwargs)
        spawned.append(proc)
        return proc
    with patch.object(usage.subprocess, "Popen", side_effect=tracked):
        result = usage.read_usage()
    assert spawned and all(proc.poll() is not None for proc in spawned), "Temporary app-server still running"
    print(json.dumps(dict(result=result, spawned_processes_exited=True), ensure_ascii=False))


if __name__ == "__main__":
    import sys
    if "--live" in sys.argv:
        live_check()
    else:
        unittest.main()
