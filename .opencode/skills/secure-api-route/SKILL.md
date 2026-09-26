---
name: secure-api-route
description: Apply security rules when creating/modifying API routes
---
## Rules
When creating/modifying an API route:
1. Add `@limiter.limit("15 per minute")` for mutating endpoints (or 300/min default)
2. Server-side auth via `_gate_api` (automatic) — ensure route is under `/api/`
3. Pydantic model for all inputs (`BaseModel` + `ConfigDict(str_strip_whitespace=True)`)
4. Parameterized SQL only (`%s`); never f-strings in queries
5. Ownership check: user can only touch own resources (or admin)
6. Mass assignment prevention: explicit field allowlist (Pydantic `model_dump`)
7. Safe errors: generic messages to client, detailed logs server-side
8. Rate limit: 15/min for heavy routes, 300/min default
9. Audit: `log_audit_action(conn, ACTION, entity_type, entity_id, old_value, new_value)`