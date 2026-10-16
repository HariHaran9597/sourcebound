# First pilot workflow: incident response handoff

## Decision

Sourcebound's first product workflow is **incident response and operational handoff for engineering/on-call teams**. The first user is an on-call engineer or incident commander who needs to find the current, approved procedure quickly and pass the relevant evidence to the next person.

This is a focused first use case for the general enterprise knowledge copilot. It exercises the product's distinctive capabilities—version lineage, page-backed citations, selective history retrieval, curated links, abstention, and document-level access—without claiming that Sourcebound is an incident-management system or that generated answers replace an incident commander.

## User problem

During an operational incident or shift handoff, the engineer has to locate the right runbook, determine whether it is current, find a related vendor procedure, and communicate the cited steps. A generic chatbot can return a plausible but stale paragraph without showing which revision it used. Sourcebound should make the evidence and revision explicit, and say when its indexed sources do not answer the question.

## V1 user journey

1. A workspace admin uploads an incident runbook and its next revision, plus related vendor and operational policy PDFs.
2. Sourcebound retains immutable versions and makes a new version current only after parsing and indexing succeed.
3. An on-call user asks a question such as “How soon must Security be notified?” The default answer uses the latest indexed runbook and links each claim to its source passage, version, and page.
4. A change question such as “What changed from revision 1?” deliberately includes prior indexed revisions and shows that historical scope.
5. A vendor handoff question can use an admin-curated source relationship while citations still point to the actual evidence.
6. A question unsupported by accessible documents receives an abstention. Restricted documents stay out of the answer for users without the required group.

## Demo and evaluation data

The checked-in Northstar corpus is synthetic and intentionally covers this workflow: two incident-runbook revisions, a retention standard, a vendor operations guide, and a separate restricted appendix containing a synthetic canary. The ordinary evaluation set covers current-procedure evidence, version changes, policy lookup, related vendor guidance, and abstention. The security set covers an allowed question and a denied-document canary request.

These fixtures make the onboarding journey repeatable; they are **not** a real customer dataset, production instruction, benchmark, security certification, or quality claim. A real pilot requires a corpus the organization is authorized to use and a reviewer-curated expected answer/evidence set.

## Pilot scorecard

Before a pilot is called successful, report these measurements separately:

- **Evidence retrieval:** source, page, and version recall/precision on reviewer-labeled questions.
- **Answer quality:** reviewer assessment of correctness, whether each citation supports its nearby claim, and appropriate abstention.
- **Security:** denied-source citation and canary-leak checks with ordinary test identities; include allowed questions to detect “secure” behavior caused by abstaining on everything.
- **Operational value:** time to find and verify an answer with the team's current process versus Sourcebound, using the same tasks and participants.
- **Operations:** P50/P95 answer latency, ingestion success/failure, backup recovery, and upgrade rehearsal results under a documented machine/model setup.

Set numeric pass thresholds with the pilot owner before collecting results. Publish the corpus scope, configuration, method, and failures alongside any reported numbers. Do not infer accuracy from the presence of citations or from the small synthetic fixture.

## Explicit V1 boundary

V1 is a self-hosted, single-organization PDF workflow. It does not page responders, manage incidents, execute runbook steps, ingest live monitoring data, replace identity-provider policy, or provide hosted multi-tenant service. The production deployment must enable OIDC, TLS/reverse-proxy controls, managed secrets, durable storage and backups, and complete the release gates in `roadmap.md` before sensitive documents or internet exposure.
