#!/usr/bin/env python3
"""Omarmail ultra-fast envelope lister with multi-page disk caching and background prefetching.

Usage:
  python3 list.py [page_size] [page] [--cache-only] [--force]
"""
import sys
import os
import json
import time
import subprocess
import tempfile
import imaplib
import tomllib

CACHE_BASE = os.path.expanduser("~/.cache/omarmail")
CACHE_DIR = os.path.join(CACHE_BASE, "pages")
os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
try:
    os.chmod(CACHE_BASE, 0o700)
    os.chmod(CACHE_DIR, 0o700)
except Exception:
    pass

INBOX_CACHE = os.path.join(CACHE_BASE, "inbox_cache.json")

# --- Inbox exclusion filtering ----------------------------------------------
# Hide messages matching Gmail search terms (e.g. "category:promotions") from
# every list output. Terms live in ~/.config/omarmail/excluded.json as a JSON
# array; matched message UIDs are resolved via IMAP X-GM-RAW and cached.
EXCLUDED_CONFIG = os.path.expanduser("~/.config/omarmail/excluded.json")
EXCLUDED_MSGID_CACHE = os.path.join(CACHE_BASE, "excluded_msgids.json")
EXCLUDED_TTL = 300  # seconds
HIMALAYA_CONFIG = os.path.expanduser("~/.config/himalaya/config.toml")

GMAIL_CATEGORIES = ["category:promotions", "category:social", "category:updates", "category:forums"]

def load_excluded_terms():
    """Read Gmail search terms whose matches are hidden from the inbox."""
    try:
        with open(EXCLUDED_CONFIG, "r", encoding="utf-8") as f:
            terms = json.load(f)
        if isinstance(terms, str):
            terms = [terms]
        return [t.strip() for t in terms if isinstance(t, str) and t.strip() and '"' not in t]
    except Exception:
        return []

def save_excluded_terms(terms):
    """Write Gmail search terms to excluded.json."""
    os.makedirs(os.path.dirname(EXCLUDED_CONFIG), mode=0o700, exist_ok=True)
    tmp = EXCLUDED_CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(set(terms)), f, indent=2)
    os.replace(tmp, EXCLUDED_CONFIG)

def load_imap_credentials():
    """Parse IMAP credentials from himalaya config — plain IMAP or ortie OAuth."""
    try:
        with open(HIMALAYA_CONFIG, "rb") as f:
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
                        # Search multiple pages for a @gmail.com address in to/cc fields
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

def _imap_connect(creds):
    server, user, pw, auth_type = creds
    host, _, port = server.partition(":")
    try: port = int(port) if port else 993
    except ValueError: port = 993
    conn = imaplib.IMAP4_SSL(host, port, timeout=8)
    if auth_type == "xoauth2":
        auth_str = f"user={user}\x01auth=Bearer {pw}\x01\x01"
        conn.authenticate("XOAUTH2", lambda _: auth_str.encode())
    else:
        conn.login(user, pw)
    conn.select("INBOX")
    return conn

def fetch_excluded_msgids(terms):
    """Resolve Message-IDs hidden by the given search terms, cached for EXCLUDED_TTL."""
    if os.path.exists(EXCLUDED_MSGID_CACHE):
        try:
            fresh = time.time() - os.path.getmtime(EXCLUDED_MSGID_CACHE) < EXCLUDED_TTL
            config_newer = (os.path.exists(EXCLUDED_CONFIG) and
                            os.path.getmtime(EXCLUDED_CONFIG) > os.path.getmtime(EXCLUDED_MSGID_CACHE))
            if fresh and not config_newer:
                with open(EXCLUDED_MSGID_CACHE, "r", encoding="utf-8") as f:
                    return set(json.load(f))
        except Exception:
            pass
    creds = load_imap_credentials()
    if not creds:
        return set()
    msgids = set()
    try:
        conn = _imap_connect(creds)
        try:
            uids = set()
            for term in terms:
                typ, data = conn.uid("SEARCH", f'X-GM-RAW "{term}"')
                if typ == "OK" and data and data[0]:
                    uids.update(data[0].decode().split())
            if uids:
                uid_list = sorted(uids, key=int)
                for i in range(0, len(uid_list), 200):
                    batch = ",".join(uid_list[i:i+200])
                    typ, data = conn.uid("FETCH", batch, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
                    if typ == "OK":
                        for item in data:
                            if isinstance(item, tuple) and len(item) > 1:
                                for line in item[1].decode(errors="replace").splitlines():
                                    if line.lower().startswith("message-id:"):
                                        mid = line.split(":", 1)[1].strip().strip("<>")
                                        if mid: msgids.add(mid)
        finally:
            try: conn.logout()
            except Exception: pass
    except Exception:
        return set()
    if msgids:
        try:
            tmp = EXCLUDED_MSGID_CACHE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(sorted(msgids), f)
            os.replace(tmp, EXCLUDED_MSGID_CACHE)
        except Exception:
            pass
    return msgids

def apply_exclusion(envelopes):
    """Filter out envelopes whose Message-ID matches an excluded search term."""
    terms = load_excluded_terms()
    if not terms:
        return envelopes
    excluded = fetch_excluded_msgids(terms)
    if not excluded:
        return envelopes
    return [e for e in envelopes if (e.get("message-id") or "").strip("<>") not in excluded]

def get_page_cache_path(page_size, page):
    return os.path.join(CACHE_DIR, f"p_{page_size}_{page}.json")

def get_cached_page(page_size, page):
    cache_path = get_page_cache_path(page_size, page)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                envs = data if isinstance(data, list) else data.get("envelopes", [])
                if isinstance(envs, list) and len(envs) > 0:
                    if len(envs) < page_size:
                        next_cache = get_page_cache_path(page_size, page + 1)
                        if os.path.exists(next_cache):
                            try:
                                with open(next_cache, "r", encoding="utf-8") as nf:
                                    ndata = json.load(nf)
                                    nenvs = ndata if isinstance(ndata, list) else ndata.get("envelopes", [])
                                    if nenvs:
                                        need = page_size - len(envs)
                                        envs = envs + nenvs[:need]
                            except Exception:
                                pass
                    return envs
        except Exception:
            pass
    # Fallback for page 1 to legacy inbox_cache.json
    if page == 1 and os.path.exists(INBOX_CACHE):
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

def save_page_cache(page_size, page, envelopes):
    if not envelopes:
        return
    try:
        cache_path = get_page_cache_path(page_size, page)
        tmp = cache_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(envelopes, f, ensure_ascii=False)
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        os.replace(tmp, cache_path)
        try:
            os.chmod(cache_path, 0o600)
        except Exception:
            pass
        # If page 1, also mirror to INBOX_CACHE for fast boot
        if page == 1:
            tmp_inbox = INBOX_CACHE + ".tmp"
            fd_inbox = os.open(tmp_inbox, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with open(fd_inbox, "w", encoding="utf-8") as f:
                json.dump(envelopes, f, ensure_ascii=False)
            os.replace(tmp_inbox, INBOX_CACHE)
    except Exception:
        pass

def run_himalaya_safe(cmd, timeout=12.0):
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

def fetch_envelopes_direct(page_size, page):
    cmd = ["himalaya", "envelope", "list", "--json", "-s", str(page_size), "-p", str(page)]
    try:
        out, err, code = run_himalaya_safe(cmd, timeout=12.0)
        if code == 0 and out:
            data = json.loads(out)
            envelopes = data.get("envelopes", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            if envelopes:
                save_page_cache(page_size, page, envelopes)
            return {"envelopes": envelopes, "error": ""}
        else:
            return {"envelopes": [], "error": err or "Failed to list envelopes"}
    except Exception as e:
        return {"envelopes": [], "error": str(e)}

def fetch_envelopes_filtered(page_size, page):
    """Fetch with exclusion; pull extra pages if filtering or deletion shrinks results."""
    collected = []
    has_exclusions = bool(load_excluded_terms())
    for p in range(page, page + 4):
        result = fetch_envelopes_direct(page_size, p)
        envs = result.get("envelopes", [])
        if not envs:
            if not collected and result.get("error"):
                return result
            break
        filtered = apply_exclusion(envs) if has_exclusions else envs
        collected.extend(filtered)
        if len(collected) >= page_size:
            break
    final_envs = collected[:page_size]
    if final_envs:
        save_page_cache(page_size, page, final_envs)
    return {"envelopes": final_envs, "error": ""}

def trigger_prefetch(page_size, target_page):
    """Launch background fetch for next/prev page if not cached."""
    if target_page < 1 or target_page > 20:
        return
    cache_path = get_page_cache_path(page_size, target_page)
    # If cached recently (< 5 minutes), skip
    if os.path.exists(cache_path):
        try:
            if time.time() - os.path.getmtime(cache_path) < 300:
                return
        except Exception:
            pass
    try:
        script_path = os.path.abspath(__file__)
        subprocess.Popen(
            ["python3", script_path, str(page_size), str(target_page), "--bg-fetch"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
    except Exception:
        pass

def main():
    if "--get-excluded" in sys.argv:
        print(json.dumps({"terms": load_excluded_terms(), "categories": GMAIL_CATEGORIES}))
        return
    if "--set-excluded" in sys.argv:
        idx = sys.argv.index("--set-excluded")
        terms = json.loads(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else []
        save_excluded_terms(terms)
        try: os.unlink(EXCLUDED_MSGID_CACHE)
        except Exception: pass
        print(json.dumps({"ok": True}))
        return

    page_size = 30
    page = 1
    cache_only = "--cache-only" in sys.argv
    force = "--force" in sys.argv
    is_bg = "--bg-fetch" in sys.argv

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) >= 1:
        try: page_size = max(1, min(100, int(args[0])))
        except ValueError: pass
    if len(args) >= 2:
        try: page = max(1, int(args[1]))
        except ValueError: pass

    if is_bg:
        fetch_envelopes_direct(page_size, page)
        sys.exit(0)

    if cache_only:
        cached = get_cached_page(page_size, page)
        if cached is not None:
            print(json.dumps({"envelopes": apply_exclusion(cached), "cached": True, "error": ""}))
        else:
            print(json.dumps({"envelopes": [], "cached": False, "error": ""}))
        return

    cached = get_cached_page(page_size, page)
    if cached is not None and not force:
        print(json.dumps({"envelopes": apply_exclusion(cached), "cached": True, "error": ""}))
        trigger_prefetch(page_size, page + 1)
        if page > 1: trigger_prefetch(page_size, page - 1)
        return

    result = fetch_envelopes_filtered(page_size, page)
    if result.get("error") and cached is not None:
        result["envelopes"] = apply_exclusion(cached)
        result["from_cache"] = True

    result["envelopes"] = apply_exclusion(result.get("envelopes", []))
    print(json.dumps(result))

    if not result.get("error") and result.get("envelopes"):
        trigger_prefetch(page_size, page + 1)

if __name__ == "__main__":
    main()
