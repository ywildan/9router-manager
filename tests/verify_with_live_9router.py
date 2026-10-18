#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end check against a REAL running 9router.

Opt-in: it writes to your 9router config, so it refuses to run without
--allow-writes. It uses a disposable endpoint (prefix 9rm-selftest-<random>),
removes it again, and verifies counts before/after, plus a backup/restore round
trip. Nothing else in your config is modified (except the extra backups under
the manager home).

    python3 tests/verify_with_live_9router.py --allow-writes
    python3 tests/verify_with_live_9router.py --allow-writes --base-url http://10.0.0.5:20128
"""

import argparse
import importlib.util
import json
import random
import string
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ENGINE = ROOT / "9router-manager.py"

spec = importlib.util.spec_from_file_location("nine_router_manager", str(ENGINE))
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)

CHECKS = []


def check(condition, message):
    CHECKS.append((bool(condition), message))
    print("  %s %s" % ("ok  " if condition else "FAIL", message))
    return bool(condition)


def counts(nr, plat):
    return {
        "endpoints": len(engine.list_nodes(nr)),
        "connections": len(engine.list_connections(nr)),
        "custom_models": len(engine.list_custom_models(nr)),
        "router_keys": len(engine.list_router_keys(nr)),
        "combos": len(engine.list_combos(nr)),
    }


def main():
    parser = argparse.ArgumentParser(description="live end-to-end check for 9router-manager")
    parser.add_argument("--allow-writes", action="store_true",
                        help="wajib: tes ini menulis ke config 9router asli (lalu dibersihkan)")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--token", default=None)
    args = parser.parse_args()

    if not args.allow_writes:
        print("Tes ini menulis ke config 9router asli (endpoint sementara, lalu dihapus).")
        print("Jalankan dengan: python3 tests/verify_with_live_9router.py --allow-writes")
        return 2

    plat = engine.PlatformInfo.detect()
    plat.ensure_manager_dirs()
    creds_args = argparse.Namespace(token=args.token, password=args.password)
    creds = engine.resolve_credentials(plat, creds_args, interactive=False)
    nr = engine.NineRouter(plat, creds, base_url=args.base_url)
    nr.connect(quiet=False)

    suffix = "".join(random.choice(string.ascii_lowercase) for _ in range(4))
    prefix = "9rm-selftest-%s" % suffix
    print("9router %s | data dir %s" % (nr.base_url, plat.data_dir))
    print("endpoint disposable: %s" % prefix)

    before = counts(nr, plat)
    print("counts sebelum: %s" % json.dumps(before))
    created_node = None

    try:
        check(engine.derive_cli_token(plat) is not None or creds.password,
              "auth tersedia (token CLI turunan atau password)")

        node = engine.op_add_endpoint(nr, "9rm selftest", prefix, "https://example.invalid/v1")
        created_node = node.get("id")
        check(bool(created_node), "add-endpoint: node dibuat (%s)" % created_node)
        check(engine.find_node(engine.list_nodes(nr), prefix) is not None,
              "endpoint muncul di /api/provider-nodes")

        engine.op_add_key(nr, str(created_node), "sk-selftest-unused", prefix)
        connection = [c for c in engine.list_connections(nr)
                      if str(c.get("provider")) == str(created_node)]
        check(len(connection) == 1, "add-key: koneksi terikat ke endpoint")
        check(all("apiKey" not in c for c in connection),
              "API key tidak dikembalikan oleh REST API (seperti seharusnya)")

        model = engine.op_add_model(nr, str(created_node), "selftest-model", "Self Test", "llm",
                                    {"reasoning": True})
        check(model.get("added") in (True, False), "add-model diterima server")

        updated = engine.op_edit_endpoint(nr, node, base_url="https://example.invalid/v2")
        check(updated.get("baseUrl") == "https://example.invalid/v2",
              "edit-endpoint: base URL berubah (body lengkap diterima)")

        connections_after_edit = [c for c in engine.list_connections(nr)
                                  if str(c.get("provider")) == str(created_node)]
        check(connections_after_edit and
              connections_after_edit[0].get("providerSpecificData", {}).get("baseUrl")
              == "https://example.invalid/v2",
              "edit-endpoint: base URL ikut tersebar ke koneksi")

        generated = engine.do_generate(nr, plat, str(created_node), want_json=True)
        check(generated["openai_base_url"].endswith("/v1"), "generate: base URL OpenAI benar")
        check(any(str(created_node) in m for m in generated["models"]),
              "generate: model custom terdaftar untuk endpoint")

        result = engine.op_test_connection(nr, str(connection[0]["id"]))
        print("      tes koneksi (endpoint dummy) -> %s" % json.dumps(result)[:120])
        check(isinstance(result, dict), "test-connection: server menjawab")

        engine.op_delete_endpoint(nr, str(created_node))
        created_node = None
        after_delete = counts(nr, plat)
        check(after_delete == before, "delete-endpoint: config kembali ke kondisi awal")

        backup_path = engine.make_backup(nr, plat, "verify live")
        check(backup_path.exists(), "backup: file dibuat (%s)" % backup_path.name)
        engine.restore_backup(nr, plat, backup_path)
        after_restore = counts(nr, plat)
        check(after_restore == before, "restore: config identik setelah round trip")

        models_now = engine.model_catalog(nr)
        check(len(models_now) > 0, "katalog model tetap terbaca setelah restore (%d model)"
              % len(models_now))
    finally:
        if created_node:
            print("cleanup: menghapus endpoint sisa %s" % created_node)
            try:
                engine.op_delete_endpoint(nr, str(created_node))
            except Exception as exc:  # noqa: BLE001
                print("  gagal cleanup: %s" % exc)

    failed = [m for ok_flag, m in CHECKS if not ok_flag]
    print("-" * 60)
    print("%d ok / %d fail" % (len(CHECKS) - len(failed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
