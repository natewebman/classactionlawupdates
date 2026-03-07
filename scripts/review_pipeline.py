"""
review_pipeline.py — Content Review & Rewrite Pipeline for classactionlawupdates
==================================================================================
Runs automatically after generate_content.py via GitHub Actions.

Pipeline stages:
  draft → fact_checked → fact_updated → published
                ↓ (if fake)
            regenerates → retries fact check (max 2 attempts)
                ↓ (if still fails)
            failed

Steps:
  1. FACT CHECK   — Perplexity verifies claims are real. Fake articles auto-regenerate.
  2. FACT UPDATE  — Perplexity searches the web for current data and updates outdated figures.
  3. HUMAN REWRITE — Claude Sonnet rewrites for natural human tone.
"""

import os
import sys
import json
import time
import traceback
import requests
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from supabase import create_client

# Add scripts/lib to path for shared utilities
sys.path.insert(0, str(Path(__file__).parent / "lib"))
from dedup import is_duplicate


# =============================================================================
# CONFIG
# =============================================================================

ANTHROPIC_API_KEY   = os.environ["ANTHROPIC_API_KEY"]
PERPLEXITY_API_KEY  = os.environ["PERPLEXITY_API_KEY"]
SUPABASE_URL        = os.environ["SUPABASE_URL"]
SUPABASE_KEY        = os.environ["SUPABASE_KEY"]
ADMIN_SUPABASE_URL  = os.environ.get("ADMIN_SUPABASE_URL", "")
ADMIN_SUPABASE_KEY  = os.environ.get("ADMIN_SUPABASE_KEY", "")

REWRITE_MODEL          = "claude-sonnet-4-5-20250929"   # Best model for human rewrite
PERPLEXITY_MODEL       = "sonar"               # Free tier model
MAX_REGEN_ATTEMPTS     = 2                     # Max times to regenerate a failing article

PERPLEXITY_HEADERS = {
    "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
    "Content-Type": "application/json",
}


# =============================================================================
# DB HELPERS
# =============================================================================

def get_admin_db():
    """Connect to admin Supabase. Returns None if credentials not set."""
    if not ADMIN_SUPABASE_URL or not ADMIN_SUPABASE_KEY:
        return None
    try:
        return create_client(ADMIN_SUPABASE_URL, ADMIN_SUPABASE_KEY)
    except Exception as e:
        print(f"WARNING: Could not connect to admin DB: {e}")
        return None


def update_stage(site_db, article_id: str, stage: str, content: str = None):
    """Update content_stage in site DB. Optionally update content at the same time."""
    updates = {"content_stage": stage}
    if content:
        updates["content"] = content
    site_db.table("articles").update(updates).eq("id", article_id).execute()
    print(f"   ↳ Site DB: content_stage = '{stage}'")


def sync_admin_stage(admin_db, article_id: str, stage: str):
    """Mirror the stage update to the admin DB run_articles table."""
    if not admin_db:
        return
    try:
        admin_db.table("run_articles") \
            .update({"status": stage}) \
            .eq("public_article_id", article_id) \
            .execute()
    except Exception as e:
        print(f"   WARNING: Admin DB sync failed for {article_id}: {e}")


# =============================================================================
# PERPLEXITY HELPER
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


# =============================================================================
# STEP 1: FACT CHECK
# =============================================================================

def fact_check(article: dict) -> dict:
    """
    Ask Perplexity to verify the article's key legal claims.
    Returns: {"passed": bool, "issues": str}
    """
    # Only send the first 3000 chars — enough to check key claims without wasting tokens
    snippet = article["content"][:3000]

    messages = [
        {
            "role": "system",
            "content": (
                "You are a legal fact-checker specializing in class action lawsuits. "
                "Verify whether the lawsuit, defendant company, settlement amounts, and case status "
                "described in the article are real and accurate based on your web search. "
                "Respond ONLY with a valid JSON object — no markdown, no preamble:\n"
                '{"passed": true, "issues": ""}'
                " or "
                '{"passed": false, "issues": "specific description of what is wrong"}'
            ),
        },
        {
            "role": "user",
            "content": (
                f"Fact-check this class action article:\n\n"
                f"Title: {article['title']}\n\n"
                f"Content excerpt:\n{snippet}\n\n"
                "Are the lawsuit, company, and settlement details real and accurate?"
            ),
        },
    ]

    raw = ask_perplexity(messages, max_tokens=256)

    try:
        clean = raw.strip()
        if clean.startswith("```"):
            lines = [l for l in clean.split("\n") if not l.strip().startswith("```")]
            clean = "\n".join(lines).strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        # Fallback: parse loosely from plain text response
        passed = "true" in raw.lower() and "false" not in raw.lower()
        return {"passed": passed, "issues": raw[:300]}


# =============================================================================
# STEP 2: FACT UPDATE
# =============================================================================

def fact_update(article: dict) -> str:
    """
    Ask Perplexity to search the web and return the article with updated facts.
    Returns the updated content string (or original if update fails).
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You are a legal content researcher. Search the web for the latest information "
                "about this class action lawsuit. Update any outdated settlement amounts, "
                "case statuses, deadlines, or figures with current accurate data. "
                "Return ONLY the updated article content in the exact same format as the input. "
                "Do not add any preamble, explanation, or commentary — just the updated article text."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Update this article with the latest accurate information from the web:\n\n"
                f"Title: {article['title']}\n\n"
                f"{article['content']}"
            ),
        },
    ]

    updated = ask_perplexity(messages, max_tokens=4096)
    return updated.strip()


# =============================================================================
# STEP 3: HUMAN REWRITE
# =============================================================================

def human_rewrite(claude_client: anthropic.Anthropic, article: dict) -> str:
    """
    Use Claude Sonnet to rewrite the article so it reads as naturally human-written.
    Returns the rewritten content string.
    """
    response = claude_client.messages.create(
        model=REWRITE_MODEL,
        max_tokens=4096,
        system=(
            "You are an expert legal journalist with 15 years of experience covering class action lawsuits. "
            "Rewrite the provided article so it reads as if written by an experienced human journalist. "
            "Use natural flow, varied sentence lengths, and an authoritative but conversational tone. "
            "Avoid all AI writing patterns: no overuse of bullet points, no repetitive transition phrases, "
            "no generic openers like 'In today's world' or closers like 'In conclusion'. "
            "Keep every fact exactly as given — do not add or remove information. "
            "Preserve all heading tags (<h2>, <h3>) and their hierarchy — do not merge, remove, or reorder sections. "
            "You may lightly edit heading text for natural flow, but keep the same number of sections. "
            "Return ONLY the rewritten article content. No preamble, no explanation."
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    f"Rewrite this article in a natural human journalist style:\n\n"
                    f"Title: {article['title']}\n\n"
                    f"{article['content']}"
                ),
            }
        ],
    )
    return response.content[0].text.strip()


# =============================================================================
# REGENERATE (called when fact check fails) — Perplexity research + Haiku
# =============================================================================

REGEN_MODEL = "claude-haiku-4-5-20251001"  # Cheap model for regeneration drafts

def regenerate(claude_client: anthropic.Anthropic, article: dict, existing_titles: list[str] = None) -> dict:
    """
    Regenerate content for an article that failed fact-checking.
    Uses Perplexity to research the SPECIFIC lawsuit by name, then Haiku to rewrite.
    Returns a dict with updated fields: {content, title, slug, meta_description, ...}
    """
    category = article.get('category', 'consumer protection')
    title = article.get('title', '')
    case_name = article.get('case_name', '')

    # Build a specific search query from the article's own metadata
    search_topic = case_name or title
    print(f"   ↳ Researching: {search_topic[:80]}")

    # Step 1: Research THIS SPECIFIC lawsuit/settlement via Perplexity
    research_messages = [
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
                f'Research this specific class action lawsuit or settlement: "{search_topic}"\n\n'
                f"Category: {category}\n\n"
                "Return structured information including:\n"
                "- Official case name and case number\n"
                "- Defendant company/companies\n"
                "- Settlement amount (if known)\n"
                "- Claim deadline and how to file\n"
                "- Eligibility criteria\n"
                "- Current case status (filed, pending, settled, approved, paying)\n"
                "- Claims administrator and settlement website URL\n"
                "- Source URLs\n\n"
                "If this specific case cannot be found or appears to be fabricated, "
                "say 'CASE NOT FOUND' and then provide details about a REAL, ACTIVE "
                f"class action settlement in the '{category}' category instead."
                + (
                    "\n\nDo NOT suggest any of these cases (already on our site):\n"
                    + "\n".join(f"- {t}" for t in (existing_titles or [])[:15])
                    if existing_titles else ""
                )
                + "\n\nLimit response to 300-500 words. Use bullet points."
            ),
        },
    ]
    research_context = ask_perplexity(research_messages, max_tokens=1024)

    # Step 2: Generate corrected article with Haiku — return JSON so we can update metadata too
    response = claude_client.messages.create(
        model=REGEN_MODEL,
        max_tokens=8192,
        system=(
            "You are a legal content writer for ClassActionLawUpdates.com. "
            "You will be given research context about a real lawsuit or settlement. "
            "Only write about lawsuits described in the research context. Never invent case details. "
            "If the research context does not include a fact, do not invent it.\n\n"
            "IMPORTANT: You must respond with ONLY a valid JSON object (no markdown, no code fences). "
            "The JSON must contain these keys:\n"
            '{\n'
            '  "title": "Article headline (compelling, SEO-friendly, under 70 chars)",\n'
            '  "slug": "url-safe-slug-version-of-title",\n'
            '  "content": "Full article body in HTML format. Use <h2>, <h3>, <p>, <ul>, <li> tags. Minimum 800 words.",\n'
            '  "meta_description": "SEO meta description, 150-160 characters",\n'
            '  "case_name": "Official case name (e.g., Smith v. Corporation Inc.) or null",\n'
            '  "case_status": "One of: filed, pending, settled, approved, paying, closed",\n'
            '  "settlement_amount": "Total settlement amount (e.g., $5.5 million) or null",\n'
            '  "claim_deadline": "YYYY-MM-DD or null",\n'
            '  "claim_url": "Direct URL where people can file a claim, or null",\n'
            '  "settlement_website": "Official settlement website URL, or null",\n'
            '  "claims_administrator": "Name of claims administrator, or null",\n'
            '  "class_counsel": "Name of lead plaintiff law firm, or null",\n'
            '  "proof_required": "What proof is needed (e.g., Receipt required or No proof needed), or null",\n'
            '  "potential_reward": "What individual claimants can expect (e.g., $20-$100), or null",\n'
            '  "location": "Jurisdiction or affected area (e.g., California or Nationwide)",\n'
            '  "source_url": "Real URL from research or null"\n'
            '}\n'
            "Do NOT include any text outside the JSON object."
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    f"The previous article titled '{title}' failed fact-checking.\n\n"
                    f"RESEARCH CONTEXT:\n{research_context}\n\n"
                    f"Using ONLY the research above, write an accurate article about this lawsuit/settlement. "
                    f"If the research found a different case (because the original was fabricated), "
                    f"write about the real case the research found instead — update the title to match.\n\n"
                    f"The title and content MUST be about the SAME case. "
                    f"Respond with ONLY the JSON object."
                ),
            }
        ],
    )

    raw_text = ""
    for block in response.content:
        if block.type == "text":
            raw_text += block.text

    # Parse the JSON response
    raw_text = raw_text.strip()
    # Strip markdown code fences if present
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        raw_text = "\n".join(lines).strip()

    try:
        parsed = json.loads(raw_text)
        return parsed
    except json.JSONDecodeError:
        # Fallback: return just the content as raw HTML, keep original metadata
        print(f"   ⚠ Could not parse regeneration JSON — using raw content")
        return {"content": raw_text}


# =============================================================================
# PROCESS ONE ARTICLE
# =============================================================================

def process_article(article: dict, site_db, admin_db, claude_client: anthropic.Anthropic, existing_titles: list[str] = None, existing_articles: list[dict] = None) -> bool:
    """
    Run a single article through all 3 pipeline steps.
    Returns True if article reaches 'published', False if it fails.
    """
    article_id = article["id"]
    print(f"\n── {article['title'][:65]}")
    print(f"   ID: {article_id}")

    # ─────────────────────────────────────────────────────────────
    # STEP 1: FACT CHECK
    # ─────────────────────────────────────────────────────────────
    print(f"   [1/3] Fact checking via Perplexity...")

    for attempt in range(1, MAX_REGEN_ATTEMPTS + 2):  # +2 so final attempt still runs
        result = fact_check(article)

        if result.get("passed"):
            print(f"   ✓ Fact check passed")
            update_stage(site_db, article_id, "fact_checked")
            sync_admin_stage(admin_db, article_id, "fact_checked")
            break
        else:
            print(f"   ✗ Failed: {result.get('issues', 'No details')[:120]}")

            if attempt > MAX_REGEN_ATTEMPTS:
                print(f"   ✗ Exhausted {MAX_REGEN_ATTEMPTS} regeneration attempts. Marking failed.")
                update_stage(site_db, article_id, "failed")
                sync_admin_stage(admin_db, article_id, "failed")
                return False

            print(f"   ↻ Regenerating article via Perplexity research (attempt {attempt}/{MAX_REGEN_ATTEMPTS})...")
            regen_result = regenerate(claude_client, article, existing_titles)

            # Update content
            new_content = regen_result.get("content", article["content"])
            article["content"] = new_content

            # Update metadata if regeneration provided it (title, slug, case_name, etc.)
            # Only include fields that exist as columns in the articles table
            ALLOWED_DB_FIELDS = {
                "title", "slug", "meta_description", "case_name",
                "settlement_amount", "claim_deadline", "case_status",
                "claim_url", "settlement_website", "claims_administrator",
                "class_counsel", "potential_reward", "location",
            }
            old_title = article.get("title", "")
            updates = {"content_stage": "draft", "content": new_content}
            for field in ALLOWED_DB_FIELDS:
                if regen_result.get(field):
                    updates[field] = regen_result[field]
                    article[field] = regen_result[field]

            if "title" in updates and updates["title"] != old_title:
                print(f"   ↳ New title: {updates['title'][:70]}")

            # Post-regeneration duplicate check (filter out self-match)
            new_title = updates.get("title", article.get("title", ""))
            new_case = updates.get("case_name", article.get("case_name"))
            existing_for_dedup = [
                e for e in existing_articles
                if e.get("title") != article.get("title")
            ]
            if existing_for_dedup and is_duplicate(new_title, new_case, existing_for_dedup):
                print(f"   ⚠ Regenerated article is a DUPLICATE — marking failed")
                update_stage(site_db, article_id, "failed")
                sync_admin_stage(admin_db, article_id, "failed")
                return False

            site_db.table("articles").update(updates).eq("id", article_id).execute()
            print(f"   ↳ Site DB: content_stage = 'draft'")
            time.sleep(2)  # Brief pause before re-checking

    # ─────────────────────────────────────────────────────────────
    # STEP 2: FACT UPDATE
    # ─────────────────────────────────────────────────────────────
    print(f"   [2/3] Updating facts with live web search...")
    try:
        updated_content = fact_update(article)

        # Only accept the update if Perplexity returned substantial content
        if updated_content and len(updated_content) > 300:
            article["content"] = updated_content
            update_stage(site_db, article_id, "fact_updated", updated_content)
            print(f"   ✓ Facts updated with latest data")
        else:
            # Too little returned — keep current content but advance stage
            update_stage(site_db, article_id, "fact_updated")
            print(f"   ⚠ Minimal update returned — keeping existing content, advancing stage")

    except Exception as e:
        # Don't fail the whole article over a fact update issue — just advance
        print(f"   ⚠ Fact update error ({e}) — advancing with existing content")
        update_stage(site_db, article_id, "fact_updated")

    sync_admin_stage(admin_db, article_id, "fact_updated")

    # ─────────────────────────────────────────────────────────────
    # STEP 3: HUMAN REWRITE
    # ─────────────────────────────────────────────────────────────
    print(f"   [3/3] Rewriting for human tone with Claude Sonnet...")
    try:
        rewritten = human_rewrite(claude_client, article)
        update_stage(site_db, article_id, "published", rewritten)
        sync_admin_stage(admin_db, article_id, "published")
        print(f"   ✓ Rewrite complete → published")
        return True

    except Exception as e:
        print(f"   ✗ Rewrite failed: {e}")
        update_stage(site_db, article_id, "rewrite_failed")
        sync_admin_stage(admin_db, article_id, "rewrite_failed")
        return False


# =============================================================================
# MAIN
# =============================================================================

def main():
    start_time = time.time()
    print("=" * 60)
    print(f"Review Pipeline — {datetime.now(timezone.utc).isoformat()}")
    print(f"Rewrite model:     {REWRITE_MODEL}")
    print(f"Perplexity model:  {PERPLEXITY_MODEL}")
    print("=" * 60)

    # Init clients
    claude  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    site_db = create_client(SUPABASE_URL, SUPABASE_KEY)
    admin_db = get_admin_db()

    # Fetch all draft articles
    result   = site_db.table("articles").select("*").eq("content_stage", "draft").execute()
    articles = result.data or []

    if not articles:
        print("\nNo draft articles found — pipeline has nothing to do.")
        return

    print(f"\nFound {len(articles)} draft article(s) to process.")

    # Deduplication: load existing articles for prompt-level and post-regen checks
    existing_articles = []
    existing_titles = []
    try:
        existing = site_db.table("articles") \
            .select("title, case_name") \
            .neq("content_stage", "failed") \
            .execute()
        existing_articles = existing.data or []
        for row in existing_articles:
            if row.get("title"):
                existing_titles.append(row["title"])
            if row.get("case_name"):
                existing_titles.append(row["case_name"])
        existing_titles = list(set(existing_titles))
        if existing_titles:
            print(f"Dedup: {len(existing_titles)} existing titles/cases loaded")
    except Exception as e:
        print(f"WARNING: Could not load existing titles for dedup: {e}")
    print()

    succeeded = 0
    failed    = 0

    for article in articles:
        try:
            ok = process_article(article, site_db, admin_db, claude, existing_titles, existing_articles)
            if ok:
                succeeded += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1
            print(f"  ✗ UNEXPECTED ERROR on article {article.get('id')}: {e}")
            traceback.print_exc()

    duration = int(time.time() - start_time)
    print(f"\n{'=' * 60}")
    print(f"PIPELINE COMPLETE")
    print(f"  Succeeded: {succeeded}")
    print(f"  Failed:    {failed}")
    print(f"  Duration:  {duration}s")
    print(f"{'=' * 60}")

    # Only hard-fail the GitHub Actions job if EVERY article failed
    if failed > 0 and succeeded == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
