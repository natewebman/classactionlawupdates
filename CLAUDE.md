# classactionlawupdates.com

This is a **live site** in the Website Factory system. For full architecture, see `github.com/natewebman/website-factory/CLAUDE.md`.

## Quick Facts
- **Blueprint:** news
- **Domain:** classactionlawupdates.com
- **Site Key:** `classactionlawupdates`
- **Supabase Project:** `dwyomiuzuwkmfwfwxcfx`
- **Hosting:** Cloudflare Pages
- **Framework:** Astro (static site generation)

## Local Development
```bash
npm install
cp .env.example .env   # Then fill in your Supabase keys
npm run dev             # Starts at http://localhost:4321
```

## Deploy
Connected to Cloudflare Pages via GitHub. Push to `main` triggers a build.
A deploy hook URL is stored in the `sites` table for n8n to trigger rebuilds after content changes.

## Content Flow
1. Content is generated/approved in Supabase `articles` table
2. On `content_stage = 'published'`, n8n triggers the deploy hook
3. Cloudflare Pages rebuilds, Astro fetches latest published content from Supabase
4. Static HTML is served from Cloudflare's CDN
