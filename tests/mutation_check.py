#!/usr/bin/env python3
"""Prove the rectify-names tests actually defend the gate.

    python3 tests/mutation_check.py

For each mechanism in gate/rectify_names.py: remove it, run the suite, and
require the suite to go RED. A test that still passes with the mechanism gone is
not testing the mechanism — it is decoration that reports green forever.

The list covers both halves of the gate. The six checks are the obvious half:
delete one and the defect it catches must go unreported. The five exemptions are
the half people forget — the local-scratch carve-out, `__all__`, the private
underscore, mutually exclusive branches, the allowlist. Those are what keep this
gate installable, and an untested exemption is exactly as hollow as an untested
check.

Exit 0 when every mutant was killed; 1 when any survived; 2 when this script
itself could not run (the same contract as the gate).

The file is restored from an IN-MEMORY copy in a `finally`, never with
`git checkout`: this repo may hold uncommitted work, and a checkout to undo a
mutation would take that work with it. The restore is then verified.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "gate" / "rectify_names.py"

# (name, the exact source to neuter, what to put in its place)
MUTANTS = [
    # --- the six checks ---
    ("CHECK 1 mutating-accessor", "check_mutating_accessor(sym, findings)", "pass"),
    ("CHECK 2 non-boolean-predicate", "check_boolean_predicate(sym, findings)", "pass"),
    ("CHECK 3 async-suffix-mismatch", "check_async_suffix(sym, findings)", "pass"),
    ("CHECK 4 vacuous-name", "check_vacuous_name(sym, findings)", "pass"),
    ("CHECK 5 plural/singular", "check_plurality(sym, findings)", "pass"),
    ("CHECK 6 constant-reassigned",
     "check_constant_reassignment(tree, rel, findings)", "pass"),

    # --- the exemptions that make it survivable ---
    ("EXEMPTION local scratch is not state",
     "        return root not in local", "        return True"),
    ("EXEMPTION __all__ narrows the surface",
     "        return name in exported", "        return True"),
    ("EXEMPTION a leading underscore is private",
     '    return not name.startswith("_")', "    return True"),
    ("EXEMPTION exclusive branches are not reassignment",
     "    return any(node in other and other[node] != branch for node, branch in left)",
     "    return False"),
    ("EXEMPTION the allowlist suppresses",
     "    return any(p.fullmatch(subject) for p in patterns)", "    return False"),
]


def run_suite() -> bool:
    """True when the suite is green."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(ROOT / "tests"), "-q", "-x", "--no-header"],
        capture_output=True, text=True, cwd=ROOT, check=False)
    return result.returncode == 0


def main() -> int:
    original = GATE.read_text(encoding="utf-8")

    if not run_suite():
        print("the suite is RED before any mutation — fix that first", file=sys.stderr)
        return 2

    survivors: list[str] = []
    try:
        for name, source, replacement in MUTANTS:
            if original.count(source) != 1:
                print(f"  ??  {name}: {original.count(source)} match(es) in the gate — "
                      f"the mutation list is stale")
                survivors.append(f"{name} (stale)")
                continue
            GATE.write_text(original.replace(source, replacement, 1), encoding="utf-8")
            if run_suite():
                print(f"  SURVIVED  {name} — removed it and the suite stayed green")
                survivors.append(name)
            else:
                print(f"  killed    {name}")
    finally:
        GATE.write_text(original, encoding="utf-8")

    # The restore itself is verified. A mutation runner that leaves the file
    # mutated has done more harm than the bug it was hunting.
    if GATE.read_text(encoding="utf-8") != original:
        print("gate file was NOT restored cleanly", file=sys.stderr)
        return 2

    if survivors:
        print(f"\n{len(survivors)} mutant(s) survived: {', '.join(survivors)}")
        return 1
    print(f"\nall {len(MUTANTS)} mutants killed; gate restored and verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
