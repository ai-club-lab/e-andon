# Coding Style & Architecture Rules

## File Organization
- Keep files under 300 lines; split if larger
- One component/module per file
- Group related files in feature folders
- Use index files for clean exports

## Code Structure
- Prefer composition over inheritance
- Use dependency injection for testability
- Keep functions under 50 lines
- Extract reusable logic into utils/helpers

## Naming Conventions
- Files: kebab-case (e.g., `user-service.ts`)
- Components: PascalCase (e.g., `UserProfile`)
- Functions/variables: camelCase
- Constants: SCREAMING_SNAKE_CASE
- Interfaces: PascalCase

## TypeScript / Python Preferences
- TypeScript: strict mode, prefer interfaces for objects, explicit return types on public functions, avoid `any` (use `unknown`)
- Python: type hints on public functions, follow PEP 8, prefer dataclasses/pydantic for structured data

## Error Handling
- Use custom error classes for domain errors
- Always handle async errors (try/catch or .catch())
- Log errors with context (action, timestamp, correlation id)
- Return meaningful error messages to clients

## Testing
- Co-locate tests with source (`*.test.ts` / `test_*.py`)
- Test behavior, not implementation
- Descriptive names: "should [action] when [condition]"

## Comments
- Code should be self-documenting
- Comment the "why", not the "what"
- JSDoc / docstrings for public APIs
