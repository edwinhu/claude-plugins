---
name: dev-implement
description: “Orchestrate multi-task code implementation by delegating to subagents with test-driven development, per-task verification, and structured deviation handling. Use when the user asks to implement the plan, start building, execute development tasks, start coding, or kick off implementation.”
user-invocable: false
disable-model-invocation: true
triggers:
  - implement the plan
  - start building
  - execute the tasks
  - start coding
  - kick off implementation
  - run implementation
  - start development
  - execute the plan
allowed-tools: Read, Grep, Glob, Bash, Skill, TodoWrite, Agent
hooks:
  PreToolUse:
    - matcher: “Write|Edit”
      hooks:
        - type: command
          command: “uv run python3 ${CLAUDE_PLUGIN_ROOT}/hooks/dev-delegation-guard.py”
    - matcher: “Agent”
      hooks:
        - type: command
          command: >-
            GATE_ARTIFACT=.planning/PLAN_REVIEWED.md
            GATE_STATUS=APPROVED
            GATE_DESCRIPTION=”Plan review”
            GATE_REMEDY=”Return to dev-design Phase Complete and run dev-plan-reviewer. It writes PLAN_REVIEWED.md on approval.”
            uv run python3 ${CLAUDE_PLUGIN_ROOT}/hooks/phase-gate-guard.py
---

**Announce:** “I’m using dev-implement (Phase 5) to orchestrate implementation.”

**Load shared enforcement:**

Auto-load all constraints matching `applies-to: dev-implement`:

!`uv run python3 ${CLAUDE_SKILL_DIR}/../../scripts/load-constraints.py dev-implement`

**You MUST have these constraints loaded before proceeding. No claiming you "remember" them.**

**Dynamic plan re-read:** Before starting work, re-read `.planning/PLAN.md` to catch any phases or tasks that were dynamically inserted by earlier phases. Do not rely on cached plan state from a prior phase.

## Where This Fits

```
Main Chat (you)                    Task Agent
─────────────────────────────────────────────────────
dev-implement (this skill)
  → dev-ralph-loop (per-task loops)
    → dev-delegate (spawn agents)
      → Task agent ──────────────→ follows dev-tdd
                                   uses dev-test tools
```

**Main chat orchestrates.** Task agents implement.

## Contents

- [Prerequisites](#prerequisites)
- [Implementation Strategy Choice](#implementation-strategy-choice)
- [The Iron Law of Delegation](#the-iron-law-of-delegation)
- [The Process](#the-process) (Sequential)
- [Sub-Skills Reference](#sub-skills-reference)
- [If Max Iterations Reached](#if-max-iterations-reached)
- [Agent Team Implementation (Parallel)](#agent-team-implementation-parallel)
- [Test Gap Validation Gate (MANDATORY)](#test-gap-validation-gate-mandatory)
- [Phase Complete](#phase-complete)

# Implementation (Orchestration)

<EXTREMELY-IMPORTANT>
## Prerequisites

**Do NOT start implementation without these:**

1. `.planning/SPEC.md` exists with final requirements
2. `.planning/PLAN.md` exists with chosen approach
3. **User explicitly approved** in /dev-design phase
4. **`.planning/PLAN.md` Testing Strategy section is COMPLETE** (all boxes checked)
5. **`.planning/PLAN_REVIEWED.md` exists with `status: APPROVED`**

If any prerequisite is missing, STOP and complete the earlier phases.

### Plan Review Gate Check (MANDATORY — CHECK FIRST)

Before anything else, verify the plan was reviewed:

```bash
# Check for plan review approval marker
head -5 .planning/PLAN_REVIEWED.md 2>/dev/null
```

**If `.planning/PLAN_REVIEWED.md` does not exist → STOP. Return to dev-design Phase Complete.**
**If `status:` is not `APPROVED` → STOP. Plan review is incomplete.**

This file is written by dev-plan-reviewer when it approves the plan. Its absence means the plan reviewer was SKIPPED — which means spec requirements may have been silently dropped from the plan.

| Thought | Reality |
|---------|---------|
| “I can see the plan looks complete” | Self-assessment is not review. The reviewer catches what you miss. |
| “Plan reviewer would have approved anyway” | Then it takes 30 seconds. Run it. |
| “User approved the plan directly” | User approves the approach. Reviewer checks spec coverage. Different gates. |
| “I'll review it myself as I implement” | You won't. You'll be focused on code. That's why the gate exists. |

**Check `.planning/PLAN.md` for:** files to modify, implementation order, testing strategy.

### Pre-Flight Testing Check (MANDATORY)

Before starting ANY task, verify `.planning/PLAN.md` Testing Strategy:

```
[ ] Framework specified (not empty, not “TBD”)
[ ] Test Command specified (runnable command)
[ ] First Failing Test described (specific test name)
[ ] Test File Location specified (actual path)
```

**If ANY box is unchecked → STOP. Go back to design phase.**

This is your LAST CHANCE to catch missing test strategy before writing code.
</EXTREMELY-IMPORTANT>

## Implementation Strategy Choice

After prerequisites pass, check PLAN.md for parallelization potential:

**Skip this choice when:**
- PLAN.md has fewer than 4 tasks
- All tasks are dependent (every task is `after N` with no independent groups)
- `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` is not available

**Otherwise, ask the user:**

```python
AskUserQuestion(questions=[{
  "question": "How should we implement the tasks in PLAN.md?",
  "header": "Strategy",
  "options": [
    {"label": "Sequential (Default)", "description": "One ralph loop per task, complete N before N+1. Safest, no merge conflicts."},
    {"label": "Agent team (parallel)", "description": "Spawn teammate per independent task group. Faster for 4+ independent tasks. Requires reconciliation."}
  ],
  "multiSelect": false
}])
```

**If Sequential:** Proceed to [The Process](#the-process) below (current behavior).

**If Agent team:** Skip to [Agent Team Implementation (Parallel)](#agent-team-implementation-parallel).

## The Iron Law of TDD

**YOU CANNOT WRITE IMPLEMENTATION CODE WITHOUT A FAILING TEST FIRST.** Every task follows:

```
1. READ the test description from PLAN.md
2. WRITE the test file
3. RUN the test → SEE RED (failure)
4. ONLY THEN write implementation
5. RUN the test → SEE GREEN (pass)
```

If you wrote code without a failing test first, DELETE IT and start over. If a subagent skipped tests, REJECT the work.

## The Iron Law of Delegation

**MAIN CHAT MUST NOT WRITE CODE.** Main chat orchestrates. Subagents implement.

| Allowed in Main Chat | NOT Allowed in Main Chat |
|---------------------|--------------------------|
| Spawn Task agents | Write/Edit code files |
| Review Task agent output | Direct implementation |
| Write to .planning/*.md files | “Quick fixes” |
| Run git commands | Any code editing |
| Start ralph loops | Bypassing delegation |

If you’re about to edit code directly, STOP and spawn a Task agent instead. This applies to ALL work — config, boilerplate, setup, porting. No exceptions.

### Context Monitoring

Before starting each task, check context availability:

**Thresholds:**
| Level | Remaining Context | Action |
|-------|------------------|--------|
| Normal | >35% | Proceed with task |
| Warning | 25-35% | Complete current task, then invoke dev-handoff |
| Critical | ≤25% | Invoke dev-handoff immediately — no new tasks |

**At Warning level:** After the current task completes (don't abandon mid-task), invoke:
Read `${CLAUDE_SKILL_DIR}/../../skills/dev-handoff/SKILL.md` and follow its instructions.

**At Critical level:** Stop immediately. Invoke dev-handoff before context is exhausted. A degraded handoff is better than no handoff.

**Why:** A 10-task implementation phase with 20% context remaining produces garbage for the last 5 tasks. Better to handoff cleanly and resume fresh than to push through with degraded output.

### Long-Running Task Monitoring

Use the **Monitor tool** for builds, test suites, or scripts that take >30 seconds. Monitor streams stdout events without blocking — you keep working and get notified on completion.

```
# Watch a test suite run
Monitor(
  description="test suite progress",
  timeout_ms=300000, persistent=false,
  command="npm test 2>&1 | grep --line-buffered -E '(PASS|FAIL|✓|✗|error|complete)'"
)

# Watch a build
Monitor(
  description="build progress",
  timeout_ms=300000, persistent=false,
  command="npm run build 2>&1 | grep --line-buffered -E '(error|warning|built|done|fail)'"
)
```

**When NOT to use Monitor:** For quick commands (<30s), use Bash directly. For one-shot "run and wait," use `Bash(run_in_background=true)`. Monitor is for streaming progress from longer operations.

## The Process

```
For each task N in PLAN.md:
    1. Determine loop type:
       - Visual task? → discover and read skills/visual-verify/SKILL.md via cache lookup
       - Standard task? → discover and read skills/dev-ralph-loop/SKILL.md via cache lookup

    2. Inside loop: spawn Task agent
       → discover and read skills/dev-delegate/SKILL.md via cache lookup

    3. Task agent follows TDD (dev-tdd) using testing tools (dev-test)
       Visual tasks: also render output and vision-check with look-at

    4. Verify tests pass (+ visual check passes for visual tasks), output promise

    5. Move to task N+1, start NEW loop
```

**Cache lookup pattern for all paths above:**Read `${CLAUDE_SKILL_DIR}/../../TARGET/PATH` and follow its instructions.

### Visual Task Detection

If a PLAN.md task involves rendered visual output, use **visual-verify** instead of plain ralph-loop. Visual-verify adds render → look-at → fix steps inside each iteration.

**Signals a task is visual:** task mentions "render", "slide", "chart", "figure", "layout", "UI", "screenshot", "visual", "diagram", or produces any file meant to be seen by humans (PNG, PDF, SVG).

Read `${CLAUDE_SKILL_DIR}/../../skills/visual-verify/SKILL.md` and follow its instructions.

### Step 1: Start Ralph Loop for Each Task

**REQUIRED SUB-SKILL:**

Read `${CLAUDE_SKILL_DIR}/../../skills/dev-ralph-loop/SKILL.md` and follow its instructions.

Key points from dev-ralph-loop:
- ONE loop PER TASK (not one loop for feature)
- Each task gets its own completion promise
- Don’t move to task N+1 until task N’s loop completes

### Step 2: Inside Loop - Spawn Task Agent

**REQUIRED SUB-SKILL:**

Read `${CLAUDE_SKILL_DIR}/../../skills/dev-delegate/SKILL.md` and follow its instructions.

Key points from dev-delegate:
- Implementer → Spec reviewer → Quality reviewer
- Task agent follows dev-tdd protocol
- Task agent uses dev-test tools

### Step 3: Verify and Complete (MANDATORY - DO NOT SKIP)

After Task agent returns, **you must personally verify** (not trust the agent’s report):

#### 3a. Read the Actual Code
- [ ] Code matches spec (not a different approach or substitution)

#### 3b. Check Test Reality
- [ ] Tests EXECUTE code (not grep/mock-only)
- [ ] Tests are NOT skipped (SKIP ≠ PASS)
- [ ] Integration tests exist and run

#### 3c. Run Tests Yourself
- [ ] Test command runs without error
- [ ] Tests actually pass (watch for skipped tests masking failures)

#### 3d. Verify Real Integration (for external systems)
- [ ] External system is running and feature works against it

**If ANY check fails → REJECT the work. Do NOT mark task complete.** If ALL pass → output the promise.

### Task Summary (MANDATORY after each task)

After a task passes review, append a structured summary to LEARNINGS.md:

```yaml
## Task N: [task description]

---
task: N
status: completed
implements: [REQ-01, REQ-03]
affects: [src/auth/, tests/test_auth.py]
key-files:
  created: [list of new files]
  modified: [list of changed files]
deviations: {r1: 0, r2: 1, r3: 0, r4: 0}
---

One-liner: [SUBSTANTIVE summary — not "Task complete" but "JWT refresh rotation with 7-day expiry using jose library"]

Changes: [what was added/modified and why]
Test: [test command and result]
```

**One-liner rule:** Must be SUBSTANTIVE. Good: "Added rate limiting middleware with sliding window at 100 req/min". Bad: "Implemented task 3" or "Done".

## Deviation Rules (CRITICAL)

You WILL discover unplanned work during implementation. Apply these rules automatically and track all deviations.

| Rule | Trigger | Action | Permission |
|------|---------|--------|------------|
| **1: Bug** | Broken behavior, errors, wrong queries, type errors, security vulns, race conditions, leaks | Fix → test → verify → track `[Rule 1 - Bug]` | Auto |
| **2: Missing Critical** | Missing essentials: error handling, validation, auth, CSRF/CORS, rate limiting, indexes, logging | Add → test → verify → track `[Rule 2 - Missing Critical]` | Auto |
| **3: Blocking** | Prevents completion: missing deps, wrong types, broken imports, missing env/config/files, circular deps | Fix blocker → verify proceeds → track `[Rule 3 - Blocking]` | Auto |
| **4: Architectural** | Structural change: new DB table, schema change, new service, switching libs, breaking API, new infra | STOP → present decision → track `[Rule 4 - Architectural]` | Ask user |

**Priority:** Rule 4 (STOP) > Rules 1-3 (auto) > unsure → Rule 4
**Edge cases:** missing validation → R2 | null crash → R1 | new table → R4 | new column → R1/2

### Rule 4 Format

When you encounter an architectural deviation, STOP and present:

```
⚠️ Architectural Decision Needed
- Current task: [task name]
- Discovery: [what prompted this]
- Proposed change: [modification]
- Why needed: [rationale]
- Impact: [what this affects]
- Alternatives: [other approaches]
Proceed with proposed change? (yes / different approach / defer)
```

### Documenting Deviations

All deviations tracked per task:

**[Rule N - Category] Title**
- Found during: Task X
- Issue: [description]
- Fix: [what was done]
- Files modified: [list]
- Verification: [how confirmed]

End each task summary with: **Total deviations:** N auto-fixed (R1: X, R2: Y, R3: Z). **Impact:** [assessment].

## Sub-Skills Reference

| Skill | Purpose | Used By |
|-------|---------|---------|
| `dev-ralph-loop` | Per-task loop pattern | Main chat |
| `dev-delegate` | Task agent templates | Main chat |
| `dev-tdd` | TDD protocol (RED-GREEN-REFACTOR) | Task agent |
| `dev-test` | Testing tools (pytest, Playwright, etc.) | Task agent |

## Failure Recovery Protocol

**After 3 consecutive implementation failures, escalate.**

1. **STOP** — no more “let me try a different approach”
2. **REVERT** — `git checkout <last-passing-commit>`, document attempts in `.planning/RECOVERY.md`
3. **DOCUMENT** — all 3 approaches, their test failures, and what the pattern reveals
4. **CONSULT USER** — present findings and ask for direction:
   - A) Re-examine requirements (/dev-clarify)
   - B) Try different design (/dev-design)
   - C) Investigate test failures (/dev-debug)
   - D) User provides domain knowledge

Trigger early when a failure pattern emerges — don’t wait for max iterations.

## If Max Iterations Reached

Ralph exits after max iterations. **Still do NOT ask user to manually test.**

Main chat should:
1. **Summarize** what’s failing (from LEARNINGS.md)
2. **Report** which automated tests fail and why
3. **Ask user** for direction:
   - A) Start new loop with different approach
   - B) Add more logging to debug
   - C) User provides guidance
   - D) User explicitly requests manual testing

**Never default to “please test manually”.** Always exhaust automation first.

## No Pause Between Tasks

**After completing task N, IMMEDIATELY start task N+1 in the SAME RESPONSE. Do NOT pause.**

### Post-Promise Checklist (mandatory, same response)

1. **Update PLAN.md** — Mark task `[x]` complete
2. **Log to LEARNINGS.md** — What was done, test command, exit code
3. **Start next task’s ralph loop** — No waiting

### Valid Stopping Points (only these three)

1. ALL tasks in PLAN.md are marked `[x]` complete
2. You hit a blocker requiring user input (state exactly what you need)
3. User explicitly interrupted

### Task Transition Gate

After each task’s ralph loop completes:

1. Update PLAN.md — mark completed task `[x]`
2. Append to LEARNINGS.md — what was accomplished
3. Check for blockers — dependencies from task N needed for N+1?
4. If clear → IMMEDIATELY spawn ralph loop for task N+1
5. If blocked → Ask user EXACTLY what’s missing

Never ask “should I continue?” — just continue. Pausing between tasks is procrastination disguised as courtesy.

## Agent Team Implementation (Parallel)

For parallel implementation using agent teams, read the full protocol:

Read `${CLAUDE_SKILL_DIR}/../../skills/dev-implement/references/agent-team-protocol.md` and follow its instructions.

**When to use:** User explicitly requests parallel implementation, OR 4+ independent tasks in PLAN.md.

**Key rules:**
- Each teammate gets a self-contained prompt with full context
- Main agent coordinates, does NOT implement directly
- Reconcile results after all teammates complete
- Fall back to sequential if fewer than 3 tasks

### Exit Gate

**Checkpoint type:** human-verify (all tasks pass tests — machine-verifiable)

## Test Gap Validation Gate (MANDATORY)

**After ALL implementation tasks complete, run test gap validation BEFORE proceeding to review.** TDD ensures task-level coverage; test gap ensures requirement-level coverage.

### Invoke test gap Validation

Read `${CLAUDE_SKILL_DIR}/../../skills/dev-test-gaps/SKILL.md` and follow its instructions.

### Gate Conditions

**Must produce `.planning/VALIDATION.md` before proceeding to review.**

| VALIDATION.md Status | Action |
|---------------------|--------|
| `validated` | Proceed to review phase |
| `gaps_found` (gaps filled, no escalations) | Re-run full test suite. If all pass, proceed. |
| `gaps_found` (with escalations) | Spawn targeted ralph loops for failing requirements, then re-run validation |
| Missing | STOP. Run test gap validation. |

Max 2 re-validation cycles. After that, escalate to user.

## Phase Complete

**Phase summary (append to LEARNINGS.md):**

```yaml
## Phase: Implement

---
phase: implement
status: completed
requires: [PLAN.md, PLAN_REVIEWED.md]
provides: [VALIDATION.md, implementation-complete, all-tests-passing]
tasks-completed: N/N
total-deviations: {r1: X, r2: Y, r3: Z, r4: W}
---
```

**REQUIRED SUB-SKILL:** After ALL tasks complete with passing tests AND test gap validation passes:

Read `${CLAUDE_SKILL_DIR}/../../skills/dev-review/SKILL.md` and follow its instructions.

Do NOT proceed until automated tests pass for every task AND `.planning/VALIDATION.md` status is `validated`.
