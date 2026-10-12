# Security status

No credentials are committed; `.env` is ignored by Git. Settings use secret-aware types for the JWT secret and do not expose secret values through the health endpoint. The Compose credentials are local development defaults and must not be used in deployment.

Authentication, Argon2id password hashing, short-lived signed JWT access tokens, role enforcement, audit records, and an interactive admin provisioning command are implemented. Public registration grants only `VIEWER`; there is no public role escalation. Production requires a unique JWT secret with at least 32 characters.

Distributed rate limiting, refresh/revocation tokens, MFA, secure WebSocket handling, restrictive CORS policy, key rotation, and production secret storage are not implemented. Keep this service on a trusted network until those controls are implemented and reviewed. Never place broker credentials in this workspace or enable live trading.
