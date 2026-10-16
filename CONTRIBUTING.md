# Contributing to Sourcebound

Thanks for helping improve Sourcebound. The project is an early, self-hosted open-source application; changes should preserve the evidence, version, and access-control guarantees described in the README and `docs/security.md`.

## Before opening a pull request

1. Open an issue or discussion for substantial product or architecture changes so the scope is clear.
2. Keep changes focused and update the relevant documentation, API examples, or evaluation fixtures.
3. Never include company documents, real user answers, access tokens, API keys, `.env` files, or production data. Use the fictional corpus in `evals/demo_corpus/`.
4. Do not weaken authorization filters, citation validation, abstention, deletion behavior, or audit recording without documenting the security impact.

## Local checks

From `frontend/`, run:

```sh
npm ci
npm run lint
npm run build
```

For the backend, install the package from `backend/`, then run:

```sh
python -m compileall -q src migrations ../scripts
alembic upgrade head
```

For the self-hosted integration path, copy `.env.example` to `.env`, replace every development credential, and start only with non-sensitive data:

```sh
docker compose up --build -d
```

Never publish a deployment image or expose the local Compose stack to the public internet as a substitute for the deployment hardening described in `docs/security.md` and `docs/upgrade-runbook.md`.

## Pull request expectations

- Explain the user-facing problem and the behavior that changes.
- Include migration and recovery notes when changing persisted data or indexes.
- Include security and privacy implications for changes to identity, retrieval, logging, traces, or answer history.
- Report the checks you ran and any checks that need a provider, identity tenant, or organization-approved corpus.
- Keep generated artifacts, local caches, backups, and credentials out of the commit.

Contributions are licensed under the repository's Apache-2.0 license. By submitting a contribution, you represent that you have the right to submit it under that license.
