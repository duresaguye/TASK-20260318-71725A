# TASK-20260318-71725A

Medical Operations and Process Governance Middle Platform API Service.

## Configuration

Key environment variables:

- `DATABASE_URL`: PostgreSQL DSN. Default in Docker Compose points at `db`.
- `JWT_SECRET_KEY`: required.
- `SENSITIVE_DATA_KEY`: required for encrypted field storage.
- `REQUIRE_HTTPS`: defaults to `true`. HTTPS is enforced through the reverse proxy delivery path.
- `EXPOSE_RESET_TOKEN`: defaults to `false`. Set it to `true` only in a controlled offline environment where echoing the reset token is intentional.
- `BACKUP_ENCRYPTION_KEY`: optional backup-artifact key. If omitted, the service falls back to `SENSITIVE_DATA_KEY`.
- `BACKUP_COMPRESS`: defaults to `true`. Enables compression before backup encryption.
- `ATTACHMENT_STORAGE_ROOT`: local path for workflow attachments. Docker Compose mounts a persistent volume here by default.
- `BACKUP_ROOT`: local path for daily backup artifacts. Docker Compose mounts a persistent volume here by default.

## Run With Docker

```bash
cd repo
docker compose up --build
```

Before starting Docker Compose, set `JWT_SECRET_KEY` and `SENSITIVE_DATA_KEY` in your shell or `.env` file. The stack publishes HTTPS on port `443` through NGINX, which generates a self-signed certificate on first start.

Open the service at `https://localhost/`. Because the certificate is self-signed, your browser or client may need to trust it explicitly.

## Run Tests

```bash
cd repo
./server/run_tests.sh
```

## Static Capability Map

- Auth: register, login, logout, password recovery, lockout, JWT revocation
- Organizations: create/join/list with unique org codes
- Workflows: typed definitions, business-number submission, branching, approvals, comments, attachments
- Governance: import validation, versions, rollback, lineage
- Analytics: dashboard, activity, SLA, search
- Export: whitelisted field exports with desensitization and traceability
- Operations: persistent attachment storage, encrypted backup/archive loop with retention cleanup
