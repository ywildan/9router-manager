#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""9router-manager - manage 9router custom endpoints, API keys and models.

A single-file, dependency-free manager for the 9router gateway. It talks to the
9router admin REST API instead of editing the SQLite database, so it is safe to
run while the server is live.

Talks to: http://localhost:20128 (configurable)

Auth, in order of preference:
  1. --token / NINEROUTER_TOKEN
  2. derived CLI token from <data-dir>/machine-id + <data-dir>/auth/cli-secret
     (same value the official CLI uses, header x-9r-cli-token)
  3. NINEROUTER_PEER_TOKEN (header x-9r-peer-token)
  4. dashboard password -> POST /api/auth/login -> session cookie

Runs on Linux, macOS and Windows with Python 3.8+ and only the standard library.
"""

import argparse
import hashlib
import json
import os
import platform as _platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

VERSION = "0.1.0"
PROGRAM = "9router-manager"
DEFAULT_PORT = 20128
DEFAULT_HOST = "127.0.0.1"
PREFIX_SALT = "9r-cli-auth"
BACKUP_KEEP = 20


# ---------------------------------------------------------------------------
# terminal output
# ---------------------------------------------------------------------------

class Ansi:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"


_USE_COLOR = True
_JSON_MODE = False


def _enable_windows_ansi() -> None:
    """Turn on VT processing so ANSI colors work in cmd.exe/PowerShell."""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        for handle in (-11, -12):  # stdout, stderr
            h = kernel32.GetStdHandle(handle)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                kernel32.SetConsoleMode(h, mode.value | 0x0004)
    except Exception:
        pass


def _force_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def color_supported() -> bool:
    if os.environ.get("NO_COLOR") or os.environ.get("NINEROUTER_MANAGER_NO_COLOR"):
        return False
    if os.environ.get("NINEROUTER_MANAGER_FORCE_COLOR"):
        return True
    if os.name == "nt" and not os.environ.get("WT_SESSION") and not os.environ.get("TERM"):
        return True  # we enabled VT processing above
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def set_color_enabled(enabled: bool) -> None:
    global _USE_COLOR
    _USE_COLOR = enabled


def set_json_mode(enabled: bool) -> None:
    global _JSON_MODE
    _JSON_MODE = enabled


def in_json_mode() -> bool:
    return _JSON_MODE


def paint(text: str, code: str) -> str:
    if not _USE_COLOR or not code:
        return text
    return code + text + Ansi.RESET


def _out(text: str, stream) -> None:
    try:
        stream.write(text + "\n")
        stream.flush()
    except Exception:
        pass


def say(text: str = "") -> None:
    """Normal output. In --json mode it goes to stderr to keep stdout clean."""
    _out(text, sys.stderr if _JSON_MODE else sys.stdout)


def ok(text: str) -> None:
    _out(paint("[ok] ", Ansi.GREEN) + text, sys.stderr if _JSON_MODE else sys.stdout)


def warn(text: str) -> None:
    _out(paint("[!] ", Ansi.YELLOW) + text, sys.stderr)


def err(text: str) -> None:
    _out(paint("[x] ", Ansi.RED) + text, sys.stderr)


def note(text: str) -> None:
    _out(paint(text, Ansi.DIM), sys.stderr if _JSON_MODE else sys.stdout)


def rule(width: int = 66) -> None:
    say(paint("-" * width, Ansi.DIM))


def header(title: str, subtitle: str = "") -> None:
    say("")
    say(paint(title, Ansi.BOLD + Ansi.CYAN))
    if subtitle:
        say(paint(subtitle, Ansi.DIM))
    rule()


def print_json(payload: Any) -> None:
    _out(json.dumps(payload, indent=2, ensure_ascii=False), sys.stdout)


def ask(prompt: str, default: str = "") -> str:
    suffix = " [%s]" % default if default else ""
    try:
        value = input("%s%s: " % (prompt, suffix)).strip()
    except EOFError:
        return default
    return value or default


def ask_secret(prompt: str) -> str:
    """Read a secret without echoing it.

    getpass() reads from /dev/tty (or the console handle) and bypasses Python's
    buffered stdin, which breaks when the menu is driven by a pipe. When stdin is
    not a terminal we read a normal line instead, so piped input stays predictable.
    """
    try:
        if sys.stdin is not None and not sys.stdin.isatty():
            return ask(prompt)
    except Exception:
        pass
    try:
        import getpass

        return getpass.getpass("%s: " % prompt).strip()
    except Exception:
        try:
            return input("%s: " % prompt).strip()
        except EOFError:
            return ""


def confirm(prompt: str, assume_yes: bool = False, default: bool = False) -> bool:
    """Ask a yes/no question.

    assume_yes skips the question (used by --yes). default controls what a bare
    Enter means, so a prompt can be an opt-in (y/N) or an opt-out (Y/n) question.
    """
    if assume_yes:
        return True
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        answer = input("%s %s: " % (prompt, suffix)).strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in ("y", "ya", "yes")


def pause(prompt: str = "Tekan Enter buat balik ke menu...") -> None:
    try:
        input(prompt)
    except EOFError:
        pass


def clipboard_copy(text: str) -> bool:
    """Best-effort clipboard copy. Never raises."""
    candidates: List[List[str]] = []
    if os.name == "nt":
        candidates.append(["clip"])
    elif sys.platform == "darwin":
        candidates.append(["pbcopy"])
    else:
        candidates.append(["wl-copy"])
        candidates.append(["xclip", "-selection", "clipboard"])
        candidates.append(["xsel", "--clipboard", "--input"])
    for cmd in candidates:
        if not shutil.which(cmd[0]):
            continue
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            proc.communicate(text.encode("utf-8"))
            if proc.returncode == 0:
                return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# paths (pure functions so the Windows/macOS branches are testable anywhere)
# ---------------------------------------------------------------------------

def resolve_data_dir(os_name: str, env: Dict[str, str], home: Path) -> Path:
    """Mirror 9router's own logic: DATA_DIR, else %APPDATA%/9router, else ~/.9router."""
    explicit = env.get("DATA_DIR") or env.get("NINEROUTER_DATA_DIR")
    if explicit:
        return Path(explicit).expanduser()
    if os_name == "nt":
        appdata = env.get("APPDATA") or env.get("LOCALAPPDATA")
        base = Path(appdata).expanduser() if appdata else home
        return base / "9router"
    return home / ".9router"


class PlatformInfo:
    """Where 9router keeps its state, and where this manager keeps its own."""

    def __init__(self, os_name: str, sys_platform: str, home: Path, env: Dict[str, str]):
        self.os_name = os_name
        self.sys_platform = sys_platform
        self.home = home
        self.env = env
        self.data_dir = resolve_data_dir(os_name, env, home)
        self.db_path = self.data_dir / "db" / "data.sqlite"
        self.machine_id_path = self.data_dir / "machine-id"
        self.cli_secret_path = self.data_dir / "auth" / "cli-secret"
        self.jwt_secret_path = self.data_dir / "jwt-secret"
        mgr = env.get("NINEROUTER_MANAGER_HOME")
        self.manager_home = Path(mgr).expanduser() if mgr else home / ".9router-manager"
        self.backups_dir = self.manager_home / "backups"
        self.state_path = self.manager_home / "state.json"

    @classmethod
    def detect(cls, env: Optional[Dict[str, str]] = None) -> "PlatformInfo":
        env = dict(os.environ if env is None else env)
        home = Path(env.get("HOME") or env.get("USERPROFILE") or str(Path.home()))
        return cls(os.name, sys.platform, home, env)

    def ensure_manager_dirs(self) -> None:
        try:
            self.backups_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def describe(self) -> List[Tuple[str, str]]:
        return [
            ("OS", "%s (%s)" % (self.os_name, self.sys_platform)),
            ("Python", _platform.python_version()),
            ("9router data dir", str(self.data_dir)),
            ("9router database", str(self.db_path) + ("" if self.db_path.exists() else "  (not found)")),
            ("machine-id", _file_state(self.machine_id_path)),
            ("cli-secret", _file_state(self.cli_secret_path)),
            ("manager home", str(self.manager_home)),
        ]


def _file_state(path: Path) -> str:
    if not path.exists():
        return "%s  (missing)" % path
    try:
        return "%s  (%d bytes)" % (path, path.stat().st_size)
    except Exception:
        return str(path)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(str(tmp), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    if os.name == "posix":
        try:
            os.chmod(str(tmp), mode)
        except Exception:
            pass
    os.replace(str(tmp), str(path))


def mask_key(key: Optional[str]) -> str:
    if not key:
        return "(none)"
    if len(key) <= 8:
        return "*" * len(key)
    return "%s...%s" % (key[:4], key[-4:])


def validate_prefix(value: str) -> Optional[str]:
    if not value:
        return "prefix wajib diisi"
    if value.lower() != value or not value.replace("-", "").replace("_", "").isalnum():
        return "prefix harus huruf kecil/angka (boleh - dan _), contoh: mygw"
    return None


def validate_base_url(value: str) -> Optional[str]:
    if not value:
        return "base URL wajib diisi"
    if not (value.startswith("http://") or value.startswith("https://")):
        return "base URL harus diawali http:// atau https://"
    return None


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------

def derive_cli_token(plat: PlatformInfo) -> Optional[str]:
    """The token the official CLI uses: sha256(machineId + '9r-cli-auth' + cliSecret)[:16]."""
    machine_id = read_text(plat.machine_id_path)
    cli_secret = read_text(plat.cli_secret_path)
    if not machine_id or not cli_secret:
        return None
    digest = hashlib.sha256((machine_id + PREFIX_SALT + cli_secret).encode("utf-8")).hexdigest()
    return digest[:16]


class Credentials:
    def __init__(self) -> None:
        self.token: Optional[str] = None
        self.token_source = ""
        self.peer_token: Optional[str] = None
        self.password: Optional[str] = None
        self.cookie: Optional[str] = None


def load_state(plat: PlatformInfo) -> Dict[str, Any]:
    raw = read_text(plat.state_path)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(plat: PlatformInfo, state: Dict[str, Any]) -> None:
    atomic_write(plat.state_path, json.dumps(state, indent=2, sort_keys=True) + "\n")


def resolve_credentials(plat: PlatformInfo, args: argparse.Namespace,
                        interactive: bool = False) -> Credentials:
    creds = Credentials()
    state = load_state(plat)

    explicit = getattr(args, "token", None) or plat.env.get("NINEROUTER_TOKEN")
    if explicit:
        creds.token = explicit
        creds.token_source = "--token" if getattr(args, "token", None) else "NINEROUTER_TOKEN"
    else:
        derived = derive_cli_token(plat)
        if derived:
            creds.token = derived
            creds.token_source = "derived (machine-id + cli-secret)"

    creds.peer_token = plat.env.get("NINEROUTER_PEER_TOKEN") or None
    explicit_password = getattr(args, "password", None)
    if explicit_password == "":
        explicit_password = ask_secret("Dashboard password")
    creds.password = explicit_password or plat.env.get("NINEROUTER_PASSWORD") or None
    if not creds.password and state.get("password"):
        creds.password = state["password"]
    if not creds.password and interactive:
        creds.password = ask_secret("Dashboard password (kosongkan kalau token CLI sudah cukup)")
    return creds


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

class ApiError(Exception):
    def __init__(self, message: str, status: Optional[int] = None, body: Any = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.body = body


class NineRouter:
    def __init__(self, plat: PlatformInfo, creds: Credentials,
                 base_url: Optional[str] = None, port: Optional[int] = None,
                 timeout: int = 15):
        self.plat = plat
        self.creds = creds
        self.timeout = timeout
        self.base_url = ""
        self._explicit_base = base_url
        self._port = port
        self._logged_in = False
        self.health: Optional[Dict[str, Any]] = None
        self.version: Optional[Dict[str, Any]] = None

    # -- connection ---------------------------------------------------------
    def explicit_urls(self) -> List[str]:
        """URLs the user pinned explicitly (flag or env). These never fall back."""
        urls: List[str] = []
        if self._explicit_base:
            urls.append(self._explicit_base.rstrip("/"))
        for key in ("NINEROUTER_URL", "NINEROUTER_BASE_URL"):
            value = self.plat.env.get(key)
            if value and value.rstrip("/") not in urls:
                urls.append(value.rstrip("/"))
        return urls

    def candidate_urls(self) -> List[str]:
        state = load_state(self.plat)
        pinned = self.explicit_urls()
        if pinned:
            return pinned

        ports: List[int] = []
        if self._port:
            ports.append(int(self._port))
        for key in ("NINEROUTER_PORT", "PORT"):
            value = self.plat.env.get(key)
            if value and value.isdigit():
                ports.append(int(value))
        ports.append(DEFAULT_PORT)
        for extra in (DEFAULT_PORT + 1, DEFAULT_PORT + 2):
            ports.append(extra)

        urls: List[str] = []
        if state.get("base_url"):
            urls.append(str(state["base_url"]).rstrip("/"))
        seen = set(urls)
        for port in ports:
            url = "http://%s:%d" % (DEFAULT_HOST, port)
            if url not in seen:
                seen.add(url)
                urls.append(url)
        return urls

    def connect(self, quiet: bool = False) -> str:
        """Find a live 9router and remember its URL."""
        errors: List[str] = []
        for url in self.candidate_urls():
            try:
                status, _, data = self._raw("GET", url + "/api/health", None, no_auth=True, timeout=2)
            except ApiError as exc:
                errors.append("%s: %s" % (url, exc.message))
                continue
            if status == 200 and isinstance(data, dict) and data.get("ok"):
                self.base_url = url
                self.health = data
                if not quiet:
                    note("9router: %s" % url)
                return url
            errors.append("%s: HTTP %s" % (url, status))
        raise ApiError(
            "9router server tidak terjangkau.\n" +
            "\n".join("  - %s" % e for e in errors) +
            "\nJalankan 9router dulu (npx 9router) atau set NINEROUTER_URL / --base-url."
        )

    # -- low level ----------------------------------------------------------
    def _headers(self, auth: bool = True, json_body: bool = False) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if json_body:
            headers["Content-Type"] = "application/json"
        if not auth:
            return headers
        if self.creds.token:
            headers["x-9r-cli-token"] = self.creds.token
        elif self.creds.peer_token:
            headers["x-9r-peer-token"] = self.creds.peer_token
        if self.creds.cookie:
            headers["Cookie"] = self.creds.cookie
        return headers

    def _raw(self, method: str, url: str, body: Optional[bytes], no_auth: bool = False,
             timeout: Optional[int] = None, json_body: bool = False) -> Tuple[int, Any, Any]:
        request = urllib.request.Request(
            url, data=body, headers=self._headers(auth=not no_auth, json_body=json_body),
            method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
                status = resp.status
                headers = resp.headers
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
            headers = exc.headers
        except urllib.error.URLError as exc:
            raise ApiError("tidak bisa konek ke %s (%s)" % (url, exc.reason))
        except Exception as exc:  # socket timeouts and the like
            raise ApiError("request gagal ke %s (%s)" % (url, exc))
        parsed: Any = None
        if raw:
            try:
                parsed = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                parsed = raw.decode("utf-8", "replace")
        return status, headers, parsed

    def login(self) -> bool:
        if not self.creds.password or self._logged_in:
            return self._logged_in
        url = self.base_url + "/api/auth/login"
        payload = json.dumps({"password": self.creds.password}).encode("utf-8")
        status, headers, data = self._raw("POST", url, payload, json_body=True)
        if status >= 400:
            message = data.get("error") if isinstance(data, dict) else None
            raise ApiError("login gagal (HTTP %s): %s" % (status, message or "periksa password"))
        cookies = headers.get_all("Set-Cookie") if headers is not None else None
        for cookie in cookies or []:
            if cookie.startswith("auth_token="):
                self.creds.cookie = cookie.split(";")[0]
        if not self.creds.cookie:
            raise ApiError("login tidak mengembalikan cookie sesi")
        self._logged_in = True
        return True

    def call(self, method: str, path: str, body: Optional[Dict[str, Any]] = None,
             query: Optional[Dict[str, Any]] = None, expect: Sequence[int] = (200, 201),
             retried: bool = False) -> Any:
        if not self.base_url:
            self.connect()
        url = self.base_url + path
        if query:
            clean = {k: v for k, v in query.items() if v is not None and v != ""}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        status, _, data = self._raw(method, url, payload, json_body=body is not None)
        if status == 401:
            if not retried and self.creds.password:
                self._logged_in = False
                self.login()
                return self.call(method, path, body, query, expect, retried=True)
            raise ApiError(
                "401 Unauthorized dari 9router.\n"
                "  Token CLI: %s\n"
                "  Password : %s\n"
                "Pakai --password, atau pastikan %s bisa dibaca." % (
                    self.creds.token_source or "(tidak ada)",
                    "ada" if self.creds.password else "(tidak ada)",
                    self.plat.cli_secret_path))
        if status not in expect:
            message = data.get("error") if isinstance(data, dict) else data
            raise ApiError("HTTP %s pada %s %s%s" % (
                status, method.upper(), path, (": %s" % message) if message else ""), status, data)
        return data

    # -- convenience --------------------------------------------------------
    def server_version(self) -> Dict[str, Any]:
        if self.version is None:
            self.version = self.call("GET", "/api/version", expect=(200,))
        return self.version or {}


# ---------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------

def remember_base_url(plat: PlatformInfo, url: str) -> None:
    state = load_state(plat)
    if state.get("base_url") != url:
        state["base_url"] = url
        try:
            save_state(plat, state)
        except Exception:
            pass


def list_nodes(nr: NineRouter) -> List[Dict[str, Any]]:
    data = nr.call("GET", "/api/provider-nodes", expect=(200,))
    nodes = (data or {}).get("nodes") or []
    return [n for n in nodes if isinstance(n, dict)]


def list_connections(nr: NineRouter) -> List[Dict[str, Any]]:
    data = nr.call("GET", "/api/providers", expect=(200,))
    conns = (data or {}).get("connections") or []
    return [c for c in conns if isinstance(c, dict)]


def list_custom_models(nr: NineRouter) -> List[Dict[str, Any]]:
    data = nr.call("GET", "/api/models/custom", expect=(200,))
    return [m for m in ((data or {}).get("models") or []) if isinstance(m, dict)]


def list_disabled_models(nr: NineRouter) -> Dict[str, List[str]]:
    data = nr.call("GET", "/api/models/disabled", expect=(200,))
    if isinstance(data, dict) and isinstance(data.get("ids"), list):
        return {}
    disabled = (data or {}).get("disabled") or {}
    return {k: list(v) for k, v in disabled.items() if isinstance(v, list)}


def list_aliases(nr: NineRouter) -> Dict[str, str]:
    data = nr.call("GET", "/api/models/alias", expect=(200,))
    aliases = (data or {}).get("aliases") or {}
    return {str(k): str(v) for k, v in aliases.items()}


def list_router_keys(nr: NineRouter) -> List[Dict[str, Any]]:
    data = nr.call("GET", "/api/keys", expect=(200,))
    return [k for k in ((data or {}).get("keys") or []) if isinstance(k, dict)]


def list_combos(nr: NineRouter) -> List[Dict[str, Any]]:
    data = nr.call("GET", "/api/combos", expect=(200,))
    return [c for c in ((data or {}).get("combos") or []) if isinstance(c, dict)]


def model_catalog(nr: NineRouter) -> List[Dict[str, Any]]:
    data = nr.call("GET", "/api/models", expect=(200,))
    return [m for m in ((data or {}).get("models") or []) if isinstance(m, dict)]


def live_endpoint_models(nr: NineRouter, connection_id: str) -> List[Dict[str, Any]]:
    data = nr.call("GET", "/api/providers/%s/models" % urllib.parse.quote(connection_id),
                   expect=(200,))
    models = (data or {}).get("models") or []
    return [m for m in models if isinstance(m, dict)]


def read_connection_secrets(plat: PlatformInfo) -> Dict[str, str]:
    """Best effort: read api keys straight from the SQLite file for masked display.

    The REST API never returns stored credentials, so `list` would otherwise be
    blind. Read-only, and silently degrades to {} on any failure.
    """
    if not plat.db_path.exists():
        return {}
    try:
        import sqlite3
    except Exception:
        return {}
    conn = None
    try:
        # quote() keeps spaces and '#'/'?' inside Windows paths from breaking the URI
        uri = "file:%s?mode=ro" % urllib.parse.quote(plat.db_path.as_posix())
        conn = sqlite3.connect(uri, uri=True, timeout=2)
        result: Dict[str, str] = {}
        for row in conn.execute("SELECT id, data FROM providerConnections"):
            try:
                payload = json.loads(row[1] or "{}")
            except Exception:
                continue
            key = payload.get("apiKey") or payload.get("accessToken") or ""
            if key:
                result[str(row[0])] = str(key)
        return result
    except Exception:
        return {}
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def find_node(nodes: List[Dict[str, Any]], ref: str) -> Optional[Dict[str, Any]]:
    if not ref:
        return None
    for node in nodes:
        if node.get("id") == ref:
            return node
    for node in nodes:
        if str(node.get("prefix", "")).lower() == ref.lower():
            return node
    for node in nodes:
        if str(node.get("name", "")).lower() == ref.lower():
            return node
    return None


def find_connection(conns: List[Dict[str, Any]], ref: str) -> Optional[Dict[str, Any]]:
    if not ref:
        return None
    for conn in conns:
        if conn.get("id") == ref:
            return conn
    for conn in conns:
        if str(conn.get("name", "")).lower() == ref.lower():
            return conn
    return None


def connection_node_id(nodes: List[Dict[str, Any]], connection: Dict[str, Any]) -> str:
    provider = str(connection.get("provider", ""))
    for node in nodes:
        if node.get("id") == provider:
            return provider
    return provider


# ---------------------------------------------------------------------------
# backups
# ---------------------------------------------------------------------------

def make_backup(nr: NineRouter, plat: PlatformInfo, reason: str = "manual") -> Path:
    payload = nr.call("GET", "/api/settings/database", expect=(200,))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = plat.backups_dir / ("9router-%s.json" % stamp)
    counter = 1
    while path.exists():
        path = plat.backups_dir / ("9router-%s-%d.json" % (stamp, counter))
        counter += 1
    atomic_write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    prune_backups(plat)
    note("backup: %s (%s)" % (path.name, reason))
    return path


def prune_backups(plat: PlatformInfo, keep: int = BACKUP_KEEP) -> List[Path]:
    files = list_backup_files(plat)
    removed: List[Path] = []
    for path in files[keep:]:
        try:
            path.unlink()
            removed.append(path)
        except Exception:
            pass
    return removed


def list_backup_files(plat: PlatformInfo) -> List[Path]:
    """Backups newest first.

    Sorted by mtime as well as name: several backups can land in the same second
    (every write makes one), and then the name alone does not order them.
    """
    try:
        files = [p for p in plat.backups_dir.glob("9router-*.json") if p.is_file()]
    except Exception:
        return []

    def sort_key(path: Path):
        try:
            return (path.stat().st_mtime, path.name)
        except Exception:
            return (0.0, path.name)

    files.sort(key=sort_key, reverse=True)
    return files


def restore_backup(nr: NineRouter, plat: PlatformInfo, source: Path) -> None:
    if not source.exists():
        raise ApiError("file backup tidak ditemukan: %s" % source)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ApiError("backup tidak bisa dibaca sebagai JSON: %s" % exc)
    if not isinstance(payload, dict):
        raise ApiError("isi backup harus berupa objek JSON")
    nr.call("POST", "/api/settings/database", body=payload, expect=(200, 201))


# ---------------------------------------------------------------------------
# operations: endpoints
# ---------------------------------------------------------------------------

def op_add_endpoint(nr: NineRouter, name: str, prefix: str, base_url: str,
                    api_type: str = "chat", node_type: str = "openai-compatible") -> Dict[str, Any]:
    error = validate_prefix(prefix) or validate_base_url(base_url)
    if error:
        raise ApiError(error)
    if not name.strip():
        raise ApiError("nama endpoint wajib diisi")
    if node_type == "openai-compatible" and api_type not in ("chat", "responses"):
        raise ApiError("api type harus 'chat' atau 'responses'")
    body: Dict[str, Any] = {
        "name": name.strip(),
        "prefix": prefix.strip(),
        "baseUrl": base_url.strip().rstrip("/"),
        "type": node_type,
    }
    if node_type == "openai-compatible":
        body["apiType"] = api_type
    data = nr.call("POST", "/api/provider-nodes", body=body, expect=(200, 201))
    return (data or {}).get("node") or {}


def op_edit_endpoint(nr: NineRouter, node: Dict[str, Any], name: Optional[str] = None,
                     prefix: Optional[str] = None, base_url: Optional[str] = None,
                     api_type: Optional[str] = None) -> Dict[str, Any]:
    """Update a provider node.

    9router's PUT handler validates name, prefix and baseUrl together, so the body
    always carries the full (merged) set instead of only the changed fields.
    """
    node_id = str(node.get("id") or "")
    if not node_id:
        raise ApiError("node tidak punya id")

    final_name = (name if name is not None else node.get("name") or "").strip()
    final_prefix = (prefix if prefix is not None else node.get("prefix") or "").strip()
    final_url = (base_url if base_url is not None else node.get("baseUrl") or "").strip()
    if not final_name:
        raise ApiError("nama endpoint wajib diisi")
    error = validate_prefix(final_prefix) or validate_base_url(final_url)
    if error:
        raise ApiError(error)

    body: Dict[str, Any] = {
        "name": final_name,
        "prefix": final_prefix,
        "baseUrl": final_url.rstrip("/"),
    }
    if node.get("type") == "openai-compatible":
        final_api_type = api_type or node.get("apiType") or "chat"
        if final_api_type not in ("chat", "responses"):
            raise ApiError("api type harus 'chat' atau 'responses'")
        body["apiType"] = final_api_type

    data = nr.call("PUT", "/api/provider-nodes/%s" % urllib.parse.quote(node_id),
                   body=body, expect=(200,))
    return (data or {}).get("node") or {}


def op_delete_endpoint(nr: NineRouter, node_id: str) -> None:
    nr.call("DELETE", "/api/provider-nodes/%s" % urllib.parse.quote(node_id), expect=(200,))


# ---------------------------------------------------------------------------
# operations: API keys (connections)
# ---------------------------------------------------------------------------

def op_add_key(nr: NineRouter, provider: str, api_key: str, name: str = "",
               priority: int = 1) -> Dict[str, Any]:
    if not provider:
        raise ApiError("provider/endpoint wajib diisi")
    if api_key is None:
        raise ApiError("api key wajib diisi (pakai \"\" untuk server tanpa auth)")
    body = {
        "provider": provider,
        "apiKey": api_key,
        "name": name or provider,
        "priority": priority,
        "isActive": True,
        "providerSpecificData": {},
    }
    return nr.call("POST", "/api/providers", body=body, expect=(200, 201)) or {}


def op_update_key(nr: NineRouter, connection_id: str, api_key: Optional[str] = None,
                  name: Optional[str] = None, priority: Optional[int] = None,
                  is_active: Optional[bool] = None,
                  default_model: Optional[str] = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {}
    if api_key is not None:
        body["apiKey"] = api_key
    if name is not None:
        body["name"] = name
    if priority is not None:
        body["priority"] = int(priority)
    if is_active is not None:
        body["isActive"] = bool(is_active)
    if default_model is not None:
        body["defaultModel"] = default_model
    if not body:
        raise ApiError("tidak ada perubahan yang diminta")
    data = nr.call("PUT", "/api/providers/%s" % urllib.parse.quote(connection_id),
                   body=body, expect=(200,))
    return (data or {}).get("connection") or data or {}


def op_delete_connection(nr: NineRouter, connection_id: str) -> None:
    nr.call("DELETE", "/api/providers/%s" % urllib.parse.quote(connection_id), expect=(200,))


def op_test_connection(nr: NineRouter, connection_id: str) -> Dict[str, Any]:
    data = nr.call("POST", "/api/providers/%s/test" % urllib.parse.quote(connection_id),
                   expect=(200, 201, 400, 500))
    return data if isinstance(data, dict) else {"result": data}


def op_test_model(nr: NineRouter, model: str, kind: str = "llm") -> Dict[str, Any]:
    data = nr.call("POST", "/api/models/test", body={"model": model, "kind": kind},
                   expect=(200, 201, 500))
    return data if isinstance(data, dict) else {"result": data}


# ---------------------------------------------------------------------------
# operations: models
# ---------------------------------------------------------------------------

def op_add_model(nr: NineRouter, provider_alias: str, model_id: str, name: str = "",
                 kind: str = "llm", caps: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
    if not provider_alias or not model_id:
        raise ApiError("provider alias dan model id wajib diisi")
    body: Dict[str, Any] = {
        "providerAlias": provider_alias,
        "id": model_id,
        "type": kind or "llm",
    }
    if name:
        body["name"] = name
    if caps:
        body["caps"] = caps
    return nr.call("POST", "/api/models/custom", body=body, expect=(200, 201)) or {}


def op_delete_model(nr: NineRouter, provider_alias: str, model_id: str, kind: str = "llm") -> None:
    nr.call("DELETE", "/api/models/custom",
            query={"providerAlias": provider_alias, "id": model_id, "type": kind or "llm"},
            expect=(200,))


def op_set_alias(nr: NineRouter, model: str, alias: str) -> Dict[str, Any]:
    return nr.call("PUT", "/api/models/alias", body={"model": model, "alias": alias},
                   expect=(200,)) or {}


def op_delete_alias(nr: NineRouter, alias: str) -> None:
    nr.call("DELETE", "/api/models/alias", query={"alias": alias}, expect=(200,))


def op_disable_models(nr: NineRouter, provider_alias: str, ids: List[str]) -> Dict[str, Any]:
    return nr.call("POST", "/api/models/disabled",
                   body={"providerAlias": provider_alias, "ids": ids}, expect=(200,)) or {}


def op_enable_models(nr: NineRouter, provider_alias: str, ids: Optional[List[str]] = None) -> None:
    if ids:
        for model_id in ids:
            nr.call("DELETE", "/api/models/disabled",
                    query={"providerAlias": provider_alias, "id": model_id}, expect=(200,))
    else:
        nr.call("DELETE", "/api/models/disabled", query={"providerAlias": provider_alias},
                expect=(200,))


# ---------------------------------------------------------------------------
# operations: 9router client keys and combos
# ---------------------------------------------------------------------------

def op_add_router_key(nr: NineRouter, name: str) -> Dict[str, Any]:
    if not name.strip():
        raise ApiError("nama key wajib diisi")
    return nr.call("POST", "/api/keys", body={"name": name.strip()}, expect=(200, 201)) or {}


def op_delete_router_key(nr: NineRouter, key_id: str) -> None:
    nr.call("DELETE", "/api/keys/%s" % urllib.parse.quote(key_id), expect=(200,))


def op_set_router_key_active(nr: NineRouter, key_id: str, is_active: bool) -> Dict[str, Any]:
    return nr.call("PUT", "/api/keys/%s" % urllib.parse.quote(key_id),
                   body={"isActive": bool(is_active)}, expect=(200,)) or {}


def op_add_combo(nr: NineRouter, name: str, models: List[str], kind: str = "") -> Dict[str, Any]:
    body: Dict[str, Any] = {"name": name, "models": models}
    if kind:
        body["kind"] = kind
    return nr.call("POST", "/api/combos", body=body, expect=(200, 201)) or {}


def op_delete_combo(nr: NineRouter, combo_id: str) -> None:
    nr.call("DELETE", "/api/combos/%s" % urllib.parse.quote(combo_id), expect=(200,))


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render_table(headers: Sequence[str], rows: Sequence[Sequence[Any]], indent: str = "  ") -> str:
    if not rows:
        return indent + "(kosong)"
    widths = [len(str(h)) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))
    lines = [indent + "  ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    lines.append(indent + "  ".join("-" * width for width in widths))
    for row in rows:
        lines.append(indent + "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)).rstrip())
    return "\n".join(lines)


def show(text: str) -> None:
    say(text)


def endpoint_rows(nodes: List[Dict[str, Any]], conns: List[Dict[str, Any]],
                  secrets: Dict[str, str]) -> List[List[str]]:
    rows: List[List[str]] = []
    for node in nodes:
        node_id = str(node.get("id", ""))
        bound = [c for c in conns if str(c.get("provider", "")) == node_id]
        key_cell = "(belum ada key)"
        if bound:
            key = secrets.get(str(bound[0].get("id", "")))
            key_cell = mask_key(key) if key else "(tersimpan)"
        rows.append([
            str(node.get("prefix", "")),
            str(node.get("name", "")),
            str(node.get("type", "")),
            str(node.get("apiType", "-")),
            str(node.get("baseUrl", "")),
            "%d" % len(bound),
            key_cell,
        ])
    return rows


def do_list(nr: NineRouter, plat: PlatformInfo, what: str = "all", want_json: bool = False) -> Dict[str, Any]:
    nodes = list_nodes(nr)
    conns = list_connections(nr)
    secrets = read_connection_secrets(plat)
    payload: Dict[str, Any] = {
        "server": {
            "base_url": nr.base_url,
            "version": nr.server_version().get("currentVersion"),
        }
    }

    if what in ("all", "endpoints"):
        bound_ids = set()
        endpoints = []
        for node in nodes:
            node_id = str(node.get("id", ""))
            bound = [c for c in conns if str(c.get("provider", "")) == node_id]
            for conn in bound:
                bound_ids.add(str(conn.get("id", "")))
            endpoints.append({
                "id": node_id,
                "name": node.get("name"),
                "prefix": node.get("prefix"),
                "type": node.get("type"),
                "apiType": node.get("apiType"),
                "baseUrl": node.get("baseUrl"),
                "connection_count": len(bound),
                "connections": [
                    {
                        "id": c.get("id"),
                        "name": c.get("name"),
                        "authType": c.get("authType"),
                        "isActive": c.get("isActive"),
                        "priority": c.get("priority"),
                        "testStatus": c.get("testStatus"),
                        "key": mask_key(secrets.get(str(c.get("id", "")))) if secrets.get(str(c.get("id", ""))) else None,
                    }
                    for c in bound
                ],
            })
        payload["endpoints"] = endpoints

    if what in ("all", "keys", "connections"):
        others = [c for c in conns if str(c.get("provider", "")) not in {str(n.get("id", "")) for n in nodes}]
        payload["provider_connections"] = [
            {
                "id": c.get("id"),
                "provider": c.get("provider"),
                "name": c.get("name"),
                "email": c.get("email"),
                "authType": c.get("authType"),
                "isActive": c.get("isActive"),
                "priority": c.get("priority"),
                "testStatus": c.get("testStatus"),
                "key": mask_key(secrets.get(str(c.get("id", "")))) if secrets.get(str(c.get("id", ""))) else None,
            }
            for c in others
        ]

    if what in ("all", "keys"):
        payload["router_keys"] = [
            {
                "id": k.get("id"),
                "name": k.get("name"),
                "key": mask_key(str(k.get("key"))) if k.get("key") else None,
                "isActive": k.get("isActive"),
                "machineId": k.get("machineId"),
                "createdAt": k.get("createdAt"),
            }
            for k in list_router_keys(nr)
        ]

    if what in ("all", "models"):
        payload["custom_models"] = list_custom_models(nr)
        payload["disabled_models"] = list_disabled_models(nr)
        payload["aliases"] = list_aliases(nr)

    if what in ("all", "combos"):
        payload["combos"] = list_combos(nr)

    if what in ("all", "backups"):
        payload["backups"] = [_backup_entry(p) for p in list_backup_files(plat)]

    if want_json:
        return payload

    # human readable
    server = payload["server"]
    header("9router @ %s" % server["base_url"], "versi server: %s" % (server.get("version") or "?"))
    if "endpoints" in payload:
        say(paint("Endpoints custom (%d):" % len(payload["endpoints"]), Ansi.BOLD))
        rows = endpoint_rows(nodes, conns, secrets)
        show(render_table(["prefix", "nama", "tipe", "api", "base url", "conn", "api key"], rows))
        for endpoint in payload["endpoints"]:
            if endpoint["connections"]:
                say("")
                say("  koneksi %s:" % endpoint["prefix"])
                show(render_table(
                    ["id", "nama", "auth", "aktif", "prioritas", "tes", "key"],
                    [[c["id"], c["name"], c["authType"], c["isActive"], c["priority"],
                      c["testStatus"] or "-", c["key"] or "-"] for c in endpoint["connections"]]))
    if payload.get("provider_connections"):
        say("")
        say(paint("Provider bawaan (bukan endpoint custom):", Ansi.BOLD))
        show(render_table(
            ["provider", "nama", "auth", "aktif", "tes", "key"],
            [[c["provider"], c["name"], c["authType"], c["isActive"], c["testStatus"] or "-",
              c["key"] or "-"] for c in payload["provider_connections"]]))
    if "router_keys" in payload:
        say("")
        say(paint("API key 9router (dipakai client):", Ansi.BOLD))
        show(render_table(
            ["id", "nama", "key", "aktif"],
            [[k["id"], k["name"], k["key"] or "-", k["isActive"]] for k in payload["router_keys"]]))
    if "custom_models" in payload:
        say("")
        say(paint("Model custom (%d):" % len(payload["custom_models"]), Ansi.BOLD))
        show(render_table(
            ["provider", "model", "tipe", "nama"],
            [[m.get("providerAlias"), m.get("id"), m.get("type"), m.get("name")]
             for m in payload["custom_models"]]))
        if payload["aliases"]:
            say("  alias: " + ", ".join("%s -> %s" % (a, m) for a, m in sorted(payload["aliases"].items())))
        disabled = payload["disabled_models"]
        if disabled:
            say("  disabled: " + ", ".join("%s(%d)" % (k, len(v)) for k, v in sorted(disabled.items())))
    if "combos" in payload:
        say("")
        say(paint("Combo (%d):" % len(payload["combos"]), Ansi.BOLD))
        show(render_table(
            ["id", "nama", "kind", "model"],
            [[c.get("id"), c.get("name"), c.get("kind") or "-", ", ".join(map(str, c.get("models") or []))]
             for c in payload["combos"]]))
    if "backups" in payload:
        say("")
        say(paint("Backup lokal (%d):" % len(payload["backups"]), Ansi.BOLD))
        show(render_table(["file", "waktu", "ukuran"],
                          [[b["file"], b["created"], b["bytes"]] for b in payload["backups"]]))
    return payload


def _backup_entry(path: Path) -> Dict[str, Any]:
    try:
        stat = path.stat()
        created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
        size = stat.st_size
    except Exception:
        created, size = "?", 0
    return {"file": path.name, "path": str(path), "created": created, "bytes": size}


def do_info(nr: NineRouter, plat: PlatformInfo, creds: Credentials,
            want_json: bool = False) -> Dict[str, Any]:
    version = nr.server_version()
    payload: Dict[str, Any] = {
        "manager_version": VERSION,
        "server": {
            "base_url": nr.base_url,
            "current_version": version.get("currentVersion"),
            "latest_version": version.get("latestVersion"),
            "has_update": version.get("hasUpdate"),
        },
        "auth": {
            "method": "cli-token" if creds.token else ("peer-token" if creds.peer_token else "password"),
            "token_source": creds.token_source or None,
            "password_available": bool(creds.password),
        },
        "paths": {name: value for name, value in plat.describe()},
        "backups": len(list_backup_files(plat)),
    }
    try:
        payload["counts"] = {
            "endpoints": len(list_nodes(nr)),
            "provider_connections": len(list_connections(nr)),
            "custom_models": len(list_custom_models(nr)),
            "router_keys": len(list_router_keys(nr)),
            "combos": len(list_combos(nr)),
        }
    except ApiError as exc:
        payload["counts_error"] = exc.message

    if want_json:
        return payload

    header("Info & Doctor")
    for name, value in plat.describe():
        say("  %-20s %s" % (name, value))
    say("  %-20s %s" % ("server", nr.base_url))
    say("  %-20s %s" % ("versi 9router", version.get("currentVersion") or "?"))
    if version.get("hasUpdate"):
        say("  %-20s %s" % ("update tersedia", version.get("latestVersion")))
    say("  %-20s %s (%s)" % ("auth", payload["auth"]["method"], payload["auth"]["token_source"] or "-"))
    say("  %-20s %s" % ("backup lokal", "%d file" % payload["backups"]))
    counts = payload.get("counts")
    if counts:
        say("")
        say(paint("Isi konfigurasi:", Ansi.BOLD))
        for name, value in counts.items():
            say("  %-20s %d" % (name, value))
    if payload.get("counts_error"):
        warn(payload["counts_error"])
    return payload


def do_generate(nr: NineRouter, plat: PlatformInfo, ref: str,
                want_json: bool = False) -> Dict[str, Any]:
    nodes = list_nodes(nr)
    conns = list_connections(nr)
    node = find_node(nodes, ref)
    connection = find_connection(conns, ref) if not node else None
    if node is None and connection is not None:
        node = find_node(nodes, str(connection.get("provider", "")))
    if node is None:
        raise ApiError("endpoint tidak ditemukan: %s" % ref)

    node_id = str(node.get("id", ""))
    bound = [c for c in conns if str(c.get("provider", "")) == node_id]
    models = [m for m in model_catalog(nr) if str(m.get("provider", "")) == node_id]
    model_ids = [str(m.get("fullModel") or m.get("model")) for m in models]
    keys = [k for k in list_router_keys(nr) if k.get("isActive")]
    chosen_key = str(keys[0].get("key", "")) if keys else ""

    openai_base = nr.base_url + "/v1"
    payload = {
        "endpoint": node_id,
        "prefix": node.get("prefix"),
        "base_url": nr.base_url,
        "openai_base_url": openai_base,
        "api_key": mask_key(chosen_key) if chosen_key else None,
        "api_key_id": keys[0].get("id") if keys else None,
        "connections": [c.get("id") for c in bound],
        "models": model_ids,
        "snippets": {
            "env": "export OPENAI_BASE_URL=%s\nexport OPENAI_API_KEY=<api-key-9router>" % openai_base,
            "curl": ('curl %s/chat/completions -H "Authorization: Bearer <api-key-9router>" '
                     '-H "Content-Type: application/json" -d \'{"model":"%s","messages":[{"role":"user",'
                     '"content":"ping"}]}\'' % (openai_base, model_ids[0] if model_ids else "<model>")),
            "jcode": ("jcode provider add %s --base-url %s --model %s\n"
                      "jcode --provider-profile %s" % (
                          node.get("prefix", "9router"), openai_base,
                          model_ids[0] if model_ids else "<model>", node.get("prefix", "9router"))),
        },
    }

    if want_json:
        return payload

    header("Command untuk endpoint %s" % node.get("prefix"), node_id)
    say("  %-18s %s" % ("base url server", nr.base_url))
    say("  %-18s %s" % ("base url OpenAI", openai_base))
    say("  %-18s %s" % ("api key 9router", payload["api_key"] or "(belum ada, buat lewat menu 7 / `key add-router`)"))
    say("  %-18s %s" % ("koneksi", ", ".join(payload["connections"]) or "(belum ada key endpoint)"))
    say("")
    say(paint("Model yang dilayani:", Ansi.BOLD))
    show(render_table(["model id"], [[m] for m in model_ids]) if model_ids else "  (belum ada model custom)")
    say("")
    say(paint("Snippet:", Ansi.BOLD))
    for name, snippet in payload["snippets"].items():
        say("  # %s" % name)
        for line in snippet.splitlines():
            say("  " + line)
    copied = clipboard_copy(payload["snippets"]["env"])
    if copied:
        note("(snippet env disalin ke clipboard)")
    return payload


def do_test(nr: NineRouter, plat: PlatformInfo, ref: str, want_json: bool = False) -> Dict[str, Any]:
    conns = list_connections(nr)
    connection = find_connection(conns, ref)
    if connection is None:
        raise ApiError("koneksi tidak ditemukan: %s (pakai id atau nama dari `list keys`)" % ref)
    result = op_test_connection(nr, str(connection.get("id")))
    payload = {"connection": connection.get("id"), "provider": connection.get("provider"),
               "result": result}
    if want_json:
        return payload
    header("Tes koneksi %s" % connection.get("id"))
    say(json.dumps(result, indent=2, ensure_ascii=False))
    return payload


# ---------------------------------------------------------------------------
# interactive menu
# ---------------------------------------------------------------------------

MENU_TEXT = """
  1) Tambah endpoint custom         6) Model (custom / alias / disable)
  2) Daftar endpoint, key, model    7) API key 9router untuk client
  3) Edit endpoint custom           8) Tes koneksi & generate command
  4) Ganti / tambah API key         9) Backup & restore
  5) Hapus endpoint / koneksi       I) Info & doctor
                                   0) Keluar
"""


def backed_up(nr: NineRouter, plat: PlatformInfo, args: argparse.Namespace,
              reason: str) -> None:
    if getattr(args, "no_backup", False):
        note("(backup dilewati: --no-backup)")
        return
    try:
        make_backup(nr, plat, reason)
    except ApiError as exc:
        warn("backup gagal: %s" % exc.message)
        if not confirm("Lanjut tanpa backup?", False):
            raise


def pick_endpoint(nr: NineRouter, prompt: str = "Endpoint (prefix / id / nama)") -> Optional[Dict[str, Any]]:
    nodes = list_nodes(nr)
    if not nodes:
        warn("Belum ada endpoint custom. Pakai menu 1 dulu.")
        return None
    say(render_table(["prefix", "nama", "base url"],
                     [[n.get("prefix"), n.get("name"), n.get("baseUrl")] for n in nodes]))
    ref = ask(prompt)
    node = find_node(nodes, ref)
    if node is None:
        warn("Endpoint tidak ditemukan: %s" % ref)
    return node


def pick_connection(nr: NineRouter, node: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    conns = list_connections(nr)
    if node is not None:
        conns = [c for c in conns if str(c.get("provider", "")) == str(node.get("id", ""))]
    if not conns:
        warn("Belum ada koneksi/key.")
        return None
    say(render_table(["id", "provider", "nama", "tes"],
                     [[c.get("id"), c.get("provider"), c.get("name"), c.get("testStatus") or "-"]
                      for c in conns]))
    ref = ask("Koneksi (id atau nama)")
    connection = find_connection(conns, ref)
    if connection is None:
        warn("Koneksi tidak ditemukan: %s" % ref)
    return connection


def menu_add_endpoint(nr: NineRouter, plat: PlatformInfo, args: argparse.Namespace) -> None:
    header("Tambah endpoint custom")
    name = ask("Nama endpoint (contoh: Gateway Lokal)")
    prefix = ask("Prefix model (contoh: mygw)")
    node_type = ask("Tipe endpoint [openai-compatible/custom-embedding/anthropic-compatible]",
                    "openai-compatible")
    api_type = "chat"
    if node_type == "openai-compatible":
        api_type = ask("API type [chat/responses]", "chat")
    base_url = ask("Base URL (contoh: https://api.example.com/v1)")
    node = op_add_endpoint(nr, name, prefix, base_url, api_type, node_type)
    ok("Endpoint dibuat: %s" % node.get("id", "?"))
    say(render_table(["field", "nilai"], [[k, node.get(k)] for k in
                       ("id", "name", "prefix", "type", "apiType", "baseUrl") if k in node]))
    if confirm("Tambahkan API key untuk endpoint ini sekarang?", default=True):
        menu_add_key(nr, plat, args, node)


def menu_add_key(nr: NineRouter, plat: PlatformInfo, args: argparse.Namespace,
                 node: Optional[Dict[str, Any]] = None) -> None:
    header("Tambah API key endpoint")
    node = node or pick_endpoint(nr)
    if node is None:
        return
    key = ask_secret("API key untuk %s (kosongkan kalau server tanpa auth)" % node.get("prefix"))
    name = ask("Label key", str(node.get("prefix")))
    backed_up(nr, plat, args, "sebelum tambah key")
    op_add_key(nr, str(node.get("id")), key, name)
    ok("API key ditambahkan ke %s. Cek dengan menu 2." % node.get("prefix"))


def menu_set_key(nr: NineRouter, plat: PlatformInfo, args: argparse.Namespace) -> None:
    header("Ganti API key / properti koneksi")
    node = pick_endpoint(nr)
    if node is None:
        return
    connection = pick_connection(nr, node)
    if connection is None:
        return
    key = ask_secret("API key baru (kosongkan = biarkan)")
    name = ask("Nama/label baru (kosongkan = biarkan)")
    active_raw = ask("Aktifkan? [y/n] (kosongkan = biarkan)")
    is_active = None
    if active_raw:
        is_active = active_raw.lower() in ("y", "ya", "yes")
    backed_up(nr, plat, args, "sebelum ganti key")
    op_update_key(nr, str(connection.get("id")), key or None, name or None, None, is_active)
    ok("Koneksi diperbarui.")


def menu_edit_endpoint(nr: NineRouter, plat: PlatformInfo, args: argparse.Namespace) -> None:
    header("Edit endpoint custom")
    node = pick_endpoint(nr)
    if node is None:
        return
    say("Kosongkan field yang tidak ingin diubah.")
    name = ask("Nama baru", str(node.get("name", "")))
    prefix = ask("Prefix baru", str(node.get("prefix", "")))
    api_type = ask("API type", str(node.get("apiType") or "chat"))
    base_url = ask("Base URL baru", str(node.get("baseUrl", "")))
    backed_up(nr, plat, args, "sebelum edit endpoint")
    updated = op_edit_endpoint(
        nr, node,
        name if name != node.get("name") else None,
        prefix if prefix != node.get("prefix") else None,
        base_url if base_url != node.get("baseUrl") else None,
        api_type if api_type != (node.get("apiType") or "chat") else None)
    ok("Endpoint diperbarui: prefix=%s base_url=%s" % (updated.get("prefix"), updated.get("baseUrl")))
    note("Catatan: prefix/base URL ikut berubah di semua koneksi yang terikat ke endpoint ini.")


def menu_delete(nr: NineRouter, plat: PlatformInfo, args: argparse.Namespace) -> None:
    header("Hapus endpoint / koneksi")
    say("  1) Hapus koneksi (1 API key)")
    say("  2) Hapus endpoint (beserta koneksi + model custom-nya)")
    choice = ask("Pilihan", "1")
    if choice == "2":
        node = pick_endpoint(nr)
        if node is None:
            return
        node_id = str(node.get("id"))
        if not confirm("Hapus endpoint %s dan semua koneksinya?" % node.get("prefix")):
            warn("Dibatalkan.")
            return
        backed_up(nr, plat, args, "sebelum hapus endpoint")
        op_delete_endpoint(nr, node_id)
        ok("Endpoint %s dihapus." % node.get("prefix"))
        return

    if choice == "1":
        node = pick_endpoint(nr)
        if node is None:
            return
        connection = pick_connection(nr, node)
        if connection is None:
            return
        if not confirm("Hapus koneksi %s (%s)?" % (connection.get("id"), connection.get("name"))):
            warn("Dibatalkan.")
            return
        backed_up(nr, plat, args, "sebelum hapus koneksi")
        op_delete_connection(nr, str(connection.get("id")))
        ok("Koneksi dihapus.")
        return

    warn("Pilihan tidak dikenal.")


def menu_models(nr: NineRouter, plat: PlatformInfo, args: argparse.Namespace) -> None:
    header("Model custom / alias / disable")
    say("  1) Tambah model custom ke endpoint")
    say("  2) Hapus model custom")
    say("  3) Lihat model yang dilayani server (grep)")
    say("  4) Buat alias model")
    say("  5) Hapus alias")
    say("  6) Disable model")
    say("  7) Enable model / bersihkan disable")
    choice = ask("Pilihan", "1")

    if choice == "3":
        needle = ask("Cari model (kosongkan = tampilkan yang punya prefix opsi)", "").lower()
        rows = []
        for model in model_catalog(nr):
            full = str(model.get("fullModel") or model.get("model") or "")
            if needle and needle not in full.lower():
                continue
            rows.append([full, model.get("name")])
            if len(rows) >= 60:
                break
        show(render_table(["model", "nama"], rows))
        return

    node = pick_endpoint(nr)
    if node is None:
        return
    alias_id = str(node.get("id"))
    prefix = str(node.get("prefix"))

    if choice == "1":
        model_id = ask("Model id di sisi endpoint (contoh: gpt-4o-mini)")
        name = ask("Nama tampilan (opsional)", model_id)
        kind = ask("Tipe [llm/stt/tts/embedding]", "llm")
        vision = ask("Dukung gambar? [y/n]", "n").lower() in ("y", "ya", "yes")
        reasoning = ask("Dukung reasoning? [y/n]", "n").lower() in ("y", "ya", "yes")
        backed_up(nr, plat, args, "sebelum tambah model")
        result = op_add_model(nr, alias_id, model_id, name, kind,
                              {"vision": vision, "reasoning": reasoning})
        ok("Model ditambahkan: %s/%s (baru=%s)" % (prefix, model_id, result.get("added")))
        return

    models = [m for m in list_custom_models(nr) if str(m.get("providerAlias")) == alias_id]
    if choice in ("2", "5"):
        if not models:
            warn("Endpoint ini belum punya model custom.")
            return
        show(render_table(["model", "tipe"],
                          [[m.get("id"), m.get("type")] for m in models]))
        model_id = ask("Model id")
        backed_up(nr, plat, args, "sebelum hapus model")
        if choice == "2":
            op_delete_model(nr, alias_id, model_id)
            ok("Model %s dihapus dari %s." % (model_id, prefix))
        else:
            warn("Alias dihapus lewat pilihan 5 hanya berlaku untuk alias; pakai id alias.")
        return

    if choice == "4":
        say(render_table(["model", "nama"], [[m.get("fullModel") or m.get("model"), m.get("name")]
                                             for m in model_catalog(nr)][:40]))
        model = ask("Model sumber (fullModel)")
        alias = ask("Alias baru")
        backed_up(nr, plat, args, "sebelum buat alias")
        op_set_alias(nr, model, alias)
        ok("Alias %s -> %s" % (alias, model))
        return

    if choice == "6":
        say(render_table(["model", "nama"], [[m.get("fullModel") or m.get("model"), m.get("name")]
                                             for m in model_catalog(nr)][:40]))
        raw = ask("Model id yang di-disable (pisahkan dengan koma)")
        ids = [part.strip() for part in raw.split(",") if part.strip()]
        if not ids:
            warn("Tidak ada model yang dipilih.")
            return
        backed_up(nr, plat, args, "sebelum disable model")
        op_disable_models(nr, alias_id, ids)
        ok("Disabled: %s" % ", ".join(ids))
        return

    if choice == "7":
        disabled = list_disabled_models(nr).get(alias_id, [])
        say(render_table(["provider", "model disabled"], [[alias_id, m] for m in disabled]))
        raw = ask("Model yang di-enable (koma) atau kosongkan = semua")
        ids = [part.strip() for part in raw.split(",") if part.strip()]
        backed_up(nr, plat, args, "sebelum enable model")
        op_enable_models(nr, alias_id, ids or None)
        ok("Enable selesai.")
        return

    warn("Pilihan tidak dikenal.")


def menu_router_keys(nr: NineRouter, plat: PlatformInfo, args: argparse.Namespace) -> None:
    header("API key 9router (dipakai CLI client)")
    keys = list_router_keys(nr)
    show(render_table(["id", "nama", "key", "aktif", "dibuat"],
                      [[k.get("id"), k.get("name"), mask_key(str(k.get("key") or "")),
                        k.get("isActive"), (k.get("createdAt") or "")[:19]] for k in keys]))
    say("")
    say("  1) Buat key baru")
    say("  2) Aktif/nonaktifkan key")
    say("  3) Hapus key")
    choice = ask("Pilihan", "")
    if choice == "1":
        name = ask("Nama key", "cli-manager")
        result = op_add_router_key(nr, name)
        ok("Key dibuat.")
        say("  id  : %s" % result.get("id"))
        say("  name: %s" % result.get("name"))
        say("  key : %s" % result.get("key"))
        note("Key hanya ditampilkan sekarang, salin kalau perlu.")
        clipboard_copy(str(result.get("key", "")))
        return
    if not keys:
        warn("Belum ada key.")
        return
    ref = ask("Id atau nama key")
    key = None
    for candidate in keys:
        if candidate.get("id") == ref or str(candidate.get("name", "")).lower() == ref.lower():
            key = candidate
            break
    if key is None:
        warn("Key tidak ditemukan.")
        return
    if choice == "2":
        op_set_router_key_active(nr, str(key.get("id")), not bool(key.get("isActive")))
        ok("Status key diubah.")
    elif choice == "3":
        if confirm("Hapus key %s?" % key.get("name")):
            op_delete_router_key(nr, str(key.get("id")))
            ok("Key dihapus.")
    else:
        warn("Pilihan tidak dikenal.")


def menu_test_generate(nr: NineRouter, plat: PlatformInfo, args: argparse.Namespace) -> None:
    header("Tes koneksi & generate command")
    say("  1) Tes koneksi endpoint")
    say("  2) Tes satu model")
    say("  3) Generate command untuk client")
    choice = ask("Pilihan", "1")
    want_json = in_json_mode()
    if choice == "1":
        node = pick_endpoint(nr)
        if node is None:
            return
        connection = pick_connection(nr, node)
        if connection is None:
            return
        do_test(nr, plat, str(connection.get("id")), want_json)
        return
    if choice == "2":
        model = ask("Model (fullModel)")
        result = op_test_model(nr, model, "llm")
        say(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if choice == "3":
        node = pick_endpoint(nr)
        if node is None:
            return
        do_generate(nr, plat, str(node.get("id")), want_json)
        return
    warn("Pilihan tidak dikenal.")


def menu_backup(nr: NineRouter, plat: PlatformInfo, args: argparse.Namespace) -> None:
    header("Backup & restore")
    files = list_backup_files(plat)
    show(render_table(["file", "waktu", "ukuran"],
                      [[b["file"], b["created"], b["bytes"]] for b in
                       [_backup_entry(p) for p in files]]))
    say("")
    say("  1) Buat backup sekarang")
    say("  2) Restore dari backup")
    choice = ask("Pilihan", "1")
    if choice == "1":
        path = make_backup(nr, plat, "manual dari menu")
        ok("Backup: %s" % path)
        return
    if choice == "2":
        if not files:
            warn("Belum ada backup.")
            return
        ref = ask("Nama file backup")
        target = None
        for path in files:
            if path.name == ref:
                target = path
                break
        if target is None:
            warn("File tidak ditemukan.")
            return
        if not confirm("Restore config 9router dari %s? Config sekarang akan di-backup dulu." % target.name):
            warn("Dibatalkan.")
            return
        make_backup(nr, plat, "sebelum restore")
        restore_backup(nr, plat, target)
        ok("Restore selesai dari %s" % target.name)
        return
    warn("Pilihan tidak dikenal.")


def menu_loop(nr: NineRouter, plat: PlatformInfo, creds: Credentials,
              args: argparse.Namespace) -> int:
    want_json = in_json_mode()
    while True:
        server = nr.base_url
        say("")
        say(paint("9router-manager %s" % VERSION, Ansi.BOLD + Ansi.CYAN) +
            paint("   server: %s" % server, Ansi.DIM))
        say(MENU_TEXT)
        try:
            choice = input("Pilihan: ").strip().lower()
        except EOFError:
            return 0
        try:
            if choice in ("0", "q", "quit", "exit"):
                say("Sampai jumpa.")
                return 0
            if choice == "1":
                menu_add_endpoint(nr, plat, args)
            elif choice == "2":
                do_list(nr, plat, "all", want_json)
            elif choice == "3":
                menu_edit_endpoint(nr, plat, args)
            elif choice == "4":
                say("  1) Tambah API key baru   2) Ganti API key koneksi")
                sub = ask("Pilihan", "1")
                if sub == "2":
                    menu_set_key(nr, plat, args)
                else:
                    menu_add_key(nr, plat, args)
            elif choice == "5":
                menu_delete(nr, plat, args)
            elif choice == "6":
                menu_models(nr, plat, args)
            elif choice == "7":
                menu_router_keys(nr, plat, args)
            elif choice == "8":
                menu_test_generate(nr, plat, args)
            elif choice == "9":
                menu_backup(nr, plat, args)
            elif choice == "i":
                do_info(nr, plat, creds, want_json)
            else:
                warn("Pilihan tidak dikenal.")
        except ApiError as exc:
            err(exc.message)
        except KeyboardInterrupt:
            say("")
            warn("Dibatalkan.")
        if choice not in ("0", "q", "quit", "exit"):
            pause()


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------

EPILOG = """contoh:
  %(prog)s                              # menu interaktif
  %(prog)s info
  %(prog)s list endpoints --json
  %(prog)s add-endpoint "GW Lokal" mygw https://api.example.com/v1
  %(prog)s add-key mygw --api-key-stdin < key.txt
  %(prog)s set-key <connection-id> --api-key sk-baru
  %(prog)s add-model mygw gpt-4o-mini --name "GPT-4o mini" --vision
  %(prog)s delete-endpoint mygw --yes
  %(prog)s backup && %(prog)s restore 9router-20261018-120000.json --yes
"""


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", dest="base_url", default=argparse.SUPPRESS,
                        help="URL server 9router (default: auto-detect http://127.0.0.1:20128)")
    common.add_argument("--port", type=int, default=argparse.SUPPRESS, help="port server 9router")
    common.add_argument("--token", default=argparse.SUPPRESS,
                        help="override token CLI 9router (default: diturunkan dari machine-id + cli-secret)")
    common.add_argument("--password", nargs="?", const="", default=argparse.SUPPRESS,
                        help="password dashboard (tanpa nilai = ditanyakan)")
    common.add_argument("--save-password", action="store_true", default=argparse.SUPPRESS,
                        help="simpan password di state.json (mode 600, plain text)")
    common.add_argument("--data-dir", dest="data_dir", default=argparse.SUPPRESS,
                        help="direktori data 9router (default: ~/.9router atau %%APPDATA%%/9router)")
    common.add_argument("--manager-home", dest="manager_home", default=argparse.SUPPRESS,
                        help="direktori kerja manager (default: ~/.9router-manager)")
    common.add_argument("--timeout", type=int, default=argparse.SUPPRESS, help="timeout HTTP detik")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="keluarkan JSON (aman untuk skrip)")
    common.add_argument("--yes", "-y", action="store_true", default=argparse.SUPPRESS,
                        help="jawab ya untuk semua konfirmasi")
    common.add_argument("--no-backup", action="store_true", default=argparse.SUPPRESS,
                        help="jangan backup otomatis sebelum menulis")
    common.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS,
                        help="matikan warna")

    parser = argparse.ArgumentParser(
        prog=PROGRAM, parents=[common], formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Kelola endpoint custom, API key dan model 9router lewat REST admin API.",
        epilog=EPILOG)
    parser.add_argument("--version", action="version",
                        version="%s %s" % (PROGRAM, VERSION))
    sub = parser.add_subparsers(dest="command", metavar="PERINTAH")

    sub.add_parser("menu", parents=[common], help="menu interaktif (default)")
    sub.add_parser("info", parents=[common], help="info & doctor")

    p = sub.add_parser("list", parents=[common], help="daftar endpoint/koneksi/model")
    p.add_argument("what", nargs="?", default="all",
                   choices=["all", "endpoints", "keys", "models", "combos", "backups"])

    p = sub.add_parser("add-endpoint", parents=[common], help="tambah endpoint custom")
    p.add_argument("name", help="nama tampilan endpoint")
    p.add_argument("prefix", help="prefix model di 9router (contoh: mygw)")
    p.add_argument("url", help="base URL endpoint (contoh: https://api.example.com/v1)")
    p.add_argument("--api-type", dest="api_type", default="chat", choices=["chat", "responses"])
    p.add_argument("--type", dest="node_type", default="openai-compatible",
                   choices=["openai-compatible", "anthropic-compatible", "custom-embedding"])

    p = sub.add_parser("edit-endpoint", parents=[common], help="edit endpoint custom")
    p.add_argument("ref", help="prefix / id / nama endpoint")
    p.add_argument("--name")
    p.add_argument("--prefix")
    p.add_argument("--url", help="base URL baru")
    p.add_argument("--api-type", dest="api_type", choices=["chat", "responses"])

    p = sub.add_parser("delete-endpoint", parents=[common], help="hapus endpoint custom")
    p.add_argument("ref")

    p = sub.add_parser("add-key", parents=[common], help="tambah API key ke endpoint/provider")
    p.add_argument("ref", help="prefix / id / nama endpoint, atau provider bawaan")
    p.add_argument("--api-key", dest="api_key")
    p.add_argument("--api-key-stdin", action="store_true", help="baca API key dari stdin")
    p.add_argument("--name")
    p.add_argument("--priority", type=int, default=1)

    p = sub.add_parser("set-key", parents=[common], help="ganti API key / properti koneksi")
    p.add_argument("ref", help="id atau nama koneksi")
    p.add_argument("--api-key", dest="api_key")
    p.add_argument("--api-key-stdin", action="store_true")
    p.add_argument("--name")
    p.add_argument("--priority", type=int)
    p.add_argument("--default-model", dest="default_model")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--active", dest="is_active", action="store_true", default=None)
    group.add_argument("--inactive", dest="is_active", action="store_false", default=None)

    p = sub.add_parser("delete-key", parents=[common], help="hapus koneksi (API key)")
    p.add_argument("ref")

    p = sub.add_parser("test-connection", parents=[common], help="tes koneksi endpoint")
    p.add_argument("ref")

    p = sub.add_parser("fetch-models", parents=[common], help="ambil daftar model dari endpoint")
    p.add_argument("ref", help="id atau nama koneksi")

    p = sub.add_parser("add-model", parents=[common], help="tambah model custom ke endpoint")
    p.add_argument("ref", help="prefix / id / nama endpoint")
    p.add_argument("model", help="model id di sisi endpoint")
    p.add_argument("--name")
    p.add_argument("--kind", default="llm", help="llm / stt / tts / embedding")
    p.add_argument("--vision", action="store_true", default=None)
    p.add_argument("--reasoning", action="store_true", default=None)

    p = sub.add_parser("delete-model", parents=[common], help="hapus model custom")
    p.add_argument("ref")
    p.add_argument("model")
    p.add_argument("--kind", default="llm")

    p = sub.add_parser("set-alias", parents=[common], help="buat alias model")
    p.add_argument("model")
    p.add_argument("alias")

    p = sub.add_parser("delete-alias", parents=[common], help="hapus alias model")
    p.add_argument("alias")

    p = sub.add_parser("disable-model", parents=[common], help="disable model di endpoint")
    p.add_argument("ref")
    p.add_argument("models", nargs="+")

    p = sub.add_parser("enable-model", parents=[common], help="enable model (kosong = semua)")
    p.add_argument("ref")
    p.add_argument("models", nargs="*")

    p = sub.add_parser("list-keys", parents=[common], help="daftar API key 9router untuk client")

    p = sub.add_parser("add-router-key", parents=[common], help="buat API key 9router")
    p.add_argument("name")

    p = sub.add_parser("delete-router-key", parents=[common], help="hapus API key 9router")
    p.add_argument("ref", help="id atau nama key")

    p = sub.add_parser("toggle-router-key", parents=[common], help="aktif/nonaktifkan API key 9router")
    p.add_argument("ref")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--enable", dest="is_active", action="store_true", default=None)
    group.add_argument("--disable", dest="is_active", action="store_false", default=None)

    p = sub.add_parser("add-combo", parents=[common], help="buat combo/fallback chain")
    p.add_argument("name")
    p.add_argument("models", nargs="+")

    p = sub.add_parser("delete-combo", parents=[common], help="hapus combo")
    p.add_argument("ref", help="id atau nama combo")

    p = sub.add_parser("backup", parents=[common], help="buat backup config 9router")

    p = sub.add_parser("restore", parents=[common], help="restore config dari file backup")
    p.add_argument("file")

    sub.add_parser("list-backups", parents=[common], help="daftar backup lokal")

    p = sub.add_parser("generate", parents=[common], help="cetak command/snippet untuk client")
    p.add_argument("ref", help="prefix / id / nama endpoint")

    return parser


def read_secret_arg(args: argparse.Namespace) -> Optional[str]:
    if getattr(args, "api_key_stdin", False):
        return sys.stdin.read().strip()
    if getattr(args, "api_key", None) is not None:
        return args.api_key
    return ask_secret("API key")


def resolve_endpoint_ref(nr: NineRouter, ref: str) -> Optional[Dict[str, Any]]:
    return find_node(list_nodes(nr), ref)


def maybe_save_password(plat: PlatformInfo, args: argparse.Namespace,
                        creds: Credentials) -> None:
    if not getattr(args, "save_password", False) or not creds.password:
        return
    state = load_state(plat)
    state["password"] = creds.password
    try:
        save_state(plat, state)
        warn("password disimpan plain text di %s (mode 600)" % plat.state_path)
    except Exception as exc:
        warn("gagal menyimpan password: %s" % exc)


MUTATING_COMMANDS = (
    "add-endpoint", "edit-endpoint", "delete-endpoint",
    "add-key", "set-key", "delete-key",
    "add-model", "delete-model", "set-alias", "delete-alias",
    "disable-model", "enable-model",
    "add-router-key", "delete-router-key", "toggle-router-key",
    "add-combo", "delete-combo", "restore",
)


def backup_before(args: argparse.Namespace, nr: NineRouter, plat: PlatformInfo,
                  command: str) -> None:
    """Export the current 9router config before any command that writes."""
    if command not in MUTATING_COMMANDS or getattr(args, "no_backup", False):
        return
    try:
        make_backup(nr, plat, "sebelum %s" % command)
    except ApiError as exc:
        warn("backup gagal: %s" % exc.message)


def run_command(args: argparse.Namespace, plat: PlatformInfo, creds: Credentials,
                nr: NineRouter) -> int:
    command = args.command or ("menu" if sys.stdin.isatty() else None)
    want_json = bool(getattr(args, "json", False))
    yes = bool(getattr(args, "yes", False))

    if command is None:
        build_parser().print_help()
        return 0
    if command == "menu":
        return menu_loop(nr, plat, creds, args)

    backup_before(args, nr, plat, command)

    if command == "info":
        payload = do_info(nr, plat, creds, want_json)
        if want_json:
            print_json(payload)
        return 0

    if command == "list":
        payload = do_list(nr, plat, args.what, want_json)
        if want_json:
            print_json(payload)
        return 0

    if command == "list-backups":
        entries = [_backup_entry(p) for p in list_backup_files(plat)]
        if want_json:
            print_json({"backups": entries})
        else:
            header("Backup lokal")
            show(render_table(["file", "waktu", "ukuran"],
                              [[e["file"], e["created"], e["bytes"]] for e in entries]))
        return 0

    if command == "backup":
        path = make_backup(nr, plat, "manual cli")
        if want_json:
            print_json(_backup_entry(path))
        else:
            ok("Backup: %s" % path)
        return 0

    if command == "restore":
        source = Path(args.file)
        if not source.exists():
            candidate = plat.backups_dir / args.file
            if candidate.exists():
                source = candidate
        if not confirm("Restore config 9router dari %s? Config sekarang akan di-backup dulu." % source, yes):
            warn("Dibatalkan.")
            return 1
        restore_backup(nr, plat, source)
        if want_json:
            print_json({"restored": str(source)})
        else:
            ok("Restore selesai dari %s" % source)
        return 0

    if command == "add-endpoint":
        node = op_add_endpoint(nr, args.name, args.prefix, args.url, args.api_type, args.node_type)
        if want_json:
            print_json(node)
        else:
            ok("Endpoint dibuat: %s (%s)" % (node.get("prefix"), node.get("id")))
            say(render_table(["field", "nilai"], [[k, node.get(k)] for k in
                               ("id", "name", "prefix", "type", "apiType", "baseUrl") if k in node]))
            note("Langkah berikutnya: tambah API key -> add-key %s --api-key ..." % node.get("prefix"))
        return 0

    if command == "edit-endpoint":
        node = resolve_endpoint_ref(nr, args.ref)
        if node is None:
            raise ApiError("endpoint tidak ditemukan: %s" % args.ref)
        updated = op_edit_endpoint(nr, node, args.name, args.prefix, args.url, args.api_type)
        if want_json:
            print_json(updated)
        else:
            ok("Endpoint diperbarui: %s -> %s" % (node.get("prefix"), updated.get("baseUrl")))
            note("prefix/base URL baru ikut berlaku di koneksi yang terikat.")
        return 0

    if command == "delete-endpoint":
        node = resolve_endpoint_ref(nr, args.ref)
        if node is None:
            raise ApiError("endpoint tidak ditemukan: %s" % args.ref)
        if not confirm("Hapus endpoint %s beserta koneksi dan model custom-nya?" % node.get("prefix"), yes):
            warn("Dibatalkan.")
            return 1
        op_delete_endpoint(nr, str(node.get("id")))
        if want_json:
            print_json({"deleted": node.get("id"), "prefix": node.get("prefix")})
        else:
            ok("Endpoint %s dihapus." % node.get("prefix"))
        return 0

    if command == "add-key":
        node = resolve_endpoint_ref(nr, args.ref)
        provider = str(node.get("id")) if node else args.ref
        key = read_secret_arg(args)
        result = op_add_key(nr, provider, key or "", args.name or (node or {}).get("prefix", provider),
                            args.priority)
        if want_json:
            print_json(result)
        else:
            ok("API key ditambahkan untuk %s." % args.ref)
        return 0

    if command == "set-key":
        connection = find_connection(list_connections(nr), args.ref)
        if connection is None:
            raise ApiError("koneksi tidak ditemukan: %s" % args.ref)
        key = None
        if getattr(args, "api_key_stdin", False) or getattr(args, "api_key", None) is not None:
            key = read_secret_arg(args)
        updated = op_update_key(nr, str(connection.get("id")), key, args.name, args.priority,
                                args.is_active, args.default_model)
        if want_json:
            print_json(updated)
        else:
            ok("Koneksi %s diperbarui." % connection.get("id"))
        return 0

    if command == "delete-key":
        connection = find_connection(list_connections(nr), args.ref)
        if connection is None:
            raise ApiError("koneksi tidak ditemukan: %s" % args.ref)
        if not confirm("Hapus koneksi %s (%s)?" % (connection.get("id"), connection.get("name")), yes):
            warn("Dibatalkan.")
            return 1
        op_delete_connection(nr, str(connection.get("id")))
        if want_json:
            print_json({"deleted": connection.get("id")})
        else:
            ok("Koneksi dihapus.")
        return 0

    if command == "test-connection":
        payload = do_test(nr, plat, args.ref, want_json)
        if want_json:
            print_json(payload)
        return 0

    if command == "fetch-models":
        connection = find_connection(list_connections(nr), args.ref)
        if connection is None:
            raise ApiError("koneksi tidak ditemukan: %s" % args.ref)
        models = live_endpoint_models(nr, str(connection.get("id")))
        if want_json:
            print_json({"connection": connection.get("id"), "models": models})
        else:
            header("Model di endpoint %s" % connection.get("id"))
            show(render_table(["model", "nama"],
                              [[m.get("id"), m.get("name") or m.get("id")] for m in models]))
        return 0

    if command == "add-model":
        node = resolve_endpoint_ref(nr, args.ref)
        if node is None:
            raise ApiError("endpoint tidak ditemukan: %s" % args.ref)
        caps = {}
        if args.vision is not None:
            caps["vision"] = bool(args.vision)
        if args.reasoning is not None:
            caps["reasoning"] = bool(args.reasoning)
        result = op_add_model(nr, str(node.get("id")), args.model, args.name or args.model,
                              args.kind, caps or None)
        if want_json:
            print_json(result)
        else:
            ok("Model %s/%s ditambahkan (baru=%s)." % (node.get("prefix"), args.model,
                                                      result.get("added")))
        return 0

    if command == "delete-model":
        node = resolve_endpoint_ref(nr, args.ref)
        if node is None:
            raise ApiError("endpoint tidak ditemukan: %s" % args.ref)
        if not confirm("Hapus model %s dari %s?" % (args.model, node.get("prefix")), yes):
            warn("Dibatalkan.")
            return 1
        op_delete_model(nr, str(node.get("id")), args.model, args.kind)
        if want_json:
            print_json({"deleted": args.model})
        else:
            ok("Model %s dihapus." % args.model)
        return 0

    if command == "set-alias":
        result = op_set_alias(nr, args.model, args.alias)
        if want_json:
            print_json(result)
        else:
            ok("Alias %s -> %s" % (args.alias, args.model))
        return 0

    if command == "delete-alias":
        op_delete_alias(nr, args.alias)
        if want_json:
            print_json({"deleted": args.alias})
        else:
            ok("Alias %s dihapus." % args.alias)
        return 0

    if command == "disable-model":
        node = resolve_endpoint_ref(nr, args.ref)
        if node is None:
            raise ApiError("endpoint tidak ditemukan: %s" % args.ref)
        op_disable_models(nr, str(node.get("id")), args.models)
        if want_json:
            print_json({"disabled": args.models, "provider": node.get("id")})
        else:
            ok("Disabled di %s: %s" % (node.get("prefix"), ", ".join(args.models)))
        return 0

    if command == "enable-model":
        node = resolve_endpoint_ref(nr, args.ref)
        if node is None:
            raise ApiError("endpoint tidak ditemukan: %s" % args.ref)
        op_enable_models(nr, str(node.get("id")), args.models or None)
        if want_json:
            print_json({"enabled": args.models or "all", "provider": node.get("id")})
        else:
            ok("Enable selesai untuk %s." % node.get("prefix"))
        return 0

    if command == "list-keys":
        keys = list_router_keys(nr)
        if want_json:
            print_json({"keys": [{**k, "key": mask_key(str(k.get("key") or ""))} for k in keys]})
        else:
            header("API key 9router")
            show(render_table(["id", "nama", "key", "aktif"],
                              [[k.get("id"), k.get("name"), mask_key(str(k.get("key") or "")),
                                k.get("isActive")] for k in keys]))
        return 0

    if command == "add-router-key":
        result = op_add_router_key(nr, args.name)
        if want_json:
            print_json(result)
        else:
            ok("API key dibuat (tampil sekali saja):")
            say("  id  : %s" % result.get("id"))
            say("  key : %s" % result.get("key"))
        return 0

    if command in ("delete-router-key", "toggle-router-key"):
        keys = list_router_keys(nr)
        target = None
        for key in keys:
            if key.get("id") == args.ref or str(key.get("name", "")).lower() == args.ref.lower():
                target = key
                break
        if target is None:
            raise ApiError("key tidak ditemukan: %s" % args.ref)
        if command == "delete-router-key":
            if not confirm("Hapus API key %s?" % target.get("name"), yes):
                warn("Dibatalkan.")
                return 1
            op_delete_router_key(nr, str(target.get("id")))
            if want_json:
                print_json({"deleted": target.get("id")})
            else:
                ok("API key %s dihapus." % target.get("name"))
            return 0
        is_active = args.is_active
        if is_active is None:
            is_active = not bool(target.get("isActive"))
        op_set_router_key_active(nr, str(target.get("id")), bool(is_active))
        if want_json:
            print_json({"id": target.get("id"), "isActive": bool(is_active)})
        else:
            ok("API key %s -> isActive=%s" % (target.get("name"), bool(is_active)))
        return 0

    if command == "add-combo":
        result = op_add_combo(nr, args.name, args.models)
        if want_json:
            print_json(result)
        else:
            ok("Combo %s dibuat." % args.name)
        return 0

    if command == "delete-combo":
        combos = list_combos(nr)
        target = None
        for combo in combos:
            if combo.get("id") == args.ref or str(combo.get("name", "")).lower() == args.ref.lower():
                target = combo
                break
        if target is None:
            raise ApiError("combo tidak ditemukan: %s" % args.ref)
        if not confirm("Hapus combo %s?" % target.get("name"), yes):
            warn("Dibatalkan.")
            return 1
        op_delete_combo(nr, str(target.get("id")))
        if want_json:
            print_json({"deleted": target.get("id")})
        else:
            ok("Combo dihapus.")
        return 0

    if command == "generate":
        payload = do_generate(nr, plat, args.ref, want_json)
        if want_json:
            print_json(payload)
        return 0

    err("perintah tidak dikenal: %s" % command)
    return 2


def main(argv: Optional[Sequence[str]] = None) -> int:
    _enable_windows_ansi()
    _force_utf8_console()

    raw = list(sys.argv[1:] if argv is None else argv)
    legacy = {"--list": "list", "--info": "info", "--doctor": "info", "--menu": "menu"}
    if raw and raw[0] in legacy:
        raw = [legacy[raw[0]]] + raw[1:]

    parser = build_parser()
    args = parser.parse_args(raw)

    if not getattr(args, "no_color", False) and color_supported():
        set_color_enabled(True)
    else:
        set_color_enabled(False)
    set_json_mode(bool(getattr(args, "json", False)))

    env = dict(os.environ)
    if getattr(args, "data_dir", None):
        env["NINEROUTER_DATA_DIR"] = args.data_dir
    if getattr(args, "manager_home", None):
        env["NINEROUTER_MANAGER_HOME"] = args.manager_home
    plat = PlatformInfo.detect(env)
    plat.ensure_manager_dirs()

    command = args.command or ("menu" if sys.stdin.isatty() else None)
    interactive = command == "menu" and sys.stdin.isatty()
    try:
        creds = resolve_credentials(plat, args, interactive=interactive)
        timeout = int(getattr(args, "timeout", 15) or 15)
        nr = NineRouter(plat, creds, base_url=getattr(args, "base_url", None),
                        port=getattr(args, "port", None), timeout=timeout)
        nr.connect(quiet=bool(getattr(args, "json", False)))
        remember_base_url(plat, nr.base_url)
        maybe_save_password(plat, args, creds)
        return run_command(args, plat, creds, nr)
    except ApiError as exc:
        err(exc.message)
        return 1
    except KeyboardInterrupt:
        warn("Dibatalkan.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
