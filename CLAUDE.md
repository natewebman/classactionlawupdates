# classactionlawupdates.com

Class action lawsuit news and settlement tracker. Auto-generates, fact-checks, and publishes articles about active lawsuits and open settlements.

## Quick Facts
- **Domain:** classactionlawupdates.com
- **Site Key:** `classactionlawupdates`
- **Supabase Project:** `dwyomiuzuwkmfwfwxcfx`
- **Hosting:** Cloudflare Pages (static + SSR hybrid)
- **Framework:** Astro 5.7 + Tailwind CSS 3.4
- **Content AI:** Claude (Anthropic), Perplexity, GPT Image 1.5 (OpenAI)
- **Ads:** Google AdSense (`ca-pub-7899412908629784`)

## Local Development
```bash
npm install
cp .env.example .env   # Fill in Supabase keys
npm run dev             # http://localhost:4321
npm run validate:seo    # Run 67-check SEO validation
```

## Architecture

### Multi-tenant data model
All data is scoped by `site_id`. The `sites` table maps `site_key` → `site_id`. At build time, `PUBLIC_SITE_KEY` is used to resolve the site and filter queries.

### Supabase tables used
- `sites` — site registry (site_key, id, deploy_hook_url)
- `articles` — all content (news + settlements), filtered by `content_stage` and `news_type`
- `case_candidates` — discovery backlog of potential cases to cover (status: discovered → processing → processed/failed/duplicate)
- `subscribers` — email signups with unsubscribe support (upsert on site_id + email, status: active/unsubscribed)
- `submissions` — form submissions (attorney portal, etc.)

### Content stages
`draft` → `fact_checked` → `fact_updated` → `published`

Articles that fail fact-checking are regenerated (up to 2 retries) before being marked `failed`.

### Rendering model
- **Prerendered** (static at build): homepage, category hubs, about, editorial-policy, static pages
- **SSR** (`prerender = false`): settlement details, news details, news index, open settlements, deadlines ending soon, settlement database, brand pages, state pages, sitemap-articles.xml, content-stats API
- **Hero image fallback**: settlement and news detail pages show a gradient placeholder when `hero_image` is null
- **Hero image optimization**: hero `<img>` tags include `width="1536" height="1024"` and `fetchpriority="high"` for LCP/CLS

### Ads
- Google AdSense script loaded asynchronously in `BaseLayout.astro` with `dns-prefetch` and `preconnect` hints
- `AdSlot.astro` — reusable component with `slotId`, `format`, and `className` props. Uses `is:inline` script to ensure each ad slot gets its own `adsbygoogle.push()` call (Astro deduplicates bundled scripts)
- No ad placements are wired up yet — only the infrastructure is in place

## Page Types

| Route | Type | Description |
|-------|------|-------------|
| `/` | Static | Homepage with top settlements + latest news |
| `/settlements` | Static | All settlements with category filter tabs |
| `/settlements/[slug]` | SSR | Settlement detail with data block, FAQ, similar settlements |
| `/news` | SSR | News index |
| `/news/[slug]` | SSR | News article detail |
| `/category/[slug]` | Static | Category hub — intro text, largest settlements table, article grid, cross-links |
| `/open-class-action-settlements` | SSR | Open settlements with active claims, sorted by nearest deadline |
| `/claim-deadlines-ending-soon` | SSR | Settlements with claim deadlines within 14 days |
| `/class-action-settlements-database` | SSR | Sortable/filterable table of all settlements |
| `/brand/[slug]` | SSR | All settlements/news for a specific company |
| `/state/[slug]` | SSR | Settlements by US state location |
| `/about` | Static | About page with Organization schema |
| `/editorial-policy` | Static | Editorial policy |
| `/unsubscribe` | Static | Unsubscribe from email alerts |
| `/api/content-stats` | SSR | JSON endpoint with article counts and category breakdown |
| `/api/subscribe` | SSR | POST endpoint for email subscriptions (used by StickyBanner) |
| `/sitemap-articles.xml` | SSR | Dynamic XML sitemap for all SSR pages |

## Key Library Modules

### `src/lib/supabase.ts`
Supabase client and data fetching. Key query functions:
- `getArticles()` — published articles, filterable by category/newsType
- `getArticleBySlug()` — single article lookup
- `getOpenSettlements()` — settlements with active status or future claim deadlines
- `getExpiringSettlements()` — settlements with claim deadlines within the next 14 days (excludes closed)
- `getAllSettlements()` — all published settlements (for brand/state extraction)
- `addSubscriber()` — upsert subscriber (re-subscribing resets status to active)
- `unsubscribeEmail()` — mark subscriber as unsubscribed with timestamp

### `src/lib/structured-data.ts`
JSON-LD schema generators:
- `getOrganization()` — Organization (includes logo, sameAs, address)
- `getAuthor()` — Person (Editorial Team)
- `getBreadcrumbList()` — BreadcrumbList from {name, url} array
- `getWebSite()` — WebSite (homepage only)
- `getNewsArticle()` — NewsArticle with articleSection
- `getSettlementArticle()` — Article with additionalProperty (case data)
- `getFAQPage()` — FAQPage from question/answer pairs
- `getItemList()` — ItemList with positioned entries
- `getCollectionPage()` — CollectionPage with optional mainEntity

### `src/lib/seo-helpers.ts`
- `settlementTitle()` / `newsTitle()` — optimized title tag generation
- `formatDate()` — returns {display, iso} for `<time>` elements
- `addHeadingIds()` — injects id attributes on H2s for anchor linking
- `extractHeadings()` — extracts H2 headings for table of contents

### `src/lib/settlement-amount.ts`
- `parseSettlementAmount()` — parses "$4.2 million", "$500K", "$1.5 billion" etc. to numeric values
- `sortBySettlementAmount()` — sorts articles by parsed amount descending

### `src/lib/brand-state-extraction.ts`
- `extractBrand()` / `extractBrands()` — extracts company names from case_name ("v. CompanyName") or title
- `extractState()` / `extractStates()` — matches US states from article location fields
- `US_STATES` — complete slug→name map of all 50 states + DC

## Settlement Closed State
Settlements are automatically marked "Closed" at render time when `claim_deadline` exists and is in the past — no DB changes needed. This affects:
- **CaseStatusTracker** — 5th "Closed" step with red styling and lock icon; all previous steps shown as completed (blue)
- **SettlementDataBlock** — Status badge overrides to red "Closed"; claim CTA swaps to emerald "Sign Up for Alerts"
- **Settlement detail page** — Mobile and inline CTAs swap to signup buttons; JSON-LD `case_status` overrides to "closed"

## SEO Infrastructure

### Structured data on every page
- Organization JSON-LD rendered in BaseLayout on all pages
- BreadcrumbList JSON-LD when `breadcrumbs` prop is provided to BaseLayout

### Sitemaps
- `/sitemap-index.xml` — @astrojs/sitemap for prerendered pages (homepage priority 1.0, category hubs 0.8)
- `/sitemap-articles.xml` — custom SSR endpoint for articles (0.6), brand/state/open-settlements/deadlines/database pages (0.8)
- Both referenced in `public/robots.txt`

### Internal linking
Settlement detail pages link to: category hub, brand page, state page, open settlements, similar settlements, cross-category links. Category hub pages link to: all their articles, largest settlements, latest news, cross-category links, open settlements. Open settlements page links to: deadlines ending soon, settlement database. The footer includes links to open settlements, settlement database, and all categories.

### Validation
`npm run validate:seo` runs `scripts/validate-seo.mjs` — 67 static checks across all page templates verifying schemas, internal links, components, sitemap configuration, and hero image fallbacks.

## Content Pipeline

Runs via GitHub Actions. Two workflows:

- **Generate Articles** (`generate-articles.yml`) — generates news + settlement articles, daily at 15:00 UTC (9 AM Central) + manual
- **Update Case Status** (`update-case-status.yml`) — weekly on Sundays at 2 PM UTC + manual. Checks all non-closed published articles against Perplexity for source-supported status changes. Updates only settlement metadata fields (`case_status`, `settlement_amount`, `claim_deadline`, `claim_url`, `settlement_website`, `claims_administrator`, `potential_reward`, `location`). Never touches `title`, `slug`, `content`, or `content_stage`. Uses batch size 5 by default, with single-case fallback if batch parsing fails. Supports dry-run mode.

The `CONTENT_TYPE` input controls what gets generated: `mixed` (default, roughly half news/half settlements), `news`, or `settlement`. Categories are auto-balanced to keep hub pages even. Default output is 1 article per run.

3-job pipeline:
1. **generate** — Discovery phase (Perplexity finds ~50 cases, stores in `case_candidates` backlog) → selects oldest candidate → Perplexity deep-researches → Claude Haiku drafts article (`scripts/generate_articles.py`)
2. **review** — fact-check → fact-update → human-tone rewrite (Claude Sonnet). Only runs on generate success.
3. **images** — Claude Haiku writes prompt → GPT Image 1.5 generates photorealistic hero image (2 retry attempts per image, strict gate blocks deploy if ANY image fails) → Cloudflare Pages rebuild via deploy hook

### Case Discovery & Backlog
Each run discovers ~50 candidate cases per category/content_type via Perplexity, deduplicates them against existing articles and candidates using `is_case_duplicate()`, and stores new ones in the `case_candidates` table. A per-category/content_type backlog cap (100) prevents any single category from dominating. Articles are generated from the oldest unprocessed candidate; if the backlog is empty, falls back to direct research.

### Deduplication
Hybrid approach: category-scoped soft checks for discovery relevance, global hard checks for case identity.

1. **Case-identity dedup** (`is_case_duplicate()`) — blocks duplicate cases globally across all categories using docket number match, 75% case title similarity (SequenceMatcher), or defendant + court + filing date (±3 days). Does NOT block by company name alone.
2. **Pre-research avoidance** — `build_avoidance_data()` extracts company names + titles from DB, sent to Perplexity prompt to avoid known topics. Category-scoped (last 50 in same category). Multi-retry (up to 2 retries with progressively stronger avoidance lists).
3. **Post-research check** — `check_research_context()` extracts "X v. Y" patterns and labeled fields from Perplexity output. Category-scoped (same-category articles only) to prevent cross-category false positives.
4. **Post-generation check** — `is_duplicate()` compares generated title + case_name against all existing globally (Jaccard >= 0.35). Intra-batch tracking ensures articles within the same run don't duplicate each other.

## HTTP Headers (`public/_headers`)
- **Security**: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy` (camera/mic/geo denied), HSTS (2 year max-age, includeSubDomains, preload)
- **AI crawling**: `X-Robots-Tag: ai-train=no, ai-input=yes`
- **Cache**: `/_astro/*` immutable (1 year) — files have content hashes. `/favicon.svg` 1 week.
- **Not added yet**: CSP (needs audit of inline scripts + external origins), COOP (may break AdSense popups), `/images/*` cache

## Deploy
Push to `main` triggers Cloudflare Pages build. The pipeline also triggers a rebuild via `DEPLOY_HOOK_URL` after publishing content.

## Environment Variables

### Site (Astro build)
- `PUBLIC_SUPABASE_URL` — Supabase project URL
- `PUBLIC_SUPABASE_ANON_KEY` — Supabase anon key
- `PUBLIC_SITE_KEY` — Site identifier (`classactionlawupdates`)

### Scripts (GitHub Actions secrets)
- `ANTHROPIC_API_KEY` — Claude API key
- `PERPLEXITY_API_KEY` — Perplexity API key
- `OPENAI_API_KEY` — OpenAI API key (GPT Image 1.5)
- `SUPABASE_URL` — Supabase project URL
- `SUPABASE_KEY` — Supabase anon key
- `SUPABASE_SERVICE_ROLE_KEY` — Supabase service role key (storage uploads)
- `ADMIN_SUPABASE_URL` — Admin tracking DB (optional)
- `ADMIN_SUPABASE_KEY` — Admin tracking DB key (optional)
- `DEPLOY_HOOK_URL` — Cloudflare Pages deploy hook
