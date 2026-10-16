# Security policy

Sourcebound is an active pre-release project. Do not expose a local Compose instance to the public internet or upload sensitive company documents until the deployment has been reviewed and hardened for its environment.

## Reporting a vulnerability

Please do not publish details of an exploitable vulnerability in a public issue. Use GitHub's private vulnerability reporting for this repository when it is enabled. If private reporting is unavailable, contact the maintainers privately and include a concise impact description and reproduction steps. Avoid attaching real documents, credentials, or personal data.

Maintainers should acknowledge a report, assess scope and severity, prepare a fix, and coordinate a disclosure window with the reporter. There is no guaranteed response-time or remediation SLA for this pre-release project.

## Deployment security boundaries

- Keep `.env`, provider keys, OIDC client secrets, backups, trace data, and uploaded documents out of GitHub.
- Rotate any key that has been posted in a chat, issue, pull request, terminal log, or repository history.
- Terminate TLS at a trusted reverse proxy; keep PostgreSQL, Qdrant, RabbitMQ, metrics, and tracing interfaces on private networks.
- Enable OIDC and test organization and document-level authorization with the production identity provider before onboarding users.
- Treat answer history, logs, traces, source files, parsed text, and backups as sensitive data; configure retention, encryption, access control, and deletion for the deployment.
- Review `docs/security.md`, `docs/observability.md`, and `docs/backup-restore.md` before operating the service.

The application's audit events are stored in the same database as application data and are not tamper-proof against database administrators. The current self-hosted release has not undergone an independent security audit or certification.
