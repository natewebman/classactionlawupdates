"""
Weekly Case Status Updater

Checks all non-closed published settlement articles against Perplexity for
source-supported metadata changes. Updates ONLY settlement metadata fields.
Never touches title, slug, content, or content_stage.

Usage:
  python scripts/update_case_status.py

Environment:
  PERPLEXITY_API_KEY  — required
  SUPABASE_URL        — required
  SUPABASE_KEY        — required
  DRY_RUN             — optional, set to "1" to log without writing
"""

import json
import os
import re
import sys
import time
from datetime import datetime

import requests
from supabase import create_client

# ── Config ──

PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

PERPLEXITY_MODEL = "sonar"
PERPLEXITY_HEADERS = {
    "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
    "Content-Type": "application/json",
}
PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"

BATCH_SIZE = 5
MAX_RETRIES = 3
INITIAL_BACKOFF = 2  # seconds

# Fields this script is allowed to update
ALLOWED_FIELDS = {
    "case_status",
    "settlement_amount",
    "claim_deadline",
    "claim_url",
    "settlement_website",
    "claims_administrator",
    "potential_reward",
    "location",
}

VALID_STATUSES = {"filed", "pending", "settled", "approved", "paying", "closed"}

# ── Helpers ──


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def ask_perplexity(prompt: str, retries: int = MAX_RETRIES) -> str | None:
    """Send a prompt to Perplexity with exponential backoff."""
    backoff = INITIAL_BACKOFF
    for attempt in range(retries):
        try:
            resp = requests.post(
                PERPLEXITY_URL,
                headers=PERPLEXITY_HEADERS,
                json={
                    "model": PERPLEXITY_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 4096,
                    "temperature": 0.1,
                },
                timeout=60,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            elif resp.status_code in (429, 529):
                log(f"  Rate limited ({resp.status_code}), retrying in {backoff}s...")
                time.sleep(backoff)
                backoff *= 2
            else:
                log(f"  Perplexity error {resp.status_code}: {resp.text[:200]}")
                time.sleep(backoff)
                backoff *= 2
        except requests.exceptions.Timeout:
            log(f"  Perplexity timeout, retrying in {backoff}s...")
            time.sleep(backoff)
            backoff *= 2
        except Exception as e:
            log(f"  Perplexity exception: {e}")
            time.sleep(backoff)
            backoff *= 2
    return None


def normalize_date(val: str | None) -> str | None:
    """Normalize a date string to YYYY-MM-DD."""
    if not val:
        return None
    val = val.strip()
    if not val:
        return None
    # Try common formats
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(val, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return val  # Return as-is if parsing fails


def normalize_url(val: str | None) -> str | None:
    """Normalize a URL by stripping whitespace and trailing slashes."""
    if not val:
        return None
    val = val.strip().rstrip("/")
    return val if val else None


def normalize_string(val: str | None) -> str | None:
    """Normalize a generic string field."""
    if not val:
        return None
    val = val.strip()
    return val if val else None


def normalize_status(val: str | None) -> str | None:
    """Normalize case_status to lowercase valid value."""
    if not val:
        return None
    val = val.strip().lower()
    return val if val in VALID_STATUSES else None


def values_differ(current: str | None, proposed: str | None, field: str) -> bool:
    """Check if two values are materially different after normalization."""
    if field == "case_status":
        c = normalize_status(current)
        p = normalize_status(proposed)
    elif field == "claim_deadline":
        c = normalize_date(current)
        p = normalize_date(proposed)
    elif field in ("claim_url", "settlement_website"):
        c = normalize_url(current)
        p = normalize_url(proposed)
    else:
        c = normalize_string(current)
        p = normalize_string(proposed)

    # Both null/empty — no change
    if not c and not p:
        return False
    # One is null, other is not
    if not c or not p:
        return True
    # Case-insensitive compare for most fields
    return c.lower() != p.lower()


def build_batch_prompt(articles: list[dict]) -> str:
    """Build the Perplexity prompt for a batch of cases."""
    case_list = []
    for i, a in enumerate(articles):
        name = a.get("case_name") or a.get("title", "Unknown")
        case_list.append(f"{i + 1}. {name}")

    cases_text = "\n".join(case_list)

    return f"""Check the current status of these class action settlement cases. For each case, determine if any of the following metadata fields have changed:
- case_status (valid values: filed, pending, settled, approved, paying, closed)
- settlement_amount
- claim_deadline
- claim_url
- settlement_website
- claims_administrator
- potential_reward
- location

Specifically check:
- Has the claim deadline passed?
- Are claims still being accepted?
- Has the case status changed?

Cases to check:
{cases_text}

IMPORTANT INSTRUCTIONS:
- Respond with ONLY compact JSON, no prose or explanation outside the JSON.
- Return a JSON array of objects, one per case, in the same order as listed above.
- Each object must have: "case_index" (1-based), "case_name", "changed_fields" (object with only fields that changed, mapping field name to new value), "sources" (short array of source URLs).
- Include ONLY fields that have actually changed. If nothing changed, use an empty object for changed_fields.
- Keep sources short (just URLs, no descriptions).
- Prefer official/primary sources: settlement website, claims administrator page, court notice/order, official legal notice page.
- Omit any change that is guessed or not supported by a concrete source.
- Leave a field unchanged unless there is concrete source support for the new value.
- Do NOT set case_status to "closed" unless BOTH: (1) the claim deadline has passed AND (2) there is explicit evidence claims are no longer accepted.

Respond with ONLY the JSON array, nothing else."""


def build_single_prompt(article: dict) -> str:
    """Build a Perplexity prompt for a single case (fallback)."""
    name = article.get("case_name") or article.get("title", "Unknown")
    return build_batch_prompt([article])


def extract_json(raw: str) -> list | None:
    """Extract a JSON array from a Perplexity response, handling markdown fences."""
    text = raw.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        elif isinstance(parsed, dict):
            return [parsed]
    except json.JSONDecodeError:
        pass

    # Try to find JSON array in the text
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None


def compute_updates(article: dict, changes: dict) -> dict:
    """Compute which fields should actually be updated."""
    updates = {}
    for field, new_value in changes.items():
        if field not in ALLOWED_FIELDS:
            continue
        if new_value is None:
            continue

        new_str = str(new_value).strip()
        if not new_str:
            continue

        # Special handling for case_status
        if field == "case_status":
            normalized = normalize_status(new_str)
            if not normalized:
                log(f"    Skipping invalid status: {new_str}")
                continue

            # Conservative auto-close check
            if normalized == "closed":
                current_deadline = article.get("claim_deadline")
                if current_deadline:
                    deadline_date = normalize_date(current_deadline)
                    if deadline_date:
                        today = datetime.now().strftime("%Y-%m-%d")
                        if deadline_date >= today:
                            log(f"    Skipping auto-close: deadline {deadline_date} is not yet passed")
                            continue
                # If no deadline, we rely on Perplexity's source evidence
                # (the prompt instructs it to only close with explicit evidence)

            new_str = normalized

        # Normalize date fields
        if field == "claim_deadline":
            normalized_date = normalize_date(new_str)
            if normalized_date:
                new_str = normalized_date

        # Normalize URL fields
        if field in ("claim_url", "settlement_website"):
            normalized_url = normalize_url(new_str)
            if normalized_url:
                new_str = normalized_url

        current = article.get(field)
        if values_differ(current, new_str, field):
            updates[field] = new_str

    return updates


# ── Main ──


def main():
    if not PERPLEXITY_API_KEY:
        print("ERROR: PERPLEXITY_API_KEY not set")
        sys.exit(1)
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL and SUPABASE_KEY must be set")
        sys.exit(1)

    if DRY_RUN:
        log("🔍 DRY RUN MODE — no writes will be made")

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Resolve site_id
    site_key = "classactionlawupdates"
    site_resp = supabase.table("sites").select("id").eq("site_key", site_key).single().execute()
    if not site_resp.data:
        print(f"ERROR: Site not found for key: {site_key}")
        sys.exit(1)
    site_id = site_resp.data["id"]

    # Fetch all non-closed published articles
    log("Fetching non-closed published articles...")
    resp = (
        supabase.table("articles")
        .select("id, title, case_name, case_status, settlement_amount, claim_deadline, claim_url, location, potential_reward, settlement_website, claims_administrator")
        .eq("site_id", site_id)
        .eq("content_stage", "published")
        .neq("case_status", "closed")
        .execute()
    )
    articles = resp.data or []
    # Also include articles with null case_status (they're not closed)
    resp_null = (
        supabase.table("articles")
        .select("id, title, case_name, case_status, settlement_amount, claim_deadline, claim_url, location, potential_reward, settlement_website, claims_administrator")
        .eq("site_id", site_id)
        .eq("content_stage", "published")
        .is_("case_status", "null")
        .execute()
    )
    articles.extend(resp_null.data or [])

    log(f"Found {len(articles)} non-closed articles to check")

    if not articles:
        log("No articles to check. Done.")
        return

    # Stats
    total_checked = 0
    total_updated = 0
    total_unchanged = 0
    total_errors = 0
    total_dry_run_proposed = 0
    total_batch_fallbacks = 0

    # Process in batches
    batches = [articles[i : i + BATCH_SIZE] for i in range(0, len(articles), BATCH_SIZE)]

    for batch_idx, batch in enumerate(batches):
        batch_names = [a.get("case_name") or a.get("title", "?")[:50] for a in batch]
        log(f"\n─── Batch {batch_idx + 1}/{len(batches)} ({len(batch)} cases) ───")
        for name in batch_names:
            log(f"  • {name}")

        prompt = build_batch_prompt(batch)
        raw_response = ask_perplexity(prompt)

        if not raw_response:
            log("  ✗ Perplexity returned no response, falling back to singles")
            total_batch_fallbacks += 1
            # Fallback: process each article individually
            for article in batch:
                total_checked += 1
                name = article.get("case_name") or article.get("title", "?")[:50]
                single_prompt = build_single_prompt(article)
                single_raw = ask_perplexity(single_prompt)
                if not single_raw:
                    log(f"  ✗ Single retry failed for: {name}")
                    total_errors += 1
                    continue
                parsed = extract_json(single_raw)
                if not parsed or len(parsed) == 0:
                    log(f"  ✗ Could not parse single response for: {name}")
                    log(f"    Raw: {single_raw[:300]}")
                    total_errors += 1
                    continue
                result = parsed[0]
                changes = result.get("changed_fields", {})
                sources = result.get("sources", [])
                updates = compute_updates(article, changes)
                if updates:
                    if DRY_RUN:
                        log(f"  [DRY RUN] Would update {name}: {updates}")
                        log(f"    Sources: {sources}")
                        total_dry_run_proposed += 1
                    else:
                        try:
                            supabase.table("articles").update(updates).eq("id", article["id"]).execute()
                            log(f"  ✓ Updated {name}: {updates}")
                            log(f"    Sources: {sources}")
                            total_updated += 1
                        except Exception as e:
                            log(f"  ✗ Supabase update failed for {name}: {e}")
                            total_errors += 1
                else:
                    log(f"  — No changes for: {name}")
                    total_unchanged += 1
            continue

        # Parse batch response
        parsed = extract_json(raw_response)
        if not parsed:
            log(f"  ✗ Could not parse batch JSON, falling back to singles")
            log(f"    Raw: {raw_response[:500]}")
            total_batch_fallbacks += 1
            # Fallback to singles
            for article in batch:
                total_checked += 1
                name = article.get("case_name") or article.get("title", "?")[:50]
                single_prompt = build_single_prompt(article)
                single_raw = ask_perplexity(single_prompt)
                if not single_raw:
                    log(f"  ✗ Single retry failed for: {name}")
                    total_errors += 1
                    continue
                single_parsed = extract_json(single_raw)
                if not single_parsed or len(single_parsed) == 0:
                    log(f"  ✗ Could not parse single response for: {name}")
                    total_errors += 1
                    continue
                result = single_parsed[0]
                changes = result.get("changed_fields", {})
                sources = result.get("sources", [])
                updates = compute_updates(article, changes)
                if updates:
                    if DRY_RUN:
                        log(f"  [DRY RUN] Would update {name}: {updates}")
                        log(f"    Sources: {sources}")
                        total_dry_run_proposed += 1
                    else:
                        try:
                            supabase.table("articles").update(updates).eq("id", article["id"]).execute()
                            log(f"  ✓ Updated {name}: {updates}")
                            log(f"    Sources: {sources}")
                            total_updated += 1
                        except Exception as e:
                            log(f"  ✗ Supabase update failed for {name}: {e}")
                            total_errors += 1
                else:
                    log(f"  — No changes for: {name}")
                    total_unchanged += 1
            continue

        # Map responses back to articles
        if len(parsed) != len(batch):
            log(f"  ⚠ Response has {len(parsed)} results for {len(batch)} cases, matching by index")

        for i, article in enumerate(batch):
            total_checked += 1
            name = article.get("case_name") or article.get("title", "?")[:50]

            # Try to find matching result
            result = None
            if i < len(parsed):
                result = parsed[i]
            else:
                # Try to match by case_index
                for r in parsed:
                    if r.get("case_index") == i + 1:
                        result = r
                        break

            if not result:
                log(f"  ⚠ No matching result for: {name}")
                total_errors += 1
                continue

            changes = result.get("changed_fields", {})
            sources = result.get("sources", [])
            updates = compute_updates(article, changes)

            if updates:
                if DRY_RUN:
                    log(f"  [DRY RUN] Would update {name}: {updates}")
                    log(f"    Sources: {sources}")
                    total_dry_run_proposed += 1
                else:
                    try:
                        supabase.table("articles").update(updates).eq("id", article["id"]).execute()
                        log(f"  ✓ Updated {name}: {updates}")
                        log(f"    Sources: {sources}")
                        total_updated += 1
                    except Exception as e:
                        log(f"  ✗ Supabase update failed for {name}: {e}")
                        total_errors += 1
            else:
                log(f"  — No changes for: {name}")
                total_unchanged += 1

    # Final summary
    log("\n" + "=" * 50)
    log("SUMMARY")
    log("=" * 50)
    log(f"  Total articles checked:    {total_checked}")
    log(f"  Total updated:             {total_updated}")
    log(f"  Total unchanged:           {total_unchanged}")
    if DRY_RUN:
        log(f"  Total dry-run proposed:    {total_dry_run_proposed}")
    log(f"  Total errors:              {total_errors}")
    log(f"  Total batch fallbacks:     {total_batch_fallbacks}")
    log("=" * 50)


if __name__ == "__main__":
    main()
