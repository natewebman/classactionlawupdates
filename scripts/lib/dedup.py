"""
dedup.py — Keyword-based duplicate detection for article titles and case names.

Used by generate_content.py, generate_settlements.py, and review_pipeline.py
to prevent publishing multiple articles about the same lawsuit.
"""

from __future__ import annotations

import re
from typing import Optional

# Common words to ignore when comparing titles
STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "need", "must",
    "up", "out", "if", "about", "into", "through", "after", "before",
    "between", "under", "over", "during", "without", "again", "each",
    "how", "what", "when", "where", "why", "who", "which", "that", "this",
    "these", "those", "than", "its", "it", "your", "their", "our",
    "not", "no", "nor", "so", "very", "just", "also", "now", "new",
    # Domain-specific stop words
    "class", "action", "lawsuit", "settlement", "claim", "claims", "file",
    "filing", "filed", "case", "update", "news", "latest", "million",
    "billion", "consumers", "customers", "members", "users", "owners",
    "you", "your", "how", "what", "know", "need",
}


def _extract_keywords(text: str) -> set[str]:
    """Extract significant lowercase keywords from a title or case name."""
    text = text.lower()
    # Remove dollar amounts but keep the number
    text = re.sub(r'\$', '', text)
    # Remove non-alphanumeric chars (keep spaces)
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    words = text.split()
    return {w for w in words if w not in STOP_WORDS and len(w) > 1}


def _jaccard(a: set, b: set) -> float:
    """Jaccard similarity between two sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_duplicate(new_title: str, new_case_name: Optional[str], existing_entries: list) -> bool:
    """
    Check if a new article is a duplicate of any existing article.
    Uses pairwise keyword comparison across titles and case names.

    Args:
        new_title: Title of the new article
        new_case_name: Case name of the new article (can be None)
        existing_entries: List of dicts with 'title' and optional 'case_name' keys

    Returns:
        True if the article appears to be a duplicate
    """
    new_title_kw = _extract_keywords(new_title)
    new_case_kw = _extract_keywords(new_case_name) if new_case_name else set()

    if not new_title_kw and not new_case_kw:
        return False

    for existing in existing_entries:
        ex_title_kw = _extract_keywords(existing.get("title", ""))
        ex_case_kw = _extract_keywords(existing.get("case_name", "")) if existing.get("case_name") else set()

        if not ex_title_kw and not ex_case_kw:
            continue

        # Check all pairwise comparisons — any high similarity means duplicate
        # 1. New title vs existing title
        if _jaccard(new_title_kw, ex_title_kw) >= 0.4:
            return True

        # 2. New title vs existing case name
        if _jaccard(new_title_kw, ex_case_kw) >= 0.4:
            return True

        # 3. New case name vs existing title
        if _jaccard(new_case_kw, ex_title_kw) >= 0.4:
            return True

        # 4. New case name vs existing case name (2+ keyword overlap = same party)
        if new_case_kw and ex_case_kw:
            case_overlap = new_case_kw & ex_case_kw
            if len(case_overlap) >= 2:
                return True

    return False


def load_existing_articles(site_db) -> list[dict]:
    """Load existing article titles and case names from the database for dedup."""
    try:
        existing = site_db.table("articles") \
            .select("title, case_name") \
            .neq("content_stage", "failed") \
            .execute()
        return existing.data or []
    except Exception as e:
        print(f"WARNING: Could not load existing articles for dedup: {e}")
        return []
