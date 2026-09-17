#!/usr/bin/env python3
"""The Junzi Harness — 正名, the rectification of names, before the name lands.

A Claude Code `PreToolUse` hook for `Edit`, `Write` and `MultiEdit`. Before
the tool runs, it hands the file AS IT WILL BE after the edit to the
rectify-names gate (`gate/rectify_names.py`) — the same seven checks, the
same allowlist — and, if the change leaves an exported name that does not
describe what its symbol does, tells the agent so in the tool result. It
warns; it does not block. See hooks/README.md for the wiring and the README
for why.

    "hooks": {"PreToolUse": [{"matcher": "Edit|Write|MultiEdit", "hooks": [
        {"type": "command", "command": "python3 /abs/path/to/junzi-harness/hooks/rectify-names-before-write.py",
         "timeout": 10}]}]}

WHY A HOOK AND NOT ONLY THE GATE
--------------------------------
The gate reads a diff, in CI, after the commit. That is the right place for a
verdict and the wrong place for a correction: by the time it runs, the
`get_user()` that empties a cache has been written, three callers have been
written against the name, and the summary has gone out. Li 禮 · 1 — heal in
passing: fix the defect within reach of the edit you are already making — is
kept or broken at the moment of the edit, and the name is the first thing the
next reader inherits from it. Asked what he would do first if given a state
to govern, Confucius answered: rectify the names (正名, Analects XIII.3). This
hook is the same gate at that moment, on the one file about to change.

WHAT IT DOES
------------
1. Reads the hook payload from stdin: `tool_name`, `tool_input`, `cwd`,
   `session_id`. Ignores every tool but the three above, without a receipt.
   `Bash` is left out on purpose: the gate reads a file by its suffix, and a
   command has none. `NotebookEdit` too: a notebook is JSON.
2. Imports the gate by path with `HARNESS_ROOT` set to the session's working
   directory — BEFORE the import, because the gate fixes its root and its
   allowlist (`.conduct/names-allow.txt`) when it loads. So the allowlist is
   the working repository's, and paths are spelled relative to it, not to
   this repository.
3. Judges only the suffixes the gate judges (`PY_SUFFIXES`, `JS_SUFFIXES` —
   the gate's own constants, one definition). Any other file is skipped, with
   a receipt saying so; `skip` is not the same word as `ok`.
4. SIMULATES the change. `Edit` / `MultiEdit`: the file is read from disk and
   `old_string` is replaced by `new_string` (every occurrence with
   `replace_all`; the edits of a `MultiEdit` in order). `Write`: the content.
   If an `old_string` is not in the file, the tool will refuse the edit on its
   own; what is judged instead is every `new_string`, dedented, on its own,
   and the receipt says `simulated: false`. A fragment is not a module, so in
   that one case the file-level `unparseable` is not raised about it — the
   next edit that does simulate answers whether the FILE parses.
5. Runs the gate's checks — `analyze_text`, the gate's own dispatch — TWICE:
   over the file on disk and over the simulated result. Only the findings the
   result carries and the disk did not are reported (the DELTA). Two findings
   are the same lie when they share the check, the allowlist subject and the
   qualified name the message opens with — never the line, which every edit
   above it moves. A name that already lied before this edit is not this
   edit's debt; the gate's README says the same of a diff. Then the working
   directory's allowlist is applied the way the gate applies it.
6. If anything remains: prints `{"hookSpecificOutput": {"hookEventName":
   "PreToolUse", "additionalContext": "..."}}` and exits 0. Claude Code adds
   that text to the agent's context alongside the tool result. The permission
   flow is not touched. At most four findings are shown; the file name in the
   text is sanitised (backticks, line breaks and control characters become
   `?`), and the whole text stays under 2,000 characters.
7. Appends one receipt line per run to `RECTIFY_NAMES_RECEIPTS` (default
   `~/.local/state/junzi-harness/rectify-names-receipts.jsonl`, under
   `XDG_STATE_HOME` when that is set; `off` disables):
   `{ts, session, tool, path, verdict, checks, findings, judged, debt,
   exempted, ms}` — names of checks and counts, never a line of the file and
   never a symbol's name. `judged` is the number of exported symbols in the
   result; `debt` the findings the file carried before the edit. The receipts
   are how the rate gets measured on real sessions, which is the number this
   hook needs before anyone should let it block.

`judge(tool, tool_input, root)` is importable and returns `(findings,
judged)`, so that rate can be measured over recorded sessions without a
process per tool call.

WHAT IT DOES NOT SEE (stated so they stay decisions)
---------------------------------------------------
* The gate judges one file at a time and resolves nothing across files —
  `__all__`, a class and its methods, a constant and its rebinding all live
  in the module being edited — so the hook needs no more context than the one
  file. What it does not see is the OTHER file: a rename here that makes an
  importer's name a lie is the importer's edit to be warned about.
* A file that does not parse on disk holds no names the gate can read, so
  the edit that makes it parse again is charged with every lie in it, old
  ones included. There was no "before" to subtract.
* An `Edit` whose `old_string` matches more than once without `replace_all`:
  the tool refuses it; the hook judges the first match and says nothing about
  the refusal.
* A file outside the working directory is judged by its absolute path, so an
  allowlist entry anchored on a repository-relative path does not reach it.
* JavaScript/TypeScript gets exactly what the gate gives it: the declaration
  line, two checks, no body. Partial, and the gate says so.
* Any runtime other than Claude Code, and any tool but the three named.

MODES AND FAIL-OPEN
-------------------
`RECTIFY_NAMES_HOOK_MODE=warn` (default) injects the text and exits 0.
`RECTIFY_NAMES_HOOK_MODE=block` writes it to stderr and exits 2, which Claude
Code treats as a denial. Block is shipped so the switch exists; it is not the
default, because a guard whose false-positive rate nobody has measured on
real sessions is switched off by the first person it wrongly stops.
Any error of the hook's own is a receipt with `verdict: error` and exit 0.
A malformed allowlist is such an error — the gate's `bad-allowlist` finding —
never a silent pass and never a warning it did not earn. The hook is never
the reason a session cannot proceed.

Tests: tests/test_rectify_names_hook.py · mutants: tests/mutation_check_rectify_names_hook.py
"""
from __future__ import annotations

import collections
import importlib.util
import json
import os
import pathlib
import re
import sys
import textwrap
import time

HERE = pathlib.Path(__file__).resolve().parent
GATE = pathlib.Path(os.environ.get("RECTIFY_NAMES_GATE") or HERE.parent / "gate" / "rectify_names.py")


def _default_receipts() -> str:
    state = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(state, "junzi-harness", "rectify-names-receipts.jsonl")


RECEIPTS = os.environ.get("RECTIFY_NAMES_RECEIPTS") or _default_receipts()
MODE = os.environ.get("RECTIFY_NAMES_HOOK_MODE", "warn")         # warn | block
TOOLS = {"Edit", "Write", "MultiEdit"}
# mutation-anchor: TOOLS
MAX_SHOWN = 4
MAX_MESSAGE = 260                # per finding; the runtime caps hook output at 10,000
# Findings about the FILE rather than a symbol. Not raised about a fragment judged on
# its own (see WHAT IT DOES, 4): a fragment is not a module, and calling it unparseable
# would be a statement about text that is not the file.
FILE_LEVEL = frozenset({"unparseable", "unreadable"})
# The qualified name a symbol finding opens with: `Owner.name` or `name`, in backticks.
QUALIFIED = re.compile(r"`([^`]+)`")


class HookError(RuntimeError):
    """The hook could not judge. Distinct from the hook returning a verdict."""


# --- receipts ----------------------------------------------------------------

def receipt(**row: object) -> None:
    """One JSON line per run. Check names and counts only — never a line of the file, never
    a symbol's name."""
    if RECEIPTS == "off":
        return
    try:
        pathlib.Path(RECEIPTS).parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **row}
        with open(RECEIPTS, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — a receipt never brings the hook down
        pass


# --- the gate ----------------------------------------------------------------

_GATES: dict[tuple[str, str], object] = {}


def load_gate(root: str):
    """Import the gate by path with `HARNESS_ROOT` = the session's working directory.
    The gate fixes its root — and with it the allowlist — at import time. Cached per
    root, so a measurement over thousands of calls imports it once per repository."""
    key = (str(GATE), root)
    if key in _GATES:
        return _GATES[key]
    os.environ["HARNESS_ROOT"] = root
    spec = importlib.util.spec_from_file_location("junzi_rectify_names_gate", GATE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod                        # dataclasses resolve annotations here
    spec.loader.exec_module(mod)
    _GATES[key] = mod
    return mod


# --- the simulated change ----------------------------------------------------

def simulate(text: str, edits: list[dict]) -> tuple[str, bool]:
    """Apply the edits in order to `text`. Returns (result, simulated); `simulated` is
    False when an `old_string` was not found — the tool will refuse that edit, and what
    is judged instead is every `new_string`, dedented, on its own."""
    for e in edits:
        old = str(e.get("old_string") or "")
        new = str(e.get("new_string") or "")
        if not old or old not in text:
            return textwrap.dedent("\n".join(str(x.get("new_string") or "") for x in edits)), False
        text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    return text, True
# mutation-anchor: simulate


def edits_of(tool: str, given: dict) -> list[dict]:
    if tool == "MultiEdit" and isinstance(given.get("edits"), list):
        return [e for e in given["edits"] if isinstance(e, dict)]
    return [given]


def rel_path(target: str, root: str) -> str:
    """The path as the gate spells it: repository-relative, forward slashes. Outside the
    root it stays absolute — an anchored allowlist entry cannot reach it, and should not."""
    rel = os.path.relpath(target, root)
    if rel.startswith(".."):
        return target.replace(os.sep, "/")
    return rel.replace(os.sep, "/")


def prepare(ro, tool: str, given: dict, root: str) -> tuple[dict | None, dict]:
    """What the gate will read for this one call — the file as it is on disk and as it
    will be after the tool ran — and what the receipt should say about it. `None` with a
    reason when there is nothing the gate would read."""
    target = str(given.get("file_path") or "")
    if not target:
        return None, {"skip": "no-path"}
    if not os.path.isabs(target):
        target = os.path.join(root, target)
    target = os.path.normpath(target)
    info: dict = {"path": target}
    suffix = pathlib.Path(target).suffix
    if suffix not in ro.PY_SUFFIXES and suffix not in ro.JS_SUFFIXES:
        return None, {**info, "skip": "not-code"}          # the gate's own scope, one definition
    # mutation-anchor: scope
    exists = os.path.isfile(target)
    try:
        disk = pathlib.Path(target).read_text(encoding="utf-8") if exists else ""
    except (OSError, UnicodeDecodeError):
        return None, {**info, "skip": "unreadable"}       # binary: the tool refuses it too

    if tool == "Write":
        result, simulated = str(given.get("content") or ""), True
    else:
        result, simulated = simulate(disk if exists else "", edits_of(tool, given))
        info["simulated"] = simulated
    return {"rel": rel_path(target, root), "suffix": suffix, "before": disk if exists else None,
            "after": result, "simulated": simulated}, info


# --- the predicate -----------------------------------------------------------

def identity(finding) -> tuple[str, str, str]:
    """What makes two findings the same lie: the check, the allowlist subject and the
    qualified name the message opens with — never the line, which every edit above it
    moves, and never the reason, which a lie can change while staying a lie."""
    match = QUALIFIED.search(finding.message)
    return finding.check, finding.subject, match.group(1) if match else ""


def new_findings(before: list, after: list) -> list:
    """The findings of `after` that `before` did not already carry, as a multiset: a second
    lie of the same kind on a second symbol of the same name is new; the same lie moved
    three lines down is not."""
    debt = collections.Counter(identity(f) for f in before)
    out = []
    for finding in after:
        key = identity(finding)
        if debt[key] > 0:
            debt[key] -= 1
            continue
        out.append(finding)
    return out


def run_gate(ro, rel: str, suffix: str, before: str | None, after: str, simulated: bool
             ) -> tuple[list, int, int, int]:
    """The gate's checks on the result, minus what the file on disk already carried, its
    allowlist applied as the gate applies it. Returns (findings kept, exported symbols
    judged, findings the file carried before, findings exempted)."""
    prior: list = []
    if before is not None:
        ro.analyze_text(before, rel, suffix, prior)
    now: list = []
    judged = ro.analyze_text(after, rel, suffix, now)
    if not simulated:
        now = [f for f in now if f.check not in FILE_LEVEL]  # a fragment is not a module
    # mutation-anchor: fragment
    new = new_findings(prior, now)
    # mutation-anchor: delta
    scratch: list = []
    patterns = ro.load_allow_patterns(scratch)             # a bad line is the gate's finding…
    if scratch:                                            # …and this hook's error, never a pass
        raise HookError("allowlist: " + "; ".join(f.message for f in scratch))
    # mutation-anchor: bad-allowlist
    kept = [f for f in new if not ro._allowed(f, patterns)]
    # mutation-anchor: allowlist
    kept.sort(key=lambda f: (f.line, f.check))
    return kept, judged, len(prior), len(new) - len(kept)


def assess(tool: str, given: dict, root: str) -> tuple[list, int, dict]:
    """(findings, exported symbols judged, receipt info) for one tool call."""
    ro = load_gate(root)
    target, info = prepare(ro, tool, given, root)
    if target is None:
        return [], 0, info
    kept, judged, debt, exempted = run_gate(ro, **target)
    info.update(debt=debt, exempted=exempted)
    return kept, judged, info


def judge(tool: str, tool_input: dict, root: str) -> tuple[list, int]:
    """Findings the gate raises for ONE tool call that the file did not already carry,
    judged from `root`, and the number of exported symbols it judged. Importable, so the
    rate can be measured over real sessions without spawning a process per call."""
    findings, judged, _ = assess(tool, tool_input or {}, root)
    return findings, judged


# --- the warning -------------------------------------------------------------

def safe(text: str) -> str:
    """A path without what could break the warning's markdown or smuggle text shaped like
    an instruction (backticks, line breaks, control characters). The agent already saw the
    path in its own tool input; this is depth, not a boundary."""
    return re.sub(r"[`\r\n\t\x00-\x1f\x7f]", "?", text)[:120]


def message(findings: list) -> str:
    parts = [f"[{f.check}] `{safe(f.path)}`:{f.line}: {f.message[:MAX_MESSAGE]}."
             for f in findings[:MAX_SHOWN]]
    more = f" (+{len(findings) - MAX_SHOWN} more)" if len(findings) > MAX_SHOWN else ""
    return ("rectify-names: this change leaves an exported name that does not say what its "
            "symbol does. " + " ".join(parts) + more
            + " Li 禮 1, heal in passing: the name is the first thing the next reader inherits, "
            "and 正名 (Analects XIII.3) asks that it accord with the thing. Rename the symbol to "
            "what its body does, or make the body do what the name says; a name that stays "
            "wrong on purpose goes in .conduct/names-allow.txt with its reason. Only what this "
            "change adds is reported: a name that already lied is not its debt. Warning mode: "
            "this change is NOT blocked.")


# --- main --------------------------------------------------------------------

def main() -> int:
    t0 = time.time()
    payload = json.loads(sys.stdin.read() or "{}")
    tool = payload.get("tool_name")
    if tool not in TOOLS:
        return 0
    given = payload.get("tool_input") or {}
    if not isinstance(given, dict):
        given = {}
    root = str(payload.get("cwd") or os.getcwd())
    session = str(payload.get("session_id") or "")[:8]
    common = {"session": session, "tool": tool, "mode": MODE}

    findings, judged, info = assess(tool, given, root)
    ms = int((time.time() - t0) * 1000)
    if "skip" in info:
        if info["skip"] == "no-path":
            return 0                                     # nothing was handed in
        receipt(verdict="skip", why=info["skip"], path=info.get("path"), ms=ms, **common)
        return 0
    common["path"] = info.get("path")
    if "simulated" in info:
        common["simulated"] = info["simulated"]
    counts = {"judged": judged, "debt": info["debt"], "exempted": info["exempted"]}
    if not findings:
        receipt(verdict="ok", ms=ms, **counts, **common)
        return 0
    # mutation-anchor: warning
    receipt(verdict="finding", findings=len(findings),
            checks=sorted({f.check for f in findings}), ms=ms, **counts, **common)
    text = message(findings)
    if MODE == "block":
        sys.stderr.write(text.replace("Warning mode: this change is NOT blocked.",
                                      "Block mode: this change was not made.") + "\n")
        return 2
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "additionalContext": text}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — fail open on purpose: the hook is never the blocker
        receipt(verdict="error", error=f"{type(exc).__name__}: {exc}"[:200])
        sys.exit(0)
