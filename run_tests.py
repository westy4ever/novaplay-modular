#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NovaPlay test runner — headless, side-effect-free.

Discovers tests/test_*.py, runs every test_* function, reports pass/fail.
NO network. NO state writes. NO enigma (works over SSH while Enigma2 runs).

Usage:
    python3 run_tests.py              # run all
    python3 run_tests.py test_referers  # one module
"""

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR = os.path.join(HERE, "tests")
sys.path.insert(0, HERE)


def _load_module(name):
    import importlib
    return importlib.import_module(name)


def run(only=None):
    if not os.path.isdir(TESTS_DIR):
        print("no tests/ directory")
        return 1
    mods = sorted(f for f in os.listdir(TESTS_DIR)
                  if f.startswith("test_") and f.endswith(".py"))
    if only:
        mods = [m for m in mods if only in m]
    passed = failed = 0
    failures = []
    for fname in mods:
        modname = "tests." + fname[:-3]
        try:
            mod = _load_module(modname)
        except Exception:
            print("IMPORT FAIL {}".format(fname))
            failures.append((fname, "import", traceback.format_exc()))
            failed += 1
            continue
        for attr in sorted(dir(mod)):
            if not attr.startswith("test_"):
                continue
            fn = getattr(mod, attr)
            if not callable(fn):
                continue
            label = "{}.{}".format(fname[:-3], attr)
            try:
                fn()
                print("PASS  {}".format(label))
                passed += 1
            except Exception:
                print("FAIL  {}".format(label))
                failures.append((label, "run", traceback.format_exc()))
                failed += 1
    print("-" * 60)
    print("total: {} passed, {} failed".format(passed, failed))
    for label, kind, tb in failures:
        print("\n--- {} ({}) ---\n{}".format(label, kind, tb[-800:]))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else None))