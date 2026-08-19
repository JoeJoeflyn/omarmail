#!/usr/bin/env python3
"""Omarmail auth helper — drives the ortie OAuth flow from the plugin UI.

Handles full first-run setup:
  0. Writes ortie + himalaya configs if missing
  1. Calls `ortie auth get --json` to get the authorization URL + state + PKCE
  2. Opens the URL in the default browser
  3. Starts a local HTTP server on port 8421 to catch the OAuth redirect
  4. When Google redirects back, calls `ortie auth resume` with the redirect URI
  5. Prints "OK" on success, or an error message on failure

Exit code 0 = authenticated, non-zero = failed.
"""
import http.server
import json
import os
import subprocess
import sys
import threading

REDIRECT_PORT = 8421
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}"
ACCOUNT = "gmail"

ORTIE_CONFIG = """\
# Ortie OAuth config for Gmail — written by Omarmail plugin.
[accounts.gmail]
default = true
client-id = "406964657835-aq8lmia8j95dhl1a2bvharmfk3t1hgqj.apps.googleusercontent.com"
client-secret.raw = "kSmqreRr0qwBWJgbf5Y-PjSU"
endpoints.redirection = "http://localhost:8421"
endpoints.authorization = "https://accounts.google.com/o/oauth2/v2/auth"
endpoints.token = "https://oauth2.googleapis.com/token"
scopes = ["https://www.googleapis.com/auth/carddav", "https://mail.google.com/"]
extras.access_type = "offline"
storage.read.command = "secret-tool lookup token ortie-gmail"
storage.write.command = "secret-tool store --label ortie-gmail token ortie-gmail"
auto-refresh = true
"""

HIMALAYA_CONFIG = """\
# Himalaya config for Omarmail — Gmail REST API backend with ortie OAuth.
[accounts.gmail]
default = true
gmail.auth.token.command = ["ortie", "token", "show", "-a", "gmail"]

[mailbox.alias]
inbox = "INBOX"
sent = "SENT"
drafts = "DRAFTS"
trash = "TRASH"
"""

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def ensure_configs():
    """Write ortie + himalaya configs if they don't exist."""
    ortie_path = os.path.expanduser("~/.config/ortie/config.toml")
    himalaya_path = os.path.expanduser("~/.config/himalaya/config.toml")

    if not os.path.exists(ortie_path):
        os.makedirs(os.path.dirname(ortie_path), exist_ok=True)
        with open(ortie_path, "w") as f:
            f.write(ORTIE_CONFIG)

    if not os.path.exists(himalaya_path):
        os.makedirs(os.path.dirname(himalaya_path), exist_ok=True)
        with open(himalaya_path, "w") as f:
            f.write(HIMALAYA_CONFIG)

def check_dependencies():
    """Verify himalaya and ortie are installed."""
    for binary in ["himalaya", "ortie"]:
        stdout, _, code = run(["which", binary])
        if code != 0:
            print(f"ERROR: {binary} is not installed")
            if binary == "himalaya":
                print("  Install with: omarchy pkg add himalaya")
            else:
                print("  Install with: curl -sSL https://raw.githubusercontent.com/pimalaya/ortie/master/install.sh | PREFIX=~/.local sh")
            sys.exit(1)

def main():
    check_dependencies()
    ensure_configs()

    # 1. Start the OAuth flow
    stdout, stderr, code = run(["ortie", "auth", "get", "-a", ACCOUNT, "--json"])
    if code != 0:
        print(f"ERROR: ortie auth get failed: {stderr or stdout}")
        sys.exit(1)

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        print(f"ERROR: could not parse ortie output: {stdout}")
        sys.exit(1)

    auth_url = data.get("authorization_uri", "")
    state = data.get("state", "")
    pkce = data.get("pkce_code_verifier", "")

    if not auth_url or not state or not pkce:
        print("ERROR: ortie auth get returned incomplete data")
        sys.exit(1)

    # 2. Open the authorization URL in the browser
    subprocess.run(["xdg-open", auth_url], capture_output=True)

    # 3. Start a local HTTP server to catch the redirect
    result = {"uri": None}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            result["uri"] = f"http://localhost:{REDIRECT_PORT}{self.path}"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {
    margin: 0;
    height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #1a1b26;
    color: #a9b1d6;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }
  .card {
    text-align: center;
    padding: 48px;
  }
  .icon {
    font-size: 64px;
    margin-bottom: 24px;
  }
  h1 {
    font-size: 24px;
    font-weight: 600;
    color: #7aa2f7;
    margin: 0 0 12px 0;
  }
  p {
    font-size: 15px;
    color: #565f89;
    margin: 0;
  }
</style>
</head>
<body>
  <div class="card">
    <div class="icon">\xf0\x9f\x93\xa7</div>
    <h1>Connected to Gmail</h1>
    <p>You can close this tab and return to Omarmail.</p>
  </div>
</body>
</html>""")
        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), Handler)
    server.timeout = 300  # 5 min timeout

    server.handle_request()
    server.server_close()

    if not result["uri"]:
        print("ERROR: timed out waiting for browser redirect")
        sys.exit(1)

    # 4. Resume the OAuth flow with the redirect URI
    stdout, stderr, code = run([
        "ortie", "auth", "resume", "-a", ACCOUNT, "--json",
        result["uri"],
        "-s", state,
        "-p", pkce,
        "-r", REDIRECT_URI,
    ])

    if code != 0:
        print(f"ERROR: ortie auth resume failed: {stderr or stdout}")
        sys.exit(1)

    # 5. Verify the token is stored
    stdout, stderr, code = run(["ortie", "token", "show", "-a", ACCOUNT])
    if code != 0:
        print(f"ERROR: token verification failed: {stderr or stdout}")
        sys.exit(1)

    print("OK")

if __name__ == "__main__":
    main()
