"""Small security boundary for helper processes and private on-disk state."""

import json
import os
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from pathlib import Path

DEFAULT_MAX_READ_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_OUTPUT_BYTES = 4 * 1024 * 1024


def ensure_private_dir(path):
    """Create a user-owned directory and reject symlink/non-directory targets."""
    path = os.path.abspath(os.fspath(path))
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        os.makedirs(path, mode=0o700, exist_ok=True)
        info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise OSError(f"Unsafe private directory: {path}")
    os.chmod(path, 0o700)
    return path


def _open_regular_nofollow(path):
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(os.fspath(path), flags)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        os.close(fd)
        raise OSError(f"Unsafe private file: {path}")
    os.fchmod(fd, 0o600)
    return fd, info


def harden_private_file(path):
    """Set an existing user-owned regular file to mode 0600 without following links."""
    try:
        fd, _ = _open_regular_nofollow(path)
        os.close(fd)
        return True
    except OSError:
        return False


def read_bytes(path, default=None, max_bytes=DEFAULT_MAX_READ_BYTES):
    """Read a bounded, user-owned regular file without following symlinks."""
    try:
        fd, info = _open_regular_nofollow(path)
        try:
            if info.st_size > max_bytes:
                raise OSError(f"Private file exceeds {max_bytes} byte limit: {path}")
            chunks = []
            remaining = max_bytes + 1
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > max_bytes:
                raise OSError(f"Private file exceeds {max_bytes} byte limit: {path}")
            return data
        finally:
            os.close(fd)
    except (OSError, ValueError):
        return default


def read_text(path, default=None, max_bytes=DEFAULT_MAX_READ_BYTES, encoding="utf-8"):
    data = read_bytes(path, default=None, max_bytes=max_bytes)
    if data is None:
        return default
    try:
        return data.decode(encoding)
    except UnicodeDecodeError:
        return default


def read_json(path, default=None, max_bytes=DEFAULT_MAX_READ_BYTES):
    text = read_text(path, default=None, max_bytes=max_bytes)
    if text is None:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def safe_mtime(path):
    try:
        info = os.lstat(os.fspath(path))
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            return None
        return info.st_mtime
    except OSError:
        return None


def atomic_write_bytes(path, data, mode=0o600):
    """Atomically replace a private file using an unpredictable same-dir temp file."""
    path = os.path.abspath(os.fspath(path))
    parent = ensure_private_dir(os.path.dirname(path))
    fd, temporary = tempfile.mkstemp(prefix=f".{Path(path).name}.", dir=parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb", closefd=True) as stream:
            fd = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def atomic_write_text(path, text, mode=0o600, encoding="utf-8"):
    atomic_write_bytes(path, text.encode(encoding), mode=mode)


def atomic_write_json(path, value, mode=0o600, **dump_kwargs):
    text = json.dumps(value, **dump_kwargs)
    atomic_write_text(path, text, mode=mode)


def _kill_process_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def run_bounded(cmd, timeout=15.0, max_output_bytes=DEFAULT_MAX_OUTPUT_BYTES):
    """Run a command with a hard timeout and a combined stdout/stderr byte cap."""
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    previous_handlers = {}

    def forward_termination(signum, _frame):
        _kill_process_group(proc)
        raise SystemExit(128 + signum)

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, forward_termination)
    except ValueError:
        # Signal handlers can only be installed by Python's main thread.
        previous_handlers = {}

    selector = selectors.DefaultSelector()
    streams = {proc.stdout: bytearray(), proc.stderr: bytearray()}
    for stream in streams:
        selector.register(stream, selectors.EVENT_READ)

    failure = None
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = f"Request timed out after {timeout:g}s"
                break
            events = selector.select(min(0.1, remaining))
            for key, _ in events:
                stream = key.fileobj
                chunk = os.read(stream.fileno(), 65536)
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                total = sum(len(buffer) for buffer in streams.values())
                if total + len(chunk) > max_output_bytes:
                    failure = f"Helper output limit exceeded ({max_output_bytes} bytes)"
                    break
                streams[stream].extend(chunk)
            if failure:
                break

        if not failure:
            try:
                proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                failure = f"Request timed out after {timeout:g}s"

        if failure:
            _kill_process_group(proc)
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            return "", failure, 1

        stdout = bytes(streams.get(proc.stdout, b"")).decode("utf-8", "replace").strip()
        stderr = bytes(streams.get(proc.stderr, b"")).decode("utf-8", "replace").strip()
        return stdout, stderr, proc.returncode
    finally:
        selector.close()
        for stream in streams:
            if not stream.closed:
                stream.close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
