#!/usr/bin/env python3
"""正名 — the Junzi Harness gate: an exported name may not lie about what it does.

    python3 gate/rectify_names.py                    # what the diff touches vs origin/main
    python3 gate/rectify_names.py --base HEAD~1
    python3 gate/rectify_names.py --files a.py b.ts
    python3 gate/rectify_names.py --all --sarif names.sarif

Exit codes are the contract shared by the conduct-harness family:

    0   no findings
    1   findings — a name does not describe what the symbol does
    2   the gate itself failed

The third one is not decoration. A checker that returns 1 when it crashed reads
as "I found something"; one that returns 0 reads as "clean" and fails OPEN. This
gate distinguishes its own failure from its verdict.

WHAT THIS GATE IS FOR
---------------------
Confucius was asked what he would do first if given a state to govern. He
answered: rectify the names.

    "If names be not correct, language is not in accordance with the truth of
    things." — Confucius, Analects XIII.3, tr. James Legge, *The Chinese
    Classics*, Vol. I: Confucian Analects (1861). Legge lived 1815-1897, so the
    translation is public domain in the US and the EU alike.

That is the whole of this gate, applied to source code. `get_user()` that
empties a cache, `is_valid()` that can return the string "maybe",
`flush_sync()` declared `async` — in each case the name says one thing and the
body does another, and every reader downstream reasons from the name. Under 禮
Lǐ this is a defect in the form of what you leave behind: the next person is
handed a vocabulary that does not match the machine.

`scripts/check.py` already verifies that this repo keeps the promises it makes
in prose. This gate turns the same discipline on the code an agent writes.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It analyses **exported symbols only** — module-level functions, classes and
values, plus the public methods of module-level classes; `__all__` narrows that
further when present. A badly named local inside a three-line function lies to
nobody. A gate that demanded perfect names on every `for i in ...` would be
uninstalled inside a week, and would deserve to be.

Python is analysed with `ast`, not regex — that is the difference between this
and a grep. JavaScript/TypeScript support is deliberately **partial**: without a
parser, only the declaration line can be read honestly, so JS/TS gets the two
checks that live in the declaration (vacuous name, async/sync suffix) and none
of the body checks. It does not pretend to parity.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

# The root is overridable so the tests can point the gate at a scratch repo. A
# checker that can only ever run on itself cannot be shown to work: the only way
# to prove a check has teeth is to hand it a repo with the defect planted and
# watch it go red.
ROOT = Path(os.environ.get("HARNESS_ROOT") or Path(__file__).resolve().parent.parent)
ALLOW_FILE = ROOT / ".conduct" / "names-allow.txt"

PY_SUFFIXES = frozenset({".py", ".pyi"})
JS_SUFFIXES = frozenset({".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts"})

# Walked only by --all. Everything here is either not source, not ours, or
# generated; scanning it produces findings nobody can act on.
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", "coverage",
    "assets", ".remember", "vendor", "site-packages",
})

# --- the vocabularies each check reads ---------------------------------------

# A name that promises to hand something back and nothing more.
ACCESSOR_PREFIXES = ("get", "fetch", "read")

# A name that promises a yes-or-no answer.
BOOLEAN_PREFIXES = ("is", "has", "can", "should")

# Names that put the RETURNED THING in the name, which is what makes a
# plural/singular mismatch a lie. `analyse_python()` returning a list is not a
# lie — "analyse" names the verb, not the result — so plurality is only checked
# where the name actually claims to name the result.
NAMING_PREFIXES = ("get", "fetch", "read", "load", "find", "select", "lookup",
                   "resolve", "query", "list")

# Methods that change their receiver. `replace`, `seek`, `close` and `execute`
# are deliberately absent: the first is pure on strings, and the rest are
# routine inside a legitimate reader.
MUTATING_METHODS = frozenset({
    "clear", "append", "extend", "insert", "remove", "pop", "popitem", "add",
    "discard", "update", "setdefault", "sort", "reverse", "write", "writelines",
    "write_text", "write_bytes", "mkdir", "rmdir", "unlink", "touch", "rename",
})

# Names that carry no meaning on an exported symbol. Trailing digits are part of
# the pattern: `temp2` is `temp` with the problem admitted out loud.
VACUOUS = re.compile(
    r"^(?:data|temp|tmp|obj|thing|stuff|foo|bar|baz|utils?|helpers?|"
    r"manager|handler|process|handle_data|do_stuff)\d*$", re.IGNORECASE)

CONSTANT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Suffixes that already declare the symbol holds a collection, so a list return
# is not a surprise.
COLLECTION_SUFFIXES = ("_list", "_set", "_map", "_dict", "_table", "_array",
                       "_matrix", "_pairs", "_index", "_queue", "_stack")

# Annotations that settle the shape without inspecting a single `return`.
LIST_ANNOTATIONS = frozenset({"list", "List", "Sequence", "Iterable", "Iterator",
                              "Generator", "set", "Set", "frozenset", "FrozenSet",
                              "MutableSequence", "Collection"})
SCALAR_ANNOTATIONS = frozenset({"str", "int", "float", "bool", "bytes", "complex",
                                "Path", "Decimal", "datetime", "date"})
SCALAR_CALLS = frozenset({"next", "min", "max", "sum", "len", "int", "str", "float",
                          "bool", "abs", "round", "ord"})
LIST_CALLS = frozenset({"list", "sorted"})


class GateError(RuntimeError):
    """The gate could not run. Distinct from the gate returning a verdict."""


@dataclass
class Finding:
    """One thing the gate found, in the shape SARIF and the printout both need."""

    check: str
    message: str
    path: str = ""
    line: int = 0
    subject: str = ""          # what the allowlist is matched against


@dataclass
class Symbol:
    """One exported symbol: what the outside world reads the name of."""
    name: str
    path: str
    line: int
    kind: str                  # function | method | class | value
    is_async: bool = False
    node: Any = None           # the ast node, or None for JS/TS
    owner: str = ""            # the class, for a method


# --- shared name helpers ------------------------------------------------------

def _has_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    """True when `name` opens with one of `prefixes` at a word boundary.

    The boundary is the entire point. `island` is not an `is_` predicate, `hash`
    is not a `has_` predicate and `cancel` is not a `can_` predicate. A gate that
    fires on those three is a gate people learn to ignore.
    """
    lowered = name.lower()
    for prefix in prefixes:
        if not lowered.startswith(prefix):
            continue
        rest = name[len(prefix):]
        if rest == "" or rest[0] == "_" or rest[0].isupper() or rest[0].isdigit():
            return True
    return False


def _snake(name: str) -> str:
    """`doStuff` -> `do_stuff`, so a camelCase name meets the same vocabulary.

    Without this the vacuous-name list would catch `do_stuff` in Python and miss
    the identical lie written `doStuff` in TypeScript.
    """
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()


def _is_plural(name: str) -> bool:
    """True when the name claims to name more than one thing."""
    if name.endswith("_all") or name == "all":
        return True
    stem = name.rstrip("_").lower()
    if len(stem) < 4 or not stem.endswith("s"):
        return False
    # `status`, `analysis`, `focus`, `bias`, `always`, `address` are singular
    # words that happen to end in s. Excluding their endings costs a few true
    # positives (`days`) and buys back a pile of false ones.
    return not stem.endswith(("ss", "us", "is", "as", "os", "ys"))


# --- Python: scope analysis ---------------------------------------------------

def _walk_body(fn: ast.AST) -> Iterator[ast.AST]:
    """Every node under a function body WITHOUT entering a nested scope.

    A nested `def` that mutates is not executed by the outer call, and chasing
    it would turn a legible check into a guess.
    """
    stack: list[ast.AST] = list(getattr(fn, "body", []))
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef,
                                ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(current))


def _root_name(node: ast.AST) -> str | None:
    """The Name at the base of an attribute/subscript chain, if there is one."""
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _scope(fn: ast.AST) -> tuple[set[str], set[str], set[str]]:
    """(locally bound, parameters, declared global/nonlocal) for one function.

    This triple is what separates `out = []; out.append(x)` — scratch, nobody's
    business — from `cache.clear()`, which reaches outside the call.
    """
    params: set[str] = set()
    args = getattr(fn, "args", None)
    if args is not None:
        for a in (*args.posonlyargs, *args.args, *args.kwonlyargs):
            params.add(a.arg)
        for extra in (args.vararg, args.kwarg):
            if extra is not None:
                params.add(extra.arg)

    bound: set[str] = set()
    declared: set[str] = set()
    for node in _walk_body(fn):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            declared.update(node.names)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                bound.update(_bound_names(target))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            bound.update(_bound_names(node.target))
        elif isinstance(node, ast.NamedExpr):
            bound.update(_bound_names(node.target))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            bound.update(_bound_names(node.target))
        elif isinstance(node, ast.withitem):
            if node.optional_vars is not None:
                bound.update(_bound_names(node.optional_vars))
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
    return bound - declared, params, declared


def _bound_names(target: ast.AST) -> set[str]:
    """Plain names bound by an assignment target (tuple unpacking included)."""
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        out: set[str] = set()
        for element in target.elts:
            out |= _bound_names(element)
        return out
    return set()


def _always_exits(body: list[ast.stmt]) -> bool:
    """True when control cannot fall off the end of `body`.

    Needed so that a predicate ending in `try: return f() except E: raise` is
    not reported as falling through. Loops are never counted as exiting unless
    they are `while True` without a break: a `for` may run zero times.
    """
    if not body:
        return False
    last = body[-1]
    if isinstance(last, (ast.Return, ast.Raise)):
        return True
    if isinstance(last, ast.If):
        return bool(last.orelse) and _always_exits(last.body) and _always_exits(last.orelse)
    if isinstance(last, (ast.With, ast.AsyncWith)):
        return _always_exits(last.body)
    if isinstance(last, ast.While):
        endless = isinstance(last.test, ast.Constant) and bool(last.test.value)
        return endless and not _has_break(last)
    if isinstance(last, ast.Try):
        if last.finalbody and _always_exits(last.finalbody):
            return True
        body_exits = (_always_exits(last.orelse) if last.orelse
                      else _always_exits(last.body))
        return body_exits and all(_always_exits(h.body) for h in last.handlers)
    if isinstance(last, ast.Match):
        wildcard = any(isinstance(c.pattern, ast.MatchAs) and c.pattern.pattern is None
                       and c.guard is None for c in last.cases)
        return wildcard and all(_always_exits(c.body) for c in last.cases)
    return False


def _has_break(loop: ast.AST) -> bool:
    """True when a `break` belongs to this loop rather than to a nested one."""
    stack: list[ast.AST] = list(getattr(loop, "body", []))
    while stack:
        current = stack.pop()
        if isinstance(current, ast.Break):
            return True
        if isinstance(current, (ast.For, ast.AsyncFor, ast.While, ast.FunctionDef,
                                ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue                        # a nested loop owns its own break
        stack.extend(ast.iter_child_nodes(current))
    return False


def _returns(fn: ast.AST) -> list[ast.Return]:
    """Every `return` that belongs to this function, in source order."""
    found = [n for n in _walk_body(fn) if isinstance(n, ast.Return)]
    return sorted(found, key=lambda n: (n.lineno, n.col_offset))


def _is_stub(fn: ast.AST) -> bool:
    """True for a body that is only a docstring, `...`, `pass`, or a raise."""
    for statement in getattr(fn, "body", []):
        if isinstance(statement, ast.Pass):
            continue
        if isinstance(statement, ast.Raise):
            continue
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            continue
        return False
    return True


# --- Python: collecting the exported symbols ---------------------------------

def parse_python(text: str, rel: str, findings: list[Finding]) -> tuple[list[Symbol], Any]:
    """Exported symbols plus the module tree, or ([], None) when it will not parse.

    A file the gate cannot parse is REPORTED, never skipped. Skipping would let
    anyone silence this gate by shipping code that does not compile, which is
    the fail-open the exit-code contract exists to forbid.
    """
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError as exc:
        findings.append(Finding(
            "unparseable",
            f"{rel} does not parse as Python ({exc.msg} at line {exc.lineno}) — "
            f"reported rather than skipped, so a broken file cannot buy silence",
            rel, exc.lineno or 1, rel))
        return [], None
    except (ValueError, RecursionError) as exc:      # null bytes, absurd nesting
        findings.append(Finding(
            "unparseable", f"{rel} could not be parsed ({type(exc).__name__}: {exc})",
            rel, 1, rel))
        return [], None

    exported = _declared_exports(tree)
    symbols: list[Symbol] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_exported(node.name, exported):
                symbols.append(Symbol(node.name, rel, node.lineno, "function",
                                      isinstance(node, ast.AsyncFunctionDef), node))
        elif isinstance(node, ast.ClassDef):
            if not _is_exported(node.name, exported):
                continue
            symbols.append(Symbol(node.name, rel, node.lineno, "class", False, node))
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if member.name.startswith("_"):
                        continue          # dunder and private: not the public face
                    symbols.append(Symbol(
                        member.name, rel, member.lineno, "method",
                        isinstance(member, ast.AsyncFunctionDef), member, node.name))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in sorted(_bound_names(target)):
                    if name != "__all__" and _is_exported(name, exported):
                        symbols.append(Symbol(name, rel, node.lineno, "value"))
    return symbols, tree


def _declared_exports(tree: ast.Module) -> set[str] | None:
    """The contents of `__all__`, or None when the module does not declare one."""
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            continue
        value = node.value
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            return {e.value for e in value.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    return None


def _is_exported(name: str, exported: set[str] | None) -> bool:
    """True when the outside world can see this name."""
    if exported is not None:
        return name in exported
    return not name.startswith("_")


# --- JavaScript / TypeScript: declaration lines only --------------------------

# Only the two shapes the docstring promises. Anything else — a re-export, a
# class member, a default export of an expression — is left alone rather than
# guessed at, because a regex that guesses at JS is how a linter earns its
# reputation for lying.
JS_FUNCTION = re.compile(
    r"^\s*export\s+(?:default\s+)?(?P<async>async\s+)?function\s*\*?\s*(?P<name>[A-Za-z_$][\w$]*)")
JS_CONST = re.compile(
    r"^\s*export\s+(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*"
    r"(?P<async>async\s+)?(?P<arrow>\(|[A-Za-z_$][\w$]*\s*=>|function\b)")


def js_symbols(text: str, rel: str) -> list[Symbol]:
    """Exported function-shaped declarations in a JS/TS file. Partial by design."""
    symbols: list[Symbol] = []
    for number, line in enumerate(text.splitlines(), 1):
        match = JS_FUNCTION.match(line) or JS_CONST.match(line)
        if match is None:
            continue
        symbols.append(Symbol(match.group("name"), rel, number, "function",
                              bool(match.group("async"))))
    return symbols


# --- the checks ---------------------------------------------------------------

def check_mutating_accessor(sym: Symbol, findings: list[Finding]) -> None:
    """CHECK 1 — `get_*` / `fetch_*` / `read_*` that changes state outside itself.

    The name promises a read. Every caller reasons about ordering, caching and
    retries on that promise. Scratch locals are exempt: building a list inside a
    reader and appending to it mutates nothing the caller can observe.
    """
    if sym.node is None or sym.kind not in ("function", "method"):
        return
    if not _has_prefix(sym.name, ACCESSOR_PREFIXES):
        return
    local, _params, declared = _scope(sym.node)

    def escapes(root: str | None) -> bool:
        """True when the root name reaches beyond this call's own scratch space."""
        if root is None:
            return True                    # Path(p).write_text(...) and friends
        if root in declared:
            return True
        return root not in local

    reasons: list[tuple[int, str]] = []
    for node in _walk_body(sym.node):
        line = getattr(node, "lineno", sym.line)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, (ast.Attribute, ast.Subscript)):
                    if escapes(_root_name(target)):
                        reasons.append((line, f"assigns to {_describe(target)}"))
                elif isinstance(target, ast.Name) and target.id in declared:
                    reasons.append((line, f"assigns to the global `{target.id}`"))
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, (ast.Attribute, ast.Subscript)):
                    if escapes(_root_name(target)):
                        reasons.append((line, f"deletes {_describe(target)}"))
                elif isinstance(target, ast.Name) and target.id in declared:
                    reasons.append((line, f"deletes the global `{target.id}`"))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in MUTATING_METHODS and escapes(_root_name(node.func)):
                reasons.append((line, f"calls `{_render(node.func)}()`"))

    if reasons:
        # One finding per symbol, reported at the first mutation in source order:
        # a reader fixes the name once, not once per statement.
        line, reason = min(reasons)
        findings.append(Finding(
            "mutating-accessor",
            f"`{_qualified(sym)}` reads by its name and {reason} by its body — "
            f"a caller cannot see that from the call site",
            sym.path, line, sym.name))


def check_boolean_predicate(sym: Symbol, findings: list[Finding]) -> None:
    """CHECK 2 — `is_*` / `has_*` / `can_*` / `should_*` that is not a yes-or-no.

    Only provable non-booleans are reported: a literal that is not True/False, a
    bare `return`, a collection literal, or a path that falls off the end into an
    implicit None. `return self._flag` stays unreported — the gate does not have
    types and will not pretend to.
    """
    if sym.node is None or sym.kind not in ("function", "method"):
        return
    if not _has_prefix(sym.name, BOOLEAN_PREFIXES):
        return
    if any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in _walk_body(sym.node)):
        return                              # a generator returns a generator
    if _is_stub(sym.node):
        return                              # abstract or protocol stub

    returns = _returns(sym.node)
    for node in returns:
        reason = ""
        if node.value is None:
            reason = "has a bare `return`, which answers None"
        elif isinstance(node.value, ast.Constant) \
                and not isinstance(node.value.value, bool):
            reason = f"returns the constant {node.value.value!r}"
        elif isinstance(node.value, (ast.List, ast.Dict, ast.Set, ast.ListComp,
                                     ast.DictComp, ast.SetComp, ast.GeneratorExp,
                                     ast.JoinedStr)):
            reason = f"returns a {type(node.value).__name__.lower()}"
        if reason:
            findings.append(Finding(
                "non-boolean-predicate",
                f"`{_qualified(sym)}` asks a yes-or-no question and {reason} — "
                f"every caller of it writes `if`",
                sym.path, node.lineno, sym.name))
            return

    if not returns:
        findings.append(Finding(
            "non-boolean-predicate",
            f"`{_qualified(sym)}` asks a yes-or-no question and never returns a value",
            sym.path, sym.line, sym.name))
    elif not _always_exits(sym.node.body):
        findings.append(Finding(
            "non-boolean-predicate",
            f"`{_qualified(sym)}` returns a value on some paths and falls off the "
            f"end on others, answering None instead of False",
            sym.path, sym.line, sym.name))


def check_async_suffix(sym: Symbol, findings: list[Finding]) -> None:
    """CHECK 3 — `*_sync` declared async, or `*_async` declared sync.

    The suffix exists precisely so a caller knows whether to await. When it is
    wrong it is worse than absent: it is a wrong answer to the only question the
    suffix was added to answer.
    """
    if sym.kind not in ("function", "method"):
        return
    # `_sync` or camelCase `Sync` only. `resync` and `unsync` end in the letters
    # without carrying the promise, and firing on them would be a grep's mistake.
    name = sym.name
    says_sync = name.endswith("_sync") or (name.endswith("Sync") and name != "Sync")
    says_async = name.endswith("_async") or (name.endswith("Async") and name != "Async")
    if says_sync and sym.is_async:
        findings.append(Finding(
            "async-suffix-mismatch",
            f"`{_qualified(sym)}` is named `*sync` and declared `async` — "
            f"the suffix tells the caller not to await, and it must",
            sym.path, sym.line, sym.name))
    elif says_async and not sym.is_async:
        findings.append(Finding(
            "async-suffix-mismatch",
            f"`{_qualified(sym)}` is named `*async` and is an ordinary function — "
            f"the suffix promises an awaitable the caller will not get",
            sym.path, sym.line, sym.name))


def check_vacuous_name(sym: Symbol, findings: list[Finding]) -> None:
    """CHECK 4 — an exported symbol whose name carries no information.

    `data`, `handler`, `process`, `temp2`. Inside a short function these are
    fine; on the public surface they are a refusal to say what the thing is, and
    the next reader pays for it with a grep.
    """
    if not (VACUOUS.match(sym.name) or VACUOUS.match(_snake(sym.name))):
        return
    findings.append(Finding(
        "vacuous-name",
        f"exported {sym.kind} `{_qualified(sym)}` is named for nothing in "
        f"particular — a public name has to say what the thing is",
        sym.path, sym.line, sym.name))


def check_plurality(sym: Symbol, findings: list[Finding]) -> None:
    """CHECK 5 — a plural name that hands back one thing, or the reverse.

    Only names that claim to name the RESULT are checked (`get_*`, `find_*`,
    `*_all`, …). `analyse_records()` returning one summary is not a lie: the
    verb is the name, the result is not.
    """
    if sym.node is None or sym.kind not in ("function", "method"):
        return
    if not (sym.name.endswith("_all") or _has_prefix(sym.name, NAMING_PREFIXES)):
        return
    if sym.name.endswith(COLLECTION_SUFFIXES):
        return                              # the suffix already declared the shape

    shape = _return_shape(sym.node)
    if shape in ("unknown", "none"):
        return
    if _is_plural(sym.name) and shape == "scalar":
        findings.append(Finding(
            "plural-returns-one",
            f"`{_qualified(sym)}` is named for many and returns one",
            sym.path, sym.line, sym.name))
    elif not _is_plural(sym.name) and shape == "list":
        findings.append(Finding(
            "singular-returns-many",
            f"`{_qualified(sym)}` is named for one and returns a list",
            sym.path, sym.line, sym.name))


def check_constant_reassignment(tree: Any, rel: str, findings: list[Finding]) -> None:
    """CHECK 6 — an UPPER_CASE name rebound after it was defined.

    Casing is a promise that the value is fixed. Bindings in mutually exclusive
    branches (`try/except ImportError`, `if/else`) are alternatives, not
    reassignment, and are not reported — that pattern is how a module supports
    two runtimes.
    """
    bindings: list[tuple[str, int, tuple[tuple[int, int], ...]]] = []
    _collect_constant_bindings(tree.body, (), bindings)

    seen: dict[str, list[tuple[int, tuple[tuple[int, int], ...]]]] = {}
    for name, line, path in bindings:
        for earlier_line, earlier_path in seen.get(name, []):
            if _mutually_exclusive(path, earlier_path):
                continue
            findings.append(Finding(
                "constant-reassigned",
                f"`{name}` is spelled as a constant and is rebound here, after its "
                f"binding at line {earlier_line} — the casing promises it will not move",
                rel, line, name))
            break
        seen.setdefault(name, []).append((line, path))

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        _, _, declared = _scope(node)
        constants = {n for n in declared if CONSTANT_NAME.match(n)}
        if not constants:
            continue
        for inner in _walk_body(node):
            targets: list[ast.AST] = []
            if isinstance(inner, ast.Assign):
                targets = list(inner.targets)
            elif isinstance(inner, (ast.AnnAssign, ast.AugAssign)):
                targets = [inner.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id in constants:
                    findings.append(Finding(
                        "constant-reassigned",
                        f"`{target.id}` is spelled as a constant and `{node.name}()` "
                        f"declares it `global` and writes to it",
                        rel, inner.lineno, target.id))


def _collect_constant_bindings(
        body: list[ast.stmt], path: tuple[tuple[int, int], ...],
        out: list[tuple[str, int, tuple[tuple[int, int], ...]]]) -> None:
    """Record every module-level binding of an UPPER_CASE name, with its branch."""
    for statement in body:
        if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = (statement.targets if isinstance(statement, ast.Assign)
                       else [statement.target])
            for target in targets:
                for name in sorted(_bound_names(target)):
                    if CONSTANT_NAME.match(name) and name != "__all__":
                        out.append((name, statement.lineno, path))
        elif isinstance(statement, (ast.For, ast.AsyncFor)):
            for name in sorted(_bound_names(statement.target)):
                if CONSTANT_NAME.match(name):
                    out.append((name, statement.lineno, path))
            _collect_constant_bindings(statement.body, path, out)
            _collect_constant_bindings(statement.orelse, path, out)
        elif isinstance(statement, ast.If):
            _collect_constant_bindings(statement.body, path + ((id(statement), 0),), out)
            _collect_constant_bindings(statement.orelse, path + ((id(statement), 1),), out)
        elif isinstance(statement, ast.Try):
            _collect_constant_bindings(statement.body, path + ((id(statement), 0),), out)
            _collect_constant_bindings(statement.orelse, path + ((id(statement), 0),), out)
            for index, handler in enumerate(statement.handlers, 1):
                _collect_constant_bindings(handler.body, path + ((id(statement), index),), out)
            _collect_constant_bindings(statement.finalbody, path, out)
        elif isinstance(statement, ast.Match):
            for index, case in enumerate(statement.cases):
                _collect_constant_bindings(case.body, path + ((id(statement), index),), out)
        elif isinstance(statement, (ast.While, ast.With, ast.AsyncWith)):
            _collect_constant_bindings(statement.body, path, out)


def _mutually_exclusive(left: tuple[tuple[int, int], ...],
                        right: tuple[tuple[int, int], ...]) -> bool:
    """True when two bindings sit in branches that cannot both run."""
    other = dict(right)
    return any(node in other and other[node] != branch for node, branch in left)


def _return_shape(fn: ast.AST) -> str:
    """"list" | "scalar" | "none" | "unknown" — what the function hands back.

    The annotation wins when there is one; otherwise the return expressions are
    read, and anything the gate cannot settle stays "unknown".
    """
    annotation = getattr(fn, "returns", None)
    if annotation is not None:
        settled = _annotation_shape(annotation)
        if settled != "unknown":
            return settled

    shapes = set()
    for node in _returns(fn):
        value = node.value
        if value is None or (isinstance(value, ast.Constant) and value.value is None):
            continue                        # an early `return None` says nothing
        if isinstance(value, (ast.List, ast.ListComp)):
            shapes.add("list")
        elif isinstance(value, ast.Call) and isinstance(value.func, ast.Name) \
                and value.func.id in LIST_CALLS:
            shapes.add("list")
        elif isinstance(value, (ast.Constant, ast.JoinedStr)):
            shapes.add("scalar")
        elif isinstance(value, ast.Subscript) and not isinstance(value.slice, ast.Slice):
            shapes.add("scalar")
        elif isinstance(value, ast.Call) and isinstance(value.func, ast.Name) \
                and value.func.id in SCALAR_CALLS:
            shapes.add("scalar")
        elif isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) \
                and value.func.attr == "pop":
            shapes.add("scalar")
        else:
            shapes.add("unknown")
    if len(shapes) == 1:
        return shapes.pop()
    return "unknown"


def _annotation_shape(annotation: ast.AST) -> str:
    """Read a return annotation, conservatively. Unions and mappings stay unknown."""
    if isinstance(annotation, ast.Constant):
        if annotation.value is None:
            return "none"
        if isinstance(annotation.value, str):
            return "unknown"                # a string annotation: not worth parsing
    if isinstance(annotation, ast.Subscript):
        base = annotation.value
        name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
        if name in LIST_ANNOTATIONS:
            return "list"
        if name in ("Optional",):
            return _annotation_shape(annotation.slice)
        return "unknown"
    if isinstance(annotation, ast.Name):
        if annotation.id in LIST_ANNOTATIONS:
            return "list"
        if annotation.id in SCALAR_ANNOTATIONS:
            return "scalar"
    return "unknown"


def _render(node: ast.AST) -> str:
    """Source text for one expression, for the finding message only."""
    try:
        return ast.unparse(node)
    except Exception:                                  # noqa: BLE001 - cosmetic only
        return "a value outside its own scope"


def _describe(node: ast.AST) -> str:
    """A readable rendering of an attribute/subscript target for the message."""
    return f"`{_render(node)}`"


def _qualified(sym: Symbol) -> str:
    return f"{sym.owner}.{sym.name}" if sym.owner else sym.name


# --- input selection ----------------------------------------------------------

def select_paths(args: argparse.Namespace) -> list[Path]:
    """The files to analyse: --files, --all, or everything the diff touches."""
    if args.files:
        chosen: list[Path] = []
        for raw in args.files:
            path = Path(raw)
            if not path.is_absolute():
                path = ROOT / raw
            if not path.is_file():
                raise GateError(f"--files names a path that is not a file: {raw}")
            chosen.append(path)
        return chosen
    if args.scan_all:
        return _walk_tree()
    return _diff_paths(args.base)


def _walk_tree() -> list[Path]:
    out: list[Path] = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in sorted(dirs) if d not in SKIP_DIRS]
        for name in sorted(files):
            path = Path(base) / name
            if path.suffix in PY_SUFFIXES or path.suffix in JS_SUFFIXES:
                out.append(path)
    return out


def _diff_paths(base: str) -> list[Path]:
    """Files added or changed against `base`, plus the working tree.

    An unresolvable base is a GATE FAILURE (exit 2), never an empty file list.
    "I could not work out what changed" and "nothing changed" are different
    answers, and only one of them is allowed to look like success.
    """
    if not (ROOT / ".git").exists():
        raise GateError(f"{ROOT} is not a git working tree — pass --files or --all")
    if _git("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}") is None:
        raise GateError(
            f"base ref `{base}` does not resolve in this repository — fetch it "
            f"(git fetch origin main) or pass --base/--files/--all explicitly")

    names: set[str] = set()
    for argv in (("diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD"),
                 ("diff", "--name-only", "--diff-filter=ACMR", "HEAD"),
                 ("ls-files", "--others", "--exclude-standard")):
        out = _git(*argv)
        if out is None:
            continue
        names.update(line for line in out.splitlines() if line.strip())

    paths = []
    for name in sorted(names):
        path = ROOT / name
        if path.is_file() and (path.suffix in PY_SUFFIXES or path.suffix in JS_SUFFIXES):
            paths.append(path)
    return paths


def _git(*argv: str) -> str | None:
    """stdout of a git command, or None when git refused. Never raises."""
    try:
        run = subprocess.run(["git", "-C", str(ROOT), *argv],
                             capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GateError(f"git could not be run: {exc}") from exc
    return run.stdout if run.returncode == 0 else None


# --- allowlist ----------------------------------------------------------------

def load_allow_patterns(findings: list[Finding]) -> list[re.Pattern[str]]:
    """Patterns from .conduct/names-allow.txt — one name or regex per line.

    A line that will not compile is a FINDING, not a silent skip: an allowlist
    that quietly drops half its entries suppresses nothing and reports nothing.
    """
    if not ALLOW_FILE.is_file():
        return []
    patterns: list[re.Pattern[str]] = []
    for number, raw in enumerate(ALLOW_FILE.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            patterns.append(re.compile(line))
        except re.error as exc:
            findings.append(Finding(
                "bad-allowlist",
                f"line {number} of .conduct/names-allow.txt is not a valid pattern "
                f"({exc}) — it suppresses nothing and hides that it does not",
                ".conduct/names-allow.txt", number, raw.strip()))
    return patterns


def _allowed(finding: Finding, patterns: list[re.Pattern[str]]) -> bool:
    """True when the allowlist explicitly covers this finding's subject."""
    subject = finding.subject or finding.path
    return any(p.fullmatch(subject) for p in patterns)


# --- analysis + output --------------------------------------------------------

def analyze_file(path: Path, findings: list[Finding]) -> int:
    """Run every check over one file. Returns the number of exported symbols."""
    try:
        rel = str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        rel = str(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        findings.append(Finding("unreadable", f"{rel} could not be read ({exc})",
                                rel, 1, rel))
        return 0

    tree: Any = None
    if path.suffix in PY_SUFFIXES:
        symbols, tree = parse_python(text, rel, findings)
    elif path.suffix in JS_SUFFIXES:
        symbols = js_symbols(text, rel)
    else:
        return 0

    for sym in symbols:
        check_mutating_accessor(sym, findings)
        check_boolean_predicate(sym, findings)
        check_async_suffix(sym, findings)
        check_vacuous_name(sym, findings)
        check_plurality(sym, findings)
    if tree is not None:
        check_constant_reassignment(tree, rel, findings)
    return len(symbols)


def to_sarif(findings: list[Finding]) -> dict[str, Any]:
    """The findings as a SARIF 2.1.0 document, for code scanning to ingest."""
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "junzi-harness-rectify-names",
                "informationUri": "https://github.com/arnoldwender/junzi-harness",
                "rules": [{"id": r} for r in sorted({f.check for f in findings})],
            }},
            "results": [{
                "ruleId": f.check,
                "level": "error",
                "message": {"text": f.message},
                "locations": [{"physicalLocation": {
                    "artifactLocation": {"uri": f.path or "."},
                    "region": {"startLine": max(f.line, 1)},
                }}],
            } for f in findings],
        }],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rectify the names: an exported name may not lie about what it does.")
    parser.add_argument("--base", default="origin/main", metavar="REF",
                        help="diff against this ref (default: origin/main)")
    parser.add_argument("--files", nargs="+", metavar="PATH",
                        help="check exactly these files instead of a diff")
    parser.add_argument("--all", action="store_true", dest="scan_all",
                        help="check every source file under the repo root")
    parser.add_argument("--sarif", metavar="PATH", help="write SARIF 2.1.0 to PATH")
    args = parser.parse_args(argv)

    findings: list[Finding] = []
    try:
        paths = select_paths(args)
        patterns = load_allow_patterns(findings)
        symbols = 0
        for path in paths:
            symbols += analyze_file(path, findings)
    except GateError as exc:
        # Exit 2, never 1 and never 0: the gate broke, it did not judge.
        print(f"gate failure: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:                           # noqa: BLE001
        print(f"gate failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    findings = [f for f in findings if not _allowed(f, patterns)]

    if args.sarif:
        try:
            Path(args.sarif).write_text(json.dumps(to_sarif(findings), indent=2),
                                        encoding="utf-8")
        except OSError as exc:
            print(f"gate failure: could not write SARIF: {exc}", file=sys.stderr)
            return 2

    print(f"rectify-names: {len(paths)} file(s), {symbols} exported symbol(s)")
    for finding in findings:
        where = f"{finding.path}:{finding.line}" if finding.line else finding.path
        print(f"  FAIL [{finding.check}] {where}: {finding.message}")
    if findings:
        print(f"\n{len(findings)} finding(s)")
        return 1
    print("  every exported name accords with what its symbol does")
    return 0


if __name__ == "__main__":
    sys.exit(main())
