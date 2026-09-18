"""M0 基础层自检（不依赖 pytest）：python scripts/m0_selftest.py

在 Windows 与 FreeBSD (/data/dsh/venv/bin/python) 均可直接运行。
按模块收集 test_* 函数并逐个执行，输出 PASS/FAIL 汇总。
"""
import importlib.util
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MODULES = ["tests.test_db", "tests.test_llm", "tests.test_auth", "tests.test_workflow",
           "tests.test_workflow_m1"]


def load_module(name: str):
    path = os.path.join(ROOT, name.replace(".", os.sep) + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    total = passed = failed = 0
    for mod_name in MODULES:
        mod = load_module(mod_name)
        tests = sorted(
            (n, f) for n, f in vars(mod).items()
            if n.startswith("test_") and callable(f)
        )
        print(f"\n== {mod_name} ({len(tests)} tests) ==")
        for name, fn in tests:
            total += 1
            try:
                fn()
                passed += 1
                print(f"  PASS {name}")
            except Exception:
                failed += 1
                print(f"  FAIL {name}")
                traceback.print_exc()
    print(f"\n结果: {passed}/{total} 通过, {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
