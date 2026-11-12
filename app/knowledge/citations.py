"""Validate citations against actual successful tool results, never model metadata."""

import json
import re

from app.knowledge.types import DocumentCitation

_REFERENCE = re.compile(r"\[D:([^\]\s]+)\]")


def collect_citations(answer, steps):
    available = {}
    for step in steps:
        if step.tool != "search_documents" or not step.ok:
            continue
        try:
            payload = json.loads(step.output)
            for hit in payload.get("hits", []):
                available[hit["reference"]] = DocumentCitation(
                    reference=hit["reference"],
                    document_id=hit["document_id"],
                    chunk_id=hit["chunk_id"],
                    chunk_index=hit["chunk_index"],
                    filename=hit["filename"],
                    snippet=hit["content"][:300],
                    **{key: hit.get(key) for key in ("document_date", "page_number", "section")},
                    document_type=hit.get("document_type", "reference"),
                )
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    citations, errors, seen = [], [], set()
    for item in _REFERENCE.findall(answer):
        ref = "D:" + item
        if ref in seen:
            continue
        seen.add(ref)
        if ref in available:
            citations.append(available[ref])
        else:
            errors.append(f"引用未出现在本轮检索证据中: {ref}")
    if (
        available
        and not citations
        and not errors
        and answer.strip()
        not in (
            "当前资料不足以回答",
            "当前资料不足以回答。",
            "当前资料不足以回答该问题。",
        )
    ):
        errors.append("使用文档证据的回答缺少可验证引用")
    return citations, errors
