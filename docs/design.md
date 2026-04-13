1. System Overview

Medical Operations and Process Governance Middle Platform API Service is a backend-only, API-first system designed to support hospital operations, workflow governance, auditing, and analytics in a secure multi-tenant environment.

Primary roles:

Administrator
Department Reviewer
General User
Auditor

Core capabilities:

identity and organization management
role-based access control (RBAC)
workflow approval and process automation
operational analytics and reporting APIs
export with data governance controls
data quality validation and lineage tracking
audit logging and compliance enforcement

The system is built using FastAPI with PostgreSQL and SQLAlchemy, running in a fully offline environment. All business logic is handled in the backend layer with no frontend dependency.

2. Design Goals
Fully backend-only, API-first architecture
Strict multi-tenant organization isolation
Strong auditability and compliance tracking
Configurable workflow engine with approvals and SLA tracking
High data integrity via idempotency and transaction safety
Secure handling of sensitive medical data
Offline deployment support via Docker
Modular domain-based architecture for scalability
3. High-Level Architecture

The system follows a layered backend architecture:

FastAPI Controllers (API Layer)
        ↓
Service Layer (Business Logic)
        ↓
Domain Layer (Workflow / Identity / Analytics / Governance)
        ↓
Repository Layer (SQLAlchemy)
        ↓
PostgreSQL Database

Supporting infrastructure:

Internal asyncio scheduler loops for SLA and maintenance tasks
Docker (deployment runtime)
4. Domain-Driven Architecture

The system is divided into the following domains:

4.1 Identity Domain
user registration/login/logout
password recovery
RBAC (4 roles)
organization creation/joining
organization-level isolation
4.2 Workflow Domain
approval process definitions
approval instances
task assignment engine
conditional branching workflows
parallel and sequential approvals
SLA tracking (default 48 hours)
approval comments and attachments
full audit trail generation
4.3 Operations Analytics Domain
KPI dashboards APIs
activity metrics
message reach tracking
attendance anomaly detection
work order SLA tracking
multi-criteria search (patients/doctors/appointments/expenses)
4.4 Export Domain
async export job processing
field whitelist filtering
sensitive data desensitization
export task tracking
export audit logs for compliance
4.5 Data Governance Domain
data validation rules (missing, duplicate, invalid ranges)
batch import validation pipeline
snapshot/versioning system
rollback support
data lineage tracking
daily backups and 30-day archival policy
retry mechanism (max 3 attempts)
4.6 Security & Compliance Domain
encrypted storage for sensitive fields (ID, contact info)
role-based response masking
HTTPS-only enforcement
immutable audit logs
login risk control (5 failures → 30 min lockout)
file upload validation (≤20MB)
file deduplication via hash fingerprint
access control for attachments by org and business context
5. Data Model Design

Core entities:

users
organizations
roles
permissions
role_authorizations
approval_process_definitions
approval_instances
approval_tasks
attachments
export_jobs
audit_logs
metric_snapshots
data_dictionary
6. Multi-Tenancy Design

The system enforces strict organization-level isolation.

Strategy:
every table includes organization_id
all queries are filtered by organization context
service layer enforces access boundaries
optional DB-level indexing for performance isolation
7. Workflow Engine Design

Workflow system supports two core types:

resource application → approval → allocation
credit change approval workflow
Execution flow:
request submitted
→ create approval instance
→ evaluate workflow definition
→ assign tasks (parallel/sequential)
→ apply SLA timer
→ collect approvals
→ finalize decision
→ write audit log
Features:
conditional branching logic
parallel approval completion requirement
SLA-based reminders
attachment support
approval comments history
full traceability
8. Idempotency Design

To prevent duplicate processing:

business request key used as idempotency key
duplicates within 24 hours return cached result
enforced at service + database level
indexed for fast lookup
9. Export System Design
Flow:
export request → validation → snapshot creation → async processing → file generation → audit log
Rules:
only whitelisted fields allowed
sensitive fields automatically masked
export tracked as immutable task record
10. Data Governance Design
Validation pipeline:
schema validation
duplicate detection
range checking
business rule validation
Versioning:
every batch import creates snapshot version
rollback restores previous snapshot state
lineage tracking connects source → transformation → final state
11. Security Design
Authentication:
JWT-based auth
password hashing using bcrypt/PBKDF2
password rule enforcement (≥8 chars, letters + numbers)
Login protection:
5 failed attempts within 10 minutes → 30-minute lock
Encryption:
AES encryption for sensitive fields
encryption key derived from password or system key
File security:
SHA-256 deduplication
size limit 20MB
type validation
org-based access control
Backup security:
AES-256-GCM artifact encryption
optional compression before encryption
persistent mounted storage
12. Audit & Logging Design
all system actions logged in immutable audit table
includes:
user actions
workflow transitions
export operations
login attempts
logs are append-only and cannot be modified or deleted
13. Background Processing Design

Uses internal scheduler loops for async tasks:

export generation
SLA reminders
retry failed jobs
data validation batch processing
encrypted backup artifact generation

Retry policy:

max 3 retries
exponential backoff
failure recorded in audit log
14. Scheduler Design
runs periodic tasks (SLA checks, reminders)
processes overdue workflow instances
handles retry queue cleanup
ensures time-based enforcement
15. Error Handling Strategy
all API errors are structured JSON responses
validation errors handled at schema level (Pydantic)
workflow errors logged in audit system
no silent failures for critical operations
16. Transaction & Consistency Design
SQLAlchemy session-based transactions
atomic operations for workflow transitions
rollback on failure
idempotent write guarantees for critical operations
17. Performance Considerations
indexed fields:
organization_id
timestamps
status enums
query optimization for analytics filters
background processing for heavy operations (export/metrics)
18. Deployment Design
Docker-based deployment
docker-compose for:
FastAPI service
PostgreSQL
persistent attachment storage
persistent backup storage
fully offline capable environment
19. Future Extensibility

System is designed to support:

external frontend integration
external HIS/EMR systems
event-driven architecture upgrade
microservice decomposition (if needed)
20. Summary

This system is designed as a secure, modular, and scalable backend platform for hospital operations, combining workflow automation, analytics, governance, and compliance under a unified API architecture.

