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
os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
os.makedirs(MSG_CACHE_DIR, mode=0o700, exist_ok=True)

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

        # Follow redirects safely (recursive call does its own validate+pin+connect)
        if resp.status in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location", "")
            conn.close()
            if location:
                redirect_url = urllib.parse.urljoin(url, location)
                return download_image(redirect_url, _depth + 1)
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
    def __init__(self, img_map=None, panel_width=480):
        super().__init__()
        self.img_map = img_map or {}
        self.panel_width = panel_width
        self.out = []
        self.in_skip = False
        self.skip_tag = None
        self.current_link = None
        self.row_cells = []
        self.in_cell = False
        self.cell_buf = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        tag = tag.lower()
        if tag in ["style", "script", "head", "title"]:
            self.in_skip = True
            self.skip_tag = tag
            return
        if tag in ["meta", "link"]:
            # Self-closing void elements — do NOT set in_skip!
            return
        if self.in_skip:
            return

        if tag == "img":
            src = attrs_dict.get("src", "").strip()
            local = self.img_map.get(src)
            if local and os.path.exists(local):
                real_local = os.path.realpath(local)
                real_cache = os.path.realpath(CACHE_DIR)
                if real_local.startswith(real_cache):
                    img_tag = get_scaled_img_tag(real_local, self.panel_width)
                    if img_tag:
                        if self.in_cell:
                            self.cell_buf.append(img_tag)
                        else:
                            self.out.append(img_tag)
        elif tag == "a":
            href = attrs_dict.get("href", "").strip()
            # Only allow safe schemes (case-insensitive — HtTp:// is valid HTML)
            href_lower = href.lower()
            if href_lower.startswith("http://") or href_lower.startswith("https://") or href_lower.startswith("mailto:"):
                self.current_link = href
                link_tag = f'<a href="{html.escape(href, quote=True)}" style="color: #60a5fa; text-decoration: underline; font-weight: 500;">'
            else:
                self.current_link = None
                link_tag = "<a>"
            if self.in_cell:
                self.cell_buf.append(link_tag)
            else:
                self.out.append(link_tag)
        elif tag in ["b", "strong"]:
            target = self.cell_buf if self.in_cell else self.out
            target.append("<b>")
        elif tag in ["i", "em"]:
            target = self.cell_buf if self.in_cell else self.out
            target.append("<i>")
        elif tag in ["code", "tt"]:
            target = self.cell_buf if self.in_cell else self.out
            target.append('<tt style="background: rgba(255,255,255,0.08); padding: 2px 4px; border-radius: 3px; font-family: monospace;">')
        elif tag == "pre":
            target = self.cell_buf if self.in_cell else self.out
            target.append('<pre style="margin: 8px 0; padding: 10px; background: rgba(0,0,0,0.3); border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; font-family: monospace;">')
        elif tag == "blockquote":
            target = self.cell_buf if self.in_cell else self.out
            target.append('<blockquote style="border-left: 3px solid #3b82f6; margin: 8px 0; padding: 6px 12px; background: rgba(59, 130, 246, 0.08); border-radius: 4px; color: #cbd5e1;">')
        elif tag in ["h1", "h2", "h3", "h4", "h5", "h6"]:
            target = self.cell_buf if self.in_cell else self.out
            target.append(f'<{tag} style="margin: 12px 0 6px 0; font-weight: bold; color: #ffffff;">')
        elif tag == "p":
            target = self.cell_buf if self.in_cell else self.out
            target.append('<p style="margin: 8px 0; line-height: 1.5;">')
        elif tag == "div":
            target = self.cell_buf if self.in_cell else self.out
            target.append('<div style="margin: 4px 0; line-height: 1.45;">')
        elif tag == "br":
            target = self.cell_buf if self.in_cell else self.out
            target.append("<br>")
        elif tag == "hr":
            target = self.cell_buf if self.in_cell else self.out
            target.append('<hr style="border: 0; border-top: 1px solid rgba(255,255,255,0.12); margin: 12px 0;">')
        elif tag == "li":
            target = self.cell_buf if self.in_cell else self.out
            target.append('<li style="margin: 4px 0;">')
        elif tag in ["ul", "ol"]:
            target = self.cell_buf if self.in_cell else self.out
            target.append(f'<{tag} style="margin: 8px 0; padding-left: 20px;">')
        elif tag in ["td", "th"]:
            self.in_cell = True
            self.cell_buf = []
        elif tag == "tr":
            self.row_cells = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ["style", "script", "head", "title"]:
            if self.skip_tag == tag or not self.skip_tag:
                self.in_skip = False
                self.skip_tag = None
            return
        if self.in_skip:
            return

        if tag == "a":
            target = self.cell_buf if self.in_cell else self.out
            target.append("</a>")
            self.current_link = None
        elif tag in ["b", "strong"]:
            target = self.cell_buf if self.in_cell else self.out
            target.append("</b>")
        elif tag in ["i", "em"]:
            target = self.cell_buf if self.in_cell else self.out
            target.append("</i>")
        elif tag in ["code", "tt"]:
            target = self.cell_buf if self.in_cell else self.out
            target.append("</tt>")
        elif tag == "pre":
            target = self.cell_buf if self.in_cell else self.out
            target.append("</pre>")
        elif tag == "blockquote":
            target = self.cell_buf if self.in_cell else self.out
            target.append("</blockquote>")
        elif tag in ["h1", "h2", "h3", "h4", "h5", "h6"]:
            target = self.cell_buf if self.in_cell else self.out
            target.append(f"</{tag}>")
        elif tag == "p":
            target = self.cell_buf if self.in_cell else self.out
            target.append("</p>")
        elif tag == "div":
            target = self.cell_buf if self.in_cell else self.out
            target.append("</div>")
        elif tag == "li":
            target = self.cell_buf if self.in_cell else self.out
            target.append("</li>")
        elif tag in ["ul", "ol"]:
            target = self.cell_buf if self.in_cell else self.out
            target.append(f"</{tag}>")
        elif tag in ["td", "th"]:
            self.in_cell = False
            cell_text = "".join(self.cell_buf).strip()
            if cell_text:
                self.row_cells.append(cell_text)
            self.cell_buf = []
        elif tag == "tr":
            if len(self.row_cells) == 1:
                self.out.append(f'<div style="margin: 4px 0;">{self.row_cells[0]}</div>')
            elif len(self.row_cells) == 2:
                c1, c2 = self.row_cells[0], self.row_cells[1]
                self.out.append(f'<table width="100%" cellpadding="4" cellspacing="0" style="margin: 4px 0;"><tr><td style="color: #94a3b8; padding: 4px 8px; vertical-align: top;">{c1}</td><td style="text-align: right; padding: 4px 8px; vertical-align: top;">{c2}</td></tr></table>')
            elif len(self.row_cells) > 2:
                if all("<img" in c and len(re.sub(r"<[^>]+>", "", c).strip()) == 0 for c in self.row_cells):
                    pass
                else:
                    cells_html = "".join([f'<td style="padding: 4px 8px; vertical-align: top; border-bottom: 1px solid rgba(255,255,255,0.06);">{c}</td>' for c in self.row_cells])
                    self.out.append(f'<table width="100%" cellpadding="2" cellspacing="0" style="margin: 4px 0; border: 1px solid rgba(255,255,255,0.08); border-radius: 4px;"><tr>{cells_html}</tr></table>')
            self.row_cells = []

    def handle_data(self, data):
        if self.in_skip:
            return
        t = data
        if not t.strip() and "\n" in t:
            return
        # ALWAYS escape text data to prevent entity-encoded tag bypass
        t_clean = html.escape(t, quote=True)
        if self.in_cell:
            self.cell_buf.append(t_clean)
        else:
            if t.strip():
                self.out.append(t_clean)

    def get_html(self):
        res = "".join(self.out)
        res = re.sub(r"<h[1-6][^>]*>\s*<\/h[1-6]>", "", res)
        res = re.sub(r"<a[^>]*>\s*<\/a>", "", res)
        res = re.sub(r"<p[^>]*>\s*<\/p>", "", res)
        res = re.sub(r"<div[^>]*>\s*<\/div>", "", res)
        res = re.sub(r"(?:<br\s*\/?>\s*){3,}", "<br><br>", res)
        return res

def sanitize_and_enrich_html(raw_html, panel_width=360):
    """Clean HTML for Qt Quick RichText, downloading images and adapting styling."""
    if not raw_html:
        return ""

    img_urls = re.findall(r"<img[^>]+src=[\"\x27](https?://[^\s\"\x27>]+)", raw_html, re.IGNORECASE)[:10]
    img_map = {}
    if img_urls:
        with ThreadPoolExecutor(max_workers=4) as ex:
            img_map = dict(ex.map(download_image, set(img_urls)))

    builder = CleanEmailBuilder(img_map=img_map, panel_width=panel_width)
    builder.feed(raw_html)
    return builder.get_html()

def text_to_rich_html(raw_text):
    """Convert plain text email into clean rich HTML with clickable links and markdown formatting."""
    if not raw_text:
        return ""
    t = html.escape(raw_text, quote=True)
    t = re.sub(r'(https?://[^\s<]+)', r'<a href="\1">\1</a>', t)
    t = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t)
    t = re.sub(r'(?<!\*)\*([^\*\n]+)\*(?!\*)', r'<i>\1</i>', t)
    t = re.sub(r'`([^`]+)`', r'<tt>\1</tt>', t)
    t = re.sub(r'(?m)^&gt;\s*(.*?)$', r'<blockquote style="border-left: 2px solid #666; margin: 4px 0; padding-left: 8px; color: #aaa;">\1</blockquote>', t)
    t = re.sub(r'(?m)^[-*•]\s+(.*?)$', r'• \1', t)
    t = re.sub(r'(?m)^#{1,3}\s+(.*?)$', r'<b>\1</b>', t)
    paragraphs = t.split('\n\n')
    formatted = ['<p style="margin: 0 0 10px 0; line-height: 1.4;">' + p.replace('\n', '<br>') + '</p>' for p in paragraphs if p.strip()]
    return ''.join(formatted)

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No message ID provided"}))
        sys.exit(1)

    mid = sys.argv[1]
    # Validate message ID — only allow alphanumeric, dash, underscore, dot
    # Prevents path traversal via ../ in the cache filename
    if not re.match(r'^[A-Za-z0-9._\-]+$', mid):
        print(json.dumps({"error": "Invalid message ID", "id": mid}))
        sys.exit(1)
    force = "--force" in sys.argv
    # Use basename to strip any path components as defense-in-depth
    cache_file = os.path.join(MSG_CACHE_DIR, os.path.basename(f"{mid}.json"))

    # Fast path: instant return from cache (TOCTOU-safe — open directly)
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

    out, err, code = run(["himalaya", "message", "read", "--json", mid])
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
    raw_text = ""

    # Prefer HTML part for rich view
    html_parts = [p for p in parts if p.get("body", {}).get("Html")]
    text_parts = [p for p in parts if p.get("body", {}).get("Text")]

    if html_parts:
        raw_html = html_parts[0]["body"]["Html"]
        body_html = sanitize_and_enrich_html(raw_html)
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

    output = {
        "id": mid,
        "subject": subject_str or "(No Subject)",
        "from": from_list,
        "from_name": from_name,
        "from_email": from_email,
        "from_initials": from_initials,
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
