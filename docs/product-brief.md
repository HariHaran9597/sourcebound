# Sourcebound product brief

## Problem

Teams keep policies, runbooks, technical references, and operating procedures across files that change over time. Employees lose time finding the right passage, cannot tell which revision a chatbot used, and may receive plausible answers when a source is missing or another document disagrees.

## Product

Sourcebound is an Apache-2.0 self-hosted, open-source knowledge copilot for organizations that need to ask questions across their internal documents and inspect where each answer came from. It indexes PDF versions as immutable evidence; answers cite exact document versions and page-backed passages; version changes are reviewable.

## First user and workflow

The first user is an on-call engineer or incident commander who needs to find and verify the current incident procedure, understand what changed between revisions, and hand off cited evidence. This is the first wedge for a general enterprise knowledge copilot, not an incident management platform. The self-hosted first release supports one organization per deployment. OIDC mode is organization-scoped and can restrict individual documents to identity-provider groups. See [the first pilot workflow](first-pilot-workflow.md) for the user journey, fixtures, and release scorecard.

## Why this is different from a generic document chatbot

- **Changing knowledge is first-class:** every uploaded revision is retained, and a new version becomes latest only after parsing and indexing succeed. Time-aware questions selectively search prior indexed versions and disclose that scope.
- **Evidence is inspectable:** retrieved text keeps immutable version IDs and parser page provenance; the answer view exposes the actual passages.
- **Disagreement and relationships are visible:** answers can surface model-suggested conflicts with links to source passages; workspace admins can also curate same-organization document links that guide explicit relationship searches without bypassing access rules.
- **Retrieval combines semantic and exact-term signals:** dense plus sparse retrieval is fused instead of relying on one vector score.
- **Trust limits are visible:** unsupported retrieval can abstain; the project publishes current security/quality gaps instead of claiming citation links prove correctness.
- **Self-hosting is part of the product:** files, metadata, vectors, workers, and model endpoints can remain in an organization's environment.

## V1 scope

1. Upload and version PDF sources.
2. Parse to structured text with page provenance.
3. Index dense and sparse representations.
4. Ask questions against the latest successfully indexed versions by default; temporal/comparison questions also search older indexed versions.
5. Return a grounded answer with source passage/version/page citations or abstain when there are no search results.
6. Review a text diff between the latest two parsed versions.
7. Operate a single-organization self-hosted installation with OIDC as the supported identity integration path.

## Not yet in V1

Managed connectors, non-PDF ingestion and OCR review, cross-document graph retrieval, streaming answers, published evaluation results, usage billing, hosted multi-tenancy, compliance certification, and a public SaaS control plane.

## Product success measures

These are targets to validate with a real corpus, not current performance claims:

- Retrieval recall@8 on a curated source-linked question set.
- Citation precision: fraction of citations that support the nearby answer claim.
- Answer correctness and abstention quality from a blinded human review.
- P50/P95 end-to-end latency and cost per answered question under a published hardware/model setup.
- Time to find and verify an answer compared with the team's existing search process.
- No cross-organization or unauthorized-document retrieval in the security evaluation before production deployment.

The repository's evaluation runner reports source, optional page and version-number, abstention, and conflict metrics; latency; and dataset/model/retrieval/prompt versions. These are measurements to collect on a reviewed corpus, not achieved targets or published results.

## Initial design constraint

The references that motivated this project describe large heterogeneous corpora and real operational RAG pipelines. Sourcebound should adopt the useful engineering lessons—metadata-aware parsing, hybrid retrieval, reranking where measured, reliable ingestion, and source lineage—without pretending the initial PDF-only build has already solved those larger workloads.

\n