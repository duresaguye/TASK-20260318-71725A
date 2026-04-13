# Self-Test Report

## Scope

Repository structure, core FastAPI modules, and test scaffolding were updated toward the medical operations prompt.

## Checks

- Required root folders and files were created or aligned with the expected submission layout.
- Docker Compose wiring includes FastAPI and PostgreSQL services.
- Security hardening includes JWT revocation, HTTPS enforcement hook, audit immutability guards, and idempotency persistence.
- Workflow support now includes typed workflow families, business-number submissions, conditional step evaluation, reminders, and attachment metadata/storage.
- Meaningful pytest coverage replaced the original placeholder tests in `server/tests`, `unit_tests`, and `API_tests`.
- Backup/archive maintenance jobs were added as background operational controls.

## Notes

- The runnable project files live under `repo/`.
- Static compilation checks were executed during development; Docker startup and database-backed integration tests were not executed in this environment.
- `REQUIRE_HTTPS=false` is a DEV ONLY setting and is not part of the delivery path.
