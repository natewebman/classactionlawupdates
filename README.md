# classactionlawupdates.com

A class action lawsuit news and settlement tracker that automatically generates, fact-checks, and publishes articles about active lawsuits and open settlements consumers can claim.

## Tech Stack

- **Framework:** [Astro](https://astro.build/) 5.7 (static site generation)
- **Styling:** Tailwind CSS 3.4
- **Database:** [Supabase](https://supabase.com/) (PostgreSQL + Storage)
- **Hosting:** Cloudflare Pages
- **Content AI:** Claude (Anthropic), Perplexity, DALL-E 3 (OpenAI)

## Local Development

```bash
npm install
cp .env.example .env   # Fill in your Supabase keys
npm run dev             # http://localhost:4321
```

## Project Structure

```
src/
  pages/
    index.astro              # Homepage — top settlements + latest news
    settlements/             # Settlement listing + [slug] detail pages
    news/                    # News listing + [slug] detail pages
    category/[slug].astro    # Category filter page
    attorney-portal.astro    # Attorney portal
    subscribe.astro          # Newsletter subscription
    login.astro / signup.astro
    privacy-policy / terms / disclaimer
  components/
    Header.astro             # Site navigation
    Footer.astro             # Footer links
    Hero.astro               # Homepage hero banner
    ArticleCard.astro        # Article preview card
    SignupForm.astro         # Email signup form
    ClaimDetailsSidebar.astro # Settlement claim info sidebar
    SettlementSidebar.astro  # Settlement details sidebar
    CaseStatusTracker.astro  # Visual case status tracker
    RelatedArticles.astro    # Related articles widget
  lib/
    supabase.ts              # Supabase client + data fetching helpers
scripts/
  generate_content.py        # General article generation
  generate_settlements.py    # Settlement-focused generation
  review_pipeline.py         # Fact-check, update, rewrite pipeline
  generate-missing-images.ts # Hero image generation (DALL-E 3)
  prompts/                   # LLM prompt templates
  lib/                       # Shared TypeScript utilities
```

## Content Pipeline

Articles are generated and published via GitHub Actions in a 4-step pipeline:

```
1. GENERATE        Perplexity researches real lawsuits → Claude Haiku drafts article
2. REVIEW          Fact-check → Fact-update → Human-tone rewrite (Claude Sonnet)
3. IMAGES          Claude Haiku writes image prompt → DALL-E 3 generates hero image
4. DEPLOY          Cloudflare Pages rebuild via deploy hook
```

### Content stages

`draft` → `fact_checked` → `fact_updated` → `published`

Articles that fail fact-checking are regenerated (up to 2 retries) before being marked `failed`.

### Running the pipeline

The pipeline runs via two GitHub Actions workflows:

- **Generate Content** (`generate-content.yml`) — general news articles, runs daily at 12:00 UTC + manual trigger
- **Generate Settlements** (`generate-settlements.yml`) — settlement-specific articles, manual trigger only

Both accept inputs for article count, model, categories, and optional topic URL/idea.

## Environment Variables

### Site (Astro build)

| Variable | Description |
|---|---|
| `PUBLIC_SUPABASE_URL` | Supabase project URL |
| `PUBLIC_SUPABASE_ANON_KEY` | Supabase anonymous key (client reads) |
| `PUBLIC_SITE_KEY` | Site identifier (`classactionlawupdates`) |

### Scripts (GitHub Actions)

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key |
| `PERPLEXITY_API_KEY` | Perplexity API key |
| `OPENAI_API_KEY` | OpenAI API key (DALL-E 3) |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase anon key |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key (storage uploads) |
| `ADMIN_SUPABASE_URL` | Admin tracking database URL (optional) |
| `ADMIN_SUPABASE_KEY` | Admin tracking database key (optional) |
| `DEPLOY_HOOK_URL` | Cloudflare Pages deploy hook URL |

## Content Categories

- Stocks & Securities
- Personal Injury
- Product Recalls
- Drugs & Pharmacy
- Financial
- Online Privacy

## Deploy

Push to `main` triggers a Cloudflare Pages build. The GitHub Actions pipeline also triggers a rebuild via deploy hook after publishing new content.
