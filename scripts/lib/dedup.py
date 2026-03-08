"""
dedup.py — Keyword-based duplicate detection for article titles and case names.

Used by generate_content.py, generate_settlements.py, and review_pipeline.py
to prevent publishing multiple articles about the same lawsuit.
"""

from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher
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


# Common proper noun phrases that are NOT company/person names
_PROPER_NOUN_STOPLIST = {
    "united states", "supreme court", "district court", "circuit court",
    "federal court", "appeals court", "court of appeals", "new york",
    "los angeles", "san francisco", "san diego", "class action",
    "class members", "personal injury", "product recall", "data breach",
    "consumer protection", "fair labor", "equal employment",
}


def _extract_proper_noun_phrases(text: str) -> list[str]:
    """Extract capitalized multi-word phrases (likely company/person names)."""
    # Match sequences of 2+ capitalized words (e.g., "Wells Fargo", "Johnson & Johnson")
    phrases = re.findall(r'\b(?:[A-Z][a-z]+(?:\s+(?:&\s+)?[A-Z][a-z]+)+)\b', text)
    # Also catch single-word proper nouns followed by common legal suffixes
    # that indicate companies (e.g., "Apple Inc", "Google LLC")
    phrases += re.findall(r'\b([A-Z][a-z]+\s+(?:Inc|LLC|Corp|Co|Ltd|Group|Holdings))\b', text)
    # Deduplicate, skip short matches, filter out common non-entity phrases
    seen = set()
    result = []
    for p in phrases:
        p = p.strip()
        if len(p) > 3 and p not in seen and p.lower() not in _PROPER_NOUN_STOPLIST:
            seen.add(p)
            result.append(p)
    return result


_LEGAL_SUFFIXES = re.compile(
    r',?\s*\b(?:Inc\.?|LLC|L\.L\.C\.?|Corp(?:oration)?\.?|Co\.?|Ltd\.?|'
    r'Group|Holdings|Enterprises?|International|LP|L\.P\.?|PLC|NA|N\.A\.?)\s*$',
    re.IGNORECASE,
)


def _normalize_company(name: str) -> str:
    """Strip legal suffixes and normalize a company name for matching."""
    name = _LEGAL_SUFFIXES.sub('', name).strip().rstrip('.,;')
    return name


def _jaccard(a: set, b: set) -> float:
    """Jaccard similarity between two sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_duplicate(
    new_title: str,
    new_case_name: Optional[str],
    existing_entries: list,
) -> bool:
    """
    Check if a new article is a duplicate of any existing article.
    Uses pairwise keyword comparison across titles and case names.

    Does NOT block by company name alone — multiple articles about the
    same company (different cases) are allowed. Use is_case_duplicate()
    for case-identity level dedup.

    Args:
        new_title: Title of the new article
        new_case_name: Case name of the new article (can be None)
        existing_entries: List of dicts with 'title' and optional 'case_name' keys

    Returns:
        True if the article appears to be a duplicate
    """
    THRESHOLD = 0.35

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
        if _jaccard(new_title_kw, ex_title_kw) >= THRESHOLD:
            return True

        # 2. New title vs existing case name
        if _jaccard(new_title_kw, ex_case_kw) >= THRESHOLD:
            return True

        # 3. New case name vs existing title
        if _jaccard(new_case_kw, ex_title_kw) >= THRESHOLD:
            return True

        # 4. New case name vs existing case name (2+ keyword overlap = same party)
        if new_case_kw and ex_case_kw:
            case_overlap = new_case_kw & ex_case_kw
            if len(case_overlap) >= 2:
                return True

    return False


def check_research_context(
    research_text: str,
    existing_articles: list[dict],
    use_proper_nouns: bool = True,
) -> tuple[bool, str | None]:
    """
    Check if Perplexity research covers a topic already in the database.

    Extracts candidate case names ("X v. Y" patterns) and labeled entities
    from the research text, then checks against existing article titles
    and case names using keyword similarity.

    Args:
        research_text: The research or article text to check
        existing_articles: List of existing article dicts
        use_proper_nouns: Whether to extract proper noun phrases (default True).
            Set to False for full article body checks to avoid false positives
            from generic legal terms in long text.

    Returns (is_duplicate, matched_existing_title) so the caller can log
    which existing article was matched.
    """
    candidates: list[str] = []

    # 1. Case name patterns: "X v. Y" or "X vs. Y"
    for m in re.finditer(r'[A-Z][\w.\s&\'-]+?\s+(?:v\.|vs\.?)\s+[A-Z][\w.\s&\'-]+', research_text):
        text = m.group().strip()
        if len(text) > 10:
            candidates.append(text)

    # 2. Labeled fields: "Case: ...", "Defendant: ...", etc.
    for m in re.finditer(
        r'(?:case\s*(?:name)?|defendant|company|plaintiff)\s*[:\-]\s*([^\n]{5,100})',
        research_text, re.IGNORECASE,
    ):
        candidates.append(m.group(1).strip().rstrip('.'))

    # 3. Proper noun phrases — catches "The lawsuit against Wells Fargo..."
    # Skipped for full article body checks (too many false positives in long text)
    if use_proper_nouns:
        for phrase in _extract_proper_noun_phrases(research_text):
            candidates.append(phrase)

    if not candidates:
        return False, None

    for candidate in candidates:
        cand_kw = _extract_keywords(candidate)
        if len(cand_kw) < 2:
            continue

        for existing in existing_articles:
            ex_title_kw = _extract_keywords(existing.get("title", ""))
            ex_case_kw = (
                _extract_keywords(existing.get("case_name", ""))
                if existing.get("case_name")
                else set()
            )

            # Title keyword overlap
            if _jaccard(cand_kw, ex_title_kw) >= 0.4:
                return True, existing.get("title")

            # Case name keyword overlap
            if _jaccard(cand_kw, ex_case_kw) >= 0.4:
                return True, existing.get("title")

            # 2+ shared keywords between case names = likely same parties
            if ex_case_kw and len(cand_kw & ex_case_kw) >= 2:
                return True, existing.get("title")

    return False, None


def extract_keywords(text: str) -> set[str]:
    """Public wrapper around _extract_keywords for use by other modules."""
    return _extract_keywords(text)


def extract_company_from_case_name(case_name: str) -> str | None:
    """Extract and normalize defendant company from 'X v. Y' case name."""
    if not case_name:
        return None
    match = re.match(r'.+?\s+(?:v\.|vs\.?)\s+(.+)', case_name, re.IGNORECASE)
    if not match:
        return None
    company = match.group(1).strip().rstrip('.,;')
    company = re.sub(r',?\s*(?:Case|No\.|Civ\.|Docket).*$', '', company, flags=re.IGNORECASE).strip()
    company = _normalize_company(company)
    return company if len(company) > 2 else None


def load_existing_articles(site_db) -> list[dict]:
    """Load existing article titles, case names, and categories from the database for dedup."""
    try:
        existing = site_db.table("articles") \
            .select("title, case_name, category") \
            .neq("content_stage", "failed") \
            .execute()
        return existing.data or []
    except Exception as e:
        print(f"WARNING: Could not load existing articles for dedup: {e}")
        return []


def build_avoidance_data(existing_articles: list[dict], category: str | None = None) -> dict:
    """
    Build structured avoidance data for Perplexity research prompts.

    Returns:
        {
            "titles": list[str] — deduplicated title + case_name strings (for prompt),
            "companies": set[str] — defendant/company names from "X v. Y" patterns,
            "keywords": list[set[str]] — keyword sets parallel to titles (for Jaccard),
        }

    If category is provided, same-category articles are sorted first (most likely duplicates).
    """
    companies: set[str] = set()
    seen_titles: dict[str, None] = {}  # order-preserving dedup
    keywords: list[set[str]] = []

    # Sort same-category articles first when category is specified
    if category:
        sorted_articles = sorted(
            existing_articles,
            key=lambda a: (0 if a.get("category") == category else 1),
        )
    else:
        sorted_articles = existing_articles

    for article in sorted_articles:
        title = article.get("title", "")
        case_name = article.get("case_name", "")

        if title and title not in seen_titles:
            seen_titles[title] = None
            keywords.append(_extract_keywords(title))

        if case_name and case_name not in seen_titles:
            seen_titles[case_name] = None
            keywords.append(_extract_keywords(case_name))

            # Extract defendant company from "X v. Y" pattern
            match = re.match(r'.+?\s+(?:v\.|vs\.?)\s+(.+)', case_name, re.IGNORECASE)
            if match:
                company = match.group(1).strip().rstrip('.,;')
                # Remove trailing case numbers / court references
                company = re.sub(r',?\s*(?:Case|No\.|Civ\.|Docket).*$', '', company, flags=re.IGNORECASE).strip()
                company = _normalize_company(company)
                if len(company) > 2:
                    companies.add(company)

    return {
        "titles": list(seen_titles.keys()),
        "companies": companies,
        "keywords": keywords,
    }


def is_topic_covered(topic_text: str, avoidance_data: dict, threshold: float = 0.4) -> tuple[bool, str | None]:
    """
    Check whether a topic description overlaps with existing content.

    Uses a 3-strategy approach:
    1. Check proper noun phrases against known company names
    2. Extract keywords from proper noun phrases and Jaccard-compare
    3. Fall back to full-text keyword extraction (original behavior)

    Args:
        topic_text: Text describing a potential topic (research text, company name, etc.)
        avoidance_data: Output of build_avoidance_data()
        threshold: Jaccard threshold (default 0.4)

    Returns:
        (is_covered, matched_title_or_None)
    """
    companies = avoidance_data.get("companies", set())
    titles = avoidance_data.get("titles", [])
    keywords_list = avoidance_data.get("keywords", [])

    # Strategy 1: Check proper noun phrases against known companies
    for phrase in _extract_proper_noun_phrases(topic_text):
        phrase_lower = _normalize_company(phrase).lower()
        if len(phrase_lower) < 3:
            continue
        for company in companies:
            comp_lower = _normalize_company(company).lower()
            if len(comp_lower) < 3:
                continue
            tokens = comp_lower.split()
            if all(t in phrase_lower for t in tokens):
                return True, f"Company match: {company}"

    # Strategy 2: Extract keywords from noun phrases + check Jaccard
    phrases = _extract_proper_noun_phrases(topic_text)
    if phrases:
        phrase_kw: set[str] = set()
        for p in phrases:
            phrase_kw |= _extract_keywords(p)
        if len(phrase_kw) >= 2:
            for idx, existing_kw in enumerate(keywords_list):
                if _jaccard(phrase_kw, existing_kw) >= threshold:
                    matched = titles[idx] if idx < len(titles) else None
                    return True, matched

    # Strategy 3: Fall back to full-text keyword extraction (original behavior)
    topic_kw = _extract_keywords(topic_text)
    if len(topic_kw) >= 2:
        for idx, existing_kw in enumerate(keywords_list):
            if _jaccard(topic_kw, existing_kw) >= threshold:
                matched = titles[idx] if idx < len(titles) else None
                return True, matched

    return False, None


def is_case_duplicate(candidate: dict, existing_pool: list[dict]) -> bool:
    """
    Check if a case candidate is a duplicate based on lawsuit identity.
    Does NOT block by company name alone — only blocks identical cases.

    Duplicate if ANY of:
      A) docket_number matches
      B) case_title similarity > 75% (SequenceMatcher)
      C) same defendant AND same court AND filing_date within ±3 days

    Args:
        candidate: Dict with case_title, docket_number, defendant, court, filing_date
        existing_pool: List of dicts with the same keys (from articles + case_candidates)

    Returns:
        True if the candidate is a duplicate of an existing case
    """
    new_title = candidate.get("case_title", "").strip()
    new_docket = candidate.get("docket_number", "")
    new_defendant = _normalize_company(candidate.get("defendant", "") or "").lower()
    new_court = (candidate.get("court") or "").strip().lower()
    new_date = candidate.get("filing_date")  # ISO string or None

    for existing in existing_pool:
        ex_title = existing.get("case_title", "").strip()
        ex_docket = existing.get("docket_number", "")
        ex_defendant = _normalize_company(existing.get("defendant", "") or "").lower()
        ex_court = (existing.get("court") or "").strip().lower()
        ex_date = existing.get("filing_date")

        # Criterion A: Docket number match
        if new_docket and ex_docket and new_docket.strip() == ex_docket.strip():
            return True

        # Criterion B: Case title similarity > 75%
        if new_title and ex_title:
            ratio = SequenceMatcher(None, new_title.lower(), ex_title.lower()).ratio()
            if ratio > 0.75:
                return True

        # Criterion C: Same defendant + same court + filing dates within ±3 days
        if (new_defendant and ex_defendant and new_court and ex_court
                and new_date and ex_date):
            if new_defendant == ex_defendant and new_court == ex_court:
                try:
                    d1 = datetime.strptime(str(new_date)[:10], "%Y-%m-%d")
                    d2 = datetime.strptime(str(ex_date)[:10], "%Y-%m-%d")
                    if abs((d1 - d2).days) <= 3:
                        return True
                except (ValueError, TypeError):
                    pass

    return False
