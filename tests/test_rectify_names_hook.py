"""Tests for the live hook, hooks/rectify-names-before-write.py.

Same pair as the gate's own suite: an exported name that contradicts its body
must be WARNED about, and the same code with an honest name must pass in
silence. The hook is run as a PreToolUse subprocess with the payload on stdin,
inside a small git repository, and the exit code, the stdout JSON and the
receipt are what is asserted.

The hook itself never calls git: the fixture is a repository so it has the
shape of a real working directory. It carries `src/legacy.py`, a name that
already lies — the pre-existing debt the hook must NOT charge to an edit that
touches another line — and `src/broken.py`, a file that does not parse.

    python3 -m pytest tests/test_rectify_names_hook.py -q
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# The mutation runner points this at a mutated COPY; the real hook is never rewritten.
HOOK = Path(os.environ.get("RECTIFY_NAMES_HOOK_UNDER_TEST")
            or Path(__file__).resolve().parent.parent / "hooks" / "rectify-names-before-write.py")

IDENTITY = ("-c", "user.email=gate@example.invalid", "-c", "user.name=Gate Test")

BASELINE = {
    "src/app.py": textwrap.dedent('''\
        """Names that accord with what their symbols do."""

        DEFAULT_LIMIT = 20
        _cache = {}
        registry = {}


        def get_user(uid):
            return _cache.get(uid)


        def read_entry(key):
            return registry.get(key)


        def fetch_entry(key):
            return registry.get(key)


        def is_valid(value):
            return bool(value)


        async def flush_async():
            return True


        def collect(rows):
            out = []
            for row in rows:
                out.append(row)
            return out
        '''),
    "src/legacy.py": textwrap.dedent('''\
        """A name that already lies. Debt the hook must not charge to the next edit."""

        cache = {}


        def get_config(key):
            cache.clear()
            return key


        def limit():
            return 3
        '''),
    "src/broken.py": "def broken(:\n",
    "src/api.ts": textwrap.dedent("""\
        export function total(items: number[]): number {
          return items.reduce((a, b) => a + b, 0);
        }
        """),
    "NOTES.md": "# get_user, data, handler: words in prose, not names in code\n",
}

# One planted defect per check, as the gate's own suite plants them.
PLANTED = {
    "mutating-accessor": "cache = {}\n\n\ndef get_user(uid):\n    cache.clear()\n    return uid\n",
    "non-boolean-predicate": 'def is_valid(value):\n    return "maybe"\n',
    "async-suffix-mismatch": "async def flush_sync():\n    return True\n",
    "vacuous-name": "data = [1, 2, 3]\n",
    "plural-returns-one": "def get_records(rows):\n    return rows[0]\n",
    "singular-returns-many": "def get_record(rows):\n    return [rows[0]]\n",
    "constant-reassigned": "MAX_RETRIES = 3\nMAX_RETRIES = 5\n",
    "unparseable": "def broken(:\n",
}

# The same intent with the name made true. Every one of these must pass in silence.
HONEST = {
    "mutating-accessor": "cache = {}\n\n\ndef get_user(uid):\n    return cache.get(uid)\n",
    "non-boolean-predicate": "def is_valid(value):\n    return value > 0\n",
    "async-suffix-mismatch": "def flush_sync():\n    return True\n",
    "vacuous-name": "records = [1, 2, 3]\n",
    "plural-returns-one": "def get_records(rows):\n    return [row for row in rows]\n",
    "singular-returns-many": "def get_record(rows):\n    return rows[0]\n",
    "constant-reassigned": "MAX_RETRIES = 3\n",
}

# Every exported symbol of src/app.py: what `judged` counts after a harmless edit.
APP_EXPORTS = 8


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for rel, body in BASELINE.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, *IDENTITY, "commit", "-q", "-m", "baseline")
    return tmp_path


def hook(root: Path, tool: str, given: dict, env: dict[str, str] | None = None
         ) -> tuple[int, dict | None, str, dict | None]:
    receipts = root / "receipts.jsonl"
    payload = {"session_id": "test-session", "cwd": str(root), "hook_event_name": "PreToolUse",
               "tool_name": tool, "tool_input": given, "tool_use_id": "toolu_x"}
    run_env = {**os.environ, "RECTIFY_NAMES_RECEIPTS": str(receipts)}
    run_env.pop("RECTIFY_NAMES_HOOK_MODE", None)
    run_env.update(env or {})
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, env=run_env, cwd=str(root),
                       check=False, timeout=60)
    out = json.loads(r.stdout) if r.stdout.strip() else None
    rec = None
    if receipts.is_file():
        rec = json.loads(receipts.read_text(encoding="utf-8").strip().split("\n")[-1])
    return r.returncode, out, r.stderr, rec


def edit(root: Path, rel: str, old: str, new: str, replace_all: bool = False, **env: str):
    given = {"file_path": str(root / rel), "old_string": old, "new_string": new}
    if replace_all:
        given["replace_all"] = True
    return hook(root, "Edit", given, env or None)


def write(root: Path, rel: str, content: str, **env: str):
    return hook(root, "Write", {"file_path": str(root / rel), "content": content}, env or None)


def warning(out: dict | None) -> str:
    return ((out or {}).get("hookSpecificOutput") or {}).get("additionalContext") or ""


def append_to_app(root: Path, planted: str, **env: str):
    """An Edit that inserts `planted` above `collect` in src/app.py."""
    return edit(root, "src/app.py", "def collect(rows):", planted + "\n\ndef collect(rows):", **env)


# --- the control -------------------------------------------------------------

def test_a_harmless_edit_is_silent(repo: Path) -> None:
    """Without this, every test below could pass because the hook always warns."""
    rc, out, _, rec = edit(repo, "src/app.py", "        out.append(row)",
                           "        out.append(row.strip())")
    assert rc == 0 and out is None, (rc, out)
    assert rec["verdict"] == "ok" and rec["tool"] == "Edit"
    assert rec["judged"] == APP_EXPORTS and rec["simulated"] is True, rec
    assert rec["debt"] == 0 and rec["exempted"] == 0


# --- each check, on a Write that creates the file and on an Edit of one that exists ----

@pytest.mark.parametrize("check", sorted(PLANTED))
def test_each_check_fires_on_a_write_that_creates_the_file(repo: Path, check: str) -> None:
    rc, out, _, rec = write(repo, "src/new.py", PLANTED[check])
    assert rc == 0
    assert f"[{check}]" in warning(out), warning(out)
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in out["hookSpecificOutput"], "the permission flow is not touched"
    assert rec["verdict"] == "finding" and rec["checks"] == [check] and rec["tool"] == "Write"


@pytest.mark.parametrize("check", sorted(PLANTED))
def test_each_check_fires_on_an_edit_of_an_existing_file(repo: Path, check: str) -> None:
    rc, out, _, rec = append_to_app(repo, PLANTED[check])
    assert rc == 0
    assert f"[{check}]" in warning(out), warning(out)
    assert rec["verdict"] == "finding" and rec["checks"] == [check] and rec["simulated"] is True


@pytest.mark.parametrize("check", sorted(HONEST))
def test_the_honest_form_of_each_check_is_silent(repo: Path, check: str) -> None:
    """The same code with the name made true. A hook that fires here is a hook nobody keeps."""
    _, out, _, rec = append_to_app(repo, HONEST[check])
    assert out is None, warning(out)
    assert rec["verdict"] == "ok"
    _, out, _, rec = write(repo, "src/new.py", HONEST[check])
    assert out is None, warning(out)
    assert rec["verdict"] == "ok"


def test_a_getter_appending_to_its_own_local_list_is_silent(repo: Path) -> None:
    """The gate's own carve-out — scratch is not state — reaches the hook unchanged."""
    _, out, _, rec = append_to_app(repo, "def get_names(rows):\n    out = []\n    for row in rows:\n"
                                         "        out.append(row.name)\n    return out\n")
    assert out is None, warning(out)
    assert rec["verdict"] == "ok"


# --- JavaScript / TypeScript: the gate's partial support, no more and no less ---

def test_an_exported_js_handler_in_a_write_warns(repo: Path) -> None:
    _, out, _, rec = write(repo, "src/api.ts", BASELINE["src/api.ts"]
                           + "export function handler(event) { return event; }\n")
    assert "[vacuous-name]" in warning(out) and rec["checks"] == ["vacuous-name"]


def test_a_js_sync_suffix_declared_async_in_an_edit_warns(repo: Path) -> None:
    _, out, _, rec = edit(repo, "src/api.ts", "export function total",
                          "export async function flushSync() { return 1; }\nexport function total")
    assert "[async-suffix-mismatch]" in warning(out) and rec["checks"] == ["async-suffix-mismatch"]


def test_js_bodies_are_not_analysed_by_the_hook_either(repo: Path) -> None:
    """The gate's stated limit: without a parser the body of a TS function is not read, so the
    defect that fails in Python passes in TypeScript. The hook does not pretend otherwise."""
    _, out, _, rec = edit(repo, "src/api.ts", "export function total",
                          "export function getUser(uid) { cache.clear(); return uid; }\n"
                          "export function total")
    assert out is None, warning(out)
    assert rec["verdict"] == "ok" and rec["judged"] == 2


# --- the delta: a name that already lied is not this edit's debt ------------

def test_a_name_that_already_lied_is_not_charged_to_an_edit_elsewhere(repo: Path) -> None:
    """`get_config` in src/legacy.py has always emptied the cache. Touching `limit` does not
    make that this edit's finding."""
    _, out, _, rec = edit(repo, "src/legacy.py", "    return 3", "    return 4")
    assert out is None, warning(out)
    assert rec["verdict"] == "ok" and rec["debt"] == 1, rec


def test_a_new_lie_is_reported_and_the_old_one_is_not(repo: Path) -> None:
    _, out, _, rec = edit(repo, "src/legacy.py", "def limit():",
                          "def get_limit():\n    cache.clear()\n    return 3\n\n\ndef limit():")
    assert "[mutating-accessor]" in warning(out)
    assert "`get_limit`" in warning(out) and "`get_config`" not in warning(out), warning(out)
    assert rec["findings"] == 1 and rec["debt"] == 1


def test_lines_moving_under_a_pre_existing_lie_do_not_resurrect_it(repo: Path) -> None:
    """Identity is the check and the name, never the line: an insertion above the old lie
    moves it three lines down and changes nothing about it."""
    _, out, _, rec = edit(repo, "src/legacy.py", "cache = {}", "import os\nimport sys\n\ncache = {}")
    assert out is None, warning(out)
    assert rec["verdict"] == "ok" and rec["debt"] == 1


def test_the_same_lie_on_a_second_symbol_of_the_same_name_is_new(repo: Path) -> None:
    """A method `Store.get_config` that empties the cache is not the module-level
    `get_config` that already did: same check, same subject, different qualified name."""
    _, out, _, rec = edit(repo, "src/legacy.py", "def limit():",
                          "class Store:\n    def get_config(self):\n        cache.clear()\n"
                          "        return 1\n\n\ndef limit():")
    assert "`Store.get_config`" in warning(out), warning(out)
    assert rec["findings"] == 1


def test_a_reason_that_changes_while_the_name_still_lies_is_not_new(repo: Path) -> None:
    """`get_config` stops calling `clear()` and starts calling `pop()`. Still a reader that
    writes; the message changes, the lie does not."""
    _, out, _, rec = edit(repo, "src/legacy.py", "    cache.clear()", "    cache.pop(key, None)")
    assert out is None, warning(out)
    assert rec["verdict"] == "ok" and rec["debt"] == 1


def test_a_file_that_did_not_parse_before_stays_silent_when_it_still_does_not(repo: Path) -> None:
    _, out, _, rec = edit(repo, "src/broken.py", "def broken(:", "def broken(:\n    pass")
    assert out is None, warning(out)
    assert rec["verdict"] == "ok" and rec["debt"] == 1


def test_an_edit_that_breaks_a_parsing_file_warns_unparseable(repo: Path) -> None:
    """A broken file is a finding, never a skip — the gate's exit-code contract, kept here."""
    _, out, _, rec = edit(repo, "src/app.py", "def get_user(uid):", "def get_user(uid:")
    assert "[unparseable]" in warning(out) and rec["checks"] == ["unparseable"]


def test_a_write_over_an_existing_file_is_judged_against_it(repo: Path) -> None:
    body = BASELINE["src/legacy.py"]
    _, out, _, rec = write(repo, "src/legacy.py", body + "\n\ndef ceiling():\n    return 9\n")
    assert out is None, warning(out)
    assert rec["verdict"] == "ok" and rec["debt"] == 1
    _, out, _, rec = write(repo, "src/legacy.py", body + "\n\ndef get_ceiling():\n    cache.clear()\n"
                                                        "    return 9\n")
    assert "`get_ceiling`" in warning(out) and rec["findings"] == 1


# --- replace_all and MultiEdit -----------------------------------------------

def test_replace_all_judges_every_occurrence(repo: Path) -> None:
    _, out, _, rec = edit(repo, "src/app.py", "    return registry.get(key)",
                          "    registry.clear()\n    return registry.get(key)", replace_all=True)
    assert "[mutating-accessor]" in warning(out)
    assert rec["findings"] == 2, rec
    assert "`read_entry`" in warning(out) and "`fetch_entry`" in warning(out)


def test_without_replace_all_only_the_first_occurrence_changes(repo: Path) -> None:
    _, out, _, rec = edit(repo, "src/app.py", "    return registry.get(key)",
                          "    registry.clear()\n    return registry.get(key)")
    assert rec["findings"] == 1 and "`read_entry`" in warning(out)
    assert "`fetch_entry`" not in warning(out)


def test_multiedit_applies_every_edit_in_order(repo: Path) -> None:
    given = {"file_path": str(repo / "src" / "app.py"), "edits": [
        {"old_string": "    return bool(value)", "new_string": '    return "maybe"'},
        {"old_string": "async def flush_async():", "new_string": "def flush_async():"},
    ]}
    _, out, _, rec = hook(repo, "MultiEdit", given)
    assert rec["tool"] == "MultiEdit" and rec["simulated"] is True
    assert rec["checks"] == ["async-suffix-mismatch", "non-boolean-predicate"], rec


def test_a_later_edit_sees_the_result_of_an_earlier_one(repo: Path) -> None:
    """The second `old_string` exists only after the first edit was applied."""
    given = {"file_path": str(repo / "src" / "app.py"), "edits": [
        {"old_string": "def collect(rows):", "new_string": "def get_row(rows):"},
        {"old_string": "def get_row(rows):\n    out = []",
         "new_string": "def get_row(rows):\n    registry.clear()\n    out = []"},
    ]}
    _, out, _, rec = hook(repo, "MultiEdit", given)
    assert "`get_row`" in warning(out) and rec["checks"] == ["mutating-accessor"]


# --- old_string absent, missing file, new file --------------------------------

def test_an_old_string_not_found_judges_the_new_string_alone(repo: Path) -> None:
    """The tool will refuse the edit; the hook still says what the new text carries."""
    _, out, _, rec = edit(repo, "src/app.py", "nothing like this is in the file",
                          "def get_user(uid):\n    _cache.clear()\n    return uid\n")
    assert "[mutating-accessor]" in warning(out)
    assert rec["simulated"] is False and rec["judged"] == 1


def test_a_fragment_that_is_not_a_module_is_not_called_unparseable(repo: Path) -> None:
    """An `except` clause on its own, judged alone, is not a module. Saying the FILE does
    not parse would be a statement about text that is not the file."""
    _, out, _, rec = edit(repo, "src/app.py", "nothing like this is in the file",
                          "    except KeyError:\n        return None\n")
    assert out is None, warning(out)
    assert rec["verdict"] == "ok" and rec["simulated"] is False


def test_an_edit_of_a_missing_file_judges_the_new_string_alone(repo: Path) -> None:
    _, out, _, rec = edit(repo, "src/new.py", "", "data = [1, 2, 3]\n")
    assert "[vacuous-name]" in warning(out)
    assert rec["simulated"] is False


def test_a_write_that_creates_a_file_judges_every_name(repo: Path) -> None:
    _, out, _, rec = write(repo, "src/new.py", "records = []\ndata = [1, 2, 3]\n")
    assert "[vacuous-name]" in warning(out) and "`src/new.py`:2:" in warning(out)
    assert rec["judged"] == 2 and rec["debt"] == 0


# --- scope: the gate's own -----------------------------------------------------

@pytest.mark.parametrize("rel", ["NOTES.md", "package.json", "deploy.sh"])
def test_a_file_the_gate_would_not_read_is_skipped(repo: Path, rel: str) -> None:
    """Prose that names a symbol is not code that exports one."""
    _, out, _, rec = write(repo, rel, "def get_user(uid):\n    cache.clear()\n    return uid\n")
    assert out is None, warning(out)
    assert rec["verdict"] == "skip" and rec["why"] == "not-code"


def test_a_pyi_stub_is_in_scope_because_the_gate_says_so(repo: Path) -> None:
    """The suffix list is the gate's constant, not a copy: `.pyi` is judged."""
    _, out, _, rec = write(repo, "src/types.pyi", "data: list[int]\n")
    assert "[vacuous-name]" in warning(out) and rec["verdict"] == "finding"


def test_a_relative_file_path_is_resolved_against_cwd(repo: Path) -> None:
    _, out, _, rec = hook(repo, "Edit", {"file_path": "src/app.py",
                                         "old_string": "    return bool(value)",
                                         "new_string": '    return "maybe"'})
    assert "[non-boolean-predicate]" in warning(out) and "`src/app.py`:" in warning(out)
    assert rec["path"] == str(repo / "src" / "app.py")


def test_a_file_outside_the_working_directory_is_judged_by_its_absolute_path(
        repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    outside = tmp_path_factory.mktemp("outside") / "far.py"
    _, out, _, rec = hook(repo, "Write", {"file_path": str(outside), "content": "data = 1\n"})
    assert "[vacuous-name]" in warning(out)
    assert f"`{outside}`:1:" in warning(out), warning(out)


# --- the allowlist -----------------------------------------------------------

def allow(repo: Path, body: str) -> None:
    (repo / ".conduct").mkdir()
    (repo / ".conduct" / "names-allow.txt").write_text(body, encoding="utf-8")


def test_an_allowlisted_name_is_silent_and_counted(repo: Path) -> None:
    allow(repo, "# kept on purpose: the cache is the user store, documented in the module\nget_user\n")
    _, out, _, rec = edit(repo, "src/app.py", "    return _cache.get(uid)",
                          "    _cache.clear()\n    return uid")
    assert out is None, warning(out)
    assert rec["verdict"] == "ok" and rec["exempted"] == 1, rec


def test_the_allowlist_does_not_silence_everything(repo: Path) -> None:
    allow(repo, "get_user\n")
    _, out, _, _ = edit(repo, "src/app.py", "    return bool(value)", '    return "maybe"')
    assert "[non-boolean-predicate]" in warning(out)


def test_the_allowlist_is_the_working_directorys(repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Judged from a different cwd, the same edit is not exempt: the allowlist that applies
    is the one under the payload's `cwd`, not this repository's and not the file's."""
    allow(repo, "get_user\n")
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    given = {"file_path": str(repo / "src" / "app.py"), "old_string": "    return _cache.get(uid)",
             "new_string": "    _cache.clear()\n    return uid"}
    _, out, _, _ = hook(elsewhere, "Edit", given)
    assert "[mutating-accessor]" in warning(out)


def test_a_malformed_allowlist_fails_open_with_a_receipt(repo: Path) -> None:
    """The gate reports a bad line as a finding; here it is an error, never a silent pass
    and never a warning the hook did not earn."""
    allow(repo, "get_[user\n")
    rc, out, err, rec = edit(repo, "src/app.py", "    return bool(value)", '    return "maybe"')
    assert rc == 0 and out is None, (rc, out, err)
    assert rec["verdict"] == "error" and "not a valid pattern" in rec["error"], rec


# --- fail-open and modes -----------------------------------------------------

def test_other_tools_are_ignored_without_a_receipt(repo: Path) -> None:
    for tool in ("Read", "Grep", "Bash", "NotebookEdit"):
        rc, out, _, rec = hook(repo, tool, {"file_path": str(repo / "src" / "legacy.py"),
                                            "notebook_path": str(repo / "a.ipynb"),
                                            "command": "python3 -c 'data = 1'"})
        assert rc == 0 and out is None and rec is None, tool


def test_a_missing_path_is_ignored_without_a_receipt(repo: Path) -> None:
    rc, out, _, rec = hook(repo, "Edit", {"old_string": "a", "new_string": "data = 1"})
    assert rc == 0 and out is None and rec is None


def test_a_missing_gate_fails_open_with_a_receipt(repo: Path) -> None:
    rc, out, _, rec = write(repo, "src/new.py", "data = 1\n", RECTIFY_NAMES_GATE=str(repo / "none.py"))
    assert rc == 0 and out is None and rec["verdict"] == "error"


def test_block_mode_exits_2_with_the_text_on_stderr(repo: Path) -> None:
    rc, out, err, rec = write(repo, "src/new.py", "data = 1\n", RECTIFY_NAMES_HOOK_MODE="block")
    assert rc == 2 and out is None and "Block mode" in err and rec["mode"] == "block"
    assert "[vacuous-name]" in err


def test_receipts_can_be_switched_off(repo: Path) -> None:
    rc, out, _, _ = write(repo, "src/new.py", "data = 1\n", RECTIFY_NAMES_RECEIPTS="off")
    assert rc == 0 and warning(out)
    assert not (repo / "receipts.jsonl").exists()


def test_receipts_default_under_xdg_state_home(repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    state = tmp_path_factory.mktemp("state")
    payload = {"session_id": "s", "cwd": str(repo), "tool_name": "Write",
               "tool_input": {"file_path": str(repo / "src" / "new.py"), "content": "data = 1\n"}}
    env = {k: v for k, v in os.environ.items() if k != "RECTIFY_NAMES_RECEIPTS"}
    env["XDG_STATE_HOME"] = str(state)
    subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True,
                   text=True, env=env, cwd=str(repo), check=False, timeout=60)
    rec_file = state / "junzi-harness" / "rectify-names-receipts.jsonl"
    assert rec_file.is_file()
    assert json.loads(rec_file.read_text(encoding="utf-8").strip())["verdict"] == "finding"


# --- the text ----------------------------------------------------------------

def test_the_warning_cites_the_rule_and_says_it_is_not_blocking(repo: Path) -> None:
    _, out, _, _ = write(repo, "src/new.py", "data = 1\n")
    w = warning(out)
    assert w.startswith("rectify-names: ") and "Li 禮 1" in w and "Analects XIII.3" in w
    assert w.endswith("Warning mode: this change is NOT blocked.")


def test_a_hostile_file_name_is_neutralised_in_the_warning(repo: Path) -> None:
    (repo / "a`b\nc.py").write_text("x = 1\n", encoding="utf-8")
    _, out, _, _ = edit(repo, "a`b\nc.py", "x = 1", "x = 1\ndata = 2")
    w = warning(out)
    assert "a?b?c.py" in w and "\n" not in w.split("rectify-names: ")[1][:200]


def test_the_warning_is_capped_and_stays_far_below_the_runtime_cap(repo: Path) -> None:
    _, out, _, rec = write(repo, "src/many.py", "".join(f"data{i} = {i}\n" for i in range(60)))
    assert "(+56 more)" in warning(out) and rec["findings"] == 60
    assert 0 < len(warning(out)) < 2000               # the runtime caps hook output at 10,000


def test_the_receipt_carries_check_names_and_counts_never_a_symbol(repo: Path) -> None:
    _, out, _, rec = append_to_app(repo, "def get_secret_token_value(uid):\n    registry.clear()\n"
                                         "    return uid\n")
    assert "`get_secret_token_value`" in warning(out)
    for key in ("ts", "session", "tool", "path", "verdict", "checks", "findings", "judged", "ms"):
        assert key in rec, (key, rec)
    assert "secret_token_value" not in json.dumps(rec)


# --- judge(), importable -----------------------------------------------------

def test_judge_is_importable_and_writes_no_receipt(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The measurement over recorded sessions imports `judge` and calls it thousands of
    times; it must return the gate's findings and touch no receipt file."""
    receipts = repo / "judge-receipts.jsonl"
    monkeypatch.setenv("RECTIFY_NAMES_RECEIPTS", str(receipts))
    spec = importlib.util.spec_from_file_location("rectify_names_hook_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    findings, judged = mod.judge("Write", {"file_path": str(repo / "src" / "new.py"),
                                           "content": "records = []\ndata = [1]\n"}, str(repo))
    assert [f.check for f in findings] == ["vacuous-name"] and judged == 2
    findings, judged = mod.judge("Edit", {"file_path": str(repo / "src" / "legacy.py"),
                                          "old_string": "    return 3", "new_string": "    return 4"},
                                 str(repo))
    assert findings == [] and judged == 3               # cache, get_config, limit
    assert not receipts.exists()
