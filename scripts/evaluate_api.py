#!/usr/bin/env python3
"""Run a versioned, source-grounded question set against Sourcebound's Ask API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * quantile + 0.5)))
    return round(ordered[index], 2)


def load_cases(path: Path) -> list[dict]:
    cases: list[dict] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        try:
            case = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on line {line_number}: {exc}") from exc
        required = {"id", "question", "expected_filenames", "should_abstain"}
        if not isinstance(case, dict):
            raise ValueError(f"Line {line_number}: each JSONL record must be an object")
        if not required.issubset(case):
            raise ValueError(f"Line {line_number} is missing required fields: {sorted(required - set(case))}")
        if (
            not isinstance(case["id"], str)
            or not isinstance(case["question"], str)
            or not isinstance(case["expected_filenames"], list)
            or any(not isinstance(name, str) for name in case["expected_filenames"])
            or not isinstance(case["should_abstain"], bool)
        ):
            raise ValueError(f"Line {line_number}: expected_filenames must be a list and should_abstain a boolean")
        if "should_report_conflict" in case and not isinstance(case["should_report_conflict"], bool):
            raise ValueError(f"Line {line_number}: should_report_conflict must be a boolean when provided")
        if case.get("should_report_conflict") and case["should_abstain"]:
            raise ValueError(f"Line {line_number}: unsupported questions cannot be labeled as conflicts")
        expected_pages = case.get("expected_pages", {})
        forbidden_filenames = case.get("forbidden_filenames", [])
        forbidden_answer_terms = case.get("forbidden_answer_terms", [])
        if (
            not isinstance(forbidden_filenames, list)
            or any(not isinstance(name, str) or not name.strip() for name in forbidden_filenames)
        ):
            raise ValueError(f"Line {line_number}: forbidden_filenames must be a list of non-empty strings")
        if (
            not isinstance(forbidden_answer_terms, list)
            or any(not isinstance(term, str) or not term.strip() for term in forbidden_answer_terms)
        ):
            raise ValueError(f"Line {line_number}: forbidden_answer_terms must be a list of non-empty strings")
        if set(name.casefold() for name in forbidden_filenames) & set(name.casefold() for name in case["expected_filenames"]):
            raise ValueError(f"Line {line_number}: a filename cannot be both expected and forbidden")
        if (
            not isinstance(expected_pages, dict)
            or any(not isinstance(name, str) or not isinstance(pages, list) for name, pages in expected_pages.items())
            or any(not isinstance(page, int) or isinstance(page, bool) or page < 1 for pages in expected_pages.values() for page in pages)
        ):
            raise ValueError(f"Line {line_number}: expected_pages must map filenames to positive page-number lists")
        if not set(expected_pages).issubset(set(case["expected_filenames"])):
            raise ValueError(f"Line {line_number}: expected_pages filenames must also appear in expected_filenames")
        if expected_pages and case["should_abstain"]:
            raise ValueError(f"Line {line_number}: abstention cases cannot include expected_pages")
        expected_versions = case.get("expected_versions", {})
        if (
            not isinstance(expected_versions, dict)
            or any(
                not isinstance(filename, str)
                or not isinstance(versions, list)
                or not versions
                or any(not isinstance(version, int) or isinstance(version, bool) or version < 1 for version in versions)
                or len(set(versions)) != len(versions)
                for filename, versions in expected_versions.items()
            )
        ):
            raise ValueError(f"Line {line_number}: expected_versions must map filenames to positive version-number lists")
        if not set(expected_versions).issubset(set(case["expected_filenames"])):
            raise ValueError(f"Line {line_number}: expected_versions filenames must also appear in expected_filenames")
        if expected_versions and case["should_abstain"]:
            raise ValueError(f"Line {line_number}: abstention cases cannot include expected_versions")
        if not case["should_abstain"] and not case["expected_filenames"]:
            raise ValueError(f"Line {line_number}: answerable cases need at least one expected filename")
        cases.append(case)
    if not cases:
        raise ValueError("Dataset has no cases.")
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Case IDs must be unique.")
    return cases


def ask(api_url: str, question: str, token: str | None, timeout: float) -> dict:
    payload = json.dumps({"question": question}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"{api_url.rstrip('/')}/api/v1/ask", data=payload, headers=headers, method="POST")
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def evaluate(api_url: str, dataset_path: Path, timeout: float) -> dict:
    parsed_url = urlsplit(api_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        raise ValueError("API URL must be an absolute HTTP or HTTPS origin.")
    origin = f"{parsed_url.scheme}://{parsed_url.hostname}" + (f":{parsed_url.port}" if parsed_url.port else "")
    cases = load_cases(dataset_path)
    token = os.environ.get("SOURCEBOUND_EVAL_BEARER_TOKEN")
    durations: list[float] = []
    records: list[dict] = []
    citation_hits = citation_count = citation_matches = 0
    labeled_page_count = sum(
        len({
            (filename.casefold(), page)
            for filename, pages in case.get("expected_pages", {}).items()
            for page in pages
        })
        for case in cases
    )
    expected_version_pairs = [
        {
            (filename.casefold(), version)
            for filename, versions in case.get("expected_versions", {}).items()
            for version in versions
        }
        for case in cases
    ]
    labeled_version_count = sum(len(pairs) for pairs in expected_version_pairs)
    matched_version_count = returned_labeled_version_count = returned_version_matches = 0
    matched_page_count = returned_labeled_page_count = returned_page_matches = 0
    true_positive = false_positive = false_negative = 0
    conflict_true_positive = conflict_false_positive = conflict_false_negative = 0
    conflict_labeled_count = 0
    forbidden_citation_labeled_count = 0
    forbidden_citation_hit_count = 0
    forbidden_answer_labeled_count = 0
    forbidden_answer_hit_count = 0
    errors = 0

    for case_index, case in enumerate(cases):
        started = time.perf_counter()
        try:
            response = ask(api_url, case["question"], token, timeout)
            elapsed_ms = (time.perf_counter() - started) * 1000
            durations.append(elapsed_ms)
            citations = response.get("citations", [])
            expected = {name.casefold() for name in case["expected_filenames"]}
            returned = [str(citation.get("filename", "")) for citation in citations]
            forbidden_filenames = {name.casefold() for name in case.get("forbidden_filenames", [])}
            forbidden_citation_hit = any(name.casefold() in forbidden_filenames for name in returned)
            if forbidden_filenames:
                forbidden_citation_labeled_count += 1
                forbidden_citation_hit_count += int(forbidden_citation_hit)
            forbidden_terms = [term.casefold() for term in case.get("forbidden_answer_terms", [])]
            answer_text = str(response.get("answer", "")).casefold()
            forbidden_answer_hit = any(term in answer_text for term in forbidden_terms)
            if forbidden_terms:
                forbidden_answer_labeled_count += 1
                forbidden_answer_hit_count += int(forbidden_answer_hit)
            matches = sum(name.casefold() in expected for name in returned)
            citation_count += len(returned)
            citation_matches += matches
            has_expected_source = any(name.casefold() in expected for name in returned)
            expected_page_pairs = {
                (filename.casefold(), page)
                for filename, pages in case.get("expected_pages", {}).items()
                for page in pages
            }
            returned_page_pairs = {
                (str(citation.get("filename", "")).casefold(), page)
                for citation in citations
                for page in citation.get("page_numbers", [])
                if isinstance(page, int) and not isinstance(page, bool) and page > 0
            }
            matched_pages = expected_page_pairs & returned_page_pairs
            if expected_page_pairs:
                matched_page_count += len(matched_pages)
                returned_labeled_page_count += len(returned_page_pairs)
                returned_page_matches += len(matched_pages)
            case_expected_versions = expected_version_pairs[case_index]
            returned_version_pairs = {
                (str(citation.get("filename", "")).casefold(), citation.get("version_number"))
                for citation in citations
                if isinstance(citation.get("version_number"), int)
                and not isinstance(citation.get("version_number"), bool)
            }
            matched_versions = case_expected_versions & returned_version_pairs
            if case_expected_versions:
                matched_version_count += len(matched_versions)
                target_filenames = {filename for filename, _ in case_expected_versions}
                returned_for_target = {pair for pair in returned_version_pairs if pair[0] in target_filenames}
                returned_labeled_version_count += len(returned_for_target)
                returned_version_matches += len(matched_versions)
            if not case["should_abstain"]:
                citation_hits += int(has_expected_source)

            predicted_abstain = bool(response.get("abstained"))
            if predicted_abstain and case["should_abstain"]:
                true_positive += 1
            elif predicted_abstain:
                false_positive += 1
            elif case["should_abstain"]:
                false_negative += 1

            predicted_conflict = bool(response.get("conflicts", []))
            expected_conflict = case.get("should_report_conflict")
            if expected_conflict is not None:
                conflict_labeled_count += 1
                if predicted_conflict and expected_conflict:
                    conflict_true_positive += 1
                elif predicted_conflict:
                    conflict_false_positive += 1
                elif expected_conflict:
                    conflict_false_negative += 1

            records.append({
                "id": case["id"],
                "ok": True,
                "latency_ms": round(elapsed_ms, 2),
                "expected_source_hit": has_expected_source,
                "expected_page_recall": round(len(matched_pages) / len(expected_page_pairs), 4) if expected_page_pairs else None,
                "expected_version_hit": bool(matched_versions) if case_expected_versions else None,
                "expected_version_recall": round(len(matched_versions) / len(case_expected_versions), 4) if case_expected_versions else None,
                "expected_abstention": case["should_abstain"],
                "actual_abstention": predicted_abstain,
                "expected_conflict": expected_conflict,
                "actual_conflict": predicted_conflict,
                "forbidden_citation_hit": forbidden_citation_hit if forbidden_filenames else None,
                "forbidden_answer_term_hit": forbidden_answer_hit if forbidden_terms else None,
                "citation_count": len(returned),
                "returned_version_ids": sorted({str(citation.get("version_id")) for citation in citations if citation.get("version_id")}),
                "trace_id": response.get("trace_id"),
                "model_name": response.get("model_name"),
                "embedding_model": response.get("embedding_model"),
                "reranker_model": response.get("reranker_model"),
                "retrieval_version": response.get("retrieval_version"),
                "retrieval_scope": response.get("retrieval_scope"),
                "prompt_version": response.get("prompt_version"),
            })
        except HTTPError as exc:
            errors += 1
            records.append({"id": case["id"], "ok": False, "error": f"HTTP {exc.code}"})
        except (URLError, TimeoutError, ValueError, KeyError) as exc:
            errors += 1
            records.append({"id": case["id"], "ok": False, "error": type(exc).__name__})

    answerable = sum(not case["should_abstain"] for case in cases)
    abstain_precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else None
    abstain_recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else None
    abstain_f1 = (
        2 * abstain_precision * abstain_recall / (abstain_precision + abstain_recall)
        if abstain_precision is not None and abstain_recall is not None and abstain_precision + abstain_recall > 0
        else 0.0 if abstain_precision is not None and abstain_recall is not None else None
    )
    conflict_precision = (
        conflict_true_positive / (conflict_true_positive + conflict_false_positive)
        if conflict_true_positive + conflict_false_positive else None
    )
    conflict_recall = (
        conflict_true_positive / (conflict_true_positive + conflict_false_negative)
        if conflict_true_positive + conflict_false_negative else None
    )
    conflict_f1 = (
        2 * conflict_precision * conflict_recall / (conflict_precision + conflict_recall)
        if conflict_precision is not None and conflict_recall is not None and conflict_precision + conflict_recall > 0
        else 0.0 if conflict_precision is not None and conflict_recall is not None else None
    )
    dataset_hash = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    return {
        "report_version": 6,
        "generated_at": datetime.now(UTC).isoformat(),
        "api_origin": origin,
        "dataset": dataset_path.name,
        "dataset_sha256": dataset_hash,
        "model_name": next((record.get("model_name") for record in records if record.get("model_name")), None),
        "embedding_model": next((record.get("embedding_model") for record in records if record.get("embedding_model")), None),
        "reranker_model": next((record.get("reranker_model") for record in records if record.get("reranker_model")), None),
        "retrieval_version": next((record.get("retrieval_version") for record in records if record.get("retrieval_version")), None),
        "retrieval_scope": next((record.get("retrieval_scope") for record in records if record.get("retrieval_scope")), None),
        "prompt_version": next((record.get("prompt_version") for record in records if record.get("prompt_version")), None),
        "case_count": len(cases),
        "error_count": errors,
        "conflict_labeled_case_count": conflict_labeled_count,
        "forbidden_citation_labeled_case_count": forbidden_citation_labeled_count,
        "forbidden_answer_labeled_case_count": forbidden_answer_labeled_count,
        "metrics": {
            "expected_source_recall": round(citation_hits / answerable, 4) if answerable else None,
            "expected_source_precision": round(citation_matches / citation_count, 4) if citation_count else None,
            "expected_page_recall": round(matched_page_count / labeled_page_count, 4) if labeled_page_count else None,
            "expected_page_precision": round(returned_page_matches / returned_labeled_page_count, 4) if returned_labeled_page_count else None,
            "expected_version_recall": round(matched_version_count / labeled_version_count, 4) if labeled_version_count else None,
            "expected_version_precision": round(returned_version_matches / returned_labeled_version_count, 4) if returned_labeled_version_count else None,
            "abstention_precision": round(abstain_precision, 4) if abstain_precision is not None else None,
            "abstention_recall": round(abstain_recall, 4) if abstain_recall is not None else None,
            "abstention_f1": round(abstain_f1, 4) if abstain_f1 is not None else None,
            "conflict_detection_precision": round(conflict_precision, 4) if conflict_precision is not None else None,
            "conflict_detection_recall": round(conflict_recall, 4) if conflict_recall is not None else None,
            "conflict_detection_f1": round(conflict_f1, 4) if conflict_f1 is not None else None,
            "forbidden_citation_case_rate": round(forbidden_citation_hit_count / forbidden_citation_labeled_count, 4) if forbidden_citation_labeled_count else None,
            "forbidden_answer_term_case_rate": round(forbidden_answer_hit_count / forbidden_answer_labeled_count, 4) if forbidden_answer_labeled_count else None,
            "latency_p50_ms": percentile(durations, 0.50),
            "latency_p95_ms": percentile(durations, 0.95),
        },
        "cases": records,
        "limitations": [
            "Source and page metrics are computed over passages cited by the answer, not all retrieval candidates; they do not prove passage entailment or answer correctness.",
            "Conflict metrics include only cases with should_report_conflict labels; model-reported potential conflicts still require human review.",
            "Page-level labels are coarse and depend on reviewer annotations and parser page provenance.",
            "Human review is required for answer correctness and citation support.",
            "Results depend on the indexed corpus, embedding/model versions, hardware, and configuration.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="JSONL gold question set")
    parser.add_argument("--api-url", default=os.environ.get("SOURCEBOUND_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--timeout", type=float, default=150)
    parser.add_argument("--output", type=Path, default=Path("eval-report.json"))
    args = parser.parse_args()
    try:
        report = evaluate(args.api_url, args.dataset, args.timeout)
    except (OSError, ValueError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.output), "cases": report["case_count"], "errors": report["error_count"], "metrics": report["metrics"]}, indent=2))
    return 1 if report["error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
