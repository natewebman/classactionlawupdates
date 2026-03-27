"""
generate_articles.py — Unified Content Generation for classactionlawupdates
============================================================================
Replaces both generate_content.py and generate_settlements.py.

Generates news articles AND/OR settlement articles in a single run based on
the CONTENT_TYPE env var:
  - "mixed"      (default) — generates a mix of news and settlement articles
  - "news"       — only news articles (same as old generate_content.py)
  - "settlement" — only settlements (same as old generate_settlements.py)

Flow:
  1. Load config from environment variables (set by workflow inputs)
  2. Load prompts from scripts/prompts/ directory
  3. For each article:
     a. Determine content type (news vs settlement)
     b. Use appropriate Perplexity research function
     c. Use appropriate prompt pair
     d. Call Claude with research context
     e. Parse structured JSON response
     f. Write article to SITE Supabase (articles table)
     g. Write run_article to ADMIN Supabase (run_articles table)
  4. Update generation_runs in admin DB with totals + completion status
"""

import os
import sys
import json
import re
import uuid
import hashlib
import time
import random
import traceback
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from datetime import datetime, timezone

import anthropic
import requests
from supabase import create_client

# Add scripts/lib to path for shared utilities
sys.path.insert(0, str(Path(__file__).parent / "lib"))
from dedup import (
    is_duplicate, load_existing_articles, check_research_context,
    build_avoidance_data, extract_keywords, is_topic_covered,
    extract_company_from_case_name, is_case_duplicate,
)


# =============================================================================
# CONFIG
# =============================================================================

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
PERPLEXITY_API_KEY = os.environ["PERPLEXITY_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
ADMIN_SUPABASE_URL = os.environ.get("ADMIN_SUPABASE_URL", "")
ADMIN_SUPABASE_KEY = os.environ.get("ADMIN_SUPABASE_KEY", "")

PERPLEXITY_MODEL = "sonar"
PERPLEXITY_HEADERS = {
    "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
    "Content-Type": "application/json",
}

ARTICLES_COUNT = int(os.environ.get("ARTICLES_COUNT", "2"))

# Model alias map — allows friendly names from admin panel
MODEL_ALIASES = {
    "haiku 4.5": "claude-haiku-4-5-20251001",
    "haiku": "claude-haiku-4-5-20251001",
    "haiku-4.5": "claude-haiku-4-5-20251001",
    "claude haiku 4.5": "claude-haiku-4-5-20251001",
    "sonnet 4.5": "claude-sonnet-4-5-20250929",
    "sonnet": "claude-sonnet-4-5-20250929",
    "sonnet-4.5": "claude-sonnet-4-5-20250929",
    "claude sonnet 4.5": "claude-sonnet-4-5-20250929",
    "opus 4.5": "claude-opus-4-5-20250918",
    "opus": "claude-opus-4-5-20250918",
    "opus-4.5": "claude-opus-4-5-20250918",
    "claude opus 4.5": "claude-opus-4-5-20250918",
}

_raw_model = os.environ.get("MODEL", "claude-haiku-4-5-20251001")
MODEL = MODEL_ALIASES.get(_raw_model.lower().strip(), _raw_model)

GENERATION_MODE = os.environ.get("GENERATION_MODE", "standard")
CATEGORIES = os.environ.get("CATEGORIES", "")
ADMIN_RUN_ID = os.environ.get("ADMIN_RUN_ID", "")
PROMPT_VERSION = os.environ.get("PROMPT_VERSION", "v1")

# Content type: "mixed", "news", or "settlement"
CONTENT_TYPE = os.environ.get("CONTENT_TYPE", "mixed").lower().strip()

# Optional: guided content generation (settlement only)
TOPIC_URL = os.environ.get("TOPIC_URL", "")
TOPIC_IDEA = os.environ.get("TOPIC_IDEA", "")

DEFAULT_CATEGORIES = [
    "stocks",
    "personal-injury",
    "product-recalls",
    "drugs-pharmacy",
    "financial",
    "online-privacy",
]

# Semantic descriptions for each category — used in Perplexity discovery
# and Claude generation prompts to prevent miscategorization.
CATEGORY_DESCRIPTIONS = {
    "stocks": (
        "Securities fraud, shareholder class actions, stock manipulation, "
        "insider trading, IPO fraud, SEC violations, and investor lawsuits "
        "against publicly traded companies for misleading financial statements "
        "or stock price drops. Must involve stocks, securities, or investment fraud."
    ),
    "personal-injury": (
        "Bodily injury, wrongful death, toxic exposure (PFAS, asbestos, chemicals), "
        "environmental contamination, workplace injuries, defective medical devices, "
        "and mass torts causing physical harm to people."
    ),
    "product-recalls": (
        "Defective consumer products, product safety recalls, false advertising "
        "about product features, mislabeling, contaminated food/beverages, "
        "auto defects, and consumer protection lawsuits about specific products."
    ),
    "drugs-pharmacy": (
        "Pharmaceutical lawsuits, dangerous drug side effects, pharmacy overcharging, "
        "opioid litigation, PBM practices, drug pricing, medical device failures "
        "related to pharmaceuticals, and health insurance disputes."
    ),
    "financial": (
        "Banking, lending, credit card, mortgage, and insurance lawsuits. "
        "Includes fee disputes, predatory lending, debt collection abuse, "
        "CARES Act/PPP loan issues, overdraft fees, account fraud, and "
        "consumer financial protection cases. NOT stocks or securities fraud."
    ),
    "online-privacy": (
        "Data breaches, privacy violations, BIPA/CCPA lawsuits, unauthorized "
        "tracking/cookies, social media privacy, wiretapping, geolocation tracking, "
        "spam/TCPA robocall cases, and digital rights violations."
    ),
}

SCRIPT_DIR = Path(__file__).parent


# =============================================================================
# HELPERS
# =============================================================================

def sha256_short(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_prompt(filename: str) -> str:
    prompt_path = SCRIPT_DIR / "prompts" / filename
    if not prompt_path.exists():
        print(f"ERROR: Prompt file not found: {prompt_path}")
        sys.exit(1)
    return prompt_path.read_text().strip()


def slugify(title: str, article_id: str = "") -> str:
    slug = title.lower().strip()
    for char in ["'", "\u2019", '"', "\u201c", "\u201d", ":", ";", ",", ".", "!", "?", "(", ")", "[", "]", "&", "$"]:
        slug = slug.replace(char, "")
    slug = slug.replace(" ", "-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = slug.strip("-")[:120]
    if article_id:
        slug = f"{slug}-{article_id[:8]}"
    return slug


def get_admin_db():
    if not ADMIN_SUPABASE_URL or not ADMIN_SUPABASE_KEY:
        return None
    try:
        client = create_client(ADMIN_SUPABASE_URL, ADMIN_SUPABASE_KEY)
        return client
    except Exception as e:
        print(f"WARNING: Could not connect to admin DB: {e}")
        return None


# =============================================================================
# CATEGORY BALANCING
# =============================================================================

def pick_categories_balanced(site_db, count: int) -> list[str]:
    """
    Pick categories for a batch of articles, preferring under-represented categories.
    If CATEGORIES env var is set, respect that override and skip balancing.
    """
    if CATEGORIES:
        cats = [c.strip() for c in CATEGORIES.split(",") if c.strip()]
        result = []
        for i in range(count):
            result.append(cats[i % len(cats)])
        return result

    # Query existing article counts per category
    category_counts = {cat: 0 for cat in DEFAULT_CATEGORIES}
    try:
        for cat in DEFAULT_CATEGORIES:
            result = site_db.table("articles") \
                .select("id", count="exact") \
                .eq("content_stage", "published") \
                .eq("category", cat) \
                .execute()
            category_counts[cat] = result.count if result.count is not None else 0
        print(f"Category balance: {category_counts}")
    except Exception as e:
        print(f"WARNING: Could not query category counts, falling back to random: {e}")
        cats = DEFAULT_CATEGORIES.copy()
        random.shuffle(cats)
        return [cats[i % len(cats)] for i in range(count)]

    # Sort categories by count (ascending = least articles first)
    sorted_cats = sorted(category_counts.items(), key=lambda x: x[1])

    result = []
    for i in range(count):
        # Pick from least-represented categories with a small random jitter
        # to avoid always picking the exact same order
        pool_size = min(3, len(sorted_cats))
        pool = sorted_cats[:pool_size]
        chosen_cat, chosen_count = random.choice(pool)
        result.append(chosen_cat)
        # Update count so the next pick accounts for this one
        sorted_cats = [(c, cnt + (1 if c == chosen_cat else 0)) for c, cnt in sorted_cats]
        sorted_cats.sort(key=lambda x: x[1])

    return result


# =============================================================================
# CONTENT TYPE ASSIGNMENT
# =============================================================================

def assign_content_types(count: int) -> list[str]:
    """
    Determine the content type for each article in the batch.
    Returns a list of "news" or "settlement" strings.

    When TOPIC_URL or TOPIC_IDEA is set, all articles are settlements.
    """
    if TOPIC_URL or TOPIC_IDEA:
        return ["settlement"] * count

    if CONTENT_TYPE == "news":
        return ["news"] * count
    elif CONTENT_TYPE == "settlement":
        return ["settlement"] * count
    else:
        # "mixed" — roughly half and half with slight randomization
        types = []
        # Start with an even split
        n_settlements = count // 2
        n_news = count - n_settlements
        # Randomly swap one if count >= 2 to avoid always being exactly 50/50
        if count >= 2 and random.random() < 0.3:
            if random.random() < 0.5:
                n_settlements = max(0, n_settlements - 1)
                n_news = count - n_settlements
            else:
                n_settlements = min(count, n_settlements + 1)
                n_news = count - n_settlements
        types = ["settlement"] * n_settlements + ["news"] * n_news
        random.shuffle(types)
        return types


def _category_guidance(category: str) -> str:
    """Return a short category scope description for use in prompts."""
    desc = CATEGORY_DESCRIPTIONS.get(category)
    if desc:
        return f'\n\nIMPORTANT — "{category}" category scope: {desc}\nOnly include cases that clearly fit this scope.\n'
    return ""


# =============================================================================
# PERPLEXITY RESEARCH
# =============================================================================

def ask_perplexity(messages: list, max_tokens: int = 1024) -> str:
    """Send a message list to Perplexity and return the text response."""
    payload = {
        "model": PERPLEXITY_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    resp = requests.post(
        "https://api.perplexity.ai/chat/completions",
        headers=PERPLEXITY_HEADERS,
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _build_avoid_section(avoidance_data: dict) -> str:
    """Build avoidance prompt section from structured avoidance data."""
    if not avoidance_data:
        return ""

    parts = []

    # Part 1: Company names (most effective — short, easy for Perplexity to match)
    companies = avoidance_data.get("companies", set())
    if companies:
        companies_sample = sorted(companies)[:50]
        parts.append(
            "Do NOT cover lawsuits involving these companies (already on our site):\n"
            + ", ".join(companies_sample)
        )

    # Part 2: Specific titles (capped to prevent prompt bloat)
    titles = avoidance_data.get("titles", [])
    if titles:
        titles_sample = titles[:40]
        parts.append(
            "Do NOT research any of these specific cases:\n"
            + "\n".join(f"- {t}" for t in titles_sample)
        )

    if not parts:
        return ""

    return (
        "\n\nIMPORTANT — AVOID DUPLICATES:\n"
        + "\n\n".join(parts)
        + "\n\nFind a DIFFERENT lawsuit not listed above.\n"
    )


def research_topic(category: str, avoidance_data: dict = None, topic_hint: str = None) -> str:
    """Use Perplexity to research real lawsuits in a category. Returns structured research.

    If topic_hint is provided, does focused research on that specific case instead of
    broad category research. Used when a pre-vetted candidate is selected from the backlog.
    """
    avoid_section = _build_avoid_section(avoidance_data)

    cat_guidance = _category_guidance(category)

    if topic_hint:
        # Focused research on a specific pre-vetted topic
        user_content = (
            f"Research this specific class action lawsuit in detail:\n\n"
            f"{topic_hint}\n\n"
            f"Provide comprehensive details: court name, parties involved, "
            f"specific allegations, timeline, current status, and consumer impact.\n\n"
            f"Include source URLs.\n"
            f"Limit response to 300-500 words. Use bullet points."
            + cat_guidance
        )
    else:
        # Original broad research (fallback when no candidates)
        user_content = (
            f'Research real, current class action lawsuits or mass tort litigation related to "{category}".\n\n'
            "Return structured information including:\n"
            "- Specific lawsuit names and case numbers\n"
            "- Defendants/companies involved\n"
            "- Settlement amounts (if known)\n"
            "- MDL numbers (if applicable)\n"
            "- Current litigation status\n"
            "- Recent developments (last 24 months)\n"
            "- Source URLs\n\n"
            "Focus on lawsuits that are active or recently settled.\n"
            "Limit response to 300-500 words. Use bullet points."
            + cat_guidance
            + avoid_section
        )

    messages = [
        {
            "role": "system",
            "content": (
                "You are a legal research assistant. Search the web for real, current information "
                "about class action lawsuits. Return only verified facts with sources."
            ),
        },
        {"role": "user", "content": user_content},
    ]
    return ask_perplexity(messages, max_tokens=1024)


def research_settlement(category: str, topic_url: str = "", topic_idea: str = "", avoidance_data: dict = None) -> str:
    """Use Perplexity to research real settlements. Returns structured research."""
    avoid_section = ""
    if avoidance_data and not topic_url and not topic_idea:
        avoid_section = _build_avoid_section(avoidance_data)

    cat_guidance = _category_guidance(category)

    if topic_url:
        user_content = (
            f"Research the class action settlement found at this URL: {topic_url}\n\n"
            "Return structured information including:\n"
            "- Official case name\n"
            "- Settlement amount\n"
            "- Claim deadline\n"
            "- Who qualifies\n"
            "- How to file a claim (claim URL if available)\n"
            "- Claims administrator\n"
            "- Current status\n"
            "- Source URLs\n\n"
            "Limit response to 300-500 words. Use bullet points."
        )
    elif topic_idea:
        user_content = (
            f'Research this specific class action settlement or lawsuit: "{topic_idea}"\n\n'
            "Return structured information including:\n"
            "- Official case name and case number\n"
            "- Defendants/companies involved\n"
            "- Settlement amount (if known)\n"
            "- Claim deadline\n"
            "- Who qualifies\n"
            "- How to file a claim (claim URL if available)\n"
            "- Current litigation status\n"
            "- Source URLs\n\n"
            "Limit response to 300-500 words. Use bullet points."
            + cat_guidance
        )
    else:
        user_content = (
            f'Research real, current class action settlements related to "{category}" '
            "where consumers can still file claims.\n\n"
            "Return structured information including:\n"
            "- Specific settlement/case names\n"
            "- Defendants/companies involved\n"
            "- Settlement amounts\n"
            "- Claim deadlines (must be in the future)\n"
            "- Who qualifies to claim\n"
            "- Claim URLs or settlement websites\n"
            "- Claims administrator\n"
            "- Source URLs\n\n"
            "Focus on settlements where the claim deadline has NOT passed.\n"
            "Limit response to 300-500 words. Use bullet points."
            + cat_guidance
            + avoid_section
        )

    messages = [
        {
            "role": "system",
            "content": (
                "You are a legal research assistant specializing in class action settlements. "
                "Search the web for real, current settlement information. "
                "Return only verified facts with sources."
            ),
        },
        {"role": "user", "content": user_content},
    ]
    return ask_perplexity(messages, max_tokens=1024)


# =============================================================================
# TOPIC DISCOVERY & CASE BACKLOG
# =============================================================================

def _parse_date(date_str: str) -> str | None:
    """Try to parse a date string into ISO format (YYYY-MM-DD)."""
    if not date_str or date_str.strip().lower() in ("n/a", "unknown", ""):
        return None
    date_str = date_str.strip()
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%d %B %Y"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _parse_candidates(response: str) -> list[dict]:
    """Parse Perplexity's pipe-separated response into candidate dicts."""
    candidates = []
    for line in response.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('---'):
            continue
        # Strip leading numbers (1., 2., etc.)
        line = re.sub(r'^\d+[\.\)]\s*', '', line)
        parts = [p.strip() for p in line.split('|')]
        if len(parts) < 2:
            continue

        candidate = {
            "case_title": parts[0] if len(parts) > 0 else "",
            "defendant": parts[1] if len(parts) > 1 else None,
            "court": parts[2] if len(parts) > 2 else None,
            "filing_date": _parse_date(parts[3]) if len(parts) > 3 else None,
            "docket_number": parts[4] if len(parts) > 4 and parts[4].lower() not in ("n/a", "unknown", "") else None,
            "source_url": parts[5] if len(parts) > 5 and parts[5].startswith("http") else None,
            "research_summary": line,
        }
        if candidate["case_title"] and len(candidate["case_title"]) > 5:
            candidates.append(candidate)
    return candidates


def _build_discovery_avoid_section(site_db, site_id: str, category: str) -> str:
    """Build a compact avoidance section for discovery prompts from existing articles + candidates."""
    try:
        # Get recent articles in this category
        articles = site_db.table("articles") \
            .select("title, case_name") \
            .eq("site_id", site_id) \
            .eq("category", category) \
            .neq("content_stage", "failed") \
            .order("created_at", desc=True) \
            .limit(60) \
            .execute().data or []

        # Get recent candidates in this category
        candidates = site_db.table("case_candidates") \
            .select("case_title, defendant") \
            .eq("site_id", site_id) \
            .eq("category", category) \
            .order("discovered_at", desc=True) \
            .limit(60) \
            .execute().data or []

        # Build compact company + case list
        known_cases = set()
        known_companies = set()
        for a in articles:
            if a.get("title"):
                known_cases.add(a["title"])
            if a.get("case_name"):
                known_cases.add(a["case_name"])
                company = extract_company_from_case_name(a["case_name"])
                if company:
                    known_companies.add(company)
        for c in candidates:
            if c.get("case_title"):
                known_cases.add(c["case_title"])
            if c.get("defendant"):
                known_companies.add(c["defendant"])

        if not known_cases and not known_companies:
            return ""

        parts = []
        if known_companies:
            parts.append("Companies already covered: " + ", ".join(sorted(known_companies)[:40]))
        if known_cases:
            parts.append("Cases already covered:\n" + "\n".join(f"- {c}" for c in sorted(known_cases)[:30]))

        return (
            "\n\nIMPORTANT — DO NOT include any of these known cases or companies:\n"
            + "\n".join(parts)
            + "\n\nFind DIFFERENT, LESSER-KNOWN cases not listed above.\n"
        )
    except Exception as e:
        print(f"    ⚠️ Could not build discovery avoidance: {e}")
        return ""


# Different query angles to rotate through for broader discovery
_DISCOVERY_ANGLES = {
    "settlement": [
        # Angle 0: Standard (original)
        (
            "List 50 recent class action SETTLEMENTS in the '{category}' category "
            "where consumers can still file claims. For each, provide on a single line:\n"
            "Case Name | Defendant | Court | Filing/Settlement Date | Docket Number (if known) | Source URL\n\n"
            "Use the exact format above with pipe separators. "
            "Focus on settlements filed within the past 24 months."
        ),
        # Angle 1: Lesser-known / smaller settlements
        (
            "List 50 LESSER-KNOWN class action settlements in the '{category}' category "
            "where consumers can still file claims. Focus on cases that received LESS media attention — "
            "smaller settlement amounts, regional cases, or cases in less prominent courts. "
            "Avoid major headline settlements. For each, provide on a single line:\n"
            "Case Name | Defendant | Court | Filing/Settlement Date | Docket Number (if known) | Source URL\n\n"
            "Use the exact format above with pipe separators. "
            "Search across federal AND state courts from the past 36 months."
        ),
        # Angle 2: State courts and regional
        (
            "List 50 class action settlements from STATE COURTS (not federal) in the '{category}' category "
            "where consumers can still file claims. Include cases from California, New York, Illinois, "
            "Texas, Florida, Pennsylvania, and other state courts. For each, provide on a single line:\n"
            "Case Name | Defendant | Court | Filing/Settlement Date | Docket Number (if known) | Source URL\n\n"
            "Use the exact format above with pipe separators. "
            "Focus on settlements from the past 24 months."
        ),
        # Angle 3: Newly filed / preliminary approval
        (
            "List 50 class action settlements in the '{category}' category that received "
            "PRELIMINARY APPROVAL or FINAL APPROVAL in the past 6 months. Include cases where "
            "the claims process just opened. For each, provide on a single line:\n"
            "Case Name | Defendant | Court | Approval Date | Docket Number (if known) | Source URL\n\n"
            "Use the exact format above with pipe separators."
        ),
    ],
    "news": [
        # Angle 0: Standard (original)
        (
            "List 50 recent class action LAWSUITS in the '{category}' category. "
            "For each, provide on a single line:\n"
            "Case Name | Defendant | Court | Filing Date | Docket Number (if known) | Source URL\n\n"
            "Use the exact format above with pipe separators. "
            "Focus on lawsuits filed within the past 24 months with significant consumer impact."
        ),
        # Angle 1: Very recently filed
        (
            "List 50 class action lawsuits in the '{category}' category that were "
            "FILED IN THE LAST 3 MONTHS. Include newly certified classes, new MDL consolidations, "
            "and recently amended complaints. For each, provide on a single line:\n"
            "Case Name | Defendant | Court | Filing Date | Docket Number (if known) | Source URL\n\n"
            "Use the exact format above with pipe separators."
        ),
        # Angle 2: Lesser-known / regional
        (
            "List 50 LESSER-KNOWN class action lawsuits in the '{category}' category. "
            "Focus on cases that received less media attention — smaller companies, regional cases, "
            "state court filings, or cases in less prominent jurisdictions. "
            "Avoid major headline cases. For each, provide on a single line:\n"
            "Case Name | Defendant | Court | Filing Date | Docket Number (if known) | Source URL\n\n"
            "Use the exact format above with pipe separators. "
            "Search across the past 36 months."
        ),
        # Angle 3: Specific legal theories
        (
            "List 50 class action lawsuits in the '{category}' category focusing on "
            "EMERGING LEGAL THEORIES: BIPA violations, CCPA/state privacy laws, PFAS contamination, "
            "greenwashing, subscription traps, dark patterns, algorithmic discrimination, or "
            "gig economy misclassification. For each, provide on a single line:\n"
            "Case Name | Defendant | Court | Filing Date | Docket Number (if known) | Source URL\n\n"
            "Use the exact format above with pipe separators. "
            "Include cases from the past 24 months."
        ),
    ],
}


def _build_global_dedup_pool(site_db, site_id: str) -> list[dict]:
    """Build global hard dedup pool from all articles + candidates."""
    global_candidates = site_db.table("case_candidates") \
        .select("case_title, defendant, court, filing_date, docket_number, category") \
        .eq("site_id", site_id) \
        .execute().data or []

    global_articles = site_db.table("articles") \
        .select("title, case_name, category") \
        .eq("site_id", site_id) \
        .neq("content_stage", "failed") \
        .execute().data or []

    hard_dedup_pool = []
    for a in global_articles:
        hard_dedup_pool.append({
            "case_title": a.get("case_name") or a.get("title", ""),
            "defendant": None,
            "court": None,
            "filing_date": None,
            "docket_number": None,
        })
    hard_dedup_pool.extend(global_candidates)
    return hard_dedup_pool


def _store_candidates(candidates: list[dict], hard_dedup_pool: list[dict],
                      site_db, site_id: str, category: str, content_type: str) -> int:
    """Dedup candidates against global pool and store new ones. Returns count stored."""
    new_count = 0
    for candidate in candidates:
        if is_case_duplicate(candidate, hard_dedup_pool):
            print(f"    ↳ Skipping (global case duplicate): {candidate['case_title'][:80]}")
            continue

        try:
            site_db.table("case_candidates").insert({
                "site_id": site_id,
                "case_title": candidate["case_title"],
                "defendant": candidate.get("defendant"),
                "court": candidate.get("court"),
                "filing_date": candidate.get("filing_date"),
                "docket_number": candidate.get("docket_number"),
                "source_url": candidate.get("source_url"),
                "category": category,
                "content_type": content_type,
                "research_summary": candidate.get("research_summary"),
            }).execute()
            new_count += 1
            hard_dedup_pool.append(candidate)
        except Exception as e:
            print(f"    ↳ Insert failed (likely DB uniqueness constraint): {e}")

    return new_count


def discover_and_store_topics(
    category: str,
    content_type: str,
    site_db,
    site_id: str,
    angle: int = 0,
) -> int:
    """
    Ask Perplexity for ~50 topics, dedup against known cases globally,
    store new ones in case_candidates. Returns count of new candidates stored.

    Uses different query angles (0-3) to find diverse cases. If angle 0 returns 0
    new candidates, callers should retry with higher angles.
    """
    # Backlog size protection — per category/content_type cap
    backlog = site_db.table("case_candidates") \
        .select("id", count="exact") \
        .eq("site_id", site_id) \
        .eq("processed", False) \
        .eq("category", category) \
        .eq("content_type", content_type) \
        .execute()
    if backlog.count and backlog.count > 100:
        print(
            f"  ⏭️  Backlog already large for {content_type}/{category} "
            f"({backlog.count} unprocessed) — skipping discovery"
        )
        return 0

    # Build avoidance section from existing articles/candidates
    avoid_section = _build_discovery_avoid_section(site_db, site_id, category)

    # Select query angle (rotate through available angles)
    angles = _DISCOVERY_ANGLES.get(content_type, _DISCOVERY_ANGLES["news"])
    angle_idx = angle % len(angles)
    user_content = angles[angle_idx].format(category=category) + _category_guidance(category) + avoid_section

    _ANGLE_NAMES = ['standard', 'lesser-known', 'state-courts', 'recent-approvals/emerging']
    if angle_idx > 0:
        angle_label = _ANGLE_NAMES[angle_idx] if angle_idx < len(_ANGLE_NAMES) else f"angle-{angle_idx}"
        print(f"    🔀 Using discovery angle {angle_idx}: {angle_label}")

    messages = [
        {"role": "system", "content": "You are a legal research assistant. List real cases with structured metadata. Prioritize cases NOT commonly covered by major class action news sites."},
        {"role": "user", "content": user_content},
    ]
    response = ask_perplexity(messages, max_tokens=2048)

    # Parse response into candidate records
    candidates = _parse_candidates(response)

    # Global case-identity dedup pool (all categories)
    hard_dedup_pool = _build_global_dedup_pool(site_db, site_id)

    # Dedup and store
    new_count = _store_candidates(candidates, hard_dedup_pool, site_db, site_id, category, content_type)

    print(f"  📋 Discovered {len(candidates)} cases, stored {new_count} new candidates")
    return new_count


def _handle_candidate_failure(site_db, candidate_id: str, candidate: dict):
    """Handle a failed candidate: retry up to 3 times, then permanently fail."""
    current_retry = candidate.get("retry_count", 0)
    try:
        if current_retry < 3:
            site_db.table("case_candidates") \
                .update({"status": "discovered", "retry_count": current_retry + 1}) \
                .eq("id", candidate_id) \
                .execute()
            print(f"  🔄 Candidate retry {current_retry + 1}/3 — returning to queue")
        else:
            site_db.table("case_candidates") \
                .update({"status": "failed"}) \
                .eq("id", candidate_id) \
                .execute()
            print(f"  ❌ Candidate failed after 3 retries — permanently marked failed")
    except Exception as e:
        print(f"  ⚠️ Failed to update candidate {candidate_id} status: {e}")


def discover_case_updates(
    category: str,
    content_type: str,
    site_db,
    site_id: str,
) -> dict | None:
    """
    Find an existing case with genuinely new developments worth an update article.
    Returns a dict with case info + update context, or None if nothing found.

    This is the last-resort fallback when no new cases can be found.
    """
    # Get published articles in this category, ordered by oldest updated first
    try:
        articles = site_db.table("articles") \
            .select("id, title, case_name, case_status, settlement_amount, claim_deadline, created_at") \
            .eq("site_id", site_id) \
            .eq("content_stage", "published") \
            .eq("category", category) \
            .order("created_at", desc=False) \
            .limit(20) \
            .execute().data or []
    except Exception as e:
        print(f"  ⚠️ Could not load articles for update check: {e}")
        return None

    if not articles:
        return None

    # Ask Perplexity which of these cases have NEW developments
    case_list = "\n".join(
        f"- {a['case_name'] or a['title']}" + (f" (status: {a.get('case_status', 'unknown')})" if a.get('case_status') else "")
        for a in articles[:15]
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are a legal research assistant. Check for NEW developments "
                "in existing class action cases. Only report cases with genuinely "
                "new, significant developments — not just minor procedural updates."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Check which of these class action cases have had SIGNIFICANT NEW DEVELOPMENTS "
                f"in the past 30 days (new rulings, settlement approvals, deadline extensions, "
                f"appeals, new parties, class certification, amended complaints, etc.):\n\n"
                f"{case_list}\n\n"
                f"For each case with new developments, provide:\n"
                f"Case Name | Type of Update | Summary of New Development | Source URL\n\n"
                f"Use pipe separators. ONLY include cases with genuinely new, significant news. "
                f"If none have new developments, respond with 'NO_UPDATES_FOUND'."
            ),
        },
    ]

    try:
        response = ask_perplexity(messages, max_tokens=1024)
    except Exception as e:
        print(f"  ⚠️ Update discovery Perplexity call failed: {e}")
        return None

    if "NO_UPDATES_FOUND" in response:
        print(f"  ℹ️ No case updates found for {category}")
        return None

    # Parse updates — find first valid one
    for line in response.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('---'):
            continue
        line = re.sub(r'^\d+[\.\)]\s*', '', line)
        parts = [p.strip() for p in line.split('|')]
        if len(parts) < 3:
            continue

        update_case_name = parts[0]
        update_type = parts[1] if len(parts) > 1 else "development"
        update_summary = parts[2] if len(parts) > 2 else ""
        source_url = parts[3] if len(parts) > 3 and parts[3].startswith("http") else None

        # Match to an existing article
        matched_article = None
        for a in articles:
            existing_name = (a.get("case_name") or a.get("title", "")).lower()
            if not existing_name:
                continue
            # Simple substring match or high similarity
            if update_case_name.lower() in existing_name or existing_name in update_case_name.lower():
                matched_article = a
                break
            ratio = SequenceMatcher(None, update_case_name.lower(), existing_name).ratio()
            if ratio > 0.6:
                matched_article = a
                break

        if matched_article:
            print(f"  📰 Found case update: {update_case_name[:60]} — {update_type}")
            return {
                "original_article": matched_article,
                "update_case_name": update_case_name,
                "update_type": update_type,
                "update_summary": update_summary,
                "source_url": source_url,
            }

    print(f"  ℹ️ Update responses didn't match any existing articles")
    return None


def research_case_update(update_info: dict, category: str) -> str:
    """Research a specific case update in depth for an update article."""
    case_name = update_info.get("update_case_name", "Unknown")
    update_type = update_info.get("update_type", "development")
    update_summary = update_info.get("update_summary", "")

    messages = [
        {
            "role": "system",
            "content": (
                "You are a legal research assistant. Research the latest developments "
                "in this class action case. Focus on what's NEW — not rehashing the "
                "original case details. Include source URLs."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Research the latest developments in this class action case:\n\n"
                f"Case: {case_name}\n"
                f"Type of update: {update_type}\n"
                f"Summary: {update_summary}\n\n"
                f"Provide detailed information about:\n"
                f"- What specifically changed or was decided\n"
                f"- When this happened (exact dates)\n"
                f"- How this affects class members or consumers\n"
                f"- What comes next in the case\n"
                f"- Any new deadlines or action items for consumers\n"
                f"- Source URLs for the new developments\n\n"
                f"Focus on the NEW developments, not the original case background.\n"
                f"Limit response to 300-500 words. Use bullet points."
            ),
        },
    ]
    return ask_perplexity(messages, max_tokens=1024)


# =============================================================================
# CLAUDE API (NO WEB SEARCH — uses Perplexity research context)
# =============================================================================

def generate_article(client: anthropic.Anthropic, system_prompt: str, article_prompt: str) -> dict:
    max_retries = 3
    for attempt in range(max_retries + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=16384,
                system=system_prompt,
                messages=[{"role": "user", "content": article_prompt}],
            )
            break
        except anthropic.APIStatusError as e:
            if e.status_code == 529 and attempt < max_retries:
                wait = (2 ** attempt) * 10
                print(f"  ⏳ API overloaded ({e.status_code}). Retrying in {wait}s (attempt {attempt + 1}/{max_retries})...")
                time.sleep(wait)
            elif e.status_code == 429 and attempt < max_retries:
                wait = (2 ** attempt) * 15
                print(f"  ⏳ Rate limited (429). Retrying in {wait}s (attempt {attempt + 1}/{max_retries})...")
                time.sleep(wait)
            else:
                raise

    # Check for truncated response
    if response.stop_reason == "max_tokens":
        raise ValueError(
            f"Claude response was truncated (hit max_tokens limit). "
            f"Output tokens used: {response.usage.output_tokens}"
        )

    raw_text = ""
    for block in response.content:
        if block.type == "text":
            raw_text += block.text

    json_text = raw_text.strip()

    # If response has no JSON at all, Claude likely couldn't produce a valid article
    if '{' not in json_text:
        raise ValueError(
            f"Claude did not return JSON — likely no valid content found. "
            f"Response preview: {raw_text[:300]}"
        )

    # Extract JSON from response - Claude may include explanatory text before/after
    start_idx = json_text.find('{')
    end_idx = json_text.rfind('}')

    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        json_text = json_text[start_idx:end_idx + 1]

    # Strip markdown code fences if present
    if "```json" in json_text:
        json_text = json_text.split("```json")[-1]
    if "```" in json_text:
        json_text = json_text.split("```")[0]

    json_text = json_text.strip()

    try:
        article_data = json.loads(json_text)
    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to parse Claude response as JSON: {e}")
        print(f"Raw response:\n{raw_text[:1000]}")
        raise

    return {
        "article": article_data,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }


# =============================================================================
# DATABASE WRITES
# =============================================================================

def write_site_article(supabase_client, article_id: str, article_data: dict, category: str, site_id: str, content_type: str):
    """Write an article to the site DB. Handles both news and settlement types."""
    base_slug = article_data.get("slug") or slugify(article_data.get("title", "untitled"))
    unique_slug = f"{base_slug}-{article_id[:8]}" if not base_slug.endswith(article_id[:8]) else base_slug

    row = {
        "id": article_id,
        "site_id": site_id,
        "title": article_data.get("title", "Untitled"),
        "slug": unique_slug,
        "content": article_data.get("content", ""),
        "meta_description": article_data.get("meta_description", ""),
        "category": article_data.get("category", category),
        "content_stage": "draft",
        "published_at": datetime.now(timezone.utc).isoformat(),
    }

    if content_type == "settlement":
        row["news_type"] = "settlement"

        # Handle proof_required — DB expects boolean, Claude may return string
        proof_required_raw = article_data.get("proof_required")
        if isinstance(proof_required_raw, bool):
            row["proof_required"] = proof_required_raw
        elif isinstance(proof_required_raw, str):
            row["proof_required"] = "no proof" not in proof_required_raw.lower()

        # Settlement metadata fields
        row["case_name"] = article_data.get("case_name")
        row["case_status"] = article_data.get("case_status", "filed")
        row["settlement_amount"] = article_data.get("settlement_amount")
        row["claim_deadline"] = article_data.get("claim_deadline")
        row["claim_url"] = article_data.get("claim_url")
        row["settlement_website"] = article_data.get("settlement_website")
        row["claims_administrator"] = article_data.get("claims_administrator")
        row["class_counsel"] = article_data.get("class_counsel")
        row["potential_reward"] = article_data.get("potential_reward")
        row["location"] = article_data.get("location")
    else:
        # News articles
        row["news_type"] = article_data.get("news_type", "analysis")

    # Strip None values to avoid sending nulls for optional fields
    row = {k: v for k, v in row.items() if v is not None}

    result = supabase_client.table("articles").insert(row).execute()
    return result


def write_admin_run_article(admin_client, run_article: dict):
    admin_client.table("run_articles").insert(run_article).execute()


def update_admin_generation_run(admin_client, run_id: str, stats: dict):
    admin_client.table("generation_runs").update(stats).eq("id", run_id).execute()


def log_admin_error(admin_client, site_id: str, message: str, details: dict = None):
    if not admin_client:
        return
    try:
        admin_client.table("error_logs").insert({
            "site_id": site_id,
            "source": "generate_articles",
            "severity": "error",
            "message": message,
            "details": details or {},
        }).execute()
    except Exception as e:
        print(f"WARNING: Failed to log error to admin DB: {e}")


# =============================================================================
# COST ESTIMATION
# =============================================================================

PRICING = {
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-5-20250929": {"input": 3.00, "output": 15.00},
}
BATCH_DISCOUNT = 0.50


def estimate_cost(model: str, input_tokens: int, output_tokens: int, is_batch: bool = False) -> float:
    pricing = PRICING.get(model, {"input": 3.00, "output": 15.00})
    cost = (input_tokens / 1_000_000 * pricing["input"]) + (output_tokens / 1_000_000 * pricing["output"])
    if is_batch:
        cost *= BATCH_DISCOUNT
    return round(cost, 4)


# =============================================================================
# SITE ID LOOKUP
# =============================================================================

def get_site_id(admin_client) -> str | None:
    if not admin_client or not ADMIN_RUN_ID:
        return None
    try:
        result = admin_client.table("generation_runs") \
            .update({"workflow_status": "in_progress"}) \
            .eq("id", ADMIN_RUN_ID) \
            .execute()
        if result.data and len(result.data) > 0:
            return str(result.data[0].get("site_id", ""))
        return None
    except Exception as e:
        print(f"WARNING: Could not get site_id from admin DB: {e}")
        return None


# =============================================================================
# MAIN
# =============================================================================

def main():
    start_time = time.time()
    print(f"=" * 60)
    print(f"Article Generation — {datetime.now(timezone.utc).isoformat()}")
    print(f"Model: {MODEL}")
    print(f"Articles: {ARTICLES_COUNT}")
    print(f"Content Type: {CONTENT_TYPE}")
    print(f"Mode: {GENERATION_MODE}")
    print(f"Prompt Version: {PROMPT_VERSION}")
    print(f"Research: Perplexity ({PERPLEXITY_MODEL})")
    if TOPIC_URL:
        print(f"Topic URL: {TOPIC_URL}")
    if TOPIC_IDEA:
        print(f"Topic Idea: {TOPIC_IDEA[:80]}...")
    print(f"Admin Run ID: {ADMIN_RUN_ID or '(none — standalone run)'}")
    print(f"=" * 60)

    # Load all prompt pairs
    news_system_prompt = load_prompt("system_prompt.txt")
    news_article_prompt_template = load_prompt("article_prompt.txt")
    settlement_system_prompt = load_prompt("settlement_system_prompt.txt")
    settlement_article_prompt_template = load_prompt("settlement_article_prompt.txt")

    print(f"News system prompt hash:       {sha256_short(news_system_prompt)}")
    print(f"News article prompt hash:      {sha256_short(news_article_prompt_template)}")
    print(f"Settlement system prompt hash:  {sha256_short(settlement_system_prompt)}")
    print(f"Settlement article prompt hash: {sha256_short(settlement_article_prompt_template)}")

    # Init clients
    claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    site_db = create_client(SUPABASE_URL, SUPABASE_KEY)
    admin_conn = get_admin_db()

    if GENERATION_MODE == "batch":
        print("WARNING: Batch mode (Anthropic Batch API) is not yet implemented.")
        print("         Falling back to standard mode for this run.")

    site_id = get_site_id(admin_conn)
    if admin_conn and ADMIN_RUN_ID and not site_id:
        print("WARNING: Could not resolve site_id. Admin writes will be skipped for run_articles.")

    # Get site_id from site DB
    site_db_site_id = None
    try:
        sites_result = site_db.table("sites").select("id").limit(1).execute()
        if sites_result.data and len(sites_result.data) > 0:
            site_db_site_id = sites_result.data[0]["id"]
            print(f"Site DB site_id: {site_db_site_id}")
    except Exception as e:
        print(f"WARNING: Could not look up site_id from site DB: {e}")

    # Assign content types and categories
    content_types = assign_content_types(ARTICLES_COUNT)
    categories = pick_categories_balanced(site_db, ARTICLES_COUNT)

    print(f"Content plan: {list(zip(content_types, categories))}")

    # Deduplication: fetch existing articles globally for dedup checks
    existing_articles = load_existing_articles(site_db)
    avoidance_data = build_avoidance_data(existing_articles)
    print(f"Dedup: {len(avoidance_data['titles'])} titles/cases, {len(avoidance_data['companies'])} companies loaded")

    def _category_avoidance(existing: list[dict], category: str) -> dict:
        """Build category-scoped avoidance for discovery prompts (last 50 in category)."""
        # Filter to same-category articles for prompt relevance
        cat_articles = [a for a in existing if a.get("category") == category][:50]
        return build_avoidance_data(cat_articles, category=category)

    # --- DISCOVERY PHASE ---
    # Discover new case candidates and store in backlog (per category/content_type)
    # Retries with different query angles and rotates to alternate categories if saturated
    if not TOPIC_URL and not TOPIC_IDEA and site_db_site_id:
        topic_groups = defaultdict(int)
        for idx in range(ARTICLES_COUNT):
            ct = content_types[idx]
            cat = categories[idx]
            topic_groups[(ct, cat)] += 1

        total_discovered = 0
        saturated_categories = set()  # Track categories where all angles returned 0

        for (ct, cat), needed in topic_groups.items():
            print(f"\n🔍 Discovering topics for {ct}/{cat} (need {needed})...")
            try:
                # Try up to 3 different query angles if standard discovery finds nothing
                discovered = 0
                for angle in range(4):
                    discovered = discover_and_store_topics(cat, ct, site_db, site_db_site_id, angle=angle)
                    if discovered > 0:
                        break
                    if angle < 3:
                        print(f"  🔄 Angle {angle} found 0 new — trying next angle...")

                total_discovered += discovered

                if discovered == 0:
                    saturated_categories.add((ct, cat))
                    print(f"  ⚠️ Category {ct}/{cat} appears saturated (0 new from all angles)")
            except Exception as e:
                print(f"  ⚠️ Discovery failed for {ct}/{cat}: {e}")
                traceback.print_exc()

        # --- CATEGORY ROTATION for saturated slots ---
        # If any category/content_type returned 0, try to swap to an alternate category
        if saturated_categories:
            print(f"\n🔄 Attempting category rotation for {len(saturated_categories)} saturated slot(s)...")
            all_cats = DEFAULT_CATEGORIES.copy()
            assigned_cats = {cat for _, cat in topic_groups.keys()}

            for (ct, sat_cat) in saturated_categories:
                # Find alternate categories not already assigned
                alternates = [c for c in all_cats if c not in assigned_cats and c != sat_cat]
                random.shuffle(alternates)

                rotated = False
                for alt_cat in alternates:
                    print(f"  🔄 Trying alternate category: {ct}/{alt_cat}...")
                    try:
                        discovered = discover_and_store_topics(alt_cat, ct, site_db, site_db_site_id, angle=0)
                        if discovered > 0:
                            # Swap the category in the content plan
                            for idx in range(ARTICLES_COUNT):
                                if content_types[idx] == ct and categories[idx] == sat_cat:
                                    categories[idx] = alt_cat
                                    print(f"  ✓ Rotated slot {idx} from {sat_cat} → {alt_cat} ({discovered} new candidates)")
                                    assigned_cats.add(alt_cat)
                                    total_discovered += discovered
                                    rotated = True
                                    break
                        if rotated:
                            break
                    except Exception as e:
                        print(f"  ⚠️ Alternate discovery failed for {ct}/{alt_cat}: {e}")
                        continue

                if not rotated:
                    print(f"  ⚠️ No alternate category had new candidates for {ct} — will try update articles")

        print(f"\n📊 Discovery complete: {total_discovered} new candidates stored")
        print(f"  Updated content plan: {list(zip(content_types, categories))}")
    else:
        print("\n⏭️ Skipping discovery (guided topic or no site_id)")

    total_input_tokens = 0
    total_output_tokens = 0
    articles_generated = 0
    articles_published = 0
    articles_failed = 0
    errors = []

    for i in range(ARTICLES_COUNT):
        article_id = str(uuid.uuid4())
        category = categories[i]
        article_content_type = content_types[i]
        type_label = "Settlement" if article_content_type == "settlement" else "News"

        # Select appropriate prompts
        if article_content_type == "settlement":
            system_prompt = settlement_system_prompt
            article_prompt_template = settlement_article_prompt_template
        else:
            system_prompt = news_system_prompt
            article_prompt_template = news_article_prompt_template

        system_prompt_hash = sha256_short(system_prompt)

        print(f"\n── {type_label} {i + 1}/{ARTICLES_COUNT} ──")
        print(f"  UUID:     {article_id}")
        print(f"  Type:     {type_label}")
        print(f"  Category: {category}")
        print(f"  🔍 Researching via Perplexity...")

        try:
            # Outer retry loop: if post-generation dedup catches a duplicate,
            # retry the entire research→generate cycle with stronger avoidance
            max_article_attempts = 3
            article_written = False
            candidate_id = None  # Track if we're using a backlog candidate
            tried_candidate_ids = set()  # Track candidates tried this article slot

            for attempt in range(max_article_attempts):
                if attempt > 0:
                    print(f"  ↻ Retrying article generation (attempt {attempt + 1}/{max_article_attempts})...")

                # Build category-scoped avoidance for prompts (last 50 in same category)
                cat_avoidance = _category_avoidance(existing_articles, category)
                # Category-scoped articles for research overlap checks (prevents
                # cross-category false positives like AT&T stocks vs AT&T wage case)
                cat_existing_articles = [a for a in existing_articles if a.get("category") == category]

                # Step 1: Try to select a pre-vetted candidate from the backlog
                topic_hint = None
                candidate_id = None
                candidate = None

                is_update_article = False
                update_info = None

                if not TOPIC_URL and not TOPIC_IDEA and site_db_site_id:
                    query = site_db.table("case_candidates") \
                        .select("*") \
                        .eq("site_id", site_db_site_id) \
                        .eq("processed", False) \
                        .eq("status", "discovered") \
                        .eq("category", category) \
                        .eq("content_type", article_content_type)
                    # Skip candidates already tried this article slot
                    for skip_id in tried_candidate_ids:
                        query = query.neq("id", skip_id)
                    result = query \
                        .order("discovered_at", desc=False) \
                        .limit(1) \
                        .execute()

                    if result.data:
                        candidate = result.data[0]
                        candidate_id = candidate["id"]
                        tried_candidate_ids.add(candidate_id)
                        # Safely claim candidate — verify it was actually claimed
                        claim_result = site_db.table("case_candidates") \
                            .update({"status": "processing"}) \
                            .eq("id", candidate_id) \
                            .eq("status", "discovered") \
                            .execute()
                        if claim_result.data:
                            topic_hint = candidate["case_title"]
                            print(f"  📌 Using backlog candidate: {topic_hint[:80]}...")
                        else:
                            print(f"  ⚠️ Candidate {candidate_id} could not be claimed — falling back to direct research")
                            candidate_id = None
                    else:
                        print(f"  ⚠️ No backlog candidates for {article_content_type}/{category}")
                        # Last resort: try finding an update article for an existing case.
                        # Only on retries (attempt >= 1) to prefer new cases on first try.
                        if attempt >= 1:
                            print(f"  🔍 Attempting to find case update article...")
                            update_info = discover_case_updates(
                                category, article_content_type, site_db, site_db_site_id
                            )
                            if update_info:
                                is_update_article = True
                                topic_hint = f"UPDATE: {update_info['update_case_name']} — {update_info['update_type']}"
                                print(f"  📰 Will write update article: {topic_hint[:80]}")
                            else:
                                print(f"  ⚠️ No case updates found either — using direct research")

                # Step 2: Research via Perplexity
                max_research_retries = 2
                research_context = None
                research_is_dup = False

                if is_update_article and update_info:
                    # Update article: research the new developments, skip dedup
                    research_context = research_case_update(update_info, category)
                    print(f"  ✓ Update research complete ({len(research_context)} chars)")
                    # Prepend update context so Claude knows this is an update
                    research_context = (
                        f"NOTE: This is an UPDATE article about new developments in an existing case.\n"
                        f"Original case: {update_info['update_case_name']}\n"
                        f"Update type: {update_info['update_type']}\n"
                        f"The article title MUST clearly indicate this is an update/new development.\n"
                        f"Use phrases like 'Update:', 'New Ruling in', 'Court Approves', etc.\n\n"
                        f"{research_context}"
                    )
                else:
                    for retry in range(max_research_retries + 1):
                        if article_content_type == "settlement":
                            research_context = research_settlement(
                                category, TOPIC_URL,
                                TOPIC_IDEA or topic_hint,
                                cat_avoidance
                            )
                        else:
                            research_context = research_topic(category, cat_avoidance, topic_hint=topic_hint)

                        attempt_label = f"(research {retry + 1}/{max_research_retries + 1}) " if retry > 0 else ""
                        print(f"  ✓ Research {attempt_label}complete ({len(research_context)} chars)")

                        # Check if research covers an already-existing topic (category-scoped
                        # to avoid cross-category false positives from shared company names)
                        is_dup, matched_title = check_research_context(research_context, cat_existing_articles)
                        if not is_dup:
                            research_is_dup = False
                            break  # Research is clean — proceed to generation

                        research_is_dup = True
                        if retry < max_research_retries:
                            print(f"  ⚠ Research overlaps existing: {matched_title[:70] if matched_title else 'unknown'}")
                            print(f"  ↻ Retrying with stronger avoidance...")
                            # Strengthen avoidance for next attempt
                            if matched_title:
                                cat_avoidance["titles"].append(matched_title)
                                cat_avoidance["keywords"].append(extract_keywords(matched_title))
                        else:
                            print(f"  ✗ Still overlaps after {max_research_retries} retries: {matched_title[:70] if matched_title else 'unknown'}")

                    if research_is_dup:
                        # Mark candidate as failed if we were using backlog
                        if candidate_id:
                            _handle_candidate_failure(site_db, candidate_id, candidate)
                            candidate_id = None
                        continue  # Try next attempt with a different candidate

                # Step 2b: Pre-generation topic coverage check (skip for update articles)
                # Catches cases where check_research_context() misses overlap because
                # the research doesn't use exact "X v. Y" formatting
                if not is_update_article:
                    topic_covered, topic_match = is_topic_covered(research_context, cat_avoidance)
                    if topic_covered:
                        print(f"  ⚠ Research topic already covered: {topic_match[:70] if topic_match else 'unknown'}")
                        if topic_match:
                            cat_avoidance["titles"].append(topic_match)
                            cat_avoidance["keywords"].append(extract_keywords(topic_match))
                        if candidate_id:
                            _handle_candidate_failure(site_db, candidate_id, candidate)
                            candidate_id = None  # Prevent double-handling in post-loop block
                        continue  # Retry outer loop with stronger avoidance

                # Step 2: Build prompt with research context injected
                article_prompt = article_prompt_template.replace("{{category}}", category)
                article_prompt = article_prompt.replace("{{article_number}}", str(i + 1))
                article_prompt = article_prompt.replace("{{total_articles}}", str(ARTICLES_COUNT))
                article_prompt = article_prompt.replace("{{research_context}}", research_context)

                article_prompt_hash = sha256_short(article_prompt)

                # Step 3: Generate article with Claude (no web search)
                result = generate_article(claude, system_prompt, article_prompt)
                article_data = result["article"]
                input_tokens = result["input_tokens"]
                output_tokens = result["output_tokens"]
                total_input_tokens += input_tokens
                total_output_tokens += output_tokens

                title = article_data.get("title", "Untitled")
                case_name = article_data.get("case_name")
                slug = f"{article_data.get('slug') or slugify(title)}-{article_id[:8]}"
                word_count = len(article_data.get("content", "").split())
                source_url = article_data.get("source_url", "")

                print(f"  Title:  {title}")
                print(f"  Slug:   {slug}")
                if article_content_type == "settlement":
                    print(f"  Amount: {article_data.get('settlement_amount', 'N/A')}")
                    print(f"  Deadline: {article_data.get('claim_deadline', 'N/A')}")
                print(f"  Words:  {word_count}")
                print(f"  Source: {source_url[:60]}..." if len(str(source_url)) > 60 else f"  Source: {source_url}")
                print(f"  Tokens: {input_tokens} in / {output_tokens} out")

                # Post-generation duplicate check — skip for update articles since
                # they intentionally cover an existing case with new developments
                if not is_update_article:
                    # Title/case_name Jaccard similarity
                    if is_duplicate(title, case_name, existing_articles):
                        print(f"  ⚠ DUPLICATE detected (title Jaccard match)")
                        # Strengthen avoidance and retry
                        cat_avoidance["titles"].append(title)
                        cat_avoidance["keywords"].append(extract_keywords(title))
                        if case_name:
                            cat_avoidance["titles"].append(case_name)
                            cat_avoidance["keywords"].append(extract_keywords(case_name))
                        if candidate_id:
                            _handle_candidate_failure(site_db, candidate_id, candidate)
                            candidate_id = None  # Prevent double-handling in post-loop block
                        continue  # Retry outer loop

                    # Content body check — catches cases where the title is
                    # worded differently but the article body references the same case.
                    # use_proper_nouns=False avoids false positives from generic legal
                    # terms in long article text (only checks "X v. Y" and labeled fields)
                    content_body = article_data.get("content", "")
                    if content_body:
                        body_is_dup, body_match = check_research_context(
                            content_body, existing_articles, use_proper_nouns=False
                        )
                        if body_is_dup:
                            print(f"  ⚠ DUPLICATE detected in content body (matches: {body_match[:70] if body_match else 'unknown'})")
                            if body_match:
                                cat_avoidance["titles"].append(body_match)
                                cat_avoidance["keywords"].append(extract_keywords(body_match))
                            if candidate_id:
                                _handle_candidate_failure(site_db, candidate_id, candidate)
                                candidate_id = None  # Prevent double-handling in post-loop block
                            continue  # Retry outer loop
                else:
                    print(f"  ℹ️ Skipping dedup checks (update article)")

                article_written = True
                break  # Post-gen checks passed — article is clean

            if not article_written:
                # Handle candidate failure if last attempt had a candidate
                if candidate_id:
                    _handle_candidate_failure(site_db, candidate_id, candidate)
                print(f"  ✗ All {max_article_attempts} attempts exhausted — skipping")
                articles_failed += 1
                continue

            write_site_article(site_db, article_id, article_data, category, site_db_site_id, article_content_type)
            print(f"  ✓ Site DB: written")
            articles_generated += 1
            articles_published += 1

            # Mark backlog candidate as processed and link article
            if candidate_id:
                try:
                    site_db.table("case_candidates") \
                        .update({
                            "processed": True,
                            "status": "processed",
                            "processed_at": datetime.now(timezone.utc).isoformat(),
                            "article_id": article_id,
                        }) \
                        .eq("id", candidate_id) \
                        .execute()
                    print(f"  ✓ Candidate marked processed")
                except Exception as e:
                    print(f"  ⚠️ Failed to mark candidate processed: {e}")

            # Add to dedup lists so subsequent articles in this batch don't duplicate
            existing_articles.append({"title": title, "case_name": case_name, "category": category})
            # Also update avoidance data for next Perplexity call
            avoidance_data["titles"].append(title)
            avoidance_data["keywords"].append(extract_keywords(title))
            if case_name:
                avoidance_data["titles"].append(case_name)
                avoidance_data["keywords"].append(extract_keywords(case_name))
            # Extract and track company for intra-batch dedup
            new_company = extract_company_from_case_name(case_name)
            if new_company:
                avoidance_data["companies"].add(new_company)

            if admin_conn and ADMIN_RUN_ID and site_id:
                write_admin_run_article(admin_conn, {
                    "run_id": ADMIN_RUN_ID,
                    "site_id": site_id,
                    "public_article_id": article_id,
                    "title": title,
                    "slug": slug,
                    "category": category,
                    "prompt_version": PROMPT_VERSION,
                    "system_prompt_hash": system_prompt_hash,
                    "article_prompt_hash": article_prompt_hash,
                    "model_used": MODEL,
                    "temperature": None,
                    "top_p": None,
                    "generation_params": {"research": "perplexity", "content_type": article_content_type},
                    "status": "published",
                    "word_count": word_count,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                })
                print(f"  ✓ Admin DB: run_article written")

        except Exception as e:
            articles_failed += 1
            error_msg = f"{type_label} {i + 1} ({category}): {str(e)}"
            errors.append(error_msg)
            print(f"  ✗ FAILED: {e}")
            traceback.print_exc()
            log_admin_error(admin_conn, site_id, error_msg, {
                "article_number": i + 1,
                "category": category,
                "content_type": article_content_type,
                "article_id": article_id,
                "traceback": traceback.format_exc(),
            })

    duration = int(time.time() - start_time)
    is_batch = GENERATION_MODE == "batch"
    cost = estimate_cost(MODEL, total_input_tokens, total_output_tokens, is_batch)

    if admin_conn and ADMIN_RUN_ID:
        try:
            update_admin_generation_run(admin_conn, ADMIN_RUN_ID, {
                "workflow_status": "completed" if articles_failed == 0 else "completed_with_errors",
                "articles_generated": articles_generated,
                "articles_published": articles_published,
                "articles_failed": articles_failed,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "duration_seconds": duration,
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
                "estimated_cost_usd": cost,
                "error_log": "\n".join(errors) if errors else None,
            })
            print(f"\n✓ Admin DB: generation_run updated")
        except Exception as e:
            print(f"\n✗ Failed to update generation_run: {e}")
            traceback.print_exc()

    print(f"\n{'=' * 60}")
    print(f"DONE")
    print(f"  Generated:  {articles_generated}")
    print(f"  Published:  {articles_published}")
    print(f"  Failed:     {articles_failed}")
    print(f"  Tokens:     {total_input_tokens} in / {total_output_tokens} out")
    print(f"  Est. cost:  ${cost}")
    print(f"  Duration:   {duration}s")
    print(f"{'=' * 60}")

    if articles_failed > 0 and articles_generated == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
