#!/usr/bin/env python3
"""Omarmail ultra-fast envelope lister with multi-page disk caching and background prefetching.

Usage:
  python3 list.py [page_size] [page] [--mailbox inbox|trash] [--cache-only] [--force]
"""
import fcntl
import imaplib
import json
import os
import re
import subprocess
import sys
import threading
import time

from credentials import load_imap_credentials as load_credentials
from secure_io import (
    atomic_write_json,
    ensure_private_dir,
    harden_private_file,
    read_json,
    run_bounded,
    safe_mtime,
)

CACHE_BASE = ensure_private_dir(os.path.expanduser("~/.cache/omarmail"))
CACHE_DIR = ensure_private_dir(os.path.join(CACHE_BASE, "pages"))

INBOX_CACHE = os.path.join(CACHE_BASE, "inbox_cache.json")

# --- Inbox exclusion filtering ----------------------------------------------
# Hide messages matching Gmail search terms (e.g. "category:promotions") from
# every list output. Terms live in ~/.config/omarmail/excluded.json as a JSON
# array; matched IMAP/Gmail message identifiers are resolved via X-GM-RAW and cached.
EXCLUDED_CONFIG = os.path.expanduser("~/.config/omarmail/excluded.json")
EXCLUDED_MSGID_CACHE = os.path.join(CACHE_BASE, "excluded_msgids.json")
EXCLUDED_REFRESH_LOCK = os.path.join(CACHE_BASE, ".excluded-refresh.lock")
EXCLUDED_TTL = 300  # seconds
MAX_EXCLUDED_IDS = 50_000
HIMALAYA_CONFIG = os.path.expanduser("~/.config/himalaya/config.toml")

ensure_private_dir(os.path.dirname(EXCLUDED_CONFIG))
for private_file in (INBOX_CACHE, EXCLUDED_MSGID_CACHE, EXCLUDED_CONFIG, os.path.join(CACHE_BASE, "avatar_map.json")):
    harden_private_file(private_file)

GMAIL_CATEGORIES = ["category:promotions", "category:social", "category:updates", "category:forums"]

def _normalize_excluded_terms(terms):
    if isinstance(terms, str):
        terms = [terms]
    if not isinstance(terms, list):
        return []
    return sorted(set(
        term.strip()
        for term in terms
        if isinstance(term, str)
        and 0 < len(term.strip()) <= 256
        and not any(char in term for char in ('"', "\\", "\r", "\n"))
    ))


def load_excluded_terms():
    """Read Gmail search terms whose matches are hidden from the inbox."""
    return _normalize_excluded_terms(read_json(EXCLUDED_CONFIG, default=[], max_bytes=64 * 1024))

def save_excluded_terms(terms):
    """Write Gmail search terms to excluded.json."""
    ensure_private_dir(os.path.dirname(EXCLUDED_CONFIG))
    atomic_write_json(EXCLUDED_CONFIG, _normalize_excluded_terms(terms), indent=2)

def load_imap_credentials():
    return load_credentials(HIMALAYA_CONFIG)

def _imap_connect(creds):
    server, user, pw, auth_type = creds
    host, _, port = server.partition(":")
    try: port = int(port) if port else 993
    except ValueError: port = 993
    conn = imaplib.IMAP4_SSL(host, port, timeout=8)
    conn.sock.settimeout(8)
    if auth_type == "xoauth2":
        auth_str = f"user={user}\x01auth=Bearer {pw}\x01\x01"
        conn.authenticate("XOAUTH2", lambda _: auth_str.encode())
    else:
        conn.login(user, pw)
    conn.select("INBOX")
    return conn

IMAP_OP_TIMEOUT = 20  # overall budget for an IMAP exclusion lookup, seconds

def _resolve_excluded_imap(terms):
    """Resolve IDs hidden by Gmail search for IMAP and Gmail API backends.

    IMAP-backed Himalaya envelopes use UIDs. Gmail API envelopes use the
    hexadecimal form of X-GM-MSGID, so collect both identifiers in one FETCH.
    """
    creds = load_imap_credentials()
    if not creds:
        return None
    conn = None
    try:
        conn = _imap_connect(creds)
        uids = set()
        for term in terms:
            typ, data = conn.uid("SEARCH", "X-GM-RAW", f'"{term}"')
            if typ != "OK":
                return None
            if data and data[0]:
                uids.update(uid for uid in data[0].decode("ascii", "ignore").split() if uid.isdigit())
            if len(uids) > MAX_EXCLUDED_IDS:
                return None

        identifiers = set(uids)
        if uids:
            ordered_uids = sorted(uids, key=lambda uid: int(uid) if uid.isdigit() else uid)
            typ, data = conn.uid("FETCH", ",".join(ordered_uids), "(X-GM-MSGID)")
            if typ != "OK":
                return None
            for item in data or []:
                wire = item[0] if isinstance(item, tuple) else item
                if not isinstance(wire, bytes):
                    continue
                for value in re.findall(rb"X-GM-MSGID\s+(\d+)", wire, re.IGNORECASE):
                    identifiers.add(format(int(value), "x"))
        return identifiers
    except Exception:
        return None
    finally:
        if conn is not None:
            try: conn.logout()
            except Exception: pass

def _trigger_exclusion_refresh(terms):
    """Start at most one exclusion refresher across concurrent list calls."""
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_fd = os.open(EXCLUDED_REFRESH_LOCK, flags, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        try: os.close(lock_fd)
        except (NameError, OSError): pass
        return
    try:
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--resolve-excluded", json.dumps(terms)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=(lock_fd,),
        )
    except OSError:
        pass
    finally:
        os.close(lock_fd)

def fetch_excluded_msgids(terms):
    """Return cached excluded IDs immediately (fresh or stale), and refresh
    the cache in the background. Never blocks list rendering on a hung IMAP server:
    a Gmail trickle-hang previously cost 20s per list call and silently disabled
    exclusion."""
    payload = read_json(EXCLUDED_MSGID_CACHE, default=None, max_bytes=4 * 1024 * 1024)
    cached = set(payload) if isinstance(payload, list) else None
    modified = safe_mtime(EXCLUDED_MSGID_CACHE)
    if cached is not None and modified is not None and time.time() - modified < EXCLUDED_TTL:
        return cached
    _trigger_exclusion_refresh(terms)
    return cached if cached is not None else set()

def apply_exclusion(envelopes):
    """Filter envelopes by IMAP UID or Gmail API message ID."""
    terms = load_excluded_terms()
    if not terms:
        return envelopes
    excluded = fetch_excluded_msgids(terms)
    if not excluded:
        return envelopes
    return [e for e in envelopes if str(e.get("id") or "") not in excluded]

def get_page_cache_path(page_size, page, mailbox="inbox"):
    prefix = "p" if mailbox == "inbox" else f"{mailbox}_p"
    return os.path.join(CACHE_DIR, f"{prefix}_{page_size}_{page}.json")


def get_cached_page(page_size, page, mailbox="inbox"):
    data = read_json(get_page_cache_path(page_size, page, mailbox), default=None)
    if data is not None:
        envelopes = data if isinstance(data, list) else data.get("envelopes", [])
        if isinstance(envelopes, list):
            if envelopes and len(envelopes) < page_size:
                next_data = read_json(get_page_cache_path(page_size, page + 1, mailbox), default=None)
                if next_data is not None:
                    next_envelopes = next_data if isinstance(next_data, list) else next_data.get("envelopes", [])
                    if next_envelopes:
                        envelopes = envelopes + next_envelopes[:page_size - len(envelopes)]
            return envelopes
    if mailbox == "inbox" and page == 1:
        data = read_json(INBOX_CACHE, default=None)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "envelopes" in data:
            return data["envelopes"]
    return None


def save_page_cache(page_size, page, envelopes, mailbox="inbox"):
    try:
        atomic_write_json(get_page_cache_path(page_size, page, mailbox), envelopes, ensure_ascii=False)
        if mailbox == "inbox" and page == 1:
            atomic_write_json(INBOX_CACHE, envelopes, ensure_ascii=False)
    except OSError:
        pass


def run_himalaya_safe(cmd, timeout=20.0):
    return run_bounded(cmd, timeout=timeout, max_output_bytes=2 * 1024 * 1024)


def fetch_envelopes_direct(page_size, page, mailbox="inbox"):
    cmd = ["himalaya", "envelope", "list", "--json", "-s", str(page_size), "-p", str(page)]
    if mailbox != "inbox":
        cmd.extend(["--mailbox", mailbox])
    try:
        out, err, code = run_himalaya_safe(cmd, timeout=20.0)
        if code == 0 and out:
            data = json.loads(out)
            envelopes = data.get("envelopes", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            save_page_cache(page_size, page, envelopes, mailbox)
            return {"envelopes": envelopes, "error": "", "mailbox": mailbox}
        return {"envelopes": [], "error": err or "Failed to list envelopes", "mailbox": mailbox}
    except Exception as error:
        return {"envelopes": [], "error": str(error), "mailbox": mailbox}


def fetch_envelopes_filtered(page_size, page, mailbox="inbox"):
    """Fetch one mailbox; pull extra inbox pages when exclusions shrink results."""
    collected = []
    has_exclusions = mailbox == "inbox" and bool(load_excluded_terms())
    page_limit = page + 4 if has_exclusions else page + 1
    for target_page in range(page, page_limit):
        result = fetch_envelopes_direct(page_size, target_page, mailbox)
        envelopes = result.get("envelopes", [])
        if not envelopes:
            if not collected and result.get("error"):
                return result
            break
        collected.extend(apply_exclusion(envelopes) if has_exclusions else envelopes)
        if len(collected) >= page_size:
            break
    final_envelopes = collected[:page_size]
    save_page_cache(page_size, page, final_envelopes, mailbox)
    return {"envelopes": final_envelopes, "error": "", "mailbox": mailbox}


def trigger_prefetch(page_size, target_page, mailbox="inbox"):
    """Launch background fetch for next/prev page if not cached."""
    if target_page < 1 or target_page > 20:
        return
    cache_path = get_page_cache_path(page_size, target_page, mailbox)
    modified = safe_mtime(cache_path)
    if modified is not None and time.time() - modified < 300:
        return
    try:
        script_path = os.path.abspath(__file__)
        subprocess.Popen(
            ["python3", script_path, str(page_size), str(target_page), "--mailbox", mailbox, "--bg-fetch"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


def main():
    if "--resolve-excluded" in sys.argv:
        idx = sys.argv.index("--resolve-excluded")
        try:
            terms = _normalize_excluded_terms(json.loads(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else [])
        except ValueError:
            terms = []
        # Background refresher: self-terminate if IMAP hangs (Gmail trickle), so a
        # stuck server can never leave an orphaned process behind.
        watchdog = threading.Timer(IMAP_OP_TIMEOUT + 5, os._exit, args=(0,))
        watchdog.daemon = True
        watchdog.start()
        identifiers = _resolve_excluded_imap(terms)
        watchdog.cancel()
        if identifiers is not None:
            try:
                atomic_write_json(EXCLUDED_MSGID_CACHE, sorted(identifiers))
            except OSError:
                pass
        print(json.dumps(sorted(identifiers or [])))
        return
    if "--get-excluded" in sys.argv:
        print(json.dumps({"terms": load_excluded_terms(), "categories": GMAIL_CATEGORIES}))
        return
    if "--set-excluded" in sys.argv:
        idx = sys.argv.index("--set-excluded")
        terms = _normalize_excluded_terms(json.loads(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else [])
        save_excluded_terms(terms)
        try: os.unlink(EXCLUDED_MSGID_CACHE)
        except Exception: pass
        # Kick a background re-resolution so the new exclusion set takes effect
        # without blocking; the refresher self-terminates on a hung server.
        _trigger_exclusion_refresh(terms)
        print(json.dumps({"ok": True}))
        return

    page_size = 30
    page = 1
    cache_only = "--cache-only" in sys.argv
    force = "--force" in sys.argv
    is_bg = "--bg-fetch" in sys.argv
    mailbox = "inbox"
    args = []
    index = 1
    while index < len(sys.argv):
        argument = sys.argv[index]
        if argument == "--mailbox":
            mailbox = sys.argv[index + 1].lower() if index + 1 < len(sys.argv) else ""
            index += 2
            continue
        if not argument.startswith("--"):
            args.append(argument)
        index += 1
    if mailbox not in ("inbox", "trash"):
        print(json.dumps({"envelopes": [], "error": "Invalid mailbox"}))
        sys.exit(1)

    if len(args) >= 1:
        try: page_size = max(1, min(100, int(args[0])))
        except ValueError: pass
    if len(args) >= 2:
        try: page = max(1, int(args[1]))
        except ValueError: pass

    if is_bg:
        fetch_envelopes_direct(page_size, page, mailbox)
        sys.exit(0)

    if cache_only:
        cached = get_cached_page(page_size, page, mailbox)
        if cached is not None:
            envelopes = apply_exclusion(cached) if mailbox == "inbox" else cached
            print(json.dumps({"envelopes": envelopes, "cached": True, "error": "", "mailbox": mailbox, "page": page}))
        else:
            print(json.dumps({"envelopes": [], "cached": False, "error": "", "mailbox": mailbox, "page": page}))
        return

    cached = get_cached_page(page_size, page, mailbox)
    if cached is not None and not force:
        envelopes = apply_exclusion(cached) if mailbox == "inbox" else cached
        print(json.dumps({"envelopes": envelopes, "cached": True, "error": "", "mailbox": mailbox, "page": page}))
        trigger_prefetch(page_size, page + 1, mailbox)
        if page > 1:
            trigger_prefetch(page_size, page - 1, mailbox)
        return

    result = fetch_envelopes_filtered(page_size, page, mailbox)
    if result.get("error") and cached is not None:
        result["envelopes"] = apply_exclusion(cached) if mailbox == "inbox" else cached
        result["from_cache"] = True

    if mailbox == "inbox":
        result["envelopes"] = apply_exclusion(result.get("envelopes", []))
    result["page"] = page
    print(json.dumps(result))

    if not result.get("error") and result.get("envelopes"):
        trigger_prefetch(page_size, page + 1, mailbox)

if __name__ == "__main__":
    main()
