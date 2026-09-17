#!/usr/bin/env python3
"""Run the live hook once against a planted payload, the way the runtime would.

    python3 tests/live_hook_smoke.py tests/fixtures/planted-edit.json mutating-accessor

Reads a `PreToolUse` payload from the JSON fixture, materialises the fixture's
`scratch_files` in a fresh scratch directory (the file the Edit is about to
change, as it is on disk BEFORE the edit), fills `cwd` with that directory,
and pipes the payload — without `scratch_files`, which is not part of the
runtime's shape — to hooks/rectify-names-before-write.py exactly as Claude
Code would. It requires three things: exit 0, a
`hookSpecificOutput.additionalContext` that names the expected check, and no
`permissionDecision` — warning mode never touches the permission flow. Exit 0
when all three hold, 1 otherwise, printing what came back.

The payload and the planted file live in a JSON fixture rather than in this
file on purpose: this repository's own gate reads every Python file and
would, correctly, report a planted `get_user()` that empties a cache if it
were written here as code.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

HOOK = pathlib.Path(__file__).resolve().parent.parent / "hooks" / "rectify-names-before-write.py"


def main(fixture: str, check: str) -> int:
    payload = json.loads(pathlib.Path(fixture).read_text(encoding="utf-8"))
    planted = payload.pop("scratch_files", {})
    with tempfile.TemporaryDirectory() as scratch:
        for rel, body in planted.items():
            target = pathlib.Path(scratch) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        payload["cwd"] = scratch
        env = {**os.environ, "RECTIFY_NAMES_RECEIPTS": "off"}
        env.pop("RECTIFY_NAMES_HOOK_MODE", None)
        r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                           capture_output=True, text=True, env=env, cwd=scratch,
                           check=False, timeout=60)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    context = (out.get("hookSpecificOutput") or {}).get("additionalContext") or ""
    if r.returncode != 0:
        print(f"exit {r.returncode}, expected 0: {r.stderr.strip()[:300]}")
        return 1
    if f"[{check}]" not in context:
        print(f"no [{check}] in the warning: {out!r}")
        return 1
    if "permissionDecision" in (out.get("hookSpecificOutput") or {}):
        print(f"warning mode touched the permission flow: {out!r}")
        return 1
    print(f"planted [{check}] was warned about and not blocked")
    print(context)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
