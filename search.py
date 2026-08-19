#!/usr/bin/env python3
"""Omarmail search helper — bridges Gmail's ID-only search to envelope data.

Gmail's shared `envelope search` is unsupported, so we use `gmail messages list`
to get IDs, then `message read --json` for each to extract headers.

Output: JSON array of envelopes on stdout, same shape as `envelope list --json`.
"""
import json
import subprocess
import sys

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def extract_header_value(val):
    if isinstance(val, dict):
        if "Text" in val:
            return val["Text"]
        if "Address" in val:
            addrs = val["Address"].get("List", [])
            return [{"name": (a.get("name") or "").strip(), "email": (a.get("address") or "").strip()} for a in addrs if a]
        if "DateTime" in val:
            dt = val["DateTime"]
            y = dt.get("year", 1970)
            m = dt.get("month", 1)
            d = dt.get("day", 1)
            H = dt.get("hour", 0)
            M = dt.get("minute", 0)
            S = dt.get("second", 0)
            return f"{y:04d}-{m:02d}-{d:02d}T{H:02d}:{M:02d}:{S:02d}Z"
    return str(val) if val is not None else ""

def parse_message_to_envelope(msg_json, msg_id):
    try:
        msg = json.loads(msg_json)
    except json.JSONDecodeError:
        return None

    attachments = msg.get("attachments", [])
    env = {
        "id": msg_id,
        "flags": msg.get("flags", []),
        "subject": "(no subject)",
        "from": [],
        "to": [],
        "date": "",
        "has-attachment": len(attachments) > 0,
        "attachments": attachments
    }
    parts = msg.get("parts", [])
    if not parts:
        return env

    for h in parts[0].get("headers", []):
        name = h.get("name", "")
        if isinstance(name, dict):
            name = name.get("other", "")
        name = str(name).lower()
        val = h.get("value", "")
        if name == "from":
            parsed = extract_header_value(val)
            if isinstance(parsed, list):
                env["from"] = parsed
        elif name == "to":
            parsed = extract_header_value(val)
            if isinstance(parsed, list):
                env["to"] = parsed
        elif name == "subject":
            env["subject"] = extract_header_value(val) or "(no subject)"
        elif name == "date":
            env["date"] = extract_header_value(val)

    return env

def main():
    query = sys.argv[1] if len(sys.argv) > 1 else ""
    page_size = sys.argv[2] if len(sys.argv) > 2 else "10"
    page_token = sys.argv[3] if len(sys.argv) > 3 else ""

    if not query:
        print(json.dumps({"envelopes": [], "next_page": ""}))
        return

    # 1. Get IDs from Gmail
    cmd = ["himalaya", "gmail", "messages", "list", "--json", "-q", query, "-s", page_size]
    if page_token:
        cmd += ["--page-token", page_token]

    stdout, stderr, code = run(cmd)
    if code != 0:
        print(json.dumps({"error": stderr or stdout, "envelopes": []}))
        sys.exit(1)

    try:
        listing = json.loads(stdout)
    except json.JSONDecodeError:
        print(json.dumps({"error": "Failed to parse listing", "envelopes": []}))
        sys.exit(1)

    ids = [item["id"] for item in listing.get("ids", [])]
    next_page = listing.get("next_page", "")

    # 2. Fetch each message's headers
    envelopes = []
    for mid in ids:
        stdout, _, code = run(["himalaya", "message", "read", "--json", mid])
        if code != 0:
            continue
        env = parse_message_to_envelope(stdout, mid)
        if env:
            envelopes.append(env)

    print(json.dumps({"envelopes": envelopes, "next_page": next_page}))

if __name__ == "__main__":
    main()

