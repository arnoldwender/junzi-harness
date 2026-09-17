#!/usr/bin/env python3
"""Prove the live hook's tests actually defend it.

    python3 tests/mutation_check_rectify_names_hook.py

For each mechanism in hooks/rectify-names-before-write.py: neuter it in a
scratch COPY, run the hook's suite against the copy, and require the suite to
go RED. The real hook is never rewritten; its bytes are compared before and
after anyway. The gate's own checks are defended by tests/mutation_check.py;
this runner covers only what the hook adds on top of the gate: the tool
filter, the simulated edit, `replace_all`, the delta against the file on
disk, the fragment rule, the gate's scope, the allowlist and its failure, and
the warning itself.

Exit 0 when every mutant was killed; 1 when any survived; 2 when this script
itself could not run.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "rectify-names-before-write.py"
SUITE = ROOT / "tests" / "test_rectify_names_hook.py"

# (name, exact text to replace, replacement). Each `old` is unique in the file.
MUTANTS = [
    ("TOOLS every tool is judged, not only the three",
     'TOOLS = {"Edit", "Write", "MultiEdit"}\n# mutation-anchor: TOOLS',
     'TOOLS = {"Edit", "Write", "MultiEdit", "Read", "Grep", "Bash", "NotebookEdit"}\n'
     "# mutation-anchor: TOOLS"),
    ("SIMULATE the edit is not simulated; new_string is judged on its own",
     "        if not old or old not in text:", "        if True:"),
    ("REPLACE_ALL only the first occurrence is replaced",
     '        text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)',
     "        text = text.replace(old, new, 1)"),
    ("DELTA every finding is reported, not only what the edit adds",
     "    new = new_findings(prior, now)\n    # mutation-anchor: delta",
     "    new = list(now)\n    # mutation-anchor: delta"),
    ("FRAGMENT a fragment judged alone is called unparseable",
     "    if not simulated:\n        now = [f for f in now if f.check not in FILE_LEVEL]",
     "    if False:\n        now = [f for f in now if f.check not in FILE_LEVEL]"),
    ("SCOPE prose and JSON are judged as code",
     "    if suffix not in ro.PY_SUFFIXES and suffix not in ro.JS_SUFFIXES:", "    if False:"),
    ("ALLOWLIST the working directory's allowlist is ignored",
     "    kept = [f for f in new if not ro._allowed(f, patterns)]\n    # mutation-anchor: allowlist",
     "    kept = list(new)\n    # mutation-anchor: allowlist"),
    ("BAD-ALLOWLIST a malformed allowlist passes in silence",
     "    if scratch:                                            # …and this hook's error, never a pass",
     "    if False:                                              # …and this hook's error, never a pass"),
    ("WARNING a finding produces no text",
     "    if not findings:\n        receipt(verdict=\"ok\", ms=ms, **counts, **common)\n        return 0",
     "    if True:\n        receipt(verdict=\"ok\", ms=ms, **counts, **common)\n        return 0"),
]


def run_suite(hook: Path) -> bool:
    # The mutant lives in a scratch directory with no gate/ beside it: it must still
    # find the REAL gate, or every mutant dies of fail-open and this measures nothing.
    env = {**os.environ, "RECTIFY_NAMES_HOOK_UNDER_TEST": str(hook),
           "RECTIFY_NAMES_GATE": str(ROOT / "gate" / "rectify_names.py")}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(SUITE), "-q", "-x", "--no-header",
         "-p", "no:cacheprovider"],
        capture_output=True, text=True, cwd=ROOT, env=env, check=False)
    return result.returncode == 0


def main() -> int:
    original = HOOK.read_text(encoding="utf-8")
    if not run_suite(HOOK):
        print("the suite is RED before any mutation — fix that first", file=sys.stderr)
        return 2
    survivors: list[str] = []
    with tempfile.TemporaryDirectory() as scratch:
        mutant = Path(scratch) / "rectify-names-before-write.py"
        for name, old, new in MUTANTS:
            if original.count(old) != 1:
                print(f"  ?? {name}: anchor appears {original.count(old)} times — the "
                      f"mutation list is stale, so this script is measuring nothing")
                survivors.append(f"{name} (stale)")
                continue
            mutant.write_text(original.replace(old, new, 1), encoding="utf-8")
            if run_suite(mutant):
                print(f"  SURVIVED  {name} — removed it and the suite stayed green")
                survivors.append(name)
            else:
                print(f"  killed    {name}")
    if HOOK.read_text(encoding="utf-8") != original:
        print("the real hook file changed during the run — it must never be touched",
              file=sys.stderr)
        return 2
    if survivors:
        print(f"\n{len(survivors)} mutant(s) survived: {', '.join(survivors)}")
        return 1
    print(f"\nall {len(MUTANTS)} mutants killed; the real hook was never rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
