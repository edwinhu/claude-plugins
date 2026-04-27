---
name: ds-implement
description: "Run statistical analyses, build models, generate visualizations, and process datasets with verified output at every step. Use when the user asks to analyze data, run regressions, create plots, explore datasets, or execute a data science analysis plan."
user-invocable: false
disable-model-invocation: true
triggers:
  - analyze data
  - run regression
  - create plots
  - explore dataset
  - generate statistical summaries
  - build predictive model
  - process dataset
  - implement analysis
  - execute analysis tasks
allowed-tools: Read, Grep, Glob, Bash, Skill, TodoWrite, Agent
hooks:
  PostToolUse:
    - matcher: "Agent"
      hooks:
        - type: command
          command: "uv run python3 ${CLAUDE_PLUGIN_ROOT}/hooks/ds-post-subagent-guard.py"
  PreToolUse:
    - matcher: "Agent"
      hooks:
        - type: command
          command: "uv run python3 ${CLAUDE_PLUGIN_ROOT}/hooks/ds-pre-subagent-clear.py"
    - matcher: "Read"
      hooks:
        - type: command
          command: "uv run python3 ${CLAUDE_PLUGIN_ROOT}/hooks/ds-read-after-subagent-guard.py"
    - matcher: "Grep"
      hooks:
        - type: command
          command: "uv run python3 ${CLAUDE_PLUGIN_ROOT}/hooks/ds-read-after-subagent-guard.py"
    - matcher: "Glob"
      hooks:
        - type: command
          command: "uv run python3 ${CLAUDE_PLUGIN_ROOT}/hooks/ds-read-after-subagent-guard.py"
    - matcher: "Write"
      hooks:
        - type: command
          command: "uv run python3 ${CLAUDE_PLUGIN_ROOT}/hooks/ds-no-main-chat-code-guard.py"
    - matcher: "Edit"
      hooks:
        - type: command
          command: "uv run python3 ${CLAUDE_PLUGIN_ROOT}/hooks/ds-no-main-chat-code-guard.py"
    - matcher: "Bash"
      hooks:
        - type: command
          command: "uv run python3 ${CLAUDE_PLUGIN_ROOT}/hooks/ds-no-main-chat-code-guard.py"
---

## Overview

Apply output-first verification at every step of analysis implementation. This is Phase 3 of the `/ds` workflow.

## Contents

- [The Iron Law of DS Implementation](#the-iron-law-of-ds-implementation) - EVERY step MUST produce visible output
- [Delegation](#delegation) - Main chat orchestrates, subagents analyze
- [What Output-First Means](#what-output-first-means)
- [Red Flags](#red-flags---stop-immediately)
- [SAS Language Routing](#sas-language-routing) - Load SAS enforcement when PLAN.md specifies SAS
- [Implementation Process](#implementation-process)
- [Verification Patterns](#verification-patterns) - See `references/verification-patterns.md`
- [Common Failures](#common-failures-to-avoid)
- [Gate: Exit Implementation](#gate-exit-implementation)

# Implementation (Output-First Verification)

Implement analysis with mandatory visible output at every step.
**NO TDD** - instead, every code step MUST produce and verify output.

## The Iron Law of DS Implementation

**EVERY CODE STEP MUST PRODUCE VISIBLE OUTPUT.**

Before moving to the next step: Run the code → See the output (print shape, display samples, show stats, verify row counts, check nulls) → Verify it's correct → Document in `.planning/LEARNINGS.md` → Proceed.

## Delegation

**YOU MUST NOT WRITE ANALYSIS CODE IN MAIN CHAT.**

You orchestrate. Subagents analyze. For every task in PLAN.md:

Read `${CLAUDE_SKILL_DIR}/../../skills/ds-delegate/SKILL.md` and follow its instructions.

If you wrote analysis code in main chat, DELETE it immediately and dispatch a Task agent instead — code written in main chat is contaminated by orchestrator context.

## STOP If You Think:

| Thought | Do Instead |
|---------|------------|
| "I'll check at the end" | Check after every step |
| "I know this worked" | Print shape/stats, verify against PLAN.md |
| "Quick plot in main chat" | Spawn a Task agent |
| "Trivial, no need to delegate" | Delegate everything |
| "I'll read agent output later" | Read immediately, verify against data |


## Implementation Strategy Choice

After prerequisites pass and PLAN.md verified, check for parallelization potential:

**Skip this choice when:**
- PLAN.md has fewer than 4 tasks
- All tasks are dependent (every task is `after N` with no independent groups)
- Tasks form a pipeline (clean → merge → aggregate → model)
- `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` is not available

**Otherwise, ask the user:**

```python
AskUserQuestion(questions=[{
  "question": "How should we implement the analysis tasks in PLAN.md?",
  "header": "Strategy",
  "options": [
    {"label": "Sequential (Default)", "description": "One task at a time with output-first verification. Safest, most DS work is sequential."},
    {"label": "Agent team (parallel)", "description": "Spawn analyst per independent task group. Only for truly independent analysis branches (descriptive stats by subgroup, model comparisons). Requires reconciliation."}
  ],
  "multiSelect": false
}])
```

**If Sequential:** Proceed to [Implementation Process](#implementation-process) below (current behavior).

**If Agent team:** Skip to [Agent Team Implementation (Parallel)](#agent-team-implementation-parallel).


## SAS Language Routing

If PLAN.md specifies `Implementation Language: SAS` or `Mixed`, load SAS enforcement BEFORE dispatching any SAS tasks. Paste the enforcement block into every SAS subagent prompt.

> **Full SAS enforcement rules:** See [references/sas-enforcement.md](references/sas-enforcement.md)

## Implementation Process Flowchart

```
┌─────────────────────────┐
│  Read PLAN.md + Load    │
│  ds-delegate + ETL refs │
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│  For each task in PLAN  │◄──────────────────────┐
│  (in dependency order)  │                       │
└───────────┬─────────────┘                       │
            ▼                                     │
┌─────────────────────────┐                       │
│  Dispatch Task agent    │                       │
│  (per ds-delegate)      │                       │
└───────────┬─────────────┘                       │
            ▼                                     │
┌─────────────────────────┐     ┌──────────────┐  │
│  Read agent output      │────→│ Output wrong │  │
│  Verify output present  │     │ or missing?  │  │
│  + reasonable            │     └──────┬───────┘  │
└───────────┬─────────────┘            │           │
            │ OK                       ▼           │
            │                 ┌──────────────────┐ │
            │                 │ STOP. Investigate │ │
            │                 │ Log issue. Fix.   │ │
            │                 │ Re-verify.        │ │
            │                 └──────────────────┘ │
            ▼                                     │
┌─────────────────────────┐                       │
│  Log to LEARNINGS.md    │                       │
│  (Task N: COMPLETE)     │                       │
└───────────┬─────────────┘                       │
            ▼                                     │
        More tasks? ──── YES ─────────────────────┘
            │
            NO
            ▼
┌─────────────────────────┐
│  Exit Gate: Compare     │
│  PLAN.md vs LEARNINGS   │
│  (all tasks accounted?) │
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│  Invoke ds-validate     │
└─────────────────────────┘
```

**This flowchart IS the specification.** If the narrative below and this flowchart disagree, the flowchart wins.

## Topic Change Protocol

If user sends an off-topic message: Announce pause → Handle request → Announce resume → Reload LEARNINGS.md and PLAN.md → Continue. Do NOT silently switch context.

## Implementation Process

### Step 1: Read Plan, Load Shared Enforcement, and Delegation Skill

Auto-load all constraints matching `applies-to: ds-implement`:

!`uv run python3 ${CLAUDE_SKILL_DIR}/../../scripts/load-constraints.py ds-implement`

**You MUST have these constraints loaded before proceeding. No claiming you "remember" them.**

```
Read(".planning/PLAN.md")
```

Read `${CLAUDE_SKILL_DIR}/../../skills/ds-delegate/SKILL.md` and follow its instructions.

Follow the task order defined in the plan. Use ds-delegate's templates for every task.

**ETL Strategy Enforcement:** If PLAN.md contains an `## ETL Strategy` section, load the corresponding enforcement references before dispatching tasks:
- SAS language → Read `${CLAUDE_SKILL_DIR}/../../skills/wrds/references/sas-etl.md`
- Filter/Parallelism/Caching/Scale-Up → Read `${CLAUDE_SKILL_DIR}/../../skills/ds-implement/references/etl-enforcement.md`

If PLAN.md has no ETL Strategy section, skip to Step 2.

### Context Monitoring

Before starting each task, check context availability:

| Level | Remaining Context | Action |
|-------|------------------|--------|
| Normal | >35% | Proceed with task |
| Warning | 25-35% | Complete current task, then invoke ds-handoff |
| Critical | ≤25% | Invoke ds-handoff immediately — no new tasks |

**At Warning level:** After current task completes, invoke:
Read `${CLAUDE_SKILL_DIR}/../../skills/ds-handoff/SKILL.md` and follow its instructions.

### Step 2: Execute Each Task via Delegation

For each task in PLAN.md:
1. Dispatch analyst subagent (per ds-delegate pattern)
2. Verify outputs are present and reasonable
3. Dispatch methodology reviewer (for statistical tasks)
4. Log findings to LEARNINGS.md

### Step 3: Log to LEARNINGS.md

Document every significant step:

```markdown
## Task N: [Description] - COMPLETE

**Input:** [Describe input state]

**Operation:** [What was done]

**Output:**
- Shape: [final shape]
- Key findings: [observations]

**Verification:** [How you confirmed it worked]

**Next:** [What comes next]
```

### Task Summary (MANDATORY after each task)

After a task passes review, append a structured summary to LEARNINGS.md:

```yaml
## Task N: [task description]

---
task: N
status: completed
implements: [DATA-01, STAT-03]
affects: [notebooks/analysis.py, data/processed/]
key-files:
  created: [list of new files]
  modified: [list of changed files]
deviations: {r1: 0, r2: 1, r3: 0, r4: 0}
---

One-liner: [SUBSTANTIVE summary — not "Task complete" but "Merged CRSP-Compustat panel with winsorized returns at 1%/99%"]

Changes: [what was added/modified and why]
Output: [output files produced and their contents]
```

**One-liner rule:** Must be SUBSTANTIVE. Good: "Panel regression with firm and year FE, clustered SEs, 3 robustness checks". Bad: "Completed task 3".

## Verification Patterns

See [references/verification-patterns.md](references/verification-patterns.md) for detailed code patterns for:
- Data loading, filtering, merging
- Aggregation and model training
- Batch pipeline scale-up testing (submission, validation, cost extrapolation)
- Quick reference table by operation type

See [references/etl-enforcement.md](references/etl-enforcement.md) for ETL strategy enforcement:
- Filter push-down (database vs application vs hybrid)
- Parallelism (Task agents vs SGE vs sequential)
- Intermediate caching (parquet vs CSV vs SQLite)
- Scale-up testing domain routing

## Scale-Up Testing Protocol (Batch/ETL Operations)

**Triggers when PLAN.md includes a Scale-Up Testing Plan table.** NO FULL BATCH WITHOUT A SUCCESSFUL TEST BATCH.

Three stages: Test (~10 items) → Intermediate (~100, if total >500) → Large (~1,000, if total >5,000). Each stage has quality gates.

> **Full protocol:** See [references/scale-up-testing.md](references/scale-up-testing.md)

## If Output Looks Wrong

1. **STOP** — do not proceed
2. **Investigate** — print more details (row counts, nulls, dtypes, min/max dates)
3. **Document** — log the issue in LEARNINGS.md
4. **Ask** — if unclear, ask user for guidance
5. **Fix** — only proceed after output verified

## No Pause Between Tasks

After completing task N, IMMEDIATELY start task N+1. Only pause when ALL tasks complete or you're blocked.


## Deviation Rules

When subagents encounter unplanned issues during implementation, follow this 4-rule system:

| Rule | Trigger | Action | Permission |
|------|---------|--------|------------|
| **R1: Bug** | Data integrity bugs, wrong joins, type errors, off-by-one in date ranges, NaN propagation, index alignment errors | Fix → verify output with output-first protocol → track `[Rule 1 - Bug]` | Auto |
| **R2: Missing Critical** | Missing null handling, no dedup check after merge, missing row count verification, no dtype validation, missing outlier handling | Add → verify → track `[Rule 2 - Missing Critical]` | Auto |
| **R3: Blocking** | Missing dependency/package, wrong file path, data file unavailable, API rate limit, memory error on large data | Fix blocker → verify proceeds → track `[Rule 3 - Blocking]` | Auto |
| **R4a: Data Assumption** | Data doesn't match expected shape/schema/distribution — expected panel but got cross-section, unexpected nulls in key column, different date range than specified, unexpected categories | **STOP** → present finding with evidence → track `[Rule 4a - Data Assumption]` | **Ask user** |
| **R4b: Methodology Change** | Analysis approach needs changing — different model needed, different sample definition, different variable construction, need to add/remove control variables | **STOP** → present decision with alternatives → track `[Rule 4b - Methodology]` | **Ask user** |

**Priority:** R4a/R4b (STOP) > R1-R3 (auto) > unsure → escalate as R4.

**Tracking:** Each task summary ends with: **Deviations:** N auto-fixed (R1: X, R2: Y, R3: Z). **R4 escalations:** [list or "none"].

## Agent Team Implementation (Parallel)

> **Full protocol:** See [references/agent-team-protocol.md](references/agent-team-protocol.md) for prerequisites, spawn prompt template, lead monitoring, reconciliation (3 passes), and usage guidelines.

Key points:
1. Run foundation tasks sequentially FIRST, then spawn parallel teammates
2. Each teammate gets exclusive data scope and output files
3. After all teammates complete, lead performs 3 reconciliation passes (Collect, Verify, Methodology)
4. **Default is sequential.** Only use agent teams for 4+ tasks with true independence (different datasets/subsets, no shared output files)

## Gate: Exit Implementation

**Checkpoint type:** human-verify (all tasks pass — machine-verifiable)

Before proceeding to validation, execute this gate:

1. **IDENTIFY**: Read `.planning/PLAN.md` — list every task by number and name
2. **RUN**: Read `.planning/LEARNINGS.md` — find entries for each task
3. **READ**: For each task, confirm LEARNINGS.md contains a "Task N: [Name] - COMPLETE" entry with verified output and no unresolved issues
4. **VERIFY**: Count tasks in PLAN.md vs completed entries in LEARNINGS.md — they MUST match
5. **CLAIM**: Only if all tasks accounted for, proceed to review

**Staleness Check:** LEARNINGS.md must be updated in THIS session. If stale from a prior session, UPDATE with fresh entries before claiming completion.

**If ANY task is missing from LEARNINGS.md, implement it before proceeding.**

## Phase Complete

After passing the exit gate, IMMEDIATELY discover and read the validation phase:
Read `${CLAUDE_SKILL_DIR}/../../skills/ds-validate/SKILL.md` and follow its instructions. Follow its instructions to validate outputs before review.
