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
os.makedirs(CACHE_DIR, exist_ok=True)

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
        with open(INBOX_CACHE, "w", encoding="utf-8") as f:
            json.dump(envelopes, f, ensure_ascii=False)
    except Exception:
        pass

def fetch_envelopes(page_size=30, page=1):
    cmd = ["himalaya", "envelope", "list", "--json", "-s", str(page_size), "-p", str(page)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=12.0)
        out = res.stdout.strip()
        err = res.stderr.strip()
        if res.returncode == 0 and out:
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
    except subprocess.TimeoutExpired:
        return {"envelopes": [], "error": "Request timed out"}
    except Exception as e:
        return {"envelopes": [], "error": str(e)}

def main():
    page_size = 30
    page = 1
    cache_only = "--cache-only" in sys.argv

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) >= 1:
        try:
            page_size = int(args[0])
        except ValueError:
            pass
    if len(args) >= 2:
        try:
            page = int(args[1])
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
