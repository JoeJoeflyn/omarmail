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
EXCLUDED_UID_CACHE = os.path.join(CACHE_BASE, "excluded_uids.json")
EXCLUDED_TTL = 300  # seconds
HIMALAYA_CONFIG = os.path.expanduser("~/.config/himalaya/config.toml")

def load_excluded_terms():
    """Read Gmail search terms whose matches are hidden from the inbox."""
    try:
        with open(EXCLUDED_CONFIG, "r", encoding="utf-8") as f:
            terms = json.load(f)
        if isinstance(terms, str):
            terms = [terms]
        # Strip quotes/whitespace; terms with quotes would break X-GM-RAW syntax
        return [t.strip() for t in terms if isinstance(t, str) and t.strip() and '"' not in t]
    except Exception:
        return []

def load_imap_credentials():
    """Parse IMAP server/login/password from himalaya's config (single source of truth)."""
    try:
        with open(HIMALAYA_CONFIG, "rb") as f:
            cfg = tomllib.load(f)
        account = cfg["accounts"]["personal"]
        server = account["imap"]["server"]
        user = account["imap"]["sasl"]["plain"]["username"]
        pw = account["imap"]["sasl"]["plain"]["password"]["raw"]
        return server, user, pw
    except Exception:
        return None

def fetch_excluded_uids(terms):
    """Resolve the UID set hidden by the given search terms, cached for EXCLUDED_TTL."""
    if os.path.exists(EXCLUDED_UID_CACHE):
        try:
            fresh = time.time() - os.path.getmtime(EXCLUDED_UID_CACHE) < EXCLUDED_TTL
            config_newer = (os.path.exists(EXCLUDED_CONFIG) and
                            os.path.getmtime(EXCLUDED_CONFIG) > os.path.getmtime(EXCLUDED_UID_CACHE))
            if fresh and not config_newer:
                with open(EXCLUDED_UID_CACHE, "r", encoding="utf-8") as f:
                    return set(json.load(f))
        except Exception:
            pass
    creds = load_imap_credentials()
    if not creds:
        return set()
    server, user, pw = creds
    host, _, port = server.partition(":")
    try:
        port = int(port) if port else 993
    except ValueError:
        port = 993
    uids = set()
    try:
        conn = imaplib.IMAP4_SSL(host, port, timeout=8)
        try:
            conn.login(user, pw)
            conn.select("INBOX")
            for term in terms:
                typ, data = conn.uid("SEARCH", f'X-GM-RAW "{term}"')
                if typ == "OK" and data and data[0]:
                    uids.update(data[0].decode().split())
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception:
        # Any IMAP failure degrades to no filtering; inbox listing must keep working
        return set()
    if uids:
        try:
            tmp = EXCLUDED_UID_CACHE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(sorted(uids), f)
            os.replace(tmp, EXCLUDED_UID_CACHE)
        except Exception:
            pass
    return uids

def apply_exclusion(envelopes):
    """Filter out envelopes whose UID matches an excluded search term."""
    terms = load_excluded_terms()
    if not terms:
        return envelopes
    excluded = fetch_excluded_uids(terms)
    if not excluded:
        return envelopes
    return [e for e in envelopes if e.get("id") not in excluded]

def get_page_cache_path(page_size, page):
    return os.path.join(CACHE_DIR, f"p_{page_size}_{page}.json")

def get_cached_page(page_size, page):
    cache_path = get_page_cache_path(page_size, page)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "envelopes" in data:
                    return data["envelopes"]
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
    page_size = 30
    page = 1
    cache_only = "--cache-only" in sys.argv
    force = "--force" in sys.argv
    is_bg = "--bg-fetch" in sys.argv

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

    if is_bg:
        # Background prefetch mode: fetch and cache silently
        fetch_envelopes_direct(page_size, page)
        sys.exit(0)

    if cache_only:
        cached = get_cached_page(page_size, page)
        if cached is not None:
            print(json.dumps({"envelopes": apply_exclusion(cached), "cached": True, "error": ""}))
        else:
            print(json.dumps({"envelopes": [], "cached": False, "error": ""}))
        return

    # Instant return from cache if available and not forced
    cached = get_cached_page(page_size, page)
    if cached is not None and not force:
        # Return instantly!
        print(json.dumps({"envelopes": apply_exclusion(cached), "cached": True, "error": ""}))
        # Prefetch adjacent pages in background
        trigger_prefetch(page_size, page + 1)
        if page > 1:
            trigger_prefetch(page_size, page - 1)
        return

    # Cache miss or forced refresh: fetch from IMAP
    result = fetch_envelopes_direct(page_size, page)
    if result.get("error") and cached is not None:
        result["envelopes"] = cached
        result["from_cache"] = True

    result["envelopes"] = apply_exclusion(result.get("envelopes", []))
    print(json.dumps(result))

    # Prefetch next page in background
    if not result.get("error") and result.get("envelopes"):
        trigger_prefetch(page_size, page + 1)

if __name__ == "__main__":
    main()
