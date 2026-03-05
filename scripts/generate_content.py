"""
generate_content.py — Content Generation Script for classactionlawupdates
==========================================================================
Called by generate-content.yml via GitHub Actions workflow_dispatch.

Flow:
  1. Load config from environment variables (set by workflow inputs)
  2. Load prompts from scripts/prompts/ directory
  3. Hash prompts → SHA256[:16] for integrity tracking
  4. For each article:
     a. Generate a shared UUID (used in BOTH site DB and admin DB)
     b. Call Perplexity to research real lawsuits in the category
     c. Call Claude Haiku with research context (no web search)
     d. Parse structured JSON response
     e. Write article to SITE Supabase (articles table)
     f. Write run_article to ADMIN Supabase (run_articles table)
  5. Update generation_runs in admin DB with totals + completion status
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

ARTICLES_COUNT = int(os.environ.get("ARTICLES_COUNT", "3"))

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
PROMPT_VERSION = os.environ.get("PROMPT_VERSION", "v2")

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
    for char in ["'", "\u2019", '"', "\u201c", "\u201d", ":", ";", ",", ".", "!", "?", "(", ")", "[", "]", "&"]:
        slug = slug.replace(char, "")
    slug = slug.replace(" ", "-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = slug.strip("-")[:120]
    if article_id:
        slug = f"{slug}-{article_id[:8]}"
    return slug


def pick_categories(count: int) -> list[str]:
    if CATEGORIES:
        cats = [c.strip() for c in CATEGORIES.split(",") if c.strip()]
    else:
        cats = DEFAULT_CATEGORIES.copy()
        random.shuffle(cats)
    result = []
    for i in range(count):
        result.append(cats[i % len(cats)])
    return result


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


def research_topic(category: str) -> str:
    """Use Perplexity to research real lawsuits in a category. Returns structured research."""
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
    
    # Extract JSON from response - Claude may include explanatory text before/after
    # Find the first { and last } to extract the JSON object
    start_idx = json_text.find('{')
    end_idx = json_text.rfind('}')
    
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        json_text = json_text[start_idx:end_idx + 1]
    
    # Also strip markdown code fences if present
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

def write_site_article(supabase_client, article_id: str, article_data: dict, category: str, site_id: str):
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
        "news_type": article_data.get("news_type", "analysis"),
        "content_stage": "draft",
        "published_at": datetime.now(timezone.utc).isoformat(),
    }

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
            "source": "generate_content",
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
    print(f"Content Generation — {datetime.now(timezone.utc).isoformat()}")
    print(f"Model: {MODEL}")
    print(f"Articles: {ARTICLES_COUNT}")
    print(f"Mode: {GENERATION_MODE}")
    print(f"Prompt Version: {PROMPT_VERSION}")
    print(f"Research: Perplexity ({PERPLEXITY_MODEL})")
    print(f"Admin Run ID: {ADMIN_RUN_ID or '(none — standalone run)'}")
    print(f"=" * 60)

    system_prompt = load_prompt("system_prompt.txt")
    article_prompt_template = load_prompt("article_prompt.txt")

    system_prompt_hash = sha256_short(system_prompt)
    article_prompt_template_hash = sha256_short(article_prompt_template)

    print(f"System prompt hash:  {system_prompt_hash}")
    print(f"Article prompt template hash: {article_prompt_template_hash}")

    claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    site_db = create_client(SUPABASE_URL, SUPABASE_KEY)
    admin_conn = get_admin_db()

    if GENERATION_MODE == "batch":
        print("WARNING: Batch mode (Anthropic Batch API) is not yet implemented.")
        print("         Falling back to standard mode for this run.")

    site_id = get_site_id(admin_conn)
    if admin_conn and ADMIN_RUN_ID and not site_id:
        print("WARNING: Could not resolve site_id. Admin writes will be skipped for run_articles.")

    site_db_site_id = None
    try:
        sites_result = site_db.table("sites").select("id").limit(1).execute()
        if sites_result.data and len(sites_result.data) > 0:
            site_db_site_id = sites_result.data[0]["id"]
            print(f"Site DB site_id: {site_db_site_id}")
    except Exception as e:
        print(f"WARNING: Could not look up site_id from site DB: {e}")

    categories = pick_categories(ARTICLES_COUNT)

    total_input_tokens = 0
    total_output_tokens = 0
    articles_generated = 0
    articles_published = 0
    articles_failed = 0
    errors = []

    for i in range(ARTICLES_COUNT):
        article_id = str(uuid.uuid4())
        category = categories[i]

        print(f"\n── Article {i + 1}/{ARTICLES_COUNT} ──")
        print(f"  UUID:     {article_id}")
        print(f"  Category: {category}")
        print(f"  🔍 Researching via Perplexity...")

        try:
            # Step 1: Research real lawsuits via Perplexity
            research_context = research_topic(category)
            print(f"  ✓ Research complete ({len(research_context)} chars)")

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
            slug = f"{article_data.get('slug') or slugify(title)}-{article_id[:8]}"
            word_count = len(article_data.get("content", "").split())
            source_url = article_data.get("source_url", "")

            print(f"  Title:  {title}")
            print(f"  Slug:   {slug}")
            print(f"  Words:  {word_count}")
            print(f"  Source: {source_url[:60]}..." if len(source_url) > 60 else f"  Source: {source_url}")
            print(f"  Tokens: {input_tokens} in / {output_tokens} out")

            write_site_article(site_db, article_id, article_data, category, site_db_site_id)
            print(f"  ✓ Site DB: written")
            articles_generated += 1
            articles_published += 1

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
                    "generation_params": {"research": "perplexity"},
                    "status": "published",
                    "word_count": word_count,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                })
                print(f"  ✓ Admin DB: run_article written")

        except Exception as e:
            articles_failed += 1
            error_msg = f"Article {i + 1} ({category}): {str(e)}"
            errors.append(error_msg)
            print(f"  ✗ FAILED: {e}")
            traceback.print_exc()
            log_admin_error(admin_conn, site_id, error_msg, {
                "article_number": i + 1,
                "category": category,
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
