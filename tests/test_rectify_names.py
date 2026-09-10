"""Tests for the rectify-names gate.

Every check gets the same treatment: plant the one defect the check exists to
catch and require exit 1, then write the same code with an honest name and
require exit 0. A test that only ever sees the defect proves the gate is loud;
a test that only ever sees clean code proves nothing at all.

    python3 -m pytest tests/ -q

The gate is invoked as a SUBPROCESS rather than imported, because the exit code
is part of the contract the whole conduct-harness family shares (0 clean,
1 findings, 2 the gate itself broke). Importing would test the functions and
leave the contract untested.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parent.parent / "gate" / "rectify_names.py"

# A module the gate passes. Every fixture starts from this, so any test that
# goes red went red because of what that test planted.
CLEAN_MODULE = '''\
"""A module whose names accord with what its symbols do."""

DEFAULT_LIMIT = 20
_cache = {}


def get_user(uid):
    return _cache.get(uid)


def get_users(rows):
    return [row for row in rows]


def is_valid(value):
    return bool(value)


async def flush_async():
    return True


def collect(rows):
    out = []
    for row in rows:
        out.append(row)
    return out
'''


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "HARNESS_ROOT": str(root)}
    return subprocess.run([sys.executable, str(GATE), *args],
                          capture_output=True, text=True, env=env, check=False)


def scan(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the gate over every source file under `root`."""
    return run(root, "--all", *args)


def plant(root: Path, body: str, name: str = "planted.py") -> Path:
    """Write one module into the scratch repo and hand back its path."""
    path = root / name
    path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
    return path


def _git(root: Path, *argv: str) -> None:
    subprocess.run(["git", "-C", str(root), *argv], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A minimal repo the gate passes cleanly."""
    (tmp_path / "module.py").write_text(CLEAN_MODULE, encoding="utf-8")
    return tmp_path


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """The same repo, committed on `main`, for the --base path."""
    root = tmp_path / "work"
    root.mkdir()
    (root / "module.py").write_text(CLEAN_MODULE, encoding="utf-8")
    _git(root, "init", "-q", "-b", "main", ".")
    _git(root, "config", "user.email", "gate@example.invalid")
    _git(root, "config", "user.name", "Gate Test")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


# --- the control -------------------------------------------------------------

def test_clean_repo_passes(repo: Path) -> None:
    """Without this, every test below could pass because the gate always fails."""
    result = scan(repo)
    assert result.returncode == 0, result.stdout
    assert "accords with what its symbol does" in result.stdout


# --- CHECK 1: a reader that writes -------------------------------------------

def test_getter_that_clears_a_module_cache_is_caught(repo: Path) -> None:
    plant(repo, """
        cache = {}

        def get_user(uid):
            cache.clear()
            return uid
    """)
    result = scan(repo)
    assert result.returncode == 1, result.stdout
    assert "mutating-accessor" in result.stdout


def test_getter_that_only_reads_passes(repo: Path) -> None:
    """The same function with the mutation removed. The name is now true."""
    plant(repo, """
        cache = {}

        def get_user(uid):
            return cache.get(uid)
    """)
    assert scan(repo).returncode == 0


def test_getter_that_writes_a_self_attribute_is_caught(repo: Path) -> None:
    plant(repo, """
        class Session:
            def get_token(self):
                self.hits = 1
                return self.token
    """)
    result = scan(repo)
    assert result.returncode == 1, result.stdout
    assert "mutating-accessor" in result.stdout


def test_getter_that_deletes_from_a_global_is_caught(repo: Path) -> None:
    plant(repo, """
        cache = {}

        def read_entry(key):
            del cache[key]
            return key
    """)
    result = scan(repo)
    assert result.returncode == 1, result.stdout
    assert "mutating-accessor" in result.stdout


def test_getter_appending_to_its_own_local_list_passes(repo: Path) -> None:
    """The false positive that would get this gate uninstalled.

    Building a list inside a reader and appending to it mutates nothing the
    caller can observe. A gate that fires here is a gate nobody keeps.
    """
    plant(repo, """
        def get_names(rows):
            out = []
            for row in rows:
                out.append(row.name)
            return out
    """)
    assert scan(repo).returncode == 0


# --- CHECK 2: a question that does not answer yes or no ----------------------

def test_predicate_returning_a_string_is_caught(repo: Path) -> None:
    plant(repo, """
        def is_valid(value):
            return "maybe"
    """)
    result = scan(repo)
    assert result.returncode == 1, result.stdout
    assert "non-boolean-predicate" in result.stdout


def test_predicate_returning_a_comparison_passes(repo: Path) -> None:
    plant(repo, """
        def is_valid(value):
            return value > 0
    """)
    assert scan(repo).returncode == 0


def test_predicate_that_falls_off_the_end_is_caught(repo: Path) -> None:
    """`if x: return True` and nothing else answers None on the other path."""
    plant(repo, """
        def has_access(user):
            if user.admin:
                return True
    """)
    result = scan(repo)
    assert result.returncode == 1, result.stdout
    assert "non-boolean-predicate" in result.stdout


def test_predicate_whose_branches_all_exit_passes(repo: Path) -> None:
    """A try/except that re-raises does not fall through. The gate must know."""
    plant(repo, """
        def should_retry(attempt):
            try:
                return attempt < 3
            except TypeError:
                raise
    """)
    assert scan(repo).returncode == 0


def test_a_word_merely_starting_with_is_is_not_a_predicate(repo: Path) -> None:
    """`island` is not `is_`, `hash` is not `has_`, `cancel` is not `can_`."""
    plant(repo, """
        def island(value):
            return "an island, not a claim about truth"


        def hash_of(value):
            return "%x" % value


        def cancel(job):
            return "cancelled"
    """)
    assert scan(repo).returncode == 0


# --- CHECK 3: the suffix that answers the wrong question ---------------------

def test_sync_named_function_declared_async_is_caught(repo: Path) -> None:
    plant(repo, """
        async def flush_sync():
            return True
    """)
    result = scan(repo)
    assert result.returncode == 1, result.stdout
    assert "async-suffix-mismatch" in result.stdout


def test_async_named_function_declared_sync_is_caught(repo: Path) -> None:
    plant(repo, """
        def flush_async():
            return True
    """)
    result = scan(repo)
    assert result.returncode == 1, result.stdout
    assert "async-suffix-mismatch" in result.stdout


def test_matching_suffixes_pass(repo: Path) -> None:
    plant(repo, """
        def flush_sync():
            return True


        async def reload_async():
            return True
    """)
    assert scan(repo).returncode == 0


def test_resync_is_not_a_sync_suffix(repo: Path) -> None:
    """`resync` ends in those letters without carrying the promise."""
    plant(repo, """
        async def resync():
            return True
    """)
    assert scan(repo).returncode == 0


# --- CHECK 4: a public name that says nothing --------------------------------

def test_exported_data_is_caught(repo: Path) -> None:
    plant(repo, """
        data = [1, 2, 3]
    """)
    result = scan(repo)
    assert result.returncode == 1, result.stdout
    assert "vacuous-name" in result.stdout


def test_local_data_and_temp_pass(repo: Path) -> None:
    """A local mis-name lies to nobody. Locals are out of scope on purpose."""
    plant(repo, """
        def average(rows):
            data = [row.value for row in rows]
            temp = sum(data)
            return temp / len(data)
    """)
    assert scan(repo).returncode == 0


def test_private_name_is_out_of_scope(repo: Path) -> None:
    """A leading underscore says "not part of anyone's vocabulary".

    `_flush_sync` is here on purpose: it is the one defect in this module that
    would still fire if the underscore rule were removed, which is what makes
    this test defend the exemption instead of merely describing it.
    """
    plant(repo, """
        _records = [1, 2, 3]


        async def _flush_sync():
            _records.clear()
            return True


        def _get_user(uid):
            _records.clear()
            return uid
    """)
    assert scan(repo).returncode == 0


def test_exported_handler_function_is_caught(repo: Path) -> None:
    plant(repo, """
        def handler(event):
            return event
    """)
    result = scan(repo)
    assert result.returncode == 1, result.stdout
    assert "vacuous-name" in result.stdout


def test_dunder_all_narrows_the_public_surface(repo: Path) -> None:
    """A symbol the module does not export is not part of anyone's vocabulary."""
    plant(repo, """
        __all__ = ["records"]

        records = []
        data = [1, 2, 3]
    """)
    assert scan(repo).returncode == 0


def test_dunder_all_still_catches_what_it_does_export(repo: Path) -> None:
    plant(repo, """
        __all__ = ["data"]

        records = []
        data = [1, 2, 3]
    """)
    result = scan(repo)
    assert result.returncode == 1, result.stdout
    assert "vacuous-name" in result.stdout


# --- CHECK 5: plural and singular --------------------------------------------

def test_plural_getter_returning_one_element_is_caught(repo: Path) -> None:
    plant(repo, """
        def get_records(rows):
            return rows[0]
    """)
    result = scan(repo)
    assert result.returncode == 1, result.stdout
    assert "plural-returns-one" in result.stdout


def test_plural_getter_returning_a_list_passes(repo: Path) -> None:
    plant(repo, """
        def get_records(rows):
            return [row for row in rows]
    """)
    assert scan(repo).returncode == 0


def test_singular_getter_returning_a_list_is_caught(repo: Path) -> None:
    plant(repo, """
        def get_record(rows):
            return [rows[0]]
    """)
    result = scan(repo)
    assert result.returncode == 1, result.stdout
    assert "singular-returns-many" in result.stdout


def test_fetch_all_returning_one_element_is_caught(repo: Path) -> None:
    plant(repo, """
        def fetch_all(rows):
            return rows[0]
    """)
    result = scan(repo)
    assert result.returncode == 1, result.stdout
    assert "plural-returns-one" in result.stdout


def test_a_verb_named_function_returning_a_list_passes(repo: Path) -> None:
    """`analyse` names the verb, not the result. Only names that claim to name
    the returned thing are checked for plurality."""
    plant(repo, """
        def analyse_row(row):
            return [row.a, row.b]
    """)
    assert scan(repo).returncode == 0


# --- CHECK 6: a constant that moves ------------------------------------------

def test_constant_rebound_at_module_level_is_caught(repo: Path) -> None:
    plant(repo, """
        MAX_RETRIES = 3
        MAX_RETRIES = 5
    """)
    result = scan(repo)
    assert result.returncode == 1, result.stdout
    assert "constant-reassigned" in result.stdout


def test_constant_written_through_a_global_statement_is_caught(repo: Path) -> None:
    plant(repo, """
        MAX_RETRIES = 3


        def raise_ceiling(value):
            global MAX_RETRIES
            MAX_RETRIES = value
    """)
    result = scan(repo)
    assert result.returncode == 1, result.stdout
    assert "constant-reassigned" in result.stdout


def test_constant_bound_in_exclusive_branches_passes(repo: Path) -> None:
    """Two bindings that cannot both run are alternatives, not reassignment.

    This is how a module supports two runtimes. A gate that calls it drift
    would push people to write worse code to keep it quiet.
    """
    plant(repo, """
        try:
            import tomllib
            HAS_TOML = True
        except ImportError:
            HAS_TOML = False
    """)
    assert scan(repo).returncode == 0


# --- JavaScript / TypeScript: partial, and honest about it -------------------

def test_exported_js_handler_is_caught(repo: Path) -> None:
    (repo / "api.ts").write_text(
        "export function handler(event) { return event; }\n", encoding="utf-8")
    result = scan(repo)
    assert result.returncode == 1, result.stdout
    assert "vacuous-name" in result.stdout


def test_js_sync_suffix_declared_async_is_caught(repo: Path) -> None:
    (repo / "api.ts").write_text(
        "export async function flushSync() { return 1; }\n", encoding="utf-8")
    result = scan(repo)
    assert result.returncode == 1, result.stdout
    assert "async-suffix-mismatch" in result.stdout


def test_js_arrow_const_is_read(repo: Path) -> None:
    (repo / "api.js").write_text(
        "export const loadAsync = (url) => url;\n", encoding="utf-8")
    result = scan(repo)
    assert result.returncode == 1, result.stdout
    assert "async-suffix-mismatch" in result.stdout


def test_non_exported_js_symbol_is_out_of_scope(repo: Path) -> None:
    (repo / "api.ts").write_text(
        "function handler(event) { return event; }\n"
        "const data = 1;\n", encoding="utf-8")
    assert scan(repo).returncode == 0


def test_js_bodies_are_not_analysed(repo: Path) -> None:
    """The honest limit of regex support: the same defect that fails in Python
    passes in TypeScript, because there is no parser to see the body."""
    (repo / "api.ts").write_text(
        "export function getUser(uid) { cache.clear(); return uid; }\n",
        encoding="utf-8")
    assert scan(repo).returncode == 0


# --- the contract ------------------------------------------------------------

def test_invalid_python_is_reported_and_the_gate_survives(repo: Path) -> None:
    """A file that will not parse is a finding, never a crash and never a skip.

    Skipping it would let anyone silence this gate by shipping code that does
    not compile — the exact fail-open the exit-code contract forbids.
    """
    (repo / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    result = scan(repo)
    assert result.returncode == 1, result.stdout
    assert "unparseable" in result.stdout
    assert "Traceback" not in result.stderr


def test_a_second_file_is_still_checked_after_an_unparseable_one(repo: Path) -> None:
    """"Reports it and carries on" has to mean carries on."""
    (repo / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    plant(repo, """
        data = [1, 2, 3]
    """)
    result = scan(repo)
    assert result.returncode == 1
    assert "unparseable" in result.stdout
    assert "vacuous-name" in result.stdout


def test_non_source_files_are_ignored(repo: Path) -> None:
    (repo / "notes.md").write_text("# data temp handler get_user\n", encoding="utf-8")
    assert scan(repo).returncode == 0


def test_files_mode_checks_exactly_those_files(repo: Path) -> None:
    planted = plant(repo, """
        data = [1, 2, 3]
    """)
    assert run(repo, "--files", str(repo / "module.py")).returncode == 0
    assert run(repo, "--files", str(planted)).returncode == 1


def test_files_pointing_at_nothing_is_a_gate_failure(repo: Path) -> None:
    """Exit 2, not 0. "I could not look" must never read as "nothing there"."""
    result = run(repo, "--files", str(repo / "absent.py"))
    assert result.returncode == 2
    assert "gate failure" in result.stderr


def test_a_root_that_is_not_a_git_tree_is_a_gate_failure(repo: Path) -> None:
    result = run(repo)
    assert result.returncode == 2
    assert "gate failure" in result.stderr


def test_unresolvable_base_ref_is_a_gate_failure(git_repo: Path) -> None:
    result = run(git_repo, "--base", "origin/does-not-exist")
    assert result.returncode == 2
    assert "does not resolve" in result.stderr


def test_base_diff_checks_what_changed_and_not_what_did_not(git_repo: Path) -> None:
    """The default input is the diff. Committed-and-clean code is not re-judged."""
    assert run(git_repo, "--base", "main").returncode == 0
    plant(git_repo, """
        cache = {}

        def get_user(uid):
            cache.clear()
            return uid
    """)
    result = run(git_repo, "--base", "main")
    assert result.returncode == 1, result.stdout
    assert "mutating-accessor" in result.stdout


# --- allowlist ---------------------------------------------------------------

def test_allowlist_suppresses_a_named_finding(repo: Path) -> None:
    plant(repo, """
        cache = {}

        def get_user(uid):
            cache.clear()
            return uid
    """)
    assert scan(repo).returncode == 1
    (repo / ".conduct").mkdir()
    (repo / ".conduct" / "names-allow.txt").write_text(
        "# kept deliberately\nget_user\n", encoding="utf-8")
    assert scan(repo).returncode == 0


def test_broken_allowlist_line_is_reported_not_skipped(repo: Path) -> None:
    """An allowlist that silently drops entries suppresses nothing and says so
    to nobody."""
    (repo / ".conduct").mkdir()
    (repo / ".conduct" / "names-allow.txt").write_text("get_[user\n", encoding="utf-8")
    result = scan(repo)
    assert result.returncode == 1
    assert "bad-allowlist" in result.stdout


# --- SARIF -------------------------------------------------------------------

def test_sarif_is_written_and_well_formed(repo: Path, tmp_path: Path) -> None:
    plant(repo, """
        data = [1, 2, 3]
    """)
    out = tmp_path / "names.sarif"
    result = scan(repo, "--sarif", str(out))
    assert result.returncode == 1
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["version"] == "2.1.0"
    assert doc["runs"][0]["results"], "SARIF carries no results for a failing run"
    assert doc["runs"][0]["results"][0]["ruleId"] == "vacuous-name"
