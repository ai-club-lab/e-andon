# Refactor & Clean Skill

Remove dead code and improve structure after long sessions.

## When to Use
- After completing a feature (before PR)
- After long debugging sessions
- When file exceeds 300 lines
- When function exceeds 50 lines
- Periodically on active codebases

## Phase 1: Identify Dead Code

### Unused Imports
```bash
# TypeScript/JavaScript
npx eslint . --rule 'no-unused-vars: error' --ext .ts,.tsx

# Or use ts-prune for unused exports
npx ts-prune
```

### Unused Dependencies
```bash
npx depcheck
```

### Commented Code
Search for large commented blocks - usually safe to delete if in git history.

### Unreachable Code
- Code after `return`, `throw`, `break`
- Conditions that are always true/false
- Unused branches in switches

## Phase 2: Identify Duplication

### Find Similar Code
```bash
# JavaScript/TypeScript
npx jscpd src/ --min-lines 5 --min-tokens 50
```

### Common Patterns
- Copy-pasted functions with minor variations
- Repeated validation logic
- Similar error handling blocks
- Duplicate type definitions

## Phase 3: Refactor

### Extract Function
```typescript
// Before: Long function with mixed concerns
function processOrder(order: Order) {
  // 20 lines of validation
  // 30 lines of calculation
  // 15 lines of persistence
}

// After: Focused functions
function processOrder(order: Order) {
  validateOrder(order);
  const totals = calculateTotals(order);
  await saveOrder(order, totals);
}
```

### Extract Constant
```typescript
// Before
if (retryCount > 3) { ... }
setTimeout(fn, 5000);

// After
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 5000;

if (retryCount > MAX_RETRIES) { ... }
setTimeout(fn, RETRY_DELAY_MS);
```

### Simplify Conditionals
```typescript
// Before: Deep nesting
if (user) {
  if (user.isActive) {
    if (user.hasPermission) {
      doThing();
    }
  }
}

// After: Guard clauses
if (!user) return;
if (!user.isActive) return;
if (!user.hasPermission) return;
doThing();
```

### Split Large Files
```
// Before
src/services/user-service.ts (500 lines)

// After
src/services/user/
  index.ts           # Public exports
  create-user.ts     # Create logic
  update-user.ts     # Update logic
  user-validation.ts # Shared validation
  types.ts           # Type definitions
```

## Phase 4: Verify

After each change:
```bash
# Run tests
npm test

# Check types
npx tsc --noEmit

# Run linter
npm run lint
```

## Checklist

### Removed
- [ ] Unused imports
- [ ] Unused variables/functions
- [ ] Commented-out code
- [ ] Unused dependencies
- [ ] Dead feature flags

### Simplified
- [ ] Functions under 50 lines
- [ ] Files under 300 lines
- [ ] Nesting under 3 levels
- [ ] No magic numbers/strings

### Verified
- [ ] All tests pass
- [ ] No type errors
- [ ] No lint errors
- [ ] Functionality unchanged
