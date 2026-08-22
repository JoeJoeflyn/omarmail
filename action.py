#!/usr/bin/env python3
"""Omarmail safe action helper for flagging and moving messages with error isolation and cache synchronization."""
import sys
import os
import json
import subprocess

CACHE_DIR = os.path.expanduser("~/.cache/omarmail")
INBOX_CACHE = os.path.join(CACHE_DIR, "inbox_cache.json")
PAGES_DIR = os.path.join(CACHE_DIR, "pages")

def update_cache_flag(mid, seen=True):
    # 1. Update legacy inbox_cache.json
    if os.path.exists(INBOX_CACHE):
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

    # 2. Update multi-page cache files in pages/
    if os.path.exists(PAGES_DIR):
        try:
            for fname in os.listdir(PAGES_DIR):
                if fname.startswith("p_") and fname.endswith(".json"):
                    fpath = os.path.join(PAGES_DIR, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            page_data = json.load(f)
                        page_envs = page_data if isinstance(page_data, list) else page_data.get("envelopes", [])
                        modified = False
                        for env in page_envs:
                            if env.get("id") == mid:
                                flags = env.get("flags", [])
                                flags = [f for f in flags if (f.get("iana") if isinstance(f, dict) else str(f)).lower() != "seen"]
                                if seen:
                                    flags.append({"raw": "\\Seen", "iana": "seen"})
                                env["flags"] = flags
                                modified = True
                        if modified:
                            tmp_p = fpath + ".tmp"
                            with open(tmp_p, "w", encoding="utf-8") as f:
                                json.dump(page_envs, f, ensure_ascii=False)
                            os.replace(tmp_p, fpath)
                    except Exception:
                        pass
        except Exception:
            pass

MSG_CACHE_DIR = os.path.expanduser("~/.cache/omarmail/messages")

def remove_from_cache(mid):
    if os.path.exists(INBOX_CACHE):
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

    if os.path.exists(PAGES_DIR):
        try:
            for fname in os.listdir(PAGES_DIR):
                if fname.startswith("p_") and fname.endswith(".json"):
                    fpath = os.path.join(PAGES_DIR, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            page_data = json.load(f)
                        page_envs = page_data if isinstance(page_data, list) else page_data.get("envelopes", [])
                        new_page_envs = [e for e in page_envs if e.get("id") != mid]
                        if len(new_page_envs) != len(page_envs):
                            tmp_p = fpath + ".tmp"
                            with open(tmp_p, "w", encoding="utf-8") as f:
                                json.dump(new_page_envs, f, ensure_ascii=False)
                            os.replace(tmp_p, fpath)
                    except Exception:
                        pass
        except Exception:
            pass

    msg_file = os.path.join(MSG_CACHE_DIR, f"{mid}.json")
    if os.path.exists(msg_file):
        try:
            os.remove(msg_file)
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
                return "", "Action timed out", 1

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

import re

def main():
    if len(sys.argv) < 3:
        print(json.dumps({"success": False, "error": "Usage: action.py <mark_read|mark_unread|delete> <id>"}))
        sys.exit(1)

    action = sys.argv[1]
    mid = sys.argv[2]

    # Validate message ID
    if not re.match(r'^[A-Za-z0-9._\-]+$', mid):
        print(json.dumps({"success": False, "error": "Invalid message ID", "id": mid}))
        sys.exit(1)

    if action == "mark_read":
        update_cache_flag(mid, seen=True)
        cmd = ["himalaya", "flag", "add", "-f", "seen", "--", mid]
    elif action == "mark_unread":
        update_cache_flag(mid, seen=False)
        cmd = ["himalaya", "flag", "remove", "-f", "seen", "--", mid]
    elif action == "delete":
        remove_from_cache(mid)
        candidates = [
            ["himalaya", "message", "move", "--to", "TRASH", "--", mid],
            ["himalaya", "message", "move", "--to", "Trash", "--", mid],
            ["himalaya", "message", "move", "--to", "trash", "--", mid],
            ["himalaya", "message", "move", "--to", "[Gmail]/Trash", "--", mid],
            ["himalaya", "flag", "add", "-f", "deleted", "--", mid],
        ]
        success = False
        last_err = ""
        for c in candidates:
            out, err, code = run_himalaya_safe(c, timeout=10.0)
            if code == 0:
                success = True
                break
            last_err = err or out
        if success:
            print(json.dumps({"success": True, "id": mid, "action": action}))
        else:
            print(json.dumps({"success": False, "error": last_err or "Failed to delete message", "id": mid}))
        sys.exit(0)
    else:
        print(json.dumps({"success": False, "error": f"Unknown action: {action}"}))
        sys.exit(1)

    try:
        out, err, code = run_himalaya_safe(cmd, timeout=12.0)
        if code == 0:
            print(json.dumps({"success": True, "id": mid, "action": action}))
        else:
            print(json.dumps({"success": False, "error": err or "Himalaya error", "id": mid}))
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e), "id": mid}))

if __name__ == "__main__":
    main()
