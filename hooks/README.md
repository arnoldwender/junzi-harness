# Hooks — keeping the constants present

The Codex only works if it's *in context* when the agent acts. A one-time paste into
`AGENTS.md` works; a hook makes it automatic, every session, and opens each run with the
precept.

## `session-start.sh`

Emits, to stdout:

1. The **opening precept** + a rotating **precept of the day** (`bin/precept`, drawn from
   `precepts.txt`).
2. The **conduct block** — the four constants, precedence, and the gate limit (`codex-block.md`).

It's harness-agnostic: any harness that can run a command at session start can use it, and
its stdout is plain readable text.

## Wiring it into Claude Code

Claude Code injects a `SessionStart` hook's stdout into the session context. Add to your
`settings.json` (use the **absolute** path, and check your Claude Code version's hook docs —
the schema evolves):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "/abs/path/to/junzi-harness/hooks/session-start.sh" }
        ]
      }
    ]
  }
}
```

## Wiring it into any other harness

Run `hooks/session-start.sh` as the first step of your session bootstrap and prepend its
output to the system prompt. The precept goes first, the constants stay present.

## `rectify-names-before-write.py` — 正名, before the name lands

A Claude Code `PreToolUse` hook for `Edit`, `Write` and `MultiEdit`. Before the tool runs, it
hands the file **as it will be after the edit** to [`gate/rectify_names.py`](../gate/rectify_names.py)
— the same seven checks, the same allowlist — and, if the change leaves an exported name that
does not describe what its symbol does, tells the agent so in the tool result. It **warns**; it
does not block:

> rectify-names: this change leaves an exported name that does not say what its symbol does. [mutating-accessor] `planted.py`:5: `get_user` reads by its name and calls `cache.clear()` by its body — a caller cannot see that from the call site. Li 禮 1, heal in passing: the name is the first thing the next reader inherits, and 正名 (Analects XIII.3) asks that it accord with the thing. Rename the symbol to what its body does, or make the body do what the name says; a name that stays wrong on purpose goes in .conduct/names-allow.txt with its reason. Only what this change adds is reported: a name that already lied is not its debt. Warning mode: this change is NOT blocked.

Why a hook when the gate exists: the gate reads a diff, in CI, after the commit — a verdict, not
a correction. By the time it runs the `get_user()` that empties a cache has been written, three
callers have been written against the name, and the summary has gone out. 禮 Lǐ 1 — heal in
passing — is kept or broken at the moment of the edit, and the name is the first thing the next
reader inherits from it; this is the same gate at that moment.

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          { "type": "command",
            "command": "python3 /abs/path/to/junzi-harness/hooks/rectify-names-before-write.py",
            "timeout": 10 }
        ]
      }
    ]
  }
}
```

What it hands the gate: the file **simulated** — read from disk, `old_string` replaced by
`new_string` (`replace_all` honoured, the edits of a `MultiEdit` in order), or the `Write`'s
content — through the gate's own `analyze_text`, so the checks are the gate's and not a copy.
Only the suffixes the gate judges are judged (`PY_SUFFIXES`, `JS_SUFFIXES`: the gate's constants,
one definition); anything else is skipped with a receipt that says `skip`, which is not the same
word as `ok`. `Bash` is left out because a command has no suffix, `NotebookEdit` because a
notebook is JSON.

**The delta.** The checks run twice — over the file on disk and over the result — and only the
findings the result carries and the disk did not are reported. Two findings are the same lie
when they share the check, the allowlist subject and the qualified name the message opens with,
never the line: an insertion above an old lie moves it and changes nothing about it; a reason
that changes while the name still lies is not new; the same lie on a second symbol of the same
name (`Store.get_config` beside `get_config`) is. A name that already lied before this edit is
not this edit's debt — the same restraint the gate applies to a diff. When an `old_string` is not
in the file, the tool will refuse the edit; the hook judges every `new_string`, dedented, on its
own, says `simulated: false` in the receipt, and does not call that fragment `unparseable` — a
fragment is not a module, and whether the FILE parses is answered by the next edit that does
simulate.

The gate is imported with the session's working directory as its root — `HARNESS_ROOT` set
before the import, because the gate fixes its root and its allowlist when it loads — so
`.conduct/names-allow.txt` is the repository the agent is working in, not this one. A malformed
entry is the gate's `bad-allowlist` finding; here it is fail-open with `verdict: error`, never a
silent pass and never a warning the hook did not earn.

Every run leaves a receipt in `~/.local/state/junzi-harness/rectify-names-receipts.jsonl`
(`XDG_STATE_HOME` honoured; `RECTIFY_NAMES_RECEIPTS=…` to move it, `off` to disable) — verdict,
checks, counts (`judged` exported symbols, `debt` the findings the file already carried,
`exempted` by the allowlist), never a line of the file and never a symbol's name.
`RECTIFY_NAMES_HOOK_MODE=block` makes it deny the change instead (exit 2); shipped so the switch
exists, not the default. Any error of its own is a receipt and exit 0 — the hook is never the
reason a session cannot proceed. `judge(tool, tool_input, root)` is importable, so the rate can
be measured over recorded sessions without a process per call.

Limits, stated in the file's header so they stay decisions: the gate judges one file at a time
and resolves nothing across files, so the hook needs no more context than the one file — what it
does not see is the other file, the importer whose name a rename here turns into a lie; a file
that does not parse on disk holds no names the gate can read, so the edit that makes it parse
again is charged with every lie in it, old ones included; a file outside the working directory is
judged by its absolute path, out of the allowlist's reach; JavaScript/TypeScript gets exactly the
declaration-line checks the gate gives it, and no body.

Tests: [`tests/test_rectify_names_hook.py`](../tests/test_rectify_names_hook.py) ·
mutants: [`tests/mutation_check_rectify_names_hook.py`](../tests/mutation_check_rectify_names_hook.py) ·
smoke, the runtime's payload end to end: [`tests/live_hook_smoke.py`](../tests/live_hook_smoke.py)
with [`tests/fixtures/planted-edit.json`](../tests/fixtures/planted-edit.json).

## Just want to see it?

```sh
./hooks/session-start.sh
```
