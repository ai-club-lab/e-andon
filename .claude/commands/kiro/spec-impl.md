---
description: Execute spec tasks with TDD / BDD / ATDD school balance
allowed-tools: Bash, Read, Write, Edit, MultiEdit, Grep, Glob, LS, WebFetch, WebSearch
argument-hint: <feature-name> [task-numbers]
---

# Implementation Task Executor

<background_information>
- **Mission**: Execute implementation tasks with an explicit, per-task choice among TDD / BDD / ATDD (or N/A for doc-only work), based on approved specifications
- **Success Criteria**:
  - Each task is tagged with its driving school BEFORE implementation starts
  - The chosen school's test artifact is written before production code (TDD: unit test / BDD: Given-When-Then / ATDD: executable acceptance from EARS)
  - Code passes all tests with no regressions
  - Tasks marked as completed in tasks.md
  - Implementation aligns with design and requirements
</background_information>

<instructions>
## Core Task
Execute implementation tasks for feature **$1** with a balanced mix of TDD / BDD / ATDD chosen per task.

## Execution Steps

### Step 1: Load Context

**Read all necessary context**:
- `.kiro/specs/$1/spec.json`, `requirements.md`, `design.md`, `tasks.md`
- **Entire `.kiro/steering/` directory** for complete project memory

**Validate approvals**:
- Verify tasks are approved in spec.json (stop if not, see Safety & Fallback)

### Step 2: Select Tasks

**Determine which tasks to execute**:
- If `$2` provided: Execute specified task numbers (e.g., "1.1" or "1,2,3")
- Otherwise: Execute all pending tasks (unchecked `- [ ]` in tasks.md)

### Step 2.5: Declare School Mapping (before any edit)

**Before writing any test or production code**, classify each selected task into one of four schools and present the mapping to the user for yes/no confirmation.

**Classification heuristic** (apply in order; pick the first that fits):

1. **ATDD** — Task directly implements an EARS acceptance criterion from `requirements.md`. Write an executable acceptance test derived from the EARS clause first.
2. **BDD** — Task implements user-observable behavior that crosses component / module boundaries. Write a Given/When/Then scenario test first.
3. **TDD** — Task implements unit-level logic (pure function, class, algorithm, data transform). Write a unit test first (Kent Beck Red → Green → Refactor).
4. **N/A (checklist-driven)** — Task is doc-only, config, scaffold, index/registry update, or otherwise has no executable behavior to assert. No test required; verify artifact exists and matches design.
5. **Tie-breaker**: when ATDD and TDD both fit, prefer ATDD (EARS → test is the tightest requirements-to-code link Kiro offers).

**Output format** (print verbatim, then wait for user confirmation):

```
School mapping for $1:
  Task 1.1 → ATDD  (reason: implements EARS req X.Y)
  Task 1.2 → TDD   (reason: pure transform logic)
  Task 2.1 → BDD   (reason: UI ↔ API observable flow)
  Task 3.1 → N/A   (reason: doc/scaffold only)

Distribution: 1 ATDD / 1 TDD / 1 BDD / 1 N/A
Proceed? (yes / adjust)
```

**Balance audit** (warn before asking confirm):
- If 100% of tasks land on a single school, print: `⚠ All tasks → <school>. Re-check whether ATDD/BDD candidates were missed.`
- If 100% land on N/A, print: `ℹ All tasks → N/A. If this is a doc/index/scaffold-only spec, confirm and proceed; otherwise re-check classification.`

**On user response**:
- `yes` → proceed to Step 3
- `adjust` or specific reassignment (e.g., "1.2 → ATDD") → update mapping, re-print, re-confirm
- `--legacy-tdd` flag passed via `$2` or user reply → skip Step 2.5 entirely, fall back to TDD-only (pre-balance behavior)

**Skip Step 2.5 when**:
- `$2` selects exactly one task AND the task has an obvious single-school fit (still print a 1-line classification, no confirmation gate)
- Spec is already mid-implementation (some `- [x]` exist) AND remaining tasks are < 3 (1-line per-task classification, no gate)

### Step 3: Execute Per Declared School

For each selected task, follow the cycle for its declared school:

**ATDD cycle**:
1. Extract the EARS clause from `requirements.md` for the mapped requirement ID
2. Translate into an executable acceptance test (test name = EARS verbatim or close paraphrase)
3. Run test → must fail for the right reason (missing behavior, not syntax)
4. Implement minimum code to satisfy the acceptance
5. Refactor; re-run acceptance + any pre-existing tests

**BDD cycle**:
1. Write a Given/When/Then scenario covering the cross-boundary behavior
2. Materialize as an executable scenario test (spec/feature file or equivalent in project's stack)
3. Red → Green → Refactor across the involved components
4. Verify scenario passes end-to-end

**TDD cycle** (Kent Beck):
1. **RED**: Smallest failing unit test for next slice of functionality
2. **GREEN**: Minimal code to pass
3. **REFACTOR**: Clean up; no behavior change; all tests stay green
4. **VERIFY**: Full suite passes; no regressions

**N/A cycle**:
1. Produce the artifact (doc / config / scaffold) per design.md
2. Verify it matches design.md spec (lint / schema check / manual diff)
3. No test required; record verification in commit message

**After every task (all schools)**:
- All pre-existing tests still green (no regressions)
- Update checkbox `- [ ]` → `- [x]` in tasks.md
- Commit with school tag in message prefix (e.g., `[ATDD]`, `[TDD]`, `[BDD]`, `[N/A]`)

## Critical Constraints
- **Declare before edit**: School mapping MUST be printed (and confirmed when gate applies) before any file edit in Step 3
- **Test-first within school**: ATDD/BDD/TDD all require their respective test artifact before production code
- **N/A is opt-in per task**, never blanket — each N/A needs an explicit reason in the mapping
- **No regressions**: Existing tests must continue to pass
- **Design Alignment**: Implementation must follow design.md specifications
- **Balance audit warnings are advisory**, not blocking — user may still confirm a skewed mapping
</instructions>

## Tool Guidance
- **Read first**: Load all context before classification
- **Classify before edit**: Step 2.5 mapping precedes any Write/Edit
- Use **WebSearch/WebFetch** for library documentation when needed

## Output Description

Provide brief summary in the language specified in spec.json:

1. **School Distribution**: counts per school (e.g., "2 ATDD / 3 TDD / 1 BDD / 1 N/A")
2. **Tasks Executed**: Task numbers, school tag, test results
3. **Status**: Completed tasks marked in tasks.md, remaining tasks count

**Format**: Concise (under 200 words)

## Safety & Fallback

### Error Scenarios

**Tasks Not Approved or Missing Spec Files**:
- **Stop Execution**: All spec files must exist and tasks must be approved
- **Suggested Action**: "Complete previous phases: `/kiro:spec-requirements`, `/kiro:spec-design`, `/kiro:spec-tasks`"

**Test Failures**:
- **Stop Implementation**: Fix failing tests before continuing
- **Action**: Debug and fix, then re-run

**Classification ambiguity** (Step 2.5):
- If a task fits none of ATDD/BDD/TDD/N/A clearly, mark `?` and ask the user before proceeding
- Never silently default to TDD; surface the ambiguity

### Task Execution

**Execute specific task(s)**:
- `/kiro:spec-impl $1 1.1` - Single task
- `/kiro:spec-impl $1 1,2,3` - Multiple tasks

**Execute all pending**:
- `/kiro:spec-impl $1` - All unchecked tasks

**Legacy TDD-only mode** (skip balance gate):
- `/kiro:spec-impl $1 --legacy-tdd`

think
