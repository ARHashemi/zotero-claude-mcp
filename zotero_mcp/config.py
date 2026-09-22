"""Configuration discovery for the Zotero MCP server.

Precedence for every setting: environment variable > config file > autodetection.
Secrets live only in ~/.config/zotero-mcp/config.json (mode 600).
"""

import json
import os
import re
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "zotero-mcp"
CONFIG_PATH = CONFIG_DIR / "config.json"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "zotero-mcp"

def _appdata():
    raw = os.environ.get("APPDATA")
    return Path(raw) if raw else None


# Where Zotero keeps its profile (which records the data directory), per platform.
_PROFILE_GLOBS = [
    # Linux
    Path.home() / ".zotero" / "zotero" / "*" / "prefs.js",
    Path.home() / "snap" / "zotero-snap" / "common" / ".zotero" / "zotero" / "*" / "prefs.js",
    Path.home() / ".var" / "app" / "org.zotero.Zotero" / "data" / ".zotero" / "zotero" / "*" / "prefs.js",
    # macOS
    Path.home() / "Library" / "Application Support" / "Zotero" / "Profiles" / "*" / "prefs.js",
    # Windows (including WSL access to the Windows profile)
    *([_appdata() / "Zotero" / "Zotero" / "Profiles" / "*" / "prefs.js"] if _appdata() else []),
    # data directory that carries its own profile
    Path.home() / "Zotero" / "profile" / "prefs.js",
]


def _data_dir_candidates():
    """Default Zotero data directories, in the order Zotero itself would pick them."""
    found = [
        Path.home() / "Zotero",
        Path.home() / "Documents" / "Zotero",
        Path.home() / "snap" / "zotero-snap" / "common" / "Zotero",
        Path.home() / ".var" / "app" / "org.zotero.Zotero" / "data" / "Zotero",
    ]
    appdata = _appdata()
    if appdata:
        found.append(appdata / "Zotero" / "Zotero")
    return found


def _read_file(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise SystemExit(f"zotero-mcp: {path} is not valid JSON ({exc})")


def _prefs():
    """Parse user_pref() lines out of Zotero's prefs.js, if we can find it."""
    for pattern in _PROFILE_GLOBS:
        for prefs_file in sorted(Path(pattern.anchor).glob(str(pattern.relative_to(pattern.anchor)))):
            try:
                text = prefs_file.read_text(errors="replace")
            except OSError:
                continue
            found = dict(re.findall(r'user_pref\("([^"]+)",\s*"([^"]*)"\)', text))
            if found:
                return found
    return {}


class Config:
    def __init__(self):
        self._file = _read_file(CONFIG_PATH)
        self._prefs = None

    def _pref(self, name):
        if self._prefs is None:
            self._prefs = _prefs()
        return self._prefs.get(name)

    def _get(self, env, key, default=None):
        value = os.environ.get(env) or self._file.get(key) or default
        return value.strip() if isinstance(value, str) else value

    # -- web API ---------------------------------------------------------
    @property
    def api_key(self):
        return self._get("ZOTERO_API_KEY", "api_key")

    @property
    def library_id(self):
        value = self._get("ZOTERO_USER_ID", "user_id")
        if not value:
            value = self.local_user_id()
        return str(value) if value else None

    @property
    def library_type(self):
        return self._get("ZOTERO_LIBRARY_TYPE", "library_type", "user")

    # -- local library ---------------------------------------------------
    @property
    def data_dir(self):
        """Explicit setting, else Zotero's own preference, else the first default that exists."""
        value = self._get("ZOTERO_DATA_DIR", "data_dir") or self._pref("extensions.zotero.dataDir")
        if value:
            return Path(value).expanduser()
        for candidate in _data_dir_candidates():
            if (candidate / "zotero.sqlite").exists():
                return candidate
        return Path.home() / "Zotero"

    def describe_discovery(self):
        """Human-readable account of where the data directory came from — used by `status`."""
        if os.environ.get("ZOTERO_DATA_DIR"):
            return "ZOTERO_DATA_DIR environment variable"
        if self._file.get("data_dir"):
            return f"data_dir in {CONFIG_PATH}"
        if self._pref("extensions.zotero.dataDir"):
            return "Zotero's own preferences (prefs.js)"
        for candidate in _data_dir_candidates():
            if (candidate / "zotero.sqlite").exists():
                return "autodetected default location"
        return "fallback guess — Zotero was not found"

    @property
    def base_attachment_path(self):
        value = self._get("ZOTERO_BASE_ATTACHMENT_PATH", "base_attachment_path") or self._pref(
            "extensions.zotero.baseAttachmentPath"
        )
        return Path(value).expanduser() if value else None

    @property
    def sqlite_path(self):
        return self.data_dir / "zotero.sqlite"

    @property
    def fulltext_path(self):
        return self.data_dir / "fulltext.sqlite"

    @property
    def storage_dir(self):
        return self.data_dir / "storage"

    @property
    def default_source(self):
        """'local' (SQLite/local API) or 'web' (api.zotero.org)."""
        return self._get("ZOTERO_DEFAULT_SOURCE", "default_source", "local")

    def local_user_id(self):
        """Zotero stores the account's numeric userID in the local database."""
        from . import localdb

        try:
            return localdb.account_user_id(self)
        except Exception:
            return None

    def save(self, **updates):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = _read_file(CONFIG_PATH)
        data.update({k: v for k, v in updates.items() if v is not None})
        CONFIG_PATH.write_text(json.dumps(data, indent=2) + "\n")
        CONFIG_PATH.chmod(0o600)
        self._file = data
        return CONFIG_PATH
