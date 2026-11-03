# -*- coding: utf-8 -*-

"""DeepSeek/MiMo 数据层；不包含旧窗口渲染入口。"""

import json

import os

import sqlite3

import threading

import time

import traceback

import urllib.error

import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

DEBUG = os.environ.get("HUD_DEBUG") == "1"

LOG_FILE = os.path.join(HERE, "hud.log")

ENV_FILE = os.environ.get("HUD_ENV_FILE") or (os.path.join(HERE, ".env") if os.path.isfile(os.path.join(HERE, ".env")) else os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes", ".env"))

STATE_DB = os.environ.get("HUD_STATE_DB") or os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes", "state.db")

MIMO_COOKIE_FILE = os.environ.get("HUD_MIMO_COOKIE_FILE") or os.path.join(HERE, "mimo-cookie.txt")

MIMO_BALANCE_URL = "https://platform.xiaomimimo.com/api/v1/balance"

MODELS = [
    {"key": "deepseek", "label": "DeepSeek", "short": "DS"},
    {"key": "mimo", "label": "MiMo", "short": "米"},
]

SOURCE_KEYS = tuple(m["key"] for m in MODELS)

MIMO_RETRY_SECONDS = 6 * 60 * 60

LOG_MAX_BYTES = 256 * 1024

_lock = threading.Lock()

_refresh_lock = threading.Lock()

STATE = {
    "balance": {"deepseek": None, "mimo": None},
    "usage": {"deepseek": 0, "mimo": 0},
    "usage_cost": {"deepseek": 0.0, "mimo": 0.0},
    "source_status": {"deepseek": "pending", "mimo": "pending"},
    "source_error": {"deepseek": None, "mimo": None},
    "last_ok": None,
    "updated_at": None,
    "last_err": None,
    "mimo_retry_at": 0.0,
    "mimo_cookie_mtime": 0.0,
}

def log(*parts):
    line = " ".join(str(p) for p in parts)
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
            os.replace(LOG_FILE, LOG_FILE + ".1")
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write("%s  %s\n" % (time.strftime("%m-%d %H:%M:%S"), line))
    except Exception:
        pass

def read_env_key(name):
    if os.environ.get(name):
        return os.environ[name]
    try:
        with open(ENV_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        return None
    return None

def http_get_json(url, key):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer %s" % key})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))

def is_peak(st):
    """DeepSeek 工作日 09-12、14-18 双倍计费。st = time.localtime()"""
    if st.tm_wday >= 5:
        return False
    h = st.tm_hour + st.tm_min / 60.0
    return (9 <= h < 12) or (14 <= h < 18)

def _provider_of(provider, base_url):
    p = (provider or "").lower()
    u = (base_url or "").lower()
    if "deepseek" in p or "deepseek" in u:
        return "deepseek"
    if "xiaomimimo" in u or "mimo" in p or "mimo" in u:
        return "mimo"
    return None

def aggregate_usage():
    """读 state.db，聚合今日各来源 token（按 last_seen 落在今天的 session 累计，近似）。"""
    usage = {"deepseek": 0, "mimo": 0}
    cost = {"deepseek": 0.0, "mimo": 0.0}
    if not os.path.exists(STATE_DB):
        return usage, cost
    today0 = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % STATE_DB, uri=True, timeout=5)
        rows = conn.execute(
            "SELECT billing_provider, billing_base_url, "
            "SUM(input_tokens), SUM(output_tokens), SUM(cache_read_tokens), SUM(estimated_cost_usd) "
            "FROM session_model_usage WHERE last_seen >= ? "
            "GROUP BY billing_provider, billing_base_url",
            (today0,),
        ).fetchall()
        conn.close()
        for provider, base, itok, otok, ctok, cst in rows:
            src = _provider_of(provider, base)
            if not src:
                continue
            usage[src] += (itok or 0) + (otok or 0) + (ctok or 0)
            cost[src] += cst or 0.0
    except Exception as e:
        log("aggregate_usage 失败:", e)
    return usage, cost

def _set_source(key, status, error=None, balance_known=False, balance=None):
    """更新单个余额源状态，只在变化时写日志，避免定时请求刷爆日志。"""
    with _lock:
        old_status = STATE["source_status"].get(key)
        old_error = STATE["source_error"].get(key)
        STATE["source_status"][key] = status
        STATE["source_error"][key] = str(error) if error else None
        if balance_known:
            STATE["balance"][key] = balance
        changed = status != old_status or (error and str(error) != old_error)
    if changed and error:
        log("%s 状态: %s (%s)" % (key, status, error))
    elif changed and status == "ok":
        log("%s 状态: ok" % key)

def refresh():
    if not _refresh_lock.acquire(blocking=False):
        return False
    try:
        return _refresh_locked()
    finally:
        _refresh_lock.release()

def _refresh_locked():
    ds_key = read_env_key("DEEPSEEK_API_KEY")
    if ds_key:
        try:
            d = http_get_json("https://api.deepseek.com/user/balance", ds_key)
            bal = float(d["balance_infos"][0]["total_balance"])
            _set_source("deepseek", "ok", balance_known=True, balance=bal)
        except Exception as e:
            _set_source("deepseek", "error", error=e)
    else:
        _set_source("deepseek", "missing_key", error="未找到 DEEPSEEK_API_KEY")

    # MiMo 余额（Cookie 鉴权，会过期需重登）
    mimo_cookie_exists = os.path.exists(MIMO_COOKIE_FILE)
    try:
        mimo_cookie_mtime = os.path.getmtime(MIMO_COOKIE_FILE) if mimo_cookie_exists else 0.0
    except OSError:
        mimo_cookie_mtime = 0.0
    now = time.time()
    with _lock:
        cookie_changed = mimo_cookie_mtime > STATE["mimo_cookie_mtime"] + 0.5
        if cookie_changed:
            STATE["mimo_cookie_mtime"] = mimo_cookie_mtime
            STATE["mimo_retry_at"] = 0.0
            STATE["source_status"]["mimo"] = "pending"
            STATE["source_error"]["mimo"] = None
        mimo_retry_at = STATE["mimo_retry_at"]

    if not mimo_cookie_exists:
        _set_source("mimo", "missing_cookie", error="未找到 mimo-cookie.txt")
    elif now < mimo_retry_at:
        _set_source("mimo", "expired", error="Cookie 过期")
    else:
        try:
            with open(MIMO_COOKIE_FILE, encoding="utf-8") as fh:
                mimo_cookie = fh.read().strip()
            if mimo_cookie:
                req = urllib.request.Request(MIMO_BALANCE_URL, headers={
                    "Cookie": mimo_cookie,
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://platform.xiaomimimo.com/console/balance",
                })
                with urllib.request.urlopen(req, timeout=15) as resp:
                    d = json.loads(resp.read().decode("utf-8"))
                payload = d.get("data", d)
                raw_balance = payload.get("balance")
                if raw_balance is None:
                    raise ValueError("MiMo 余额响应缺少 balance 字段")
                _set_source("mimo", "ok", balance_known=True,
                            balance=float(raw_balance))
            else:
                _set_source("mimo", "missing_cookie", error="mimo-cookie.txt 为空")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                with _lock:
                    STATE["mimo_retry_at"] = time.time() + MIMO_RETRY_SECONDS
                _set_source("mimo", "expired", error="Cookie 过期")
            else:
                _set_source("mimo", "error", error="HTTP %s" % e.code)
        except Exception as e:
            _set_source("mimo", "error", error=e)

    usage, cost = aggregate_usage()
    with _lock:
        STATE["usage"] = usage
        STATE["usage_cost"] = cost
        STATE["last_ok"] = time.time()
        STATE["updated_at"] = STATE["last_ok"]
        STATE["last_err"] = next(
            (err for err in STATE["source_error"].values() if err), None
        )
    return True
