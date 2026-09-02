#!/usr/bin/env python3
"""Omarmail message reader — returns rich HTML with cached images and structured metadata.

Extracts:
- id, subject, from, from_name, from_email, from_initials, to, cc, date, date_formatted
- attachments info
- body_html: rich, dark-theme compatible HTML with inline local cached images
- body: clean fallback text
"""
import json
import re
import os
import html
from html.parser import HTMLParser
import subprocess
import sys
import hashlib
import struct
import socket
import ssl
import http.client
import ipaddress
import urllib.request
import urllib.parse
import urllib.error
from concurrent.futures import ThreadPoolExecutor

CACHE_DIR = os.path.expanduser("~/.cache/omarmail/images")
MSG_CACHE_DIR = os.path.expanduser("~/.cache/omarmail/messages")
BASE_CACHE_DIR = os.path.expanduser("~/.cache/omarmail")
AVATAR_MAP_PATH = os.path.join(BASE_CACHE_DIR, "avatar_map.json")
PAGES_DIR = os.path.join(BASE_CACHE_DIR, "pages")
INBOX_CACHE = os.path.join(BASE_CACHE_DIR, "inbox_cache.json")
os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
os.makedirs(MSG_CACHE_DIR, mode=0o700, exist_ok=True)


def load_avatar_map():
    try:
        with open(AVATAR_MAP_PATH, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_avatar_map(m):
    try:
        with open(AVATAR_MAP_PATH, "w") as f:
            json.dump(m, f)
    except OSError:
        pass

def update_envelope_cache_seen(mid):
    if os.path.exists(INBOX_CACHE):
        try:
            with open(INBOX_CACHE, "r", encoding="utf-8") as f:
                data = json.load(f)
            envelopes = data if isinstance(data, list) else data.get("envelopes", [])
            for env in envelopes:
                if env.get("id") == mid:
                    flags = env.get("flags", [])
                    flags = [f for f in flags if (f.get("iana") if isinstance(f, dict) else str(f)).lower() != "seen"]
                    flags.append({"raw": "\\Seen", "iana": "seen"})
                    env["flags"] = flags
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
                        modified = False
                        for env in page_envs:
                            if env.get("id") == mid:
                                flags = env.get("flags", [])
                                flags = [f for f in flags if (f.get("iana") if isinstance(f, dict) else str(f)).lower() != "seen"]
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

MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB max per image

def is_safe_public_ip(ip_str):
    """Verify IP address is a safe public IP (exclude private, loopback, link-local, cloud metadata)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if (ip.is_private or ip.is_loopback or ip.is_link_local or 
        ip.is_multicast or ip.is_reserved or ip.is_unspecified):
        return False
    # Check Carrier Grade NAT (100.64.0.0/10)
    cgnat = ipaddress.ip_network("100.64.0.0/10")
    if ip.version == 4 and ip in cgnat:
        return False
    return True

def validate_url_safe(url):
    """Validate that a URL uses http/https, valid ports, and resolves strictly to public IPs.
    Returns (is_safe, resolved_ip) where resolved_ip is the pinned IP to connect to,
    preventing DNS rebinding between the check and the actual fetch."""
    if not url or not isinstance(url, str):
        return False, None
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            return False, None
        host = parsed.hostname
        if not host:
            return False, None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if port not in (80, 443, 8080, 8443):
            return False, None

        # Check if direct IP literal
        try:
            ip_obj = ipaddress.ip_address(host)
            if not is_safe_public_ip(str(ip_obj)):
                return False, None
            return True, host  # Already an IP, pin it
        except ValueError:
            pass  # Domain name — resolve and pin

        # Resolve DNS once and pin the first safe IP — the caller must use this
        # IP for the actual connection to prevent DNS rebinding SSRF.
        addrs = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        if not addrs:
            return False, None
        for family, socktype, proto, canonname, sockaddr in addrs:
            ip = sockaddr[0]
            if not is_safe_public_ip(ip):
                return False, None
        # Return the first safe resolved IP for pinning
        return True, addrs[0][4][0]
    except Exception:
        return False, None

def is_valid_image_bytes(data):
    """Verify magic bytes for valid image formats."""
    if len(data) < 8:
        return False
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith(b"\xff\xd8\xff"):
        return True
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return True
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return True
    if data.startswith(b"BM"):
        return True
    if data.startswith(b"\x00\x00\x01\x00"):
        return True
    return False

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

def format_date_pretty(iso_or_str):
    if not iso_or_str:
        return ""
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})', iso_or_str)
    if m:
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        y, mo, d, h, mi = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
        mon_name = months[mo-1] if 1 <= mo <= 12 else str(mo)
        return f"{d} {mon_name} {y}, {h:02d}:{mi:02d}"
    return iso_or_str

def get_image_size(file_path):
    """Read PNG/JPEG/GIF dimensions from headers without third-party libraries."""
    try:
        with open(file_path, "rb") as f:
            head = f.read(32)
            if head.startswith(b"\x89PNG\r\n\x1a\n"):
                w, h = struct.unpack(">II", head[16:24])
                return int(w), int(h)
            elif head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
                w, h = struct.unpack("<HH", head[6:10])
                return int(w), int(h)
            elif head.startswith(b"\xff\xd8"):
                f.seek(0)
                data = f.read()
                size = len(data)
                idx = 2
                while idx < size:
                    if data[idx] != 0xff:
                        break
                    marker = data[idx+1]
                    idx += 2
                    if marker in (0xc0, 0xc1, 0xc2, 0xc3):
                        idx += 3
                        h, w = struct.unpack(">HH", data[idx:idx+4])
                        return int(w), int(h)
                    elif marker in (0xd9, 0xda):
                        break
                    else:
                        length, = struct.unpack(">H", data[idx:idx+2])
                        idx += length
    except Exception:
        pass
    return None, None

def get_scaled_img_tag(local_path, panel_width=360):
    lw, lh = get_image_size(local_path)
    if not lw or lw <= 2 or not lh or lh <= 2:
        return ""
    
    # Store badges (Google Play / App Store)
    if "play" in local_path.lower() or "appstore" in local_path.lower() or (lw >= 100 and lh <= 50 and (lw / max(lh, 1)) >= 2.2):
        target_w = min(lw, 110)
        target_h = int(target_w * lh / max(lw, 1))
        return f'<img src="file://{local_path}" width="{target_w}" height="{target_h}">'

    # Small icon / avatar / logo
    if lw <= 128 and lh <= 128:
        target_w = min(lw, 36)
        target_h = int(target_w * lh / max(lw, 1))
        return f'<img src="file://{local_path}" width="{target_w}" height="{target_h}">'

    # Wide banner
    if lw >= 200 and (lw / max(lh, 1)) >= 1.4:
        scaled_w = panel_width
        scaled_h = int(panel_width * lh / max(lw, 1))
        return f'<p align="center" style="margin: 6px 0;"><img src="file://{local_path}" width="{scaled_w}" height="{scaled_h}"></p>'
    
    # Large content image
    if lw > panel_width:
        scaled_w = panel_width
        scaled_h = int(panel_width * lh / max(lw, 1))
        return f'<p align="center" style="margin: 6px 0;"><img src="file://{local_path}" width="{scaled_w}" height="{scaled_h}"></p>'

    return f'<img src="file://{local_path}" width="{min(lw, panel_width)}">'

def download_image(url, _depth=0):
    """Download and cache remote email images with SSRF protection, size limits, and magic-byte checks.
    Pins the resolved IP to prevent DNS rebinding — the DNS check and actual
    connection use the same IP, so a malicious DNS server can't return a public
    IP for the check and a private IP for the fetch."""
    if not url or not isinstance(url, str):
        return url, None
    if _depth > 3:  # Redirect depth limit
        return url, None
    low = url.lower()
    if any(k in low for k in ["/track", "pixel.gif", "open.gif", "beacon.gif", "track.gif", "spacer.gif", "1x1"]):
        return url, None

    # SSRF check — returns the pinned IP to connect to
    is_safe, pinned_ip = validate_url_safe(url)
    if not is_safe or not pinned_ip:
        return url, None

    try:
        h = hashlib.sha256(url.encode("utf-8")).hexdigest()
        ext = ".png" if ".png" in low else (".jpg" if ".jpg" in low or ".jpeg" in low else (".gif" if ".gif" in low else ".png"))
        target = os.path.join(CACHE_DIR, f"{h}{ext}")
        if os.path.exists(target) and os.path.getsize(target) > 0:
            return url, target

        parsed = urllib.parse.urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        host = parsed.hostname
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        # Connect to the pinned IP directly — no second DNS lookup.
        # For HTTPS, set server_hostname to the original domain for SNI + cert verification.
        if parsed.scheme == "https":
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(pinned_ip, port, timeout=2.0, context=ctx)
            sock = socket.create_connection((pinned_ip, port), timeout=2.0)
            sock.settimeout(2.0)  # Read timeout on the socket itself
            ssl_sock = ctx.wrap_socket(sock, server_hostname=host)
            ssl_sock.settimeout(2.0)
            conn.sock = ssl_sock
            conn.request("GET", path, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
                "Host": host,
            })
        else:
            conn = http.client.HTTPConnection(pinned_ip, port, timeout=2.0)
            conn.request("GET", path, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
                "Host": host,
            })

        resp = conn.getresponse()

        if resp.status in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location", "")
            conn.close()
            if location:
                redirect_url = urllib.parse.urljoin(url, location)
                _, downloaded_target = download_image(redirect_url, _depth + 1)
                return url, downloaded_target
            return url, None

        # Enforce Content-Length header limit if present
        cl = resp.headers.get("Content-Length")
        if cl:
            try:
                if int(cl) > MAX_IMAGE_SIZE or int(cl) < 16:
                    conn.close()
                    return url, None
            except ValueError:
                pass

        chunks = []
        total_size = 0
        while True:
            chunk = resp.read(8192)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > MAX_IMAGE_SIZE:
                conn.close()
                return url, None
            chunks.append(chunk)
        conn.close()

        data = b"".join(chunks)
        if len(data) < 16 or not is_valid_image_bytes(data):
            return url, None

        tmp_target = target + ".tmp"
        fd = os.open(tmp_target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with open(fd, "wb") as f:
            f.write(data)
        try:
            os.chmod(tmp_target, 0o600)
        except Exception:
            pass
        os.replace(tmp_target, target)
        try:
            os.chmod(target, 0o600)
        except Exception:
            pass
        return url, target
    except Exception:
        return url, None

class CleanEmailBuilder(HTMLParser):
    def __init__(self, panel_width=660):
        super().__init__()
        self.panel_width = panel_width
        self.out = []
        self.skip_depth = 0
        self.in_cell = False
        self.cell_buf = []
        self.row_cells = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        tag = tag.lower()
        style = attrs_dict.get("style", "").lower()

        # Self-closing void tags & images — skip completely
        if tag in ["meta", "link", "base", "input", "img"]:
            return

        # Paired tags to skip
        if tag in ["style", "script", "head", "title", "noscript", "iframe"]:
            self.skip_depth += 1
            return
        if "display: none" in style or "display:none" in style or "visibility: hidden" in style or "mso-hide: all" in style:
            self.skip_depth += 1
            return
        if self.skip_depth > 0:
            self.skip_depth += 1
            return

        if tag == "a":
            href = attrs_dict.get("href", "").strip()
            if href.lower().startswith(("http://", "https://", "mailto:")):
                (self.cell_buf if self.in_cell else self.out).append(f'<a href="{html.escape(href, quote=True)}" style="color: #60a5fa; text-decoration: underline; font-weight: 500;">')
            else:
                (self.cell_buf if self.in_cell else self.out).append("<a>")
        elif tag in ["b", "strong"]:
            (self.cell_buf if self.in_cell else self.out).append("<b>")
        elif tag in ["i", "em"]:
            (self.cell_buf if self.in_cell else self.out).append("<i>")
        elif tag in ["code", "tt"]:
            (self.cell_buf if self.in_cell else self.out).append('<tt style="background: rgba(255,255,255,0.08); padding: 2px 4px; border-radius: 3px; font-family: monospace;">')
        elif tag == "pre":
            (self.cell_buf if self.in_cell else self.out).append('<pre style="margin: 8px 0; padding: 10px; background: rgba(0,0,0,0.3); border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; font-family: monospace;">')
        elif tag == "blockquote":
            (self.cell_buf if self.in_cell else self.out).append('<blockquote style="border-left: 3px solid #3b82f6; margin: 8px 0; padding: 6px 12px; background: rgba(59, 130, 246, 0.08); border-radius: 4px; color: #cbd5e1;">')
        elif tag in ["h1", "h2", "h3", "h4", "h5", "h6"]:
            (self.cell_buf if self.in_cell else self.out).append(f'<{tag} style="margin: 12px 0 6px 0; font-weight: bold; color: #ffffff;">')
        elif tag in ["p", "div"]:
            (self.cell_buf if self.in_cell else self.out).append("<div>")
        elif tag == "br":
            (self.cell_buf if self.in_cell else self.out).append("<br>")
        elif tag == "hr":
            (self.cell_buf if self.in_cell else self.out).append('<hr style="border: 0; border-top: 1px solid rgba(255,255,255,0.12); margin: 12px 0;">')
        elif tag == "li":
            (self.cell_buf if self.in_cell else self.out).append('<li style="margin: 4px 0;">')
        elif tag in ["ul", "ol"]:
            (self.cell_buf if self.in_cell else self.out).append(f'<{tag} style="margin: 8px 0; padding-left: 20px;">')
        elif tag in ["td", "th"]:
            self.in_cell = True
            self.cell_buf = []
        elif tag == "tr":
            self.row_cells = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ["meta", "link", "base", "input", "img"]:
            return
        if self.skip_depth > 0:
            self.skip_depth -= 1
            return

        if tag == "a":
            (self.cell_buf if self.in_cell else self.out).append("</a>")
        elif tag in ["b", "strong"]:
            (self.cell_buf if self.in_cell else self.out).append("</b>")
        elif tag in ["i", "em"]:
            (self.cell_buf if self.in_cell else self.out).append("</i>")
        elif tag in ["code", "tt"]:
            (self.cell_buf if self.in_cell else self.out).append("</tt>")
        elif tag == "pre":
            (self.cell_buf if self.in_cell else self.out).append("</pre>")
        elif tag == "blockquote":
            (self.cell_buf if self.in_cell else self.out).append("</blockquote>")
        elif tag in ["h1", "h2", "h3", "h4", "h5", "h6"]:
            (self.cell_buf if self.in_cell else self.out).append(f"</{tag}>")
        elif tag in ["p", "div"]:
            (self.cell_buf if self.in_cell else self.out).append("</div>")
        elif tag == "li":
            (self.cell_buf if self.in_cell else self.out).append("</li>")
        elif tag in ["ul", "ol"]:
            (self.cell_buf if self.in_cell else self.out).append(f"</{tag}>")
        elif tag in ["td", "th"]:
            self.in_cell = False
            cell_text = "".join(self.cell_buf).strip()
            if cell_text:
                self.row_cells.append(cell_text)
            self.cell_buf = []
        elif tag == "tr":
            if len(self.row_cells) == 1:
                self.out.append(f'<div>{self.row_cells[0]}</div>')
            elif len(self.row_cells) >= 2:
                cells_html = "".join([f'<td style="padding: 4px 8px; vertical-align: top;">{c}</td>' for c in self.row_cells])
                self.out.append(f'<table width="100%" cellpadding="2" cellspacing="0" style="margin: 4px 0;"><tr>{cells_html}</tr></table>')
            self.row_cells = []

    def handle_data(self, data):
        if self.skip_depth > 0:
            return
        t = data
        t = re.sub(r'[\u2007\u034f\u200b\u200c\u200d\ufeff\u00ad]+', '', t)
        if not t.strip():
            return
        t_clean = html.escape(t, quote=True)
        (self.cell_buf if self.in_cell else self.out).append(t_clean)

    def get_html(self):
        res = "".join(self.out)
        res = re.sub(r"<div>\s*<\/div>", "", res)
        res = re.sub(r"(?:<div>\s*){2,}", "<div>", res)
        res = re.sub(r"(?:<\/div>\s*){2,}", "</div>", res)
        res = re.sub(r"<h[1-6][^>]*>\s*<\/h[1-6]>", "", res)
        res = re.sub(r"<a[^>]*>\s*<\/a>", "", res)
        res = re.sub(r"<p[^>]*>\s*<\/p>", "", res)
        res = re.sub(r"(?:<br\s*\/?>\s*){3,}", "<br><br>", res)
        return f'<div style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif; font-size: 13px; line-height: 1.55; color: #e2e8f0; width: 100%; word-break: break-word;">\n{res}\n</div>'

def sanitize_and_enrich_html(raw_html, panel_width=660):
    """Clean HTML for Qt Quick RichText, formatting with robust pure-text reader style (no images)."""
    if not raw_html:
        return ""
    builder = CleanEmailBuilder(panel_width=panel_width)
    builder.feed(raw_html)
    return builder.get_html()

def text_to_rich_html(raw_text):
    """Convert text/markdown email into clean, modern rich HTML with tables, code blocks, headers, details cards, and links."""
    if not raw_text:
        return ""

    # Normalize CRLF / CR linebreaks
    t = raw_text.replace('\r\n', '\n').replace('\r', '\n')

    # 1. Strip raw HTML comments <!-- ... -->
    t = re.sub(r'<!--.*?-->', '', t, flags=re.DOTALL)

    # 2. Markdown tables
    def format_table(match):
        raw_tbl = match.group(0).strip()
        lines = [l.strip() for l in raw_tbl.split('\n') if l.strip()]
        if len(lines) < 2:
            return raw_tbl
        headers = [c.strip() for c in lines[0].strip('|').split('|')]
        rows = []
        for l in lines[2:]:
            cols = [c.strip() for c in l.strip('|').split('|')]
            rows.append(cols)
        th_html = "".join([f'<th style="border: 1px solid rgba(255,255,255,0.15); padding: 8px 12px; background: rgba(255,255,255,0.08); color: #ffffff; font-weight: 600; text-align: left;">{html.escape(h)}</th>' for h in headers])
        tr_html = []
        for r in rows:
            td_html = "".join([f'<td style="border: 1px solid rgba(255,255,255,0.08); padding: 7px 12px; color: #cbd5e1;">{html.escape(c)}</td>' for c in r])
            tr_html.append(f'<tr style="background: rgba(0,0,0,0.15);">{td_html}</tr>')
        return f'<table style="border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 12px; border-radius: 6px; overflow: hidden; border: 1px solid rgba(255,255,255,0.12);"><thead><tr>{th_html}</tr></thead><tbody>{"".join(tr_html)}</tbody></table>'

    table_pattern = r'(?:^[ \t]*\|[^\n]+\|[ \t]*\n[ \t]*\|[-: |]+\|[ \t]*(?:\n[ \t]*\|[^\n]+\|[ \t]*)*)'

    # 3. Clean up common GitHub raw HTML tags embedded in text:
    # <h3>...</h3>, <h2>...</h2>, <h4>...</h4>
    t = re.sub(r'<h([1-6])[^>]*>(.*?)</h\1>', r'\n\n<h\1 style="margin: 14px 0 6px 0; color: #60a5fa; font-size: 15px; font-weight: 700;">\2</h\1>\n\n', t, flags=re.IGNORECASE | re.DOTALL)
    
    # <details><summary>...</summary>...</details>
    def format_details(m):
        summary_m = re.search(r'<summary[^>]*>(.*?)</summary>', m.group(0), flags=re.IGNORECASE | re.DOTALL)
        summary_text = summary_m.group(1).strip() if summary_m else "Details"
        summary_text = re.sub(r'</?[^>]+>', '', summary_text).strip()
        inner = re.sub(r'<summary[^>]*>.*?</summary>', '', m.group(1), flags=re.IGNORECASE | re.DOTALL).strip()
        inner = re.sub(table_pattern, format_table, inner, flags=re.MULTILINE)
        return f'<div style="margin: 12px 0; padding: 10px 14px; background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1); border-radius: 8px;"><div style="font-weight: 600; color: #93c5fd; margin-bottom: 6px;">📂 {summary_text}</div><div style="margin-top: 6px;">{inner}</div></div>'
    t = re.sub(r'<details[^>]*>(.*?)</details>', format_details, t, flags=re.IGNORECASE | re.DOTALL)

    # Convert standalone tables
    t = re.sub(table_pattern, format_table, t, flags=re.MULTILINE)

    # <sub>...</sub>
    t = re.sub(r'<sub[^>]*>(.*?)</sub>', r'<small style="color: #94a3b8; font-size: 11px;">\1</small>', t, flags=re.IGNORECASE | re.DOTALL)

    # 4. GitHub Email Footer (--\nReply to this email...)
    def format_footer(m):
        footer_text = m.group(1).strip()
        footer_html = html.escape(footer_text)
        return f'<div style="margin-top: 20px; padding: 10px 14px; border-top: 1px dashed rgba(255,255,255,0.15); font-size: 11px; color: #64748b; line-height: 1.5;">{footer_html.replace(chr(10), "<br>")}</div>'
    t = re.sub(r'\n--\s*\n(Reply to this email directly.*)$', format_footer, t, flags=re.DOTALL)

    # 5. Markdown links [text](url)
    t = re.sub(r'\[([^\]]+)\]\((https?://[^\s\)]+)\)', r'<a href="\2" style="color: #60a5fa; text-decoration: underline; font-weight: 500;">\1</a>', t)

    # 6. Raw URLs — strip trailing closing punctuation (e.g. "[http://x.com]" or "http://x.com.") so it stays outside the link
    def _autolink(m):
        url = m.group(1)
        trail = ""
        while url and url[-1] in ")]},.;:!?":
            trail = url[-1] + trail
            url = url[:-1]
        if not url:
            return m.group(0)
        return f'<a href="{url}" style="color: #60a5fa; text-decoration: underline;">{url}</a>{trail}'
    t = re.sub(r'(?<!href=\")(?<!\">)(https?://[^\s<>\)]+)', _autolink, t)

    # 7. Code blocks ```...```
    def repl_cb(m):
        code = m.group(1).strip()
        return f'<pre style="margin: 10px 0; padding: 10px; background: rgba(0,0,0,0.35); border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; font-family: monospace; font-size: 12px; color: #f8fafc; overflow-x: auto;">{html.escape(code)}</pre>'
    t = re.sub(r'```(?:[a-zA-Z0-9_\-]+)?\n?(.*?)```', repl_cb, t, flags=re.DOTALL)

    # 8. Inline code `...`
    t = re.sub(r'`([^`\n]+)`', r'<tt style="background: rgba(255,255,255,0.08); padding: 2px 6px; border-radius: 4px; font-family: monospace; color: #38bdf8;">\1</tt>', t)

    # 9. Bold & Italic
    t = re.sub(r'\*\*([^\*\n]+)\*\*', r'<b style="color: #ffffff;">\1</b>', t)
    t = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r'<i>\1</i>', t)

    # 10. Blockquotes > ...
    t = re.sub(r'(?m)^&gt;\s*(.*?)$', r'<blockquote style="border-left: 3px solid #3b82f6; margin: 8px 0; padding: 6px 12px; background: rgba(59,130,246,0.08); border-radius: 4px; color: #cbd5e1;">\1</blockquote>', t)

    # 11. Headers # ...
    t = re.sub(r'(?m)^###\s+(.*?)$', r'<h4 style="margin: 12px 0 4px 0; color: #ffffff;">\1</h4>', t)
    t = re.sub(r'(?m)^##\s+(.*?)$', r'<h3 style="margin: 14px 0 6px 0; color: #ffffff;">\1</h3>', t)
    t = re.sub(r'(?m)^#\s+(.*?)$', r'<h2 style="margin: 16px 0 8px 0; color: #ffffff;">\1</h2>', t)

    # 12. Bullet points
    t = re.sub(r'(?m)^[-*•]\s+(.*?)$', r'<div style="margin: 3px 0; padding-left: 14px; color: #e2e8f0;">• \1</div>', t)

    # 13. Paragraphs
    paras = t.split('\n\n')
    out = []
    for p in paras:
        ps = p.strip()
        if not ps:
            continue
        if ps.startswith('<table') or ps.startswith('<div') or ps.startswith('<pre') or ps.startswith('<blockquote') or ps.startswith('<h') or ps.startswith('<small'):
            out.append(ps)
        else:
            out.append('<p style="margin: 8px 0; line-height: 1.55;">' + ps.replace('\n', '<br>') + '</p>')

    res = '\n'.join(out)
    return f'<div style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif; font-size: 13px; line-height: 1.55; color: #e2e8f0; width: 100%; word-break: break-word;">\n{res}\n</div>'

import tempfile

def run_himalaya_safe(cmd, timeout=15.0):
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

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(json.dumps({"error": "No message ID provided"}))
        sys.exit(1)

    mid = args[0]
    # Validate message ID — only allow alphanumeric, dash, underscore, dot
    # Prevents path traversal via ../ in the cache filename
    if not re.match(r'^[A-Za-z0-9._\-]+$', mid):
        print(json.dumps({"error": "Invalid message ID", "id": mid}))
        sys.exit(1)
    force = "--force" in sys.argv
    # Panel width drives HTML rendering; defaults to 660 (pre-resize behavior)
    panel_width = 660
    if "--width" in sys.argv:
        try:
            wi = sys.argv.index("--width")
            panel_width = max(320, min(1200, int(sys.argv[wi + 1])))
        except (ValueError, IndexError):
            pass
    # Use basename to strip any path components as defense-in-depth
    cache_file = os.path.join(MSG_CACHE_DIR, os.path.basename(f"{mid}_{panel_width}.json"))

    # Fast path: instant return from cache (TOCTOU-safe — open directly)
    update_envelope_cache_seen(mid)
    if not force:
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached_data = f.read()
                if cached_data.strip():
                    print(cached_data)
                    return
        except FileNotFoundError:
            pass
        except Exception:
            pass

    out, err, code = run_himalaya_safe(["himalaya", "message", "read", "--json", "--", mid])
    if code != 0 or not out:
        print(json.dumps({"error": err or "Failed to read message", "id": mid}))
        sys.exit(0)

    try:
        msg = json.loads(out)
    except Exception as e:
        print(json.dumps({"error": f"JSON parse error: {e}", "id": mid}))
        sys.exit(0)

    parts = msg.get("parts", [])
    subject_str = ""
    from_list = []
    to_list = []
    cc_list = []
    date_str = ""

    for p in parts:
        for h in p.get("headers", []):
            hname = h.get("name", "")
            if isinstance(hname, dict):
                hname = hname.get("Text") or hname.get("Name") or ""
            hname_lower = str(hname).lower()
            val = extract_header_value(h.get("value"))
            if hname_lower == "subject" and not subject_str:
                subject_str = val if isinstance(val, str) else ""
            elif hname_lower == "from" and not from_list:
                from_list = val if isinstance(val, list) else [{"name": str(val), "email": ""}]
            elif hname_lower == "to" and not to_list:
                to_list = val if isinstance(val, list) else [{"name": str(val), "email": ""}]
            elif hname_lower == "cc" and not cc_list:
                cc_list = val if isinstance(val, list) else [{"name": str(val), "email": ""}]
            elif hname_lower == "date" and not date_str:
                date_str = val if isinstance(val, str) else ""

    attachments = []
    for att in msg.get("attachments", []):
        if isinstance(att, dict):
            attachments.append({
                "filename": att.get("filename") or att.get("name") or "attachment",
                "mime": att.get("mime") or "",
            })

    body_html = ""
    # Pure clean text reader strategy (zero images, pure crisp typography):
    # 1. If clean text/markdown is available (> 30 chars), format into crisp reader cards
    #    (perfect for GitHub comments, CodeRabbit reviews, Linear, and notifications)
    # 2. Otherwise if HTML is available, format into clean typography (no images)
    html_parts = [p for p in parts if p.get("body", {}).get("Html")]
    text_parts = [p for p in parts if p.get("body", {}).get("Text")]

    if text_parts and text_parts[0]["body"].get("Text") and len(text_parts[0]["body"]["Text"].strip()) > 30:
        raw_text = text_parts[0]["body"]["Text"]
        body_html = text_to_rich_html(raw_text)
    elif html_parts:
        raw_html = html_parts[0]["body"]["Html"]
        body_html = sanitize_and_enrich_html(raw_html, panel_width=panel_width)
    elif text_parts:
        raw_text = text_parts[0]["body"]["Text"]
        body_html = text_to_rich_html(raw_text)

    if not body_html:
        body_html = "<p style='color: #888;'><i>(No message content)</i></p>"

    from_name = "Unknown"
    from_email = ""
    if from_list and isinstance(from_list, list) and len(from_list) > 0:
        first = from_list[0]
        if isinstance(first, dict):
            from_name = first.get("name") or first.get("email") or "Unknown"
            from_email = first.get("email") or ""
        elif isinstance(first, str):
            from_name = first

    words = [w for w in re.split(r'[\s_\-\.@]+', from_name) if w]
    if len(words) >= 2:
        from_initials = (words[0][0] + words[1][0]).upper()
    elif len(words) == 1 and len(words[0]) >= 2:
        from_initials = words[0][:2].upper()
    elif len(words) == 1:
        from_initials = words[0][0].upper()
    else:
        from_initials = "?"

    avatar_url = ""
    raw_html_content = html_parts[0]["body"]["Html"] if html_parts else ""

    # Check for GitHub user avatar
    is_github = any(
        "github.com" in (f.get("email", "") if isinstance(f, dict) else str(f)).lower()
        for f in from_list
    ) or "github" in from_name.lower()

    avatar_map = load_avatar_map() if is_github else {}
    map_key = from_name.strip().lower()

    if is_github and raw_html_content:
        # Scrape the actual avatar URL from the email body — these are stable
        # per-user URLs (avatars.githubusercontent.com/u/{id}) and always correct.
        github_avatars = re.findall(r'<img[^>]+src=[\"\'](https?://avatars\.githubusercontent\.com/[^\s\"\'>]+)', raw_html_content, re.IGNORECASE)
        target_avatar = None
        if github_avatars:
            target_avatar = html.unescape(github_avatars[0])
            target_avatar = re.sub(r's=\d+', 's=80', target_avatar)
            # Cache the from_name -> remote avatar URL so future emails from
            # this person that lack an embedded avatar still resolve correctly.
            if map_key and avatar_map.get(map_key) != target_avatar:
                avatar_map[map_key] = target_avatar
                save_avatar_map(avatar_map)
        elif map_key and map_key in avatar_map:
            # No avatar in this email's body, but we've seen one before for this sender.
            target_avatar = avatar_map[map_key]

        if target_avatar:
            _, local_avatar = download_image(target_avatar)
            if local_avatar and os.path.exists(local_avatar):
                real_av = os.path.realpath(local_avatar)
                real_cache = os.path.realpath(CACHE_DIR)
                if real_av.startswith(real_cache):
                    avatar_url = f"file://{real_av}"

    output = {
        "id": mid,
        "subject": subject_str or "(No Subject)",
        "from": from_list,
        "from_name": from_name,
        "from_email": from_email,
        "from_initials": from_initials,
        "avatar_url": avatar_url,
        "to": to_list,
        "cc": cc_list,
        "date": date_str,
        "date_formatted": format_date_pretty(date_str),
        "attachments": attachments,
        "has_attachments": len(attachments) > 0,
        "body_html": body_html,
        "body": body_html,
        "error": ""
    }

    try:
        tmp_file = cache_file + ".tmp"
        fd = os.open(tmp_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False)
        try:
            os.chmod(tmp_file, 0o600)
        except Exception:
            pass
        os.replace(tmp_file, cache_file)
        try:
            os.chmod(cache_file, 0o600)
        except Exception:
            pass
    except Exception:
        pass

    print(json.dumps(output))

if __name__ == "__main__":
    main()
