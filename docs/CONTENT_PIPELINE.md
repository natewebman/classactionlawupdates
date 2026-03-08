# Content Pipeline — Technical Reference

Complete documentation for the classactionlawupdates.com automated content generation, review, and image pipeline.

## Pipeline Overview

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────┐
│   Generate   │────▶│    Review    │────▶│    Images    │────▶│  Deploy  │
│  (Job 1)     │     │  (Job 2)     │     │  (Job 3)     │     │  (hook)  │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────┘
  Perplexity          Fact-check           Claude prompt         Cloudflare
  + Claude Haiku      Fact-update          + GPT Image 1.5       Pages
                      Human rewrite        + Supabase Storage
                      (Claude Sonnet)
```

**Trigger:** Daily at 15:00 UTC (9 AM Central) via cron, or manual `workflow_dispatch`

**Workflow file:** `.github/workflows/generate-articles.yml`

---

## Job 1: Generate (`scripts/generate_articles.py`)

### What it does
Discovers candidate cases via Perplexity (stored in `case_candidates` backlog), selects the oldest unprocessed candidate, deep-researches it, then generates structured article JSON via Claude Haiku. Multi-layer deduplication prevents duplicate topics.

### Inputs (env vars from workflow)

| Variable | Default | Description |
|----------|---------|-------------|
| `ARTICLES_COUNT` | `1` | Number of articles to generate |
| `MODEL` | `claude-haiku-4-5-20251001` | Claude model (supports aliases: "haiku", "sonnet", "opus") |
| `CONTENT_TYPE` | `mixed` | `mixed` (50/50), `news`, or `settlement` |
| `CATEGORIES` | (empty) | Comma-separated; empty = auto-balanced from DB |
| `TOPIC_URL` | (empty) | Specific settlement URL to research |
| `TOPIC_IDEA` | (empty) | Specific case name to research |
| `GENERATION_MODE` | `standard` | `standard` or `batch` (batch not yet implemented) |

### Flow

```
1. SETUP
   ├── Load 4 prompt templates from scripts/prompts/
   ├── Init Claude + Supabase clients
   ├── Resolve site_id
   ├── Assign content types (news vs settlement per slot)
   └── Pick categories (balanced auto-select or explicit)

2. LOAD DEDUP DATA
   ├── load_existing_articles() → all non-failed articles from DB
   └── build_avoidance_data() → {titles, companies, keywords}

3. DISCOVERY PHASE (per unique category/content_type)
   ├── Check backlog size — skip if >100 unprocessed candidates
   ├── Ask Perplexity for ~50 recent cases (pipe-separated format)
   ├── Parse response into structured candidate records
   ├── Load global dedup pool (all articles + all candidates)
   ├── Dedup each via is_case_duplicate() against global pool
   │   (75% title similarity, docket match, defendant+court+date)
   └── Insert new candidates into case_candidates table

4. PER-ARTICLE LOOP (×ARTICLES_COUNT)
   │
   ├── OUTER RETRY LOOP (max 3 attempts)
   │   │
   │   ├── Build category-scoped avoidance data (last 50 in category)
   │   │
   │   ├── CANDIDATE SELECTION
   │   │   ├── Select oldest unprocessed candidate from case_candidates
   │   │   ├── Safely claim candidate (atomic status check + update)
   │   │   └── If no candidates → fall back to direct research
   │   │
   │   ├── RESEARCH (Perplexity, inner retry ×3)
   │   │   ├── Call research_topic(topic_hint=...) or research_settlement()
   │   │   ├── check_research_context() → category-scoped overlap check
   │   │   ├── If duplicate: strengthen avoidance, retry research
   │   │   └── If all retries fail: mark candidate failed, break
   │   │
   │   ├── PRE-GENERATION CHECK
   │   │   ├── is_topic_covered() → 3-strategy entity check (category-scoped)
   │   │   └── If covered: mark candidate failed, retry outer loop
   │   │
   │   ├── GENERATE (Claude Haiku, max_tokens=16384)
   │   │   ├── Inject research into article prompt template
   │   │   ├── Check stop_reason for truncation
   │   │   └── Parse JSON response → article_data
   │   │
   │   ├── POST-GENERATION TITLE CHECK
   │   │   ├── is_duplicate(title, case_name, existing) — global
   │   │   └── If duplicate: mark candidate failed, retry outer loop
   │   │
   │   └── POST-GENERATION BODY CHECK
   │       ├── check_research_context(body, existing, use_proper_nouns=False) — global
   │       └── If duplicate: mark candidate failed, retry outer loop
   │
   ├── If all attempts fail → articles_failed++, continue
   │
   └── SUCCESS PATH
       ├── write_site_article() → DB (content_stage = "draft")
       ├── Mark candidate as "processed", link article_id
       ├── Update avoidance_data (titles, keywords, companies)
       └── Write admin tracking record

5. FINALIZATION
   ├── Update admin DB with totals, duration, cost
   ├── Print summary
   └── sys.exit(1) if articles_failed > 0 AND articles_generated == 0
```

### Candidate Lifecycle

```
discovered → processing → processed (linked to article_id)
                       → failed (after 3 retries)
                       → duplicate (caught by DB unique indexes)
```

Failed candidates are retried up to 3 times (retry_count incremented each time,
returned to "discovered" status). After 3 failures, permanently marked "failed".

### Article JSON Schema (Claude output)

```json
{
  "title": "SEO headline (≤70 chars)",
  "slug": "url-slug",
  "content": "<h2>...</h2><p>...</p> (min 800 words)",
  "meta_description": "150-160 chars",
  "category": "financial",
  "news_type": "analysis|report|settlement",
  "source_url": "https://...",
  "case_name": "Plaintiff v. Defendant Inc.",
  "case_status": "filed|pending|settled|approved|paying|closed",
  "settlement_amount": "$5.5 million",
  "claim_deadline": "YYYY-MM-DD",
  "claim_url": "https://...",
  "settlement_website": "https://...",
  "claims_administrator": "Name",
  "class_counsel": "Law firm name",
  "proof_required": "Receipt required | No proof needed",
  "potential_reward": "$20-$100 per claimant",
  "location": "California | Nationwide"
}
```

### Exit Conditions

| Condition | Exit Code | Effect on Pipeline |
|-----------|-----------|-------------------|
| All articles generated | 0 | Review job runs normally |
| Some failed, some succeeded | 0 | Review job runs (processes successes) |
| ALL failed, 0 generated | 1 | Review job does NOT run (conditional on generate success) |

---

## Job 2: Review (`scripts/review_pipeline.py`)

### What it does
Processes draft articles through 3 sequential stages. Articles failing fact-check are auto-regenerated (up to 2 retries). Uses Claude Sonnet for the final human-tone rewrite.

### Content Stages

```
draft → fact_checked → fact_updated → published
                ↓
         (failed fact check)
                ↓
         regenerate (Perplexity + Haiku)
                ↓
         draft (retry, max 2 regen attempts)
                ↓
         failed (if still bad)
```

### Flow

```
1. SETUP
   ├── Init Claude + Supabase clients
   ├── Fetch all articles WHERE content_stage = "draft"
   └── Load existing articles for dedup (includes drafts, excludes failed)

2. PER-ARTICLE PROCESSING
   │
   ├── [1/3] FACT CHECK
   │   ├── Send article to Perplexity for verification
   │   ├── Check: lawsuit exists, amounts correct, status accurate
   │   ├── If PASS → content_stage = "fact_checked"
   │   └── If FAIL →
   │       ├── Regenerate via Perplexity research + Claude Haiku
   │       ├── Post-regen duplicate check (is_duplicate, self-match filtered)
   │       ├── If duplicate → mark "failed"
   │       ├── Retry fact check (max 2 regeneration attempts)
   │       └── If exhausted → mark "failed"
   │
   ├── [2/3] FACT UPDATE
   │   ├── Perplexity searches for latest info on the lawsuit
   │   ├── Updates amounts, dates, status with latest web data
   │   ├── If minimal response → keep existing content, advance stage
   │   └── content_stage = "fact_updated"
   │
   └── [3/3] HUMAN REWRITE (Claude Sonnet)
       ├── Rewrites article in natural, conversational journalist tone
       ├── Preserves all facts, HTML structure, headings
       ├── content_stage = "published"
       └── If error → content_stage = "rewrite_failed"

3. FINALIZATION
   ├── Print summary (succeeded, failed, duration)
   └── sys.exit(1) if ALL articles failed AND 0 succeeded
```

### Key Design Decisions

- **Conditional on generate success** — only runs if generate job succeeds (prevents processing on empty batch)
- **Includes drafts in dedup** — current batch's drafts are checked against each other
- **Self-match filter** — an article is excluded from its own dedup comparison
- **Graceful fact-update** — errors don't fail the article, just advance with existing content

---

## Job 3: Images (`scripts/generate-missing-images.ts`)

### What it does
Generates photorealistic hero images for published articles that don't have one yet. Uses Claude Haiku to write an image prompt, then GPT Image 1.5 to generate the image, then uploads to Supabase Storage.

### Flow

```
1. Fetch published articles WHERE hero_image IS NULL
2. For each article (max 2 retries per article):
   ├── Claude Haiku → image prompt (2-3 sentences)
   ├── GPT Image 1.5 → PNG (1536×1024, medium quality)
   ├── Upload to Supabase Storage (article-images bucket)
   └── Update article record (hero_image, hero_image_alt, hero_image_filename)
3. If ANY image fails after retries → exit(1) (blocks deploy)
4. If all succeed → trigger Cloudflare Pages deploy hook
```

### Strict Gate
**ANY** image failure blocks the entire deploy. This ensures no article goes live without a hero image.

---

## Deduplication System (`scripts/lib/dedup.py`)

### Hybrid Architecture: Category-Scoped Soft Checks + Global Hard Checks

The dedup system uses a hybrid approach to balance precision and recall:
- **Category-scoped** soft checks (research overlap, topic coverage) — prevents cross-category false positives from shared company names
- **Global** hard checks (case identity, post-generation title/body) — prevents the same lawsuit from appearing under different categories

### 7-Checkpoint Architecture

| # | Checkpoint | Location | Strategy | Scope | Threshold |
|---|-----------|----------|----------|-------|-----------|
| 1 | Discovery dedup | Before backlog insert | `is_case_duplicate()` — docket match, 75% title similarity, defendant+court+date | Global | SequenceMatcher > 0.75 |
| 2 | Pre-research avoidance | Perplexity prompt | Company names + titles in "Do NOT cover" section | Category (last 50) | N/A (prompt-based) |
| 3 | Post-research check | After Perplexity | `check_research_context()` — "X v. Y" patterns, labeled fields, proper noun phrases | Category | Jaccard ≥ 0.4 |
| 4 | Pre-generation topic | After research | `is_topic_covered()` — 3-strategy entity-aware check | Category | Jaccard ≥ 0.4 |
| 5 | Post-generation title | After Claude | `is_duplicate()` — pairwise Jaccard on title + case_name | Global | Jaccard ≥ 0.35 |
| 6 | Post-generation body | After Claude | `check_research_context(body, use_proper_nouns=False)` — "X v. Y" and labeled fields only | Global | Jaccard ≥ 0.4 |
| 7 | Review regen check | After regeneration | `is_duplicate()` with self-match filter | Global | Jaccard ≥ 0.35 |

### Case-Identity Dedup (`is_case_duplicate`)

Used during discovery to prevent the same lawsuit from entering the backlog. A candidate is a duplicate if ANY of:
- **Docket number** matches exactly
- **Case title** similarity > 75% (SequenceMatcher)
- **Same defendant** AND **same court** AND **filing date** within ±3 days

Does NOT block by company name alone — multiple articles about the same company (different cases) are allowed.

### DB-Level Uniqueness Safeguards

The `case_candidates` table has unique indexes as a safety net beyond application-level dedup:
- `(site_id, docket_number)` where docket_number is not null
- `(site_id, case_title, filing_date)` where filing_date is not null

### Intra-Batch Tracking
After each successful article, the pipeline updates:
- `existing_articles` list (for Jaccard comparisons)
- `avoidance_data["titles"]` (for Perplexity prompt)
- `avoidance_data["keywords"]` (for Jaccard)
- `avoidance_data["companies"]` (for company-name matching)

### Key Functions

| Function | Purpose |
|----------|---------|
| `_extract_keywords(text)` | Lowercase, remove stop words, return keyword set |
| `_extract_proper_noun_phrases(text)` | Regex-extract capitalized multi-word phrases (company names) |
| `_normalize_company(name)` | Strip legal suffixes (Inc, LLC, Corp, etc.) |
| `_jaccard(a, b)` | Set intersection / union similarity |
| `is_case_duplicate(candidate, pool)` | Case-identity dedup: docket, title similarity, defendant+court+date |
| `is_duplicate(title, case, existing)` | Full pairwise Jaccard on title + case_name |
| `check_research_context(text, existing, use_proper_nouns)` | Extract candidates, Jaccard-compare |
| `is_topic_covered(text, avoidance_data)` | 3-strategy entity-aware overlap check |
| `extract_company_from_case_name(case_name)` | Parse "X v. Y" → normalized company |
| `build_avoidance_data(articles, category)` | Build {titles, companies, keywords} dict |
| `load_existing_articles(db)` | Fetch all non-failed articles from DB |
| `discover_and_store_topics(cat, type, db, site_id)` | Perplexity discovery → parse → dedup → insert candidates |

---

## Retry Logic Summary

| Component | Max Retries | What Gets Retried | Avoidance Strengthened? |
|-----------|-------------|-------------------|----------------------|
| Perplexity research | 2 | Research call only | Yes — matched title added |
| Article generation | 3 | Full research → generate → check cycle | Yes — matched title/company added |
| Candidate processing | 3 | Candidate returned to queue (retry_count++) | N/A — different research each time |
| Image generation | 2 | Full prompt → generate → upload cycle | No |
| Fact-check regen | 2 | Perplexity research + Claude regeneration | N/A |

---

## Environment Variables

### Required (all jobs)
| Variable | Used By | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Generate, Review | Claude API key |
| `PERPLEXITY_API_KEY` | Generate, Review | Perplexity API key |
| `SUPABASE_URL` | All | Site database URL |
| `SUPABASE_KEY` | Generate, Review | Site database anon key |
| `OPENAI_API_KEY` | Images | GPT Image 1.5 API key |
| `SUPABASE_SERVICE_ROLE_KEY` | Images | For Storage uploads |

### Optional
| Variable | Used By | Description |
|----------|---------|-------------|
| `ADMIN_SUPABASE_URL` | Generate, Review | Admin tracking DB |
| `ADMIN_SUPABASE_KEY` | Generate, Review | Admin tracking DB key |
| `DEPLOY_HOOK_URL` | Images | Cloudflare Pages rebuild hook |

---

## Cost Estimation

| Model | Input | Output | Batch Discount |
|-------|-------|--------|---------------|
| Claude Haiku | $1.00/M tokens | $5.00/M tokens | 50% |
| Claude Sonnet | $3.00/M tokens | $15.00/M tokens | 50% |

Typical run (1 article): ~$0.01-0.03 for generation (including discovery) + ~$0.05-0.10 for review (Sonnet rewrite)

---

## Failure Modes & Recovery

| Failure | Auto-Recovery | Manual Action Needed |
|---------|--------------|---------------------|
| Discovery returns duplicate cases | Yes — filtered by `is_case_duplicate()` before insert | None |
| Backlog empty for category | Yes — falls back to direct Perplexity research | None |
| Candidate fails processing | Yes — returned to queue (up to 3 retries) | None |
| Perplexity returns duplicate research | Yes — retries with stronger avoidance (up to 3×) | None |
| Claude generates duplicate title | Yes — retries with fresh research (up to 3×) | None |
| Claude response truncated | Yes — detected via `stop_reason`, raises clear error | None (max_tokens=16384) |
| Article body references known case | Yes — retries with fresh research (up to 3×) | None |
| Fact-check fails | Yes — regenerates article (up to 2×) | None |
| ALL articles fail in generation | Job exits 1; review does NOT run | Check logs, may need manual run |
| Image generation fails | Retries 2×; blocks deploy if still fails | Check OpenAI API status, re-run |
| Perplexity API down | Job fails | Wait and re-run |
| Claude API rate limited | Built-in exponential backoff (3 retries) | None unless persistent |
