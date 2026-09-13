"""Resolve direct-IMAP credentials from Himalaya without requiring plaintext secrets."""

import json
import os
import shlex
import stat

import tomllib

from secure_io import run_bounded

SECRET_OUTPUT_LIMIT = 64 * 1024
ENVELOPE_OUTPUT_LIMIT = 1024 * 1024


def _command_argv(command):
    if isinstance(command, list) and command and all(isinstance(item, str) for item in command):
        return command
    if isinstance(command, str) and command.strip():
        # Himalaya accepts command strings. Split them without invoking a shell;
        # users needing shell features can explicitly configure ["sh", "-c", ...].
        return shlex.split(command)
    return None


def resolve_secret(spec):
    if not isinstance(spec, dict):
        return None
    raw = spec.get("raw")
    if isinstance(raw, str) and raw:
        return raw
    argv = _command_argv(spec.get("command"))
    if not argv:
        return None
    try:
        stdout, _, code = run_bounded(argv, timeout=8, max_output_bytes=SECRET_OUTPUT_LIMIT)
    except OSError:
        return None
    return stdout if code == 0 and stdout else None


def _gmail_address(account):
    configured = account.get("email")
    if isinstance(configured, str) and configured:
        return configured
    try:
        for page in range(1, 6):
            stdout, _, code = run_bounded(
                ["himalaya", "envelope", "list", "--json", "-p", str(page), "-s", "10"],
                timeout=8,
                max_output_bytes=ENVELOPE_OUTPUT_LIMIT,
            )
            if code != 0 or not stdout:
                break
            payload = json.loads(stdout)
            envelopes = payload.get("envelopes", []) if isinstance(payload, dict) else []
            for envelope in envelopes:
                for field in ("to", "cc", "bcc"):
                    for recipient in envelope.get(field, []):
                        email = recipient.get("email") if isinstance(recipient, dict) else None
                        if isinstance(email, str) and email.lower().endswith("@gmail.com"):
                            return email
    except (OSError, ValueError, TypeError):
        pass
    return None


def _read_config(config_path):
    """Read a bounded user-owned config, allowing intentional dotfile symlinks."""
    try:
        with open(config_path, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 1024 * 1024:
                return None
            data = stream.read(1024 * 1024 + 1)
            return data if len(data) <= 1024 * 1024 else None
    except OSError:
        return None


def load_imap_credentials(config_path):
    """Return (server, username, secret, auth_type), or None when unavailable."""
    raw_config = _read_config(config_path)
    if raw_config is None:
        return None
    try:
        config = tomllib.loads(raw_config.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None

    accounts = config.get("accounts", {})
    if not isinstance(accounts, dict):
        return None

    for account in accounts.values():
        try:
            imap = account.get("imap", {})
            plain = imap.get("sasl", {}).get("plain", {})
            server = imap.get("server")
            username = plain.get("username")
            secret = resolve_secret(plain.get("password", {}))
            if all(isinstance(value, str) and value for value in (server, username, secret)):
                return server, username, secret, "plain"
        except (AttributeError, TypeError):
            continue

    for account in accounts.values():
        try:
            token_spec = account.get("gmail", {}).get("auth", {}).get("token", {})
            token = resolve_secret(token_spec)
            if not token:
                continue
            email = _gmail_address(account)
            if email:
                return "imap.gmail.com:993", email, token, "xoauth2"
        except (AttributeError, TypeError):
            continue

    return None
