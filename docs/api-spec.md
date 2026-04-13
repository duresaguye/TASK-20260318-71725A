# Medical Operations Platform API Contract

## Runtime Reality (Important)

* This system provides a **real backend API service built with FastAPI**.
* All functionality is exposed via **RESTful HTTP endpoints**.
* Data is persisted in **PostgreSQL using SQLAlchemy ORM**.
* The system runs in a **fully offline environment** using Docker.
* This document defines the **external API contract for client interaction with the backend services**.

---

## Source of Truth Used

* System design: `docs/design.md`
* Assumptions: `docs/questions.md`
* Domain models: backend SQLAlchemy models
* API layer: FastAPI route definitions
* Database schema: PostgreSQL tables

---

## Contract Conventions

* All IDs are UUID strings
* Timestamps are ISO 8601 / RFC 3339 strings
* Authentication via JWT Bearer Token
* All endpoints are scoped by `organization_id`
* All responses are JSON
* Sensitive fields are masked based on role

---

## Authentication

* Uses JWT access tokens
* Header format:

```
Authorization: Bearer <token>
```

---

## Multi-Tenancy

* All endpoints are scoped by `organization_id`
* Users can only access resources within their organization
* Enforcement is applied at both API and database query level

---

## Idempotency

* Workflow-related endpoints use idempotency keys based on business identifiers
* Duplicate submissions within 24 hours return the same processing result
* Enforced at both service layer and database level

---

## Status Enums

### Workflow Status

* pending
* approved
* rejected

### Task Status

* pending
* completed
* overdue

---

# Domain API Contracts

## 1. Identity Domain

### Register

POST `/api/v1/auth/register`

```
{
  "username": "string",
  "password": "string"
}
```

---

### Login

POST `/api/v1/auth/login`

Response:

```
{
  "access_token": "string",
  "token_type": "bearer"
}
```

---

### Logout

POST `/api/v1/auth/logout`

---

### Password Recovery

POST `/api/v1/auth/recover`

Alias:

POST `/api/v1/auth/password-recovery/request`

---

## 2. Organization Domain

### Create Organization

POST `/api/v1/organizations`

---

### Join Organization

POST `/api/v1/organizations/{org_id}/join`

---

### Get Organization Users

GET `/api/v1/organizations/{org_id}/users`

---

## 3. Roles & Permissions

## 4. Workflow Domain

### Create Process Definition

POST `/api/v1/workflows/definitions`

---

### Start Process

POST `/api/v1/workflows/instances/start`

---

### Get Tasks

GET `/api/v1/workflows/tasks/my`

Query:

* current user only

---

### Approve Task

POST `/api/v1/workflows/tasks/{task_id}/approve`

---

### Reject Task

POST `/api/v1/workflows/tasks/{task_id}/reject`

---

### Get Workflow History

GET `/api/v1/workflows/instances/{process_instance_id}/tasks`

---

### Comment on Task

POST `/api/v1/workflows/tasks/{task_id}/comment`

---

### Upload Attachment

POST `/api/v1/workflows/instances/{process_instance_id}/attachments`

---

### List Attachments

GET `/api/v1/workflows/instances/{process_instance_id}/attachments`

---

### Download Attachment

GET `/api/v1/workflows/instances/{process_instance_id}/attachments/{attachment_id}/download`

---

## 5. Operations Analytics

### Dashboard Metrics

GET `/api/v1/analytics/dashboard`

---

### Activity

GET `/api/v1/analytics/activity`

### SLA

GET `/api/v1/analytics/sla`

### Search

GET `/api/v1/analytics/search`

Query:

* type
* status
* user_id
* assignee_id
* start_date
* end_date
* keyword
* limit
* offset

---

## 6. Export Domain

### Create Export Job

POST `/api/v1/exports`

---

### Get Export Status

GET `/api/v1/exports/{id}`

---

### Download Export

GET `/api/v1/exports/{id}/download`

---

## 7. Attachments

## 8. Data Governance

### Import Data

POST `/api/v1/data/import`

---

### Get Import Errors

GET `/api/v1/data/import/{id}/errors`

---

### Rollback Data

POST `/api/v1/data/{entity}/{id}/rollback`

---

## Error Handling

All errors return structured JSON:

```
{
  "error_code": "STRING_CODE",
  "message": "Human readable message"
}
```

---

## Security Rules

* HTTPS-only communication
* JWT authentication required
* Role-based access control (RBAC)
* Sensitive data encrypted at rest
* 5 failed login attempts → 30-minute lockout
* File size limit: 20MB
* File deduplication via SHA-256
