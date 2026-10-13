"""Paths, persisted settings, saved sessions and query history."""
import base64
import ctypes
import json
import os
import sys
import time
import uuid

IS_WINDOWS = sys.platform == "win32"


def data_dir():
    """Per-user folder: %APPDATA% on Windows, ~/Library/Application Support on macOS, ~/.config on Linux."""
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    path = os.path.join(base, "QuantumSQL")
    os.makedirs(path, exist_ok=True)
    if not IS_WINDOWS:  # sessions, history and logs hold SQL and connection details: owner only
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
    return path


def resource_path(name):
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    if not IS_WINDOWS:
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# ── Password protection ────────────────────────────────────────────────────
# Windows: DPAPI (tied to the Windows user). macOS / Linux: Fernet encryption with a master key kept in the
# OS keychain (Keychain / Secret Service via `keyring`), or in a 0600 key file when no keychain is available.
if IS_WINDOWS:
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _dpapi(data, protect):
        buf = ctypes.create_string_buffer(data, len(data))
        blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = _Blob()
        fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
        if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
            raise OSError("DPAPI call failed")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)

_fernet = None


def _get_fernet():
    global _fernet
    if _fernet is None:
        from cryptography.fernet import Fernet
        key = None
        try:
            import keyring
            key = keyring.get_password("QuantumSQL", "master-key")
            if not key:
                key = Fernet.generate_key().decode()
                keyring.set_password("QuantumSQL", "master-key", key)
        except Exception:  # no keychain available (headless Linux, missing backend)
            path = os.path.join(data_dir(), ".master-key")
            if os.path.exists(path):
                with open(path, "r", encoding="ascii") as f:
                    key = f.read().strip()
            else:
                key = Fernet.generate_key().decode()
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="ascii") as f:
                    f.write(key)
        _fernet = Fernet(key.encode())
    return _fernet


def protect_secret(text):
    if not text:
        return ""
    try:
        if IS_WINDOWS:
            return "dpapi:" + base64.b64encode(_dpapi(text.encode("utf-8"), True)).decode()
        return "fernet:" + _get_fernet().encrypt(text.encode("utf-8")).decode()
    except Exception:
        return "b64:" + base64.b64encode(text.encode("utf-8")).decode()


def unprotect_secret(value):
    if not value:
        return ""
    try:
        if value.startswith("dpapi:"):
            return _dpapi(base64.b64decode(value[6:]), False).decode("utf-8")
        if value.startswith("fernet:"):
            return _get_fernet().decrypt(value[7:].encode()).decode("utf-8")
        if value.startswith("b64:"):
            return base64.b64decode(value[4:]).decode("utf-8")
    except Exception:
        return ""
    return value


# ── Settings ───────────────────────────────────────────────────────────────
class Settings:
    def __init__(self):
        self.path = os.path.join(data_dir(), "settings.json")
        self.data = _read_json(self.path, {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        try:
            _write_json(self.path, self.data)
        except Exception:
            pass


settings = Settings()


# ── Sessions ───────────────────────────────────────────────────────────────
SESSION_DEFAULTS = {
    "name": "New session", "host": "127.0.0.1", "port": 3306, "user": "root",
    "password": "", "database": "", "color": "", "timeout": 10, "env": "development",
    "ssh_enabled": False, "ssh_host": "", "ssh_port": 22, "ssh_user": "", "ssh_password": "", "ssh_key_file": "",
    "ssh_key_pass": "",
}
SECRET_FIELDS = ("password", "ssh_password", "ssh_key_pass")


def new_session(**overrides):
    s = dict(SESSION_DEFAULTS)
    s.update(overrides)
    s["id"] = uuid.uuid4().hex
    return s


class SessionStore:
    def __init__(self):
        self.path = os.path.join(data_dir(), "sessions.json")

    def load(self):
        raw = _read_json(self.path, None)
        if raw is None:
            raw = self._migrate_legacy()
        sessions = []
        for item in raw:
            s = dict(SESSION_DEFAULTS)
            s.update(item)
            for k in SECRET_FIELDS:
                s[k] = unprotect_secret(item.get(k, ""))
            s.setdefault("id", uuid.uuid4().hex)
            sessions.append(s)
        return sessions

    def save(self, sessions):
        out = []
        for s in sessions:
            item = dict(s)
            for k in SECRET_FIELDS:
                item[k] = protect_secret(s.get(k, ""))
            out.append(item)
        _write_json(self.path, out)

    def _migrate_legacy(self):
        """Import connections saved by the old Tkinter version (v1)."""
        candidates = [
            os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "saved_connections.json"),
            os.path.join(os.getcwd(), "saved_connections.json"),
        ]
        for path in candidates:
            old = _read_json(path, None)
            if isinstance(old, list) and old:
                return [new_session(name=c.get("name", "Server"), host=c.get("host", "localhost"),
                                    port=int(c.get("port") or 3306), user=c.get("user", "root"),
                                    password=c.get("password", "")) for c in old]
        return [new_session(name="Localhost (XAMPP)", host="127.0.0.1", user="root")]


# ── Query history ──────────────────────────────────────────────────────────
class History:
    LIMIT = 1000

    def __init__(self):
        self.path = os.path.join(data_dir(), "history.json")
        self.items = _read_json(self.path, [])

    def add(self, sql, session_name, database, duration_ms, ok):
        self.items.insert(0, {"sql": sql, "session": session_name, "db": database or "",
                              "ms": round(duration_ms, 1), "ok": ok,
                              "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        del self.items[self.LIMIT:]
        try:
            _write_json(self.path, self.items)
        except Exception:
            pass

    def clear(self):
        self.items = []
        try:
            _write_json(self.path, self.items)
        except Exception:
            pass


history = History()
