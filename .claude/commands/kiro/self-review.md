---
description: Self-review requirements, design, and implementation for a specification
allowed-tools: Read, Glob, Grep, Bash, WebSearch, WebFetch
argument-hint: <feature-name> [--phase req|design|impl|all]
---

# Specification Self-Review

<background_information>
- **Mission**: Perform comprehensive self-review across all specification phases (requirements, design, implementation) to identify gaps, inconsistencies, and improvement opportunities
- **Success Criteria**:
  - Cross-phase consistency verified (requirements ↔ design ↔ implementation)
  - Quality issues identified with severity and actionable fixes
  - External best practices compared where relevant
  - Clear summary with prioritized action items
</background_information>

<instructions>
## Core Task
Self-review the specification for feature **$ARGUMENTS** across requirements, design, and implementation phases.

Parse `$ARGUMENTS` for:
- **Feature name**: first positional argument (required)
- **Phase filter**: `--phase req|design|impl|all` (default: `all`)

If `$ARGUMENTS` is empty or only contains flags:
- Scan `.kiro/specs/` for available features
- List them and ask user to select one

## Execution Steps

### 1. Load Full Context

- Read `.kiro/specs/<feature>/spec.json` for language, phase status, and metadata
- Read `.kiro/specs/<feature>/requirements.md`
- Read `.kiro/specs/<feature>/design.md` (if exists)
- Read `.kiro/specs/<feature>/tasks.md` (if exists)
- Read `.kiro/specs/<feature>/gap-analysis.md` (if exists)
- **Load ALL steering context**: Read entire `.kiro/steering/` directory
- Read existing source code files referenced in design/tasks

### 2. Requirements Review (`req`)

#### Internal Consistency
- All requirements use EARS format correctly (When/If/While/Where/The system shall)
- No duplicate or contradictory acceptance criteria
- Each requirement has a clear, testable objective
- Numeric IDs are sequential and consistent

#### Completeness
- All user-facing scenarios covered (happy path + error cases)
- Non-functional requirements addressed (performance, security, error handling)
- Edge cases identified
- Integration points with existing system documented

#### Quality
- Requirements are implementation-agnostic (WHAT not HOW)
- Each acceptance criterion is independently testable
- No ambiguous terms ("fast", "easy", "intuitive" without metrics)
- Scope is appropriate (not too broad, not too narrow)

### 3. Design Review (`design`)

#### Requirements Traceability
- Every requirement has corresponding design elements
- No design elements without requirement justification ("gold plating")
- Gap analysis findings incorporated into design decisions

#### Technical Soundness
- Architecture decisions are justified with rationale
- Interfaces are well-defined (inputs, outputs, error cases)
- Data flow is complete and consistent
- Dependencies are identified and managed
- Security considerations addressed

#### Feasibility
- Proposed libraries/APIs exist and are maintained (use WebSearch to verify)
- Complexity is appropriate for the scope
- Risk areas have mitigation strategies

### 4. Implementation Review (`impl`)

#### Design Alignment
- Code structure matches design document
- All designed components are implemented
- No significant undocumented deviations

#### Code Quality
- Read actual source files and review for:
  - Error handling covers designed failure modes
  - Logging is consistent and useful
  - Configuration is externalized as designed
  - Tests exist and cover acceptance criteria

#### Task Completion
- Cross-reference tasks.md checkboxes with actual implementation
- Verify completed tasks are actually implemented (not just checked off)
- Identify any implemented functionality not covered by tasks

### 5. Cross-Phase Consistency

- Requirements → Design: all requirements addressed in design
- Design → Tasks: all design elements have corresponding tasks
- Tasks → Implementation: all completed tasks have corresponding code
- Implementation → Requirements: acceptance criteria are met by code
- Identify any "drift" between phases

### 6. Generate Self-Review Report

## Important Constraints
- **Honest assessment**: Flag real issues, not cosmetic ones
- **Prioritized**: Critical issues first, then warnings, then suggestions
- **Actionable**: Every issue must have a concrete fix suggestion
- **Phase-aware**: Only review phases that exist (skip missing phases gracefully)
- **External validation**: Use WebSearch to verify library versions, API availability, best practices when relevant
</instructions>

## Tool Guidance
- **Read first**: Load ALL spec files and steering before analysis
- **Grep for traceability**: Search codebase for requirement/design evidence
- **Glob for structure**: Verify file structure matches design
- **Bash for tests**: Run test suite if available (`pytest`, `npm test`, etc.)
- **WebSearch**: Verify external dependencies, library status, API availability
- **WebFetch**: Check specific library documentation or API references

## Output Description

Provide output in the language specified in spec.json with:

### Report Structure

```markdown
# Self-Review: <feature-name>
## Review Scope
- phases reviewed, spec status, files examined

## Requirements Review
### Issues (Critical / Warning / Info)
### Score: X/10

## Design Review
### Issues (Critical / Warning / Info)
### Score: X/10

## Implementation Review
### Issues (Critical / Warning / Info)
### Score: X/10

## Cross-Phase Consistency
### Traceability Matrix (summary)
### Drift / Gaps identified

## Summary
### Overall Score: X/10
### Priority Actions (top 3-5)
### Strengths
### Next Steps
```

**Severity Levels**:
- **Critical**: Blocks progress, must fix before proceeding
- **Warning**: Should fix, risk of problems if ignored
- **Info**: Improvement opportunity, optional

**Scoring Guide**:
- 9-10: Excellent, ready to proceed
- 7-8: Good, minor improvements recommended
- 5-6: Acceptable, several issues to address
- 3-4: Needs work, significant gaps
- 1-2: Major revision required

## Safety & Fallback

### Error Scenarios
- **Feature not found**: List available features in `.kiro/specs/` and ask user to select
- **Missing phases**: Review only existing phases, note missing ones as info items
- **No implementation yet**: Skip impl review, focus on req + design
- **Language undefined**: Default to English (`en`) if spec.json doesn't specify language
- **Test command unknown**: Warn and skip test execution, note as manual verification needed

### Follow-up Actions
- Fix critical issues, then re-run `/kiro:self-review <feature> --phase <affected-phase>`
- Use `/kiro:spec-requirements <feature>` to update requirements
- Use `/kiro:spec-design <feature>` to update design
- Use `/kiro:spec-impl <feature> [tasks]` to fix implementation issues
