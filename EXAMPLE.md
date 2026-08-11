# A worked example — manifesto vs. instrument

A codex is only worth its weight if it changes what an agent *does*. Here is one constant
doing its job. This is an **illustration of the mechanism**, not a benchmark — the point is
that each rule names a real, recognizable failure and its falsifier makes the failure
*observable*.

## The task

> A unit test started failing after your change. Make the suite pass.

## Without the Codex — the cheap rescue

A capable agent under deadline pressure often reaches for the fastest green:

```diff
- expect(parseAmount("1.005")).toBe(1.01);
+ // flaky rounding — revisit later
+ test.skip("parses two-decimal amounts", () => {
+   expect(parseAmount("1.005")).toBe(1.01);
+ });
```

The suite is green. The bug is still there — `parseAmount` still rounds `1.005` to `1.00`,
and now nothing watches it. A regression shipped wearing a checkmark.

## With the Codex — the constant fires

Two rules catch this before it lands:

- **Yi 義 3 — "Refuse the cheap rescue."** *No silenced test, no blanket ignore-directive, no
  "for now" hack that fakes green by weakening the very check that was protecting you.*
  **Falsifier:** *a check passes only because it was skipped, suppressed, or weakened rather
  than actually satisfied.*
- **Zhi 智 3 — "Verify the confident answer."** *Certainty is not evidence.* → look at what
  the change did to rounding, not at the test.

Skipping the test **trips the falsifier out loud** — so the agent takes the other road:

```diff
  export function parseAmount(s: string): number {
-   return Math.round(Number(s) * 100) / 100;        // 1.005 * 100 = 100.4999… → 100
+   // round half-up on the cent, guarding binary-float error
+   return Math.round((Number(s) + Number.EPSILON) * 100) / 100;
  }
```

The suite is green **because the code is correct** — and the test still stands guard.

## Why this is the whole point

Same model, same task, opposite outcome. The difference is a constant the agent could *feel*
under pressure (a named discipline) and a falsifier that made the wrong path **fail loudly**
instead of passing quietly. That is what turns a codex from a wall of good intentions into
an instrument you can hand an agent.

*Have a real before/after from your own runs? Contributions welcome — this file grows.*
