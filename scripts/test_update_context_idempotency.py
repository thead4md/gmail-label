#!/usr/bin/env python3
"""Test harness for idempotency of scripts/update_context.py.

Tests both paths:
  (A) When CONTEXT.md is already up to date
  (B) When CONTEXT.md has a dirty auto-section that needs repair
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTEXT_PATH = PROJECT_ROOT / "CONTEXT.md"
UPDATER = ["python3", "scripts/update_context.py"]


def run_updater() -> tuple[int, str, str]:
    """Run update_context.py. Returns (returncode, stdout, stderr)."""
    cp = subprocess.run(
        UPDATER,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return cp.returncode, cp.stdout, cp.stderr


def fail(reason: str) -> None:
    print(f"FAIL: {reason}")
    sys.exit(1)


def main() -> None:
    # -- Snapshot original --------------------------------------------------
    try:
        original = CONTEXT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail("CONTEXT.md not found.")
    except Exception as exc:
        fail(f"Cannot read CONTEXT.md: {exc}")

    try:
        # ==================================================================
        # Phase A - already up to date
        # ==================================================================
        print("--- Phase A: already-up-to-date ---")

        rc1, out1, err1 = run_updater()
        if rc1 != 0:
            fail(f"First run (phase A) failed: rc={rc1}\n{err1}")
        after_first = CONTEXT_PATH.read_text(encoding="utf-8")
        first_changed = original != after_first
        print(f"  first run changed : {'yes' if first_changed else 'no'}")

        rc2, out2, err2 = run_updater()
        if rc2 != 0:
            fail(f"Second run (phase A) failed: rc={rc2}\n{err2}")
        after_second = CONTEXT_PATH.read_text(encoding="utf-8")
        second_changed = after_first != after_second
        print(f"  second run changed: {'yes' if second_changed else 'no'}")

        clean_state = after_second

        if second_changed:
            a_lines = after_first.split("\n")
            b_lines = after_second.split("\n")
            for idx in range(max(len(a_lines), len(b_lines))):
                a = a_lines[idx] if idx < len(a_lines) else "<missing>"
                b = b_lines[idx] if idx < len(b_lines) else "<missing>"
                if a != b:
                    print(f"  first diff at line {idx+1}:")
                    print(f"    A: {a[:120]}")
                    print(f"    B: {b[:120]}")
                    break
            fail("Phase A: CONTEXT.md changed on second run (not idempotent)")

        print("  Phase A PASS")

        # ==================================================================
        # Phase B - inject a dirty auto-section, then repair
        # ==================================================================
        print("--- Phase B: dirty-then-repair ---")

        dirty = clean_state.replace(
            "Pass 7 complete.",
            "Pass 7 complete.___GARBAGE_INJECTED_BY_TEST___",
        )
        if dirty == clean_state:
            fail("Phase B: failed to dirty CONTEXT.md (unexpected content)")

        CONTEXT_PATH.write_text(dirty, encoding="utf-8")

        rc3, out3, err3 = run_updater()
        if rc3 != 0:
            CONTEXT_PATH.write_text(original, encoding="utf-8")
            fail(f"First run (phase B) failed: rc={rc3}\n{err3}")
        after_repair = CONTEXT_PATH.read_text(encoding="utf-8")

        if after_repair == dirty:
            fail("Phase B: updater did NOT repair the dirty section")
        if "___GARBAGE_INJECTED_BY_TEST___" in after_repair:
            fail("Phase B: garbage marker still present after run 1")
        print("  run 1 repaired dirty section: yes")

        rc4, out4, err4 = run_updater()
        if rc4 != 0:
            CONTEXT_PATH.write_text(original, encoding="utf-8")
            fail(f"Second run (phase B) failed: rc={rc4}\n{err4}")
        after_second_repair = CONTEXT_PATH.read_text(encoding="utf-8")

        if after_repair != after_second_repair:
            a_lines = after_repair.split("\n")
            b_lines = after_second_repair.split("\n")
            for idx in range(max(len(a_lines), len(b_lines))):
                a = a_lines[idx] if idx < len(a_lines) else "<missing>"
                b = b_lines[idx] if idx < len(b_lines) else "<missing>"
                if a != b:
                    print(f"  first diff at line {idx+1}:")
                    print(f"    A: {a[:120]}")
                    print(f"    B: {b[:120]}")
                    break
            fail("Phase B: repaired output changed on second run (not idempotent)")

        print("  run 2 changed nothing after repair: yes")
        print("  Phase B PASS")

    except Exception as exc:
        fail(f"Unexpected error: {exc}")
    finally:
        CONTEXT_PATH.write_text(original, encoding="utf-8")
        print("Restored original CONTEXT.md")

    print("\nPASS: update_context.py is idempotent (both phases)")
    sys.exit(0)


if __name__ == "__main__":
    main()