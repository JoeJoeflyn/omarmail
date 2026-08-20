#!/usr/bin/env python3
"""Omarmail fast envelope lister with disk caching for instant UI loading.

Usage:
  python3 list.py [page_size] [page] [--cache-only]
"""
import sys
import os
import json
import subprocess

CACHE_DIR = os.path.expanduser("~/.cache/omarmail")
INBOX_CACHE = os.path.join(CACHE_DIR, "inbox_cache.json")
os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
try:
    os.chmod(CACHE_DIR, 0o700)
except Exception:
    pass

def get_cached_envelopes():
    if os.path.exists(INBOX_CACHE):
        try:
            with open(INBOX_CACHE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "envelopes" in data:
                    return data["envelopes"]
        except Exception:
            pass
    return None

def save_cache(envelopes):
    try:
        os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
        try:
            os.chmod(CACHE_DIR, 0o700)
        except Exception:
            pass
        tmp_file = INBOX_CACHE + ".tmp"
        fd = os.open(tmp_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(envelopes, f, ensure_ascii=False)
        try:
            os.chmod(tmp_file, 0o600)
        except Exception:
            pass
        os.replace(tmp_file, INBOX_CACHE)
        try:
            os.chmod(INBOX_CACHE, 0o600)
        except Exception:
            pass
    except Exception:
        pass

import tempfile

def run_himalaya_safe(cmd, timeout=12.0):
    """Execute himalaya writing to a temp file in a detached process group to eliminate BrokenPipe SIGABRT."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False, prefix="himalaya_out_") as tmp_out, \
         tempfile.NamedTemporaryFile(mode="w+", delete=False, prefix="himalaya_err_") as tmp_err:
        tmp_out_name = tmp_out.name
        tmp_err_name = tmp_err.name
        try:
            proc = subprocess.Popen(cmd, stdout=tmp_out, stderr=tmp_err, start_new_session=True)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait()
                except Exception:
                    pass
                return "", "Request timed out", 1

            tmp_out.seek(0)
            out = tmp_out.read().strip()
            tmp_err.seek(0)
            err = tmp_err.read().strip()
            return out, err, proc.returncode
        finally:
            try:
                os.unlink(tmp_out_name)
            except Exception:
                pass
            try:
                os.unlink(tmp_err_name)
            except Exception:
                pass

def fetch_envelopes(page_size=30, page=1):
    cmd = ["himalaya", "envelope", "list", "--json", "-s", str(page_size), "-p", str(page)]
    try:
        out, err, code = run_himalaya_safe(cmd, timeout=12.0)
        if code == 0 and out:
            try:
                data = json.loads(out)
                envelopes = data.get("envelopes", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                if page == 1 and envelopes:
                    save_cache(envelopes)
                return {"envelopes": envelopes, "error": ""}
            except Exception as e:
                return {"envelopes": [], "error": f"JSON parse error: {e}"}
        else:
            return {"envelopes": [], "error": err or "Failed to list envelopes"}
    except Exception as e:
        return {"envelopes": [], "error": str(e)}

def main():
    page_size = 30
    page = 1
    cache_only = "--cache-only" in sys.argv

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) >= 1:
        try:
            page_size = max(1, min(100, int(args[0])))
        except ValueError:
            pass
    if len(args) >= 2:
        try:
            page = max(1, int(args[1]))
        except ValueError:
            pass

    if cache_only:
        cached = get_cached_envelopes()
        if cached is not None:
            print(json.dumps({"envelopes": cached, "cached": True, "error": ""}))
        else:
            print(json.dumps({"envelopes": [], "cached": False, "error": ""}))
        return

    result = fetch_envelopes(page_size, page)
    # If fetch failed (e.g. offline/timeout) and we have cache, fallback to cache
    if result.get("error") and page == 1:
        cached = get_cached_envelopes()
        if cached:
            result["envelopes"] = cached
            result["from_cache"] = True

    print(json.dumps(result))

if __name__ == "__main__":
    main()
