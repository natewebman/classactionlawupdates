# classactionlawupdates.com

Class action lawsuit news and settlement tracker. Auto-generates, fact-checks, and publishes articles about active lawsuits and open settlements.

## Quick Facts
- **Domain:** classactionlawupdates.com
- **Site Key:** `classactionlawupdates`
- **Supabase Project:** `dwyomiuzuwkmfwfwxcfx`
- **Hosting:** Cloudflare Pages (static + SSR hybrid)
- **Framework:** Astro 5.7 + Tailwind CSS 3.4
- **Content AI:** Claude (Anthropic), Perplexity, GPT Image 1.5 (OpenAI)

## Local Development
```bash
npm install
cp .env.example .env   # Fill in Supabase keys
npm run dev             # http://localhost:4321
npm run validate:seo    # Run 58-check SEO validation
```

## Architecture

### Multi-tenant data model
All data is scoped by `site_id`. The `sites` table maps `site_key` → `site_id`. At build time, `PUBLIC_SITE_KEY` is used to resolve the site and filter queries.

### Supabase tables used
- `sites` — site registry (site_key, id, deploy_hook_url)
- `articles` — all content (news + settlements), filtered by `content_stage` and `news_type`
- `subscribers` — email signups with unsubscribe support (upsert on site_id + email, status: active/unsubscribed)
- `submissions` — form submissions (attorney portal, etc.)

### Content stages
`draft` → `fact_checked` → `fact_updated` → `published`

Articles that fail fact-checking are regenerated (up to 2 retries) before being marked `failed`.

### Rendering model
- **Prerendered** (static at build): homepage, category hubs, about, editorial-policy, static pages
- **SSR** (`prerender = false`): settlement details, news details, news index, open settlements, brand pages, state pages, sitemap-articles.xml, content-stats API
- **Hero image fallback**: settlement and news detail pages show a gradient placeholder when `hero_image` is null

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
| `/brand/[slug]` | SSR | All settlements/news for a specific company |
| `/state/[slug]` | SSR | Settlements by US state location |
| `/about` | Static | About page with Organization schema |
| `/editorial-policy` | Static | Editorial policy |
| `/unsubscribe` | Static | Unsubscribe from email alerts |
| `/api/content-stats` | SSR | JSON endpoint with article counts and category breakdown |
| `/sitemap-articles.xml` | SSR | Dynamic XML sitemap for all SSR pages |

## Key Library Modules

### `src/lib/supabase.ts`
Supabase client and data fetching. Key query functions:
- `getArticles()` — published articles, filterable by category/newsType
- `getArticleBySlug()` — single article lookup
- `getOpenSettlements()` — settlements with active status or future claim deadlines
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

## SEO Infrastructure

### Structured data on every page
- Organization JSON-LD rendered in BaseLayout on all pages
- BreadcrumbList JSON-LD when `breadcrumbs` prop is provided to BaseLayout

### Sitemaps
- `/sitemap-index.xml` — @astrojs/sitemap for prerendered pages (homepage priority 1.0, category hubs 0.8)
- `/sitemap-articles.xml` — custom SSR endpoint for articles (0.6), brand/state/open-settlements pages (0.8)
- Both referenced in `public/robots.txt`

### Internal linking
Settlement detail pages link to: category hub, brand page, state page, open settlements, similar settlements, cross-category links. Category hub pages link to: all their articles, largest settlements, latest news, cross-category links, open settlements. The footer includes links to open settlements and all categories.

### Validation
`npm run validate:seo` runs `scripts/validate-seo.mjs` — 58 static checks across all page templates verifying schemas, internal links, components, sitemap configuration, and hero image fallbacks.

## Content Pipeline

Runs via GitHub Actions. Single unified workflow:

- **Generate Articles** (`generate-articles.yml`) — generates news + settlement articles, daily at 12:00 UTC + manual

The `CONTENT_TYPE` input controls what gets generated: `mixed` (default, roughly half news/half settlements), `news`, or `settlement`. Categories are auto-balanced to keep hub pages even.

3-job pipeline:
1. **generate** — Perplexity researches → Claude Haiku drafts article (`scripts/generate_articles.py`)
2. **review** — fact-check → fact-update → human-tone rewrite (Claude Sonnet)
3. **images** — Claude Haiku writes prompt → GPT Image 1.5 generates photorealistic hero image (2 retry attempts per image, strict gate blocks deploy if ANY image fails) → Cloudflare Pages rebuild via deploy hook

### Deduplication
Keyword-based Jaccard similarity (`scripts/lib/dedup.py`). Rejects articles with title or case name >= 40% overlap with existing articles.

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
