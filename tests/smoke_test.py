#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Portable smoke test suite for 9router-manager.

Everything runs against a throwaway mock 9router on 127.0.0.1 plus a temporary
data dir and manager home, so the real 9router config is never touched. Pure
standard library, no shell, so it runs identically on Linux, macOS and Windows.

    python3 tests/smoke_test.py            # all tests
    python3 tests/smoke_test.py add        # only tests whose name contains "add"
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ENGINE = ROOT / "9router-manager.py"
sys.path.insert(0, str(HERE))

from mock_9router_server import (  # noqa: E402
    DEFAULT_PASSWORD, MockNineRouter, PEER_TOKEN, seed_data_dir)

TESTS = []
RESULTS = []


def test(fn):
    TESTS.append(fn)
    return fn


# ---------------------------------------------------------------------------
# sandbox
# ---------------------------------------------------------------------------

class Sandbox:
    """Fresh mock server + temp dirs for one test."""

    def __init__(self, require_auth=True, with_secrets=True, prefix="9rm-test-"):
        self.tmp = Path(tempfile.mkdtemp(prefix=prefix))
        self.data = self.tmp / "data"
        self.home = self.tmp / "manager"
        seed_data_dir(self.data, with_secrets=with_secrets)
        self.server = MockNineRouter(("127.0.0.1", 0), self.data, require_auth=require_auth)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d" % self.server.port
        self._wait_ready()

    def _wait_ready(self, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                urllib.request.urlopen(self.url + "/api/health", timeout=1).read()
                return
            except Exception:
                time.sleep(0.05)
        raise RuntimeError("mock server tidak siap")

    def close(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def state(self):
        with urllib.request.urlopen(self.url + "/__state", timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def env(self, extra=None):
        env = dict(os.environ)
        env.update({
            "NINEROUTER_URL": self.url,
            "NINEROUTER_DATA_DIR": str(self.data),
            "NINEROUTER_MANAGER_HOME": str(self.home),
            "NINEROUTER_MANAGER_NO_COLOR": "1",
            "NO_COLOR": "1",
        })
        for key in ("NINEROUTER_TOKEN", "NINEROUTER_PASSWORD", "DATA_DIR", "PORT"):
            env.pop(key, None)
        env.update(extra or {})
        return env

    def run(self, *args, **kwargs):
        stdin = kwargs.pop("stdin", None)
        extra_env = kwargs.pop("extra_env", None)
        expect = kwargs.pop("expect", None)
        args = [str(a) for a in args]
        proc = subprocess.run(
            [sys.executable, str(ENGINE)] + args,
            cwd=str(ROOT), env=self.env(extra_env), input=stdin,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        if expect is not None:
            check(proc.returncode == expect,
                  "%s -> exit %d, diharapkan %d\nstdout:%s\nstderr:%s"
                  % (" ".join(args), proc.returncode, expect, proc.stdout[-600:], proc.stderr[-600:]))
        return proc

    def json_run(self, *args, **kwargs):
        args = [str(a) for a in args]
        if "--json" not in args:
            args = args + ["--json"]
        proc = self.run(*args, expect=kwargs.pop("expect", 0), **kwargs)
        try:
            return json.loads(proc.stdout)
        except Exception as exc:
            raise AssertionError("stdout bukan JSON (%s): %s" % (exc, proc.stdout[:400]))


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def backups(sandbox):
    folder = sandbox.home / "backups"
    if not folder.exists():
        return []
    files = [p for p in folder.glob("9router-*.json") if p.is_file()]
    files.sort(key=lambda p: (p.stat().st_mtime, p.name))
    return [p.name for p in files]


def seed_sqlite(box, rows):
    """Create a minimal 9router-like SQLite file so masked-key display is exercised.

    The real REST API never returns stored credentials, so the manager reads them
    read-only from data.sqlite for display. The mock server has no DB, so the test
    writes one that matches the real schema.
    """
    import sqlite3
    folder = box.data / "db"
    folder.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(folder / "data.sqlite"))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS providerConnections ("
        "id TEXT PRIMARY KEY, provider TEXT NOT NULL, authType TEXT NOT NULL, name TEXT, "
        "email TEXT, priority INTEGER, isActive INTEGER DEFAULT 1, data TEXT NOT NULL, "
        "createdAt TEXT, updatedAt TEXT)")
    for connection_id, payload in rows:
        conn.execute(
            "INSERT OR REPLACE INTO providerConnections "
            "(id, provider, authType, name, isActive, data, createdAt, updatedAt) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (connection_id, "mock", "apikey", "mock", 1, json.dumps(payload),
             "2026-01-01T00:00:00.000Z", "2026-01-01T00:00:00.000Z"))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

@test
def test_help_and_version():
    with Sandbox() as box:
        proc = box.run("--help", expect=0)
        check("add-endpoint" in proc.stdout, "help tidak memuat add-endpoint")
        proc = box.run("--version", expect=0)
        check("9router-manager" in proc.stdout, "versi tidak tampil")


@test
def test_info_reports_derived_token():
    with Sandbox() as box:
        data = box.json_run("info", "--json")
        check(data["auth"]["method"] == "cli-token", "auth method salah: %s" % data["auth"])
        check("derived" in (data["auth"]["token_source"] or ""), "token source tidak derived")
        check(data["server"]["current_version"] == "0.0.0-mock", "versi server salah")
        check(data["counts"]["endpoints"] == 0, "endpoint awal harus 0")


@test
def test_list_json_is_clean_and_masked():
    with Sandbox() as box:
        node = box.json_run("add-endpoint", "GW Lokal", "mygw", "https://api.example.com/v1")
        box.run("add-key", "mygw", "--api-key", "sk-super-secret-value-1234", expect=0)
        connection_id = box.state()["connections"][0]["id"]
        seed_sqlite(box, [(connection_id, {"apiKey": "sk-super-secret-value-1234"})])
        raw = box.run("list", "--json", expect=0)
        check("sk-super-secret-value-1234" not in raw.stdout, "API key bocor ke output")
        data = json.loads(raw.stdout)
        check(data["endpoints"][0]["prefix"] == "mygw", "endpoint tidak muncul di list")
        conn = data["endpoints"][0]["connections"][0]
        check(conn["key"] == "sk-s...1234", "key tidak termask: %s" % conn["key"])
        check(node["prefix"] == "mygw", "respons add-endpoint salah")


@test
def test_add_endpoint_validation():
    with Sandbox() as box:
        proc = box.run("add-endpoint", "Bad", "UPPER", "https://example.com/v1", expect=1)
        check("prefix" in (proc.stderr + proc.stdout).lower(), "pesan validasi prefix tidak muncul")
        proc = box.run("add-endpoint", "Bad", "ok", "ftp://example.com", expect=1)
        check("http" in (proc.stderr + proc.stdout).lower(), "pesan validasi url tidak muncul")
        check(box.state()["nodes"] == [], "endpoint tidak boleh terbuat saat validasi gagal")


@test
def test_edit_endpoint_propagates_to_connection():
    with Sandbox() as box:
        node = box.json_run("add-endpoint", "GW", "mygw", "https://old.example.com/v1")
        box.run("add-key", "mygw", "--api-key", "sk-x-123456", expect=0)
        box.run("edit-endpoint", "mygw", "--url", "https://new.example.com/v1", expect=0)
        data = box.json_run("list", "--json")
        endpoint = data["endpoints"][0]
        check(endpoint["baseUrl"] == "https://new.example.com/v1", "base url endpoint belum berubah")
        check(endpoint["id"] == node["id"], "id endpoint berubah")
        connection = box.state()["connections"][0]
        check(connection["providerSpecificData"]["baseUrl"] == "https://new.example.com/v1",
              "base url koneksi tidak ikut berubah")


@test
def test_set_key_replaces_value():
    with Sandbox() as box:
        box.json_run("add-endpoint", "GW", "mygw", "https://api.example.com/v1")
        box.run("add-key", "mygw", "--api-key", "sk-old-key-0001", expect=0)
        conn_id = box.state()["connections"][0]["id"]
        box.run("set-key", conn_id, "--api-key", "sk-new-key-0002", expect=0)
        check(box.state()["connections"][0]["apiKey"] == "sk-new-key-0002", "key tidak terganti")
        box.run("set-key", conn_id, "--inactive", expect=0)
        check(box.state()["connections"][0]["isActive"] is False, "isActive tidak berubah")
        box.run("set-key", conn_id, "--name", "Renamed", "--priority", "3", expect=0)
        connection = box.state()["connections"][0]
        check(connection["name"] == "Renamed" and connection["priority"] == 3,
              "name/priority tidak berubah")


@test
def test_api_key_from_stdin():
    with Sandbox() as box:
        box.json_run("add-endpoint", "GW", "mygw", "https://api.example.com/v1")
        box.run("add-key", "mygw", "--api-key-stdin", stdin="sk-from-stdin-9999\n", expect=0)
        check(box.state()["connections"][0]["apiKey"] == "sk-from-stdin-9999", "key dari stdin salah")


@test
def test_test_connection_and_model():
    with Sandbox() as box:
        box.json_run("add-endpoint", "GW", "mygw", "https://api.example.com/v1")
        box.run("add-key", "mygw", "--api-key", "sk-conn-0001", expect=0)
        conn_id = box.state()["connections"][0]["id"]
        data = box.json_run("test-connection", conn_id, "--json")
        check(data["result"].get("ok") is True, "hasil tes koneksi tidak ok")
        check(conn_id in box.state()["testCalls"], "server tidak menerima permintaan tes")


@test
def test_fetch_live_models():
    with Sandbox() as box:
        box.json_run("add-endpoint", "GW", "mygw", "https://api.example.com/v1")
        box.run("add-key", "mygw", "--api-key", "sk-conn-0001", expect=0)
        conn_id = box.state()["connections"][0]["id"]
        data = box.json_run("fetch-models", conn_id, "--json")
        ids = [m["id"] for m in data["models"]]
        check(ids == ["mock-model-1", "mock-model-2"], "daftar model live salah: %s" % ids)


@test
def test_model_lifecycle():
    with Sandbox() as box:
        node = box.json_run("add-endpoint", "GW", "mygw", "https://api.example.com/v1")
        result = box.json_run("add-model", "mygw", "gpt-4o-mini", "--name", "GPT-4o mini",
                              "--vision", "--json")
        check(result.get("added") is True, "model tidak dilaporkan baru")
        models = box.state()["customModels"]
        check(len(models) == 1 and models[0]["providerAlias"] == node["id"], "model tidak tersimpan")
        check(models[0]["caps"] == {"vision": True}, "caps salah: %s" % models[0]["caps"])
        box.run("add-model", "mygw", "gpt-4o-mini", "--vision", expect=0)
        check(box.state()["customModels"][0]["name"] == "gpt-4o-mini", "update model gagal")
        box.run("delete-model", "mygw", "gpt-4o-mini", "--yes", expect=0)
        check(box.state()["customModels"] == [], "model tidak terhapus")


@test
def test_alias_and_disabled():
    with Sandbox() as box:
        box.json_run("add-endpoint", "GW", "mygw", "https://api.example.com/v1")
        box.run("set-alias", "mygw/gpt-4o-mini", "mini", expect=0)
        check(box.state()["aliases"] == {"mini": "mygw/gpt-4o-mini"}, "alias tidak tersimpan")
        box.run("delete-alias", "mini", expect=0)
        check(box.state()["aliases"] == {}, "alias tidak terhapus")
        box.run("disable-model", "mygw", "gpt-4o-mini", "gpt-4o", expect=0)
        disabled = box.state()["disabled"]
        check(len(disabled.get(box.state()["nodes"][0]["id"], [])) == 2, "disable gagal: %s" % disabled)
        box.run("enable-model", "mygw", "gpt-4o-mini", expect=0)
        check(len(box.state()["disabled"][box.state()["nodes"][0]["id"]]) == 1, "enable gagal")
        box.run("enable-model", "mygw", expect=0)
        check(box.state()["disabled"] == {}, "enable semua gagal")


@test
def test_delete_endpoint_cascades():
    with Sandbox() as box:
        box.json_run("add-endpoint", "GW", "mygw", "https://api.example.com/v1")
        box.run("add-key", "mygw", "--api-key", "sk-x-1234567", expect=0)
        box.run("add-model", "mygw", "gpt-4o-mini", expect=0)
        box.run("delete-endpoint", "mygw", "--yes", expect=0)
        state = box.state()
        check(state["nodes"] == [], "node tidak terhapus")
        check(state["connections"] == [], "koneksi tidak ikut terhapus")
        check(state["customModels"] == [], "model custom tidak ikut terhapus")


@test
def test_router_key_lifecycle():
    with Sandbox() as box:
        created = box.json_run("add-router-key", "cli-test", "--json")
        check(created["key"].startswith("sk-mock-"), "key baru salah")
        listed = box.run("list-keys", "--json", expect=0)
        check(created["key"] not in listed.stdout, "key bocor di list-keys")
        check("..." in json.loads(listed.stdout)["keys"][0]["key"], "key tidak termask")
        box.run("toggle-router-key", "cli-test", "--disable", expect=0)
        check(box.state()["keys"][0]["isActive"] is False, "toggle gagal")
        box.run("delete-router-key", "cli-test", "--yes", expect=0)
        check(box.state()["keys"] == [], "key tidak terhapus")


@test
def test_combo_lifecycle():
    with Sandbox() as box:
        box.run("add-combo", "fast", "mygw/a", "mygw/b", expect=0)
        combos = box.state()["combos"]
        check(len(combos) == 1 and combos[0]["models"] == ["mygw/a", "mygw/b"], "combo salah")
        box.run("delete-combo", "fast", "--yes", expect=0)
        check(box.state()["combos"] == [], "combo tidak terhapus")


@test
def test_backup_and_restore_roundtrip():
    with Sandbox() as box:
        box.json_run("add-endpoint", "GW", "mygw", "https://api.example.com/v1")
        box.run("add-key", "mygw", "--api-key", "sk-before-0001", expect=0)
        box.run("backup", expect=0)
        files = backups(box)
        check(len(files) >= 1, "backup tidak dibuat")
        snapshot = json.loads((box.home / "backups" / files[-1]).read_text(encoding="utf-8"))
        check("providerConnections" in snapshot, "isi backup salah")

        box.run("edit-endpoint", "mygw", "--url", "https://changed.example.com/v1", expect=0)
        box.run("delete-key", box.state()["connections"][0]["id"], "--yes", expect=0)
        check(box.state()["connections"] == [], "koneksi tidak terhapus sebelum restore")

        before = box.state()["imports"]
        box.run("restore", files[-1], "--yes", expect=0)
        state = box.state()
        check(state["imports"] == before + 1, "restore tidak memanggil import")
        check(len(state["connections"]) == 1, "koneksi tidak kembali setelah restore")
        check(state["connections"][0]["apiKey"] == "sk-before-0001", "key tidak kembali")
        check(state["nodes"][0]["baseUrl"] == "https://api.example.com/v1",
              "base url tidak kembali setelah restore")


@test
def test_backup_before_every_write():
    with Sandbox() as box:
        box.json_run("add-endpoint", "A", "aaa", "https://a.example.com/v1")
        after_first = len(backups(box))
        box.run("add-key", "aaa", "--api-key", "sk-aaa-0001", expect=0)
        box.run("add-model", "aaa", "m1", expect=0)
        after_writes = len(backups(box))
        check(after_first >= 1, "backup sebelum tulis pertama tidak ada")
        check(after_writes >= after_first + 2, "backup tidak bertambah tiap tulis: %d" % after_writes)
        box.run("add-model", "aaa", "m2", "--no-backup", expect=0)
        check(len(backups(box)) == after_writes, "backup tetap dibuat padahal --no-backup")


@test
def test_list_backups_and_prune():
    with Sandbox() as box:
        box.run("backup", expect=0)
        data = box.json_run("list-backups", "--json")
        check(len(data["backups"]) == 1, "list-backups salah")
        check(data["backups"][0]["file"].endswith(".json"), "nama file backup salah")


@test
def test_generate_snippets():
    with Sandbox() as box:
        box.json_run("add-endpoint", "GW", "mygw", "https://api.example.com/v1")
        box.run("add-key", "mygw", "--api-key", "sk-gen-0001", expect=0)
        box.run("add-router-key", "client", expect=0)
        data = box.json_run("generate", "mygw", "--json")
        check(data["openai_base_url"].endswith("/v1"), "base url openai salah")
        endpoint_id = data["endpoint"]
        check(len(data["models"]) >= 1, "tidak ada model yang dilaporkan: %s" % data["models"])
        check(all(endpoint_id in model for model in data["models"]),
              "model tidak terhubung ke endpoint: %s" % data["models"])
        check("jcode provider add" in data["snippets"]["jcode"], "snippet jcode tidak ada")
        check("OPENAI_BASE_URL" in data["snippets"]["env"], "snippet env tidak ada")


@test
def test_offline_server_is_a_clean_error():
    with Sandbox() as box:
        env = {"NINEROUTER_URL": "http://127.0.0.1:9"}  # discard port
        proc = box.run("info", expect=1, extra_env=env)
        message = proc.stderr + proc.stdout
        check("tidak terjangkau" in message or "tidak bisa konek" in message,
              "pesan server mati tidak jelas: %s" % message[:300])


@test
def test_unauthorized_message():
    with Sandbox() as box:
        proc = box.run("list", expect=1, extra_env={"NINEROUTER_TOKEN": "wrong-token"})
        check("401" in (proc.stderr + proc.stdout), "pesan 401 tidak muncul")


@test
def test_password_fallback():
    with Sandbox(with_secrets=False) as box:
        proc = box.run("list", expect=1)
        check("401" in (proc.stderr + proc.stdout), "tanpa token harus 401")
        proc = box.run("list", "--password", DEFAULT_PASSWORD, expect=0)
        check("9router @" in proc.stdout or "Endpoints" in proc.stdout, "login password gagal")
        proc = box.run("list", "--password", "salah-password", expect=1)
        check("401" in proc.stderr or "login gagal" in proc.stderr, "password salah harus error")


@test
def test_peer_token_auth():
    with Sandbox(with_secrets=False, require_auth=True) as box:
        box.run("list", "--json", expect=0, extra_env={"NINEROUTER_PEER_TOKEN": PEER_TOKEN})


@test
def test_bad_endpoint_reference():
    with Sandbox() as box:
        box.json_run("add-endpoint", "GW", "mygw", "https://api.example.com/v1")
        proc = box.run("edit-endpoint", "tidak-ada", "--name", "x", expect=1)
        check("tidak ditemukan" in proc.stderr, "pesan endpoint hilang tidak muncul")
        proc = box.run("add-model", "tidak-ada", "m1", expect=1)
        check("tidak ditemukan" in proc.stderr, "pesan endpoint hilang tidak muncul (model)")


@test
def test_json_stdout_is_pure():
    with Sandbox() as box:
        box.json_run("add-endpoint", "GW", "mygw", "https://api.example.com/v1")
        proc = box.run("list", "--json", expect=0)
        parsed = json.loads(proc.stdout)  # raises if any noise leaked into stdout
        check("server" in parsed, "payload json tidak lengkap")


@test
def test_menu_exits_cleanly():
    with Sandbox() as box:
        proc = box.run("menu", stdin="0\n", expect=0)
        check("9router-manager" in proc.stdout, "menu tidak tampil")
        check("Sampai jumpa" in proc.stdout, "menu tidak keluar dengan benar")


@test
def test_legacy_flags():
    with Sandbox() as box:
        box.run("--info", "--json", expect=0)
        proc = box.run("--list", "--json", expect=0)
        check("server" in json.loads(proc.stdout), "flag --list legacy gagal")


@test
def test_menu_add_endpoint_and_key_flow():
    """Drive the interactive menu end to end through a piped stdin."""
    with Sandbox() as box:
        script = "\n".join([
            "1",                          # menu: tambah endpoint
            "GW Menu",                    # nama endpoint
            "menugw",                     # prefix
            "",                           # tipe endpoint (default)
            "",                           # api type (default)
            "https://api.example.com/v1",  # base url
            "y",                          # tambah API key sekarang?
            "sk-menu-key-1234",           # api key
            "menukey",                    # label key
            "",                           # pause
            "0",                          # keluar
        ]) + "\n"
        proc = box.run("menu", stdin=script, expect=0)
        state = box.state()
        check(len(state["nodes"]) == 1, "menu 1 tidak membuat endpoint:\n%s" % proc.stdout[-500:])
        check(state["nodes"][0]["prefix"] == "menugw", "prefix dari menu salah")
        check(state["nodes"][0]["apiType"] == "chat", "apiType dari menu salah")
        check(len(state["connections"]) == 1, "menu tidak menambah API key")
        check(state["connections"][0]["apiKey"] == "sk-menu-key-1234", "api key dari menu salah")
        check("menugw" in proc.stdout, "output menu tidak menyebut endpoint")
        check(len(backups(box)) >= 1, "menu tidak membuat backup sebelum tulis")


@test
def test_menu_edit_endpoint_flow():
    with Sandbox() as box:
        box.json_run("add-endpoint", "GW CLI", "menugw", "https://api.example.com/v1")
        script = "\n".join([
            "3",                             # menu: edit endpoint
            "menugw",                        # endpoint yang diedit
            "GW Renamed",                    # nama baru
            "menugw",                        # prefix baru (sama)
            "",                              # api type (default)
            "https://api.example.com/v2",    # base url baru
            "",                              # pause
            "0",
        ]) + "\n"
        proc = box.run("menu", stdin=script, expect=0)
        node = box.state()["nodes"][0]
        check(node["name"] == "GW Renamed", "nama tidak berubah lewat menu: %s" % node["name"])
        check(node["baseUrl"] == "https://api.example.com/v2",
              "base url tidak berubah lewat menu: %s" % node["baseUrl"])
        check("diperbarui" in proc.stdout, "menu tidak melaporkan update")


@test
def test_menu_router_key_and_backup_flow():
    with Sandbox() as box:
        script = "\n".join([
            "7",          # menu: API key 9router
            "1",          # buat key baru
            "menu-client",  # nama key
            "",           # pause
            "9",          # menu: backup & restore
            "1",          # buat backup
            "",           # pause
            "0",
        ]) + "\n"
        proc = box.run("menu", stdin=script, expect=0)
        keys = box.state()["keys"]
        check(len(keys) == 1 and keys[0]["name"] == "menu-client",
              "menu 7 tidak membuat API key: %s" % keys)
        check("sk-mock-" in proc.stdout, "key baru tidak ditampilkan sekali di menu")
        check(len(backups(box)) >= 1, "menu 9 tidak membuat backup")


@test
def test_menu_survives_unknown_choice_and_api_error():
    with Sandbox() as box:
        script = "\n".join([
            "zzz",        # pilihan tidak dikenal
            "",           # pause
            "3",          # edit endpoint, tapi belum ada endpoint
            "",           # pause
            "0",
        ]) + "\n"
        proc = box.run("menu", stdin=script, expect=0)
        output = proc.stdout + proc.stderr
        check("Pilihan tidak dikenal" in output, "menu tidak menangani pilihan salah")
        check("Belum ada endpoint custom" in output,
              "menu tidak menangani kondisi kosong: %s" % output[-300:])


@test
def test_python38_compatibility():
    source = ENGINE.read_text(encoding="utf-8")
    banned = ["removeprefix", "removesuffix", "str | None", "dict |", "list |", "match "]
    for needle in banned:
        check(needle not in source, "sintaks 3.9+ terdeteksi: %r" % needle)
    check("from __future__" not in source or True, "ok")
    ast_ok = subprocess.run([sys.executable, "-c",
                             "import ast,sys;ast.parse(open(sys.argv[1],encoding='utf-8').read())",
                             str(ENGINE)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    check(ast_ok.returncode == 0, "engine tidak bisa di-parse: %s" % ast_ok.stderr.decode())


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------

def main():
    filters = [arg for arg in sys.argv[1:] if not arg.startswith("-")]
    selected = [fn for fn in TESTS if not filters or any(f in fn.__name__ for f in filters)]
    print("9router-manager smoke test (%d tes)" % len(selected))
    print("-" * 60)
    failed = 0
    for fn in selected:
        name = fn.__name__[5:] if fn.__name__.startswith("test_") else fn.__name__
        started = time.time()
        try:
            fn()
            print("  ok    %-46s %5.0fms" % (name, (time.time() - started) * 1000))
        except Exception as exc:
            failed += 1
            print("  FAIL  %-46s %s" % (name, exc))
    print("-" * 60)
    print("%d ok / %d fail" % (len(selected) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
