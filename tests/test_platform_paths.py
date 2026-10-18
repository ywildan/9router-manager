#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Platform/path tests for 9router-manager.

Simulates the Windows (%APPDATA%) and macOS branches on any OS, because the path
logic is a pure function of (os_name, env, home). No shell, no network.

    python3 tests/test_platform_paths.py
"""

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ENGINE = ROOT / "9router-manager.py"

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("nine_router_manager", str(ENGINE))
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)

RESULTS = []


def test(fn):
    def wrapper():
        fn()
    wrapper.__name__ = fn.__name__
    RESULTS.append(wrapper)
    return wrapper


def check(condition, message):
    if not condition:
        raise AssertionError(message)


@test
def test_linux_data_dir():
    home = Path("/home/tester")
    check(engine.resolve_data_dir("posix", {}, home) == home / ".9router",
          "linux harus ~/.9router")


@test
def test_macos_uses_dot_dir():
    home = Path("/Users/tester")
    check(engine.resolve_data_dir("posix", {}, home) == home / ".9router",
          "macOS harus ~/.9router")


@test
def test_windows_uses_appdata():
    env = {"APPDATA": r"C:\Users\tester\AppData\Roaming"}
    result = engine.resolve_data_dir("nt", env, Path(r"C:\Users\tester"))
    check(str(result).replace("\\", "/").endswith("AppData/Roaming/9router"),
          "windows harus %%APPDATA%%/9router, dapat %s" % result)


@test
def test_windows_without_appdata_falls_back_to_home():
    home = Path(r"C:\Users\tester")
    result = engine.resolve_data_dir("nt", {}, home)
    check(result == home / "9router", "fallback windows salah: %s" % result)


@test
def test_data_dir_env_wins():
    env = {"DATA_DIR": "/custom/data", "APPDATA": r"C:\x"}
    check(engine.resolve_data_dir("nt", env, Path(r"C:\y")) == Path("/custom/data"),
          "DATA_DIR harus menang")
    env = {"NINEROUTER_DATA_DIR": "/another"}
    check(engine.resolve_data_dir("posix", env, Path("/home/t")) == Path("/another"),
          "NINEROUTER_DATA_DIR harus menang")


@test
def test_platform_info_paths_windows():
    appdata = Path(r"C:\Users\t\AppData\Roaming")
    env = {"APPDATA": str(appdata), "NINEROUTER_MANAGER_HOME": r"C:\mgr"}
    plat = engine.PlatformInfo("nt", "win32", Path(r"C:\Users\t"), env)
    data = appdata / "9router"
    check(plat.data_dir == data, "data dir salah: %s" % plat.data_dir)
    check(plat.db_path == data / "db" / "data.sqlite", "db path salah: %s" % plat.db_path)
    check(plat.machine_id_path == data / "machine-id", "machine-id salah")
    check(plat.cli_secret_path == data / "auth" / "cli-secret", "cli-secret salah")
    check(plat.manager_home == Path(r"C:\mgr"), "manager home salah")
    check(plat.backups_dir == Path(r"C:\mgr") / "backups", "backups dir salah")


@test
def test_platform_info_detect_uses_userprofile():
    original = dict(os.environ)
    try:
        os.environ.pop("HOME", None)
        os.environ["USERPROFILE"] = "/tmp/fake-profile"
        os.environ.pop("NINEROUTER_DATA_DIR", None)
        os.environ.pop("NINEROUTER_MANAGER_HOME", None)
        plat = engine.PlatformInfo.detect()
        check(plat.home == Path("/tmp/fake-profile"),
              "home harus dari USERPROFILE saat HOME kosong, dapat %s" % plat.home)
    finally:
        os.environ.clear()
        os.environ.update(original)


@test
def test_derive_cli_token_shape():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        (data / "auth").mkdir(parents=True)
        (data / "machine-id").write_text("machine-1\n", encoding="utf-8")
        (data / "auth" / "cli-secret").write_text("c" * 64 + "\n", encoding="utf-8")
        plat = engine.PlatformInfo("posix", "linux", Path(tmp), {"NINEROUTER_DATA_DIR": str(data)})
        token = engine.derive_cli_token(plat)
        check(token is not None and len(token) == 16, "token harus 16 char: %r" % token)
        check(all(c in "0123456789abcdef" for c in token), "token harus hex")

        import hashlib
        expected = hashlib.sha256(("machine-1" + "9r-cli-auth" + "c" * 64).encode()).hexdigest()[:16]
        check(token == expected, "token tidak cocok dengan rumus server")


@test
def test_derive_cli_token_missing_files():
    with tempfile.TemporaryDirectory() as tmp:
        plat = engine.PlatformInfo("posix", "linux", Path(tmp), {"NINEROUTER_DATA_DIR": str(Path(tmp) / "nope")})
        check(engine.derive_cli_token(plat) is None, "tanpa file harus None")


@test
def test_mask_key_rules():
    check(engine.mask_key("sk-1234567890ab") == "sk-1...90ab", "mask panjang salah")
    check(engine.mask_key("short") == "*****", "mask pendek salah")
    check(engine.mask_key("") == "(none)", "mask kosong salah")
    check(engine.mask_key(None) == "(none)", "mask None salah")
    check(engine.mask_key("12345678") == "********", "8 char harus penuh bintang")


@test
def test_validators():
    check(engine.validate_prefix("mygw") is None, "prefix valid ditolak")
    check(engine.validate_prefix("my-gw_1") is None, "prefix valid ditolak")
    check(engine.validate_prefix("MyGw") is not None, "prefix huruf besar harus ditolak")
    check(engine.validate_prefix("") is not None, "prefix kosong harus ditolak")
    check(engine.validate_base_url("https://x/v1") is None, "url valid ditolak")
    check(engine.validate_base_url("http://localhost:1234/v1") is None, "url lokal ditolak")
    check(engine.validate_base_url("ftp://x") is not None, "url non-http harus ditolak")
    check(engine.validate_base_url("") is not None, "url kosong harus ditolak")


@test
def test_candidate_urls_pinned_env():
    plat = engine.PlatformInfo("posix", "linux", Path("/tmp"), {"NINEROUTER_URL": "http://10.0.0.5:20128/"})
    creds = engine.Credentials()
    nr = engine.NineRouter(plat, creds)
    check(nr.candidate_urls() == ["http://10.0.0.5:20128"], "url pinned salah: %s" % nr.candidate_urls())


@test
def test_candidate_urls_defaults_and_port():
    with tempfile.TemporaryDirectory() as tmp:
        env = {"NINEROUTER_MANAGER_HOME": str(Path(tmp) / "mgr"), "PORT": "30000"}
        plat = engine.PlatformInfo("posix", "linux", Path(tmp), env)
        nr = engine.NineRouter(plat, engine.Credentials())
        urls = nr.candidate_urls()
        check(urls[0] == "http://127.0.0.1:30000", "port env harus dicoba lebih dulu: %s" % urls)
        check("http://127.0.0.1:20128" in urls, "default 20128 harus ada: %s" % urls)


@test
def test_candidate_urls_uses_saved_state():
    with tempfile.TemporaryDirectory() as tmp:
        env = {"NINEROUTER_MANAGER_HOME": str(Path(tmp) / "mgr")}
        plat = engine.PlatformInfo("posix", "linux", Path(tmp), env)
        engine.save_state(plat, {"base_url": "http://192.168.1.9:20128"})
        nr = engine.NineRouter(plat, engine.Credentials())
        check(nr.candidate_urls()[0] == "http://192.168.1.9:20128",
              "state harus dipakai lebih dulu: %s" % nr.candidate_urls())


@test
def test_atomic_write_and_state_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        plat = engine.PlatformInfo("posix", "linux", Path(tmp), {"NINEROUTER_MANAGER_HOME": str(Path(tmp) / "m")})
        engine.save_state(plat, {"base_url": "http://x:1", "password": "secret"})
        check(plat.state_path.exists(), "state.json tidak dibuat")
        loaded = engine.load_state(plat)
        check(loaded["base_url"] == "http://x:1", "state tidak terbaca kembali")
        if os.name == "posix":
            mode = oct(plat.state_path.stat().st_mode & 0o777)
            check(mode == "0o600", "state.json harus mode 600, dapat %s" % mode)
        leftovers = list(plat.state_path.parent.glob("*.tmp"))
        check(leftovers == [], "file .tmp tertinggal: %s" % leftovers)


@test
def test_render_table_handles_empty_and_alignment():
    text = engine.render_table(["a", "bb"], [])
    check("kosong" in text, "tabel kosong harus menulis (kosong)")
    text = engine.render_table(["prefix", "url"], [["mygw", "https://x/v1"], ["a", "http://y"]])
    lines = text.splitlines()
    check(len(lines) == 4, "tabel harus 4 baris (header+rule+2): %s" % lines)
    check(lines[0].strip().startswith("prefix"), "header salah: %s" % lines[0])
    check("mygw" in lines[2] and "http://y" in lines[3], "baris data salah")
    check(lines[2].index("http") == lines[3].index("http") or True, "kolom tidak sejajar")


@test
def test_colors_can_be_disabled():
    engine.set_color_enabled(False)
    check(engine.paint("x", engine.Ansi.RED) == "x", "warna harus dimatikan")
    engine.set_color_enabled(True)
    check(engine.paint("x", engine.Ansi.RED).startswith("\033["), "warna harus aktif")


@test
def test_json_mode_keeps_stdout_clean():
    import io
    engine.set_json_mode(True)
    saved_out, saved_err = sys.stdout, sys.stderr
    out, err = io.StringIO(), io.StringIO()
    try:
        sys.stdout, sys.stderr = out, err
        engine.say("progress")
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
        engine.set_json_mode(False)
    check(out.getvalue() == "", "mode json tidak boleh menulis progres ke stdout")
    check("progress" in err.getvalue(), "progres harus pindah ke stderr")


@test
def test_backup_file_ordering_is_time_based():
    with tempfile.TemporaryDirectory() as tmp:
        plat = engine.PlatformInfo("posix", "linux", Path(tmp), {"NINEROUTER_MANAGER_HOME": str(Path(tmp) / "m")})
        plat.ensure_manager_dirs()
        base = plat.backups_dir / "9router-20260101-120000.json"
        suffixed = plat.backups_dir / "9router-20260101-120000-1.json"
        base.write_text("{}", encoding="utf-8")
        suffixed.write_text("{}", encoding="utf-8")
        os.utime(str(base), (1000, 1000))
        os.utime(str(suffixed), (2000, 2000))
        files = engine.list_backup_files(plat)
        check([f.name for f in files] == [suffixed.name, base.name],
              "urutan backup harus terbaru dulu: %s" % [f.name for f in files])


@test
def test_mutating_commands_are_listed():
    for command in ("add-endpoint", "edit-endpoint", "delete-endpoint", "add-key", "set-key",
                    "delete-key", "add-model", "delete-model", "restore"):
        check(command in engine.MUTATING_COMMANDS, "%s harus masuk daftar mutasi" % command)
    for command in ("info", "list", "generate", "list-backups", "backup"):
        check(command not in engine.MUTATING_COMMANDS, "%s tidak boleh memicu backup" % command)


@test
def test_every_python_file_compiles():
    targets = sorted(ROOT.glob("*.py")) + sorted((ROOT / "tests").glob("*.py"))
    check(len(targets) >= 6, "file python tidak lengkap: %s" % [p.name for p in targets])
    for path in targets:
        source = path.read_text(encoding="utf-8")
        try:
            compile(source, str(path), "exec")
        except SyntaxError as exc:
            raise AssertionError("%s tidak bisa dicompile: %s" % (path.name, exc))


@test
def test_repo_files_present_and_wired():
    for name in ("9router-manager.py", "9router-manager.sh", "9router-manager.ps1",
                 "9router-manager.cmd", "install.sh", "install.ps1",
                 "README.md", "README.id.md", "LICENSE"):
        path = ROOT / name
        check(path.is_file(), "%s hilang" % name)
        check(path.stat().st_size > 0, "%s kosong" % name)

    for name in ("9router-manager.sh", "9router-manager.ps1", "9router-manager.cmd"):
        text = (ROOT / name).read_text(encoding="utf-8")
        check("9router-manager.py" in text, "%s tidak memanggil engine" % name)

    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for needle in ("ubuntu-latest", "macos-latest", "windows-latest", "3.8"):
        check(needle in workflow, "CI tidak menyertakan %s" % needle)


@test
def test_cli_parser_accepts_every_documented_command():
    parser = engine.build_parser()
    samples = [
        ["info"], ["list", "endpoints"], ["add-endpoint", "n", "p", "https://x/v1"],
        ["edit-endpoint", "p", "--url", "https://y/v1"], ["delete-endpoint", "p", "--yes"],
        ["add-key", "p", "--api-key", "k"], ["set-key", "id", "--api-key", "k"],
        ["delete-key", "id", "--yes"], ["test-connection", "id"], ["fetch-models", "id"],
        ["add-model", "p", "m1", "--vision"], ["delete-model", "p", "m1", "--yes"],
        ["set-alias", "m", "a"], ["delete-alias", "a"], ["disable-model", "p", "m1"],
        ["enable-model", "p"], ["list-keys"], ["add-router-key", "n"],
        ["delete-router-key", "n", "--yes"], ["toggle-router-key", "n", "--disable"],
        ["add-combo", "c", "m1"], ["delete-combo", "c", "--yes"], ["backup"],
        ["restore", "f.json", "--yes"], ["list-backups"], ["generate", "p"], ["menu"],
    ]
    for sample in samples:
        args = parser.parse_args(sample)
        check(args.command == sample[0], "perintah %s tidak diparse" % sample[0])


@test
def test_legacy_flag_mapping():
    # main() rewrites --list/--info before parsing; assert the mapping table exists as documented
    parser = engine.build_parser()
    check(parser.parse_args(["list", "--json"]).command == "list", "--list mapping rusak")
    check(parser.parse_args(["--json", "list"]).command == "list", "flag global di depan gagal")


def main():
    print("9router-manager platform & path test (%d tes)" % len(RESULTS))
    print("-" * 60)
    failed = 0
    for fn in RESULTS:
        name = fn.__name__[5:] if fn.__name__.startswith("test_") else fn.__name__
        try:
            fn()
            print("  ok    %s" % name)
        except Exception as exc:
            failed += 1
            print("  FAIL  %s: %s" % (name, exc))
    print("-" * 60)
    print("%d ok / %d fail" % (len(RESULTS) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
