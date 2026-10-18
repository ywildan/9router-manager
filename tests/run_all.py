#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run every 9router-manager test suite and aggregate the result.

    python3 tests/run_all.py
    python3 tests/run_all.py --no-network-suites   # only the offline platform tests
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITES = [
    ("platform & paths", HERE / "test_platform_paths.py", False),
    ("smoke (mock 9router)", HERE / "smoke_test.py", True),
]


def main():
    offline_only = "--no-network-suites" in sys.argv
    failed_suites = []
    for name, script, needs_network in SUITES:
        if offline_only and needs_network:
            print("== %s: dilewati (--no-network-suites)" % name)
            continue
        print("== %s: %s" % (name, script.name))
        proc = subprocess.run([sys.executable, str(script)])
        if proc.returncode != 0:
            failed_suites.append(name)
        print("")
    if failed_suites:
        print("[x] suite gagal: %s" % ", ".join(failed_suites))
        return 1
    print("[ok] semua suite lulus")
    return 0


if __name__ == "__main__":
    sys.exit(main())
