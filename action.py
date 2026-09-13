#!/usr/bin/env python3
"""Omarmail safe action helper for flagging and moving messages with error isolation and cache synchronization."""
import imaplib
import json
import os
import re
import sys

from credentials import load_imap_credentials as load_credentials
from secure_io import atomic_write_json, ensure_private_dir, read_json, run_bounded

CACHE_DIR = ensure_private_dir(os.path.expanduser("~/.cache/omarmail"))
INBOX_CACHE = os.path.join(CACHE_DIR, "inbox_cache.json")
PAGES_DIR = ensure_private_dir(os.path.join(CACHE_DIR, "pages"))
MSG_CACHE_DIR = ensure_private_dir(os.path.join(CACHE_DIR, "messages"))
HIMALAYA_CONFIG = os.path.expanduser("~/.config/himalaya/config.toml")

def _envelopes_from_cache(path):
    data = read_json(path, default=None)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        envelopes = data.get("envelopes", [])
        return envelopes if isinstance(envelopes, list) else []
    return None

def update_cache_flag(mid, seen=True, mailbox="inbox"):
    prefix = "p_" if mailbox == "inbox" else f"{mailbox}_p_"
    paths = [
        os.path.join(PAGES_DIR, name)
        for name in os.listdir(PAGES_DIR)
        if name.startswith(prefix) and name.endswith(".json")
    ]
    if mailbox == "inbox":
        paths.insert(0, INBOX_CACHE)
    for path in paths:
        envelopes = _envelopes_from_cache(path)
        if envelopes is None:
            continue
        modified = False
        for envelope in envelopes:
            if envelope.get("id") != mid:
                continue
            flags = envelope.get("flags", [])
            flags = [flag for flag in flags if (flag.get("iana") if isinstance(flag, dict) else str(flag)).lower() != "seen"]
            if seen:
                flags.append({"raw": "\\Seen", "iana": "seen"})
            envelope["flags"] = flags
            modified = True
        if modified:
            try:
                atomic_write_json(path, envelopes, ensure_ascii=False)
            except OSError:
                pass

def remove_from_cache(mid):
    inbox = _envelopes_from_cache(INBOX_CACHE)
    if inbox is not None:
        try:
            atomic_write_json(INBOX_CACHE, [env for env in inbox if env.get("id") != mid], ensure_ascii=False)
        except OSError:
            pass

    sizes = set()
    for name in os.listdir(PAGES_DIR):
        parts = name[:-5].split("_") if name.startswith("p_") and name.endswith(".json") else []
        if len(parts) == 3 and parts[1].isdigit():
            sizes.add(int(parts[1]))

    for page_size in sizes:
        envelopes = []
        paths = []
        for page in range(1, 25):
            path = os.path.join(PAGES_DIR, f"p_{page_size}_{page}.json")
            page_envelopes = _envelopes_from_cache(path)
            if page_envelopes is None:
                break
            paths.append(path)
            envelopes.extend(page_envelopes)
        envelopes = [env for env in envelopes if env.get("id") != mid]
        for index, path in enumerate(paths):
            chunk = envelopes[index * page_size:(index + 1) * page_size]
            try:
                if chunk:
                    atomic_write_json(path, chunk, ensure_ascii=False)
                    if index == 0:
                        atomic_write_json(INBOX_CACHE, chunk, ensure_ascii=False)
                else:
                    os.unlink(path)
            except OSError:
                pass

    for name in os.listdir(MSG_CACHE_DIR):
        if (name.startswith(f"{mid}_") or name.startswith(f"inbox_{mid}_")) and name.endswith(".json"):
            try:
                os.unlink(os.path.join(MSG_CACHE_DIR, name))
            except OSError:
                pass

def run_himalaya_safe(cmd, timeout=20.0):
    return run_bounded(cmd, timeout=timeout, max_output_bytes=2 * 1024 * 1024)

def load_imap_credentials():
    return load_credentials(HIMALAYA_CONFIG)

def _imap_quote(value):
    if any(char in value for char in ("\r", "\n", "\x00")):
        raise ValueError("Invalid IMAP value")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def resolve_uid_by_msgid(conn, himalaya_id):
    """Resolve an IMAP UID from a himalaya envelope ID by matching Message-ID.

    Gmail REST API returns hex envelope IDs that don't match IMAP UIDs. We look
    up the message-id from himalaya's cached envelope list, then search IMAP.
    """
    try:
        msgid = None
        for pg in range(1, 6):
            stdout, _, code = run_bounded(
                ["himalaya", "envelope", "list", "--json", "-p", str(pg), "-s", "10"],
                timeout=20,
                max_output_bytes=1024 * 1024,
            )
            if code != 0 or not stdout:
                break
            for env in json.loads(stdout).get("envelopes", []):
                if env.get("id") == himalaya_id:
                    msgid = (env.get("message-id") or "").strip("<>")
                    break
            if msgid:
                break
        if not msgid:
            return None
        typ, data = conn.uid("SEARCH", "HEADER", "Message-ID", _imap_quote(msgid))
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

IMAP_OP_TIMEOUT = 15  # overall budget for a direct-IMAP fallback operation, seconds

def _imap_delete_direct(mid):
    """Direct-IMAP trash fallback. Runs in a child process so a hung server (Gmail
    trickling bytes without ever completing a line) can be killed by the parent's
    timeout — socket.timeout and SIGALRM cannot interrupt a blocking SSL read."""
    creds = load_imap_credentials()
    if not creds or creds[3] == "xoauth2":
        return False
    server, user, pw, _auth_type = creds
    host, _, port = server.partition(":")
    try:
        port = int(port) if port else 993
        conn = imaplib.IMAP4_SSL(host, port, timeout=5)
        conn.sock.settimeout(5)
        try:
            conn.login(user, pw)
            typ, _ = conn.select("INBOX")
            if typ == "OK":
                trash = find_trash_mailbox(conn)
                if trash:
                    uid = mid if mid.isdigit() else resolve_uid_by_msgid(conn, mid)
                    if uid:
                        typ, _ = conn.uid("MOVE", uid, _imap_quote(trash))
                        if typ == "OK":
                            return True
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception:
        pass
    return False

def restore_message(mid):
    """Move a message from the trash mailbox back to the inbox."""
    out, err, code = run_himalaya_safe(
        ["himalaya", "message", "move", "--from", "trash", "--to", "inbox", "--", mid],
        timeout=20.0,
    )
    return (True, "") if code == 0 else (False, err or out or "Failed to restore message")


def delete_message(mid):
    """Move message to trash via native himalaya delete with direct IMAP fallback."""
    # 1. Native himalaya message delete — works for Gmail REST (OAuth), IMAP, JMAP, Maildir
    out, err, code = run_himalaya_safe(["himalaya", "message", "delete", "--", mid], timeout=20.0)
    if code == 0:
        return True, ""

    # 2. Direct IMAP fallback. Credential resolution happens only in the
    # isolated child so password-manager commands are not run twice.
    try:
        stdout, _, code = run_bounded(
            [sys.executable, os.path.abspath(__file__), "--imap-delete", mid],
            timeout=IMAP_OP_TIMEOUT,
            max_output_bytes=64 * 1024,
        )
        if code == 0 and stdout == "ok":
            return True, ""
    except OSError:
        pass

    return False, err or out or "Failed to delete message via himalaya"

def main():
    if "--imap-delete" in sys.argv:
        idx = sys.argv.index("--imap-delete")
        mid = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
        if not re.fullmatch(r"[A-Za-z0-9._-]+", mid):
            print("fail")
            sys.exit(1)
        print("ok" if _imap_delete_direct(mid) else "fail")
        sys.exit(0)
    if len(sys.argv) < 3:
        print(json.dumps({"success": False, "error": "Usage: action.py <mark_read|mark_unread|delete> <id>"}))
        sys.exit(1)

    action = sys.argv[1]
    mid = sys.argv[2]
    mailbox = sys.argv[3].lower() if len(sys.argv) > 3 else "inbox"

    # Validate message ID and mailbox
    if not re.fullmatch(r'[A-Za-z0-9._-]+', mid):
        print(json.dumps({"success": False, "error": "Invalid message ID", "id": mid}))
        sys.exit(1)
    if mailbox not in ("inbox", "trash"):
        print(json.dumps({"success": False, "error": "Invalid mailbox", "id": mid}))
        sys.exit(1)

    mailbox_args = ["--mailbox", mailbox] if mailbox != "inbox" else []
    if action == "mark_read":
        update_cache_flag(mid, seen=True, mailbox=mailbox)
        cmd = ["himalaya", "flag", "add", "-f", "seen", *mailbox_args, "--", mid]
    elif action == "mark_unread":
        update_cache_flag(mid, seen=False, mailbox=mailbox)
        cmd = ["himalaya", "flag", "remove", "-f", "seen", *mailbox_args, "--", mid]
    elif action == "delete" and mailbox == "inbox":
        remove_from_cache(mid)
        ok, err = delete_message(mid)
        if ok:
            print(json.dumps({"success": True, "id": mid, "action": action}))
            sys.exit(0)
        else:
            print(json.dumps({"success": False, "error": err or "Failed to delete message", "id": mid}))
            sys.exit(1)
    elif action == "restore" and mailbox == "trash":
        remove_from_cache(mid)
        ok, err = restore_message(mid)
        if ok:
            print(json.dumps({"success": True, "id": mid, "action": action}))
            sys.exit(0)
        print(json.dumps({"success": False, "error": err, "id": mid}))
        sys.exit(1)
    else:
        print(json.dumps({"success": False, "error": f"Unknown action: {action}"}))
        sys.exit(1)

    try:
        _out, err, code = run_himalaya_safe(cmd, timeout=20.0)
        if code == 0:
            print(json.dumps({"success": True, "id": mid, "action": action}))
        else:
            print(json.dumps({"success": False, "error": err or "Himalaya error", "id": mid}))
            sys.exit(1)
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e), "id": mid}))
        sys.exit(1)

if __name__ == "__main__":
    main()
