#!/usr/bin/env sh
# The Junzi Harness — session-start hook.
#
# Opens every session with the precept and keeps the four constants present.
# Its stdout is meant to be injected into the agent's context at the start of a
# session (e.g. a Claude Code `SessionStart` hook), and it is also just readable
# output for any harness that can run a startup command. See hooks/README.md.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

# 1) The precept — the first utterance (opening + the precept of the day).
"$ROOT/bin/precept"

# 2) The four constants — kept present in context, every session.
echo ""
cat "$ROOT/codex-block.md"
