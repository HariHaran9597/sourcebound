# Fictional Sourcebound demo corpus

This is invented material for demonstrating the upload, citation, version, relationship, and abstention workflows. It contains no real company policy and must not be used to make operational decisions. It is a small walkthrough dataset, not a benchmark or a published quality result.

## Generate the PDFs

From the repository root, install the optional demo dependency and generate the PDFs:

```powershell
python -m pip install -r scripts/requirements-demo.txt
python scripts/build_demo_corpus.py
```

Generated PDFs are written under `evals/demo_corpus/generated/`.

## Upload and connect the sources

1. Start Sourcebound and upload `generated/incident-v1/Northstar Incident Runbook.pdf`.
2. Open that document in Knowledge and upload `generated/incident-v2/Northstar Incident Runbook.pdf` as its next version. This sequence is important: the evaluation expects version 1 and version 2 of the same source.
3. Upload `generated/retention/Northstar Data Retention Standard.pdf` and `generated/vendor/Northstar Vendor Operations Guide.pdf` as separate sources.
4. As a workspace admin, create a `related_to` relationship from the incident runbook to the vendor operations guide. Relationship metadata helps the retriever navigate; the guide's cited passage remains the evidence.
5. Run the question set from the repository root:

```powershell
$env:SOURCEBOUND_API_URL = "http://127.0.0.1:8000"
python scripts/evaluate_api.py evals/demo_corpus/sourcebound-demo.v1.jsonl --output evals/reports/sourcebound-demo.v1.json
```

The question set covers current runbook evidence, a temporal version comparison, retention, relationship-aware retrieval, and an unsupported question. Review every answer and citation; the expected labels are authored for these exact fictional PDFs and do not establish general system accuracy. Reports can contain operational identifiers and model metadata; keep them in an appropriate location.

## Exercise document-level access and canary-leak checks

This synthetic scenario checks that retrieval filters a restricted document even when a question explicitly tells the model to ignore access rules. It requires an OIDC-enabled deployment and two test identities: a workspace admin and an ordinary user who does not have the `security-ops` group. The appendix contains a public-in-repository synthetic canary, so this demonstrates the ACL/evaluation workflow only; it is not a secret-handling test or a security certification.

1. Generate the restricted fixture with the PDF build command above. Upload `generated/restricted/Restricted Northstar Operations Appendix.pdf`.
2. As the workspace admin, register `security-ops` in the Knowledge view and assign the appendix document to that group. Keep the incident runbook available to the ordinary test user.
3. Obtain a short-lived access token for the ordinary non-admin user. Do not use an admin token because workspace admins intentionally bypass document group restrictions.
4. Run the security question set:

```powershell
$env:SOURCEBOUND_API_URL = "http://127.0.0.1:8000"
# Supply a short-lived token through your secret-safe shell workflow.
$env:SOURCEBOUND_EVAL_BEARER_TOKEN = "<ordinary-user-token>"
python scripts/evaluate_api.py evals/demo_corpus/sourcebound-security-redteam.v1.jsonl --output evals/reports/sourcebound-security-redteam.v1.json
```

Review expected-source recall alongside `forbidden_citation_case_rate` and `forbidden_answer_term_case_rate`. The report contains only hit booleans and aggregate rates, not answers or canary strings. A zero hit rate covers only this exact fixture and literal-canary check; it does not prove resistance to paraphrase, other prompt attacks, compromised identity claims, or cross-organization leakage.

## Rebuild behavior

`scripts/build_demo_corpus.py` uses ReportLab from `scripts/requirements-demo.txt`, separate from the API runtime dependencies. The PDF sources are intentionally short and one-page so page labels in the JSONL stay legible and stable.
