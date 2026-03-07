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
import uuid
import hashlib
import time
import random
import traceback
from pathlib import Path
from datetime import datetime, timezone

import anthropic
import requests
from supabase import create_client

# Add scripts/lib to path for shared utilities
sys.path.insert(0, str(Path(__file__).parent / "lib"))
from dedup import is_duplicate, load_existing_articles, check_research_context, build_avoidance_data, extract_keywords, is_topic_covered, extract_company_from_case_name


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


def research_topic(category: str, avoidance_data: dict = None) -> str:
    """Use Perplexity to research real lawsuits in a category. Returns structured research."""
    avoid_section = _build_avoid_section(avoidance_data)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a legal research assistant. Search the web for real, current information "
                "about class action lawsuits. Return only verified facts with sources."
            ),
        },
        {
            "role": "user",
            "content": (
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
                + avoid_section
            ),
        },
    ]
    return ask_perplexity(messages, max_tokens=1024)


def research_settlement(category: str, topic_url: str = "", topic_idea: str = "", avoidance_data: dict = None) -> str:
    """Use Perplexity to research real settlements. Returns structured research."""
    avoid_section = ""
    if avoidance_data and not topic_url and not topic_idea:
        avoid_section = _build_avoid_section(avoidance_data)

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
# CLAUDE API (NO WEB SEARCH — uses Perplexity research context)
# =============================================================================

def generate_article(client: anthropic.Anthropic, system_prompt: str, article_prompt: str) -> dict:
    max_retries = 3
    for attempt in range(max_retries + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=8192,
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

    # Deduplication: fetch existing articles and build structured avoidance data
    existing_articles = load_existing_articles(site_db)
    avoidance_data = build_avoidance_data(existing_articles)
    print(f"Dedup: {len(avoidance_data['titles'])} titles/cases, {len(avoidance_data['companies'])} companies loaded")

    def _category_avoidance(base_avoidance: dict, existing: list[dict], category: str) -> dict:
        """Build category-prioritized avoidance from the shared base data."""
        cat_data = build_avoidance_data(existing, category=category)
        # Merge any intra-batch companies from the shared base
        cat_data["companies"] = cat_data["companies"] | base_avoidance.get("companies", set())
        return cat_data

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
            # Step 1: Research via Perplexity with multi-retry dedup
            # Build category-scoped avoidance, merging intra-batch companies
            cat_avoidance = _category_avoidance(avoidance_data, existing_articles, category)

            max_research_retries = 2
            research_context = None
            research_is_dup = False

            for retry in range(max_research_retries + 1):
                if article_content_type == "settlement":
                    research_context = research_settlement(category, TOPIC_URL, TOPIC_IDEA, cat_avoidance)
                else:
                    research_context = research_topic(category, cat_avoidance)

                attempt_label = f"(attempt {retry + 1}/{max_research_retries + 1}) " if retry > 0 else ""
                print(f"  ✓ Research {attempt_label}complete ({len(research_context)} chars)")

                # Check if research covers an already-existing topic
                is_dup, matched_title = check_research_context(research_context, existing_articles)
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
                    print(f"  ✗ Still overlaps after {max_research_retries} retries: {matched_title[:70] if matched_title else 'unknown'} — skipping")
                    articles_failed += 1

            if research_is_dup:
                continue

            # Step 1b: Pre-generation topic coverage check
            # Catches cases where check_research_context() misses overlap because
            # the research doesn't use exact "X v. Y" formatting
            topic_covered, topic_match = is_topic_covered(research_context, cat_avoidance)
            if topic_covered:
                print(f"  ⚠ Research topic already covered: {topic_match[:70] if topic_match else 'unknown'} — skipping")
                articles_failed += 1
                continue

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

            # Post-generation duplicate check — title/case_name Jaccard + company name match
            if is_duplicate(title, case_name, existing_articles, companies=avoidance_data.get("companies")):
                print(f"  ⚠ DUPLICATE detected (title/company match) — skipping article")
                articles_failed += 1
                continue

            # Post-generation content body check — catches cases where the title is
            # worded differently but the article body references the same case
            content_body = article_data.get("content", "")
            if content_body:
                body_is_dup, body_match = check_research_context(content_body, existing_articles)
                if body_is_dup:
                    print(f"  ⚠ DUPLICATE detected in content body (matches: {body_match[:70] if body_match else 'unknown'}) — skipping")
                    articles_failed += 1
                    continue

            write_site_article(site_db, article_id, article_data, category, site_db_site_id, article_content_type)
            print(f"  ✓ Site DB: written")
            articles_generated += 1
            articles_published += 1

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
