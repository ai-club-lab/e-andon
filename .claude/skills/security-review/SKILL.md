# Security Review Skill

Systematic security review checklist.

## Quick Scan (5 minutes)

### Secrets Check
```bash
# Search for potential hardcoded secrets
grep -rn "password\|secret\|api_key\|apikey\|token" --include="*.ts" --include="*.js"
grep -rn "BEGIN.*PRIVATE KEY" .
```

- [ ] No hardcoded credentials
- [ ] No API keys in code
- [ ] No private keys committed
- [ ] .env files in .gitignore

### Input Validation
- [ ] All user input validated
- [ ] File uploads restricted by type/size
- [ ] URL parameters sanitized

## Deep Review (30 minutes)

### Injection Prevention

#### SQL Injection
```typescript
// BAD
const query = `SELECT * FROM users WHERE id = '${userId}'`;

// GOOD
const query = 'SELECT * FROM users WHERE id = $1';
await db.query(query, [userId]);
```

#### Command Injection
```typescript
// BAD
exec(`convert ${userFilename} output.png`);

// GOOD
execFile('convert', [userFilename, 'output.png']);
```

#### XSS Prevention
```typescript
// BAD
element.innerHTML = userInput;

// GOOD
element.textContent = userInput;
// Or use DOMPurify for HTML
element.innerHTML = DOMPurify.sanitize(userInput);
```

### Authentication
- [ ] Passwords hashed with bcrypt/argon2 (cost factor ≥10)
- [ ] Session tokens are random and sufficient length
- [ ] Logout invalidates session server-side
- [ ] Rate limiting on login attempts
- [ ] Account lockout after failures

### Authorization
- [ ] Every endpoint checks permissions
- [ ] No direct object references (use indirect refs)
- [ ] Role checks happen server-side
- [ ] Sensitive actions require re-authentication

### Data Protection
- [ ] PII encrypted at rest
- [ ] TLS for all external communication
- [ ] Sensitive data not in logs
- [ ] Proper data retention/deletion

### Headers (API/Web)
```typescript
// Recommended security headers
{
  'Strict-Transport-Security': 'max-age=31536000; includeSubDomains',
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Content-Security-Policy': "default-src 'self'",
  'X-XSS-Protection': '1; mode=block',
}
```

### Dependencies
```bash
# Check for known vulnerabilities
npm audit
# or
yarn audit
```

- [ ] No critical vulnerabilities
- [ ] Dependencies up to date
- [ ] Lock file committed

## Report Template

```markdown
## Security Review: [Component]

### Risk Level: Critical / High / Medium / Low

### Findings

#### [CRITICAL] SQL Injection in user search
- **Location**: src/api/users.ts:45
- **Issue**: User input concatenated into SQL query
- **Impact**: Full database access
- **Fix**: Use parameterized queries

#### [HIGH] Missing rate limiting
- **Location**: src/api/auth.ts
- **Issue**: No rate limiting on login endpoint
- **Impact**: Brute force attacks possible
- **Fix**: Add rate limiting middleware

### Recommendations
1. [Priority-ordered action items]

### Verified Secure
- [List of checked items that passed]
```
