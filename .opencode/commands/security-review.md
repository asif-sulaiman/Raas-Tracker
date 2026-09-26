---
description: Security review of recent changes (read-only)
agent: plan
subtask: true
---
Recent changes:
!`git diff HEAD~1`

Review for security: hardcoded secrets, auth gaps, missing input validation, injection risks, data exposure, mass assignment, CORS/headers, dependency risks, error leakage. List: file, severity, fix. No changes.