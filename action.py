#!/usr/bin/env python3
"""Omarmail safe action helper for flagging and moving messages with error isolation and cache synchronization."""
import sys
import os
import json
import subprocess

CACHE_DIR = os.path.expanduser("~/.cache/omarmail")
INBOX_CACHE = os.path.join(CACHE_DIR, "inbox_cache.json")

def update_cache_flag(mid, seen=True):
    if not os.path.exists(INBOX_CACHE):
        return
    try:
        with open(INBOX_CACHE, "r", encoding="utf-8") as f:
            data = json.load(f)
        envelopes = data if isinstance(data, list) else data.get("envelopes", [])
        for env in envelopes:
            if env.get("id") == mid:
                flags = env.get("flags", [])
                flags = [f for f in flags if (f.get("iana") if isinstance(f, dict) else str(f)).lower() != "seen"]
                if seen:
                    flags.append({"raw": "\\Seen", "iana": "seen"})
                env["flags"] = flags
        tmp = INBOX_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(envelopes, f, ensure_ascii=False)
        os.replace(tmp, INBOX_CACHE)
    except Exception:
        pass

def remove_from_cache(mid):
    if not os.path.exists(INBOX_CACHE):
        return
    try:
        with open(INBOX_CACHE, "r", encoding="utf-8") as f:
            data = json.load(f)
        envelopes = data if isinstance(data, list) else data.get("envelopes", [])
        envelopes = [e for e in envelopes if e.get("id") != mid]
        tmp = INBOX_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(envelopes, f, ensure_ascii=False)
        os.replace(tmp, INBOX_CACHE)
    except Exception:
        pass

def main():
    if len(sys.argv) < 3:
        print(json.dumps({"success": False, "error": "Usage: action.py <mark_read|mark_unread|delete> <id>"}))
        sys.exit(1)

    action = sys.argv[1]
    mid = sys.argv[2]

    if action == "mark_read":
        update_cache_flag(mid, seen=True)
        cmd = ["himalaya", "flag", "add", "-f", "seen", mid]
    elif action == "mark_unread":
        update_cache_flag(mid, seen=False)
        cmd = ["himalaya", "flag", "remove", "-f", "seen", mid]
    elif action == "delete":
        remove_from_cache(mid)
        cmd = ["himalaya", "message", "move", "--to", "Trash", mid]
    else:
        print(json.dumps({"success": False, "error": f"Unknown action: {action}"}))
        sys.exit(1)

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10.0)
        if res.returncode == 0:
            print(json.dumps({"success": True, "id": mid, "action": action}))
        else:
            print(json.dumps({"success": False, "error": res.stderr.strip() or "Himalaya error", "id": mid}))
    except subprocess.TimeoutExpired:
        print(json.dumps({"success": False, "error": "Action timed out", "id": mid}))
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e), "id": mid}))

if __name__ == "__main__":
    main()
