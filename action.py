#!/usr/bin/env python3
"""Omarmail safe action helper for flagging and moving messages with error isolation and cache synchronization."""
import sys
import os
import json
import subprocess
import imaplib
import tomllib
import re

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

def load_imap_credentials():
    """Parse IMAP credentials from himalaya config — plain IMAP or ortie OAuth."""
    try:
        with open(os.path.expanduser("~/.config/himalaya/config.toml"), "rb") as f:
            cfg = tomllib.load(f)
        accounts = cfg.get("accounts", {})
        for account in accounts.values():
            if "imap" in account and "sasl" in account["imap"]:
                server = account["imap"]["server"]
                user = account["imap"]["sasl"]["plain"]["username"]
                pw = account["imap"]["sasl"]["plain"]["password"]["raw"]
                return server, user, pw, "plain"
        for account in accounts.values():
            token_cmd = account.get("gmail", {}).get("auth", {}).get("token", {}).get("command")
            if token_cmd:
                token = subprocess.run(token_cmd, capture_output=True, text=True, timeout=8).stdout.strip()
                if token:
                    email = None
                    try:
                        for pg in range(1, 6):
                            r = subprocess.run(["himalaya", "envelope", "list", "--json", "-p", str(pg), "-s", "10"],
                                               capture_output=True, text=True, timeout=8)
                            if r.returncode != 0 or not r.stdout.strip():
                                break
                            for env in json.loads(r.stdout).get("envelopes", []):
                                for field in ("to", "cc", "bcc"):
                                    for recip in env.get(field, []):
                                        if recip.get("email") and "@gmail.com" in recip["email"]:
                                            email = recip["email"]; break
                                    if email: break
                                if email: break
                            if email: break
                    except Exception:
                        pass
                    if email:
                        return "imap.gmail.com:993", email, token, "xoauth2"
        return None
    except Exception:
        return None

def resolve_uid_by_msgid(conn, himalaya_id):
    """Resolve an IMAP UID from a himalaya envelope ID by matching Message-ID.

    Gmail REST API returns hex envelope IDs that don't match IMAP UIDs. We look
    up the message-id from himalaya's cached envelope list, then search IMAP.
    """
    try:
        msgid = None
        for pg in range(1, 6):
            r = subprocess.run(["himalaya", "envelope", "list", "--json", "-p", str(pg), "-s", "10"],
                               capture_output=True, text=True, timeout=12)
            if r.returncode != 0 or not r.stdout.strip():
                break
            for env in json.loads(r.stdout).get("envelopes", []):
                if env.get("id") == himalaya_id:
                    msgid = (env.get("message-id") or "").strip("<>")
                    break
            if msgid:
                break
        if not msgid:
            return None
        typ, data = conn.uid("SEARCH", f'HEADER Message-ID "{msgid}"')
        if typ == "OK" and data and data[0]:
            uids = data[0].decode().split()
            if uids:
                return uids[0]
    except Exception:
        pass
    return None

def find_trash_mailbox(conn):
    """Return the trash mailbox wire name via its \\Trash special-use attribute.

    Locale-independent: Gmail zh-TW reports "[Gmail]/&V4NXPmh2-" (垃圾桶), an
    English account "[Gmail]/Trash". Falls back to None when absent.
    """
    try:
        typ, lines = conn.list()
        if typ != "OK":
            return None
        for line in lines:
            s = line.decode("utf-8", "replace")
            if "\\Trash" in s:
                m = re.search(r'"([^"]*)"\s*$', s)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return None

def delete_message(mid):
    """Move message to trash via native himalaya delete with direct IMAP fallback."""
    # 1. Native himalaya message delete — works for Gmail REST (OAuth), IMAP, JMAP, Maildir
    out, err, code = run_himalaya_safe(["himalaya", "message", "delete", "--", mid], timeout=8.0)
    if code == 0:
        return True, ""

    # 2. Direct IMAP fallback for accounts with plain IMAP credentials
    creds = load_imap_credentials()
    if creds and creds[3] != "xoauth2":
        server, user, pw, auth_type = creds
        host, _, port = server.partition(":")
        try:
            port = int(port) if port else 993
            conn = imaplib.IMAP4_SSL(host, port, timeout=5)
            try:
                conn.login(user, pw)
                typ, _ = conn.select("INBOX")
                if typ == "OK":
                    trash = find_trash_mailbox(conn)
                    if trash:
                        uid = mid if mid.isdigit() else resolve_uid_by_msgid(conn, mid)
                        if uid:
                            typ, data = conn.uid("MOVE", uid, trash)
                            if typ == "OK":
                                return True, ""
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass
        except Exception:
            pass

    return False, err or out or "Failed to delete message via himalaya"

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
        ok, err = delete_message(mid)
        if ok:
            print(json.dumps({"success": True, "id": mid, "action": action}))
            sys.exit(0)
        else:
            print(json.dumps({"success": False, "error": err or "Failed to delete message", "id": mid}))
            sys.exit(1)
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
