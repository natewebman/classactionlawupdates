#!/usr/bin/env tsx
/**
 * Backfill script – generates hero images for all articles missing one.
 *
 * Usage:
 *   npx tsx scripts/generate-missing-images.ts
 *
 * Required env vars (in .env):
 *   PUBLIC_SUPABASE_URL, PUBLIC_SITE_KEY,
 *   SUPABASE_SERVICE_ROLE_KEY, ANTHROPIC_API_KEY, REPLICATE_API_TOKEN
 */

import "dotenv/config";
import { createClient } from "@supabase/supabase-js";
import { generateArticleImage } from "./lib/generate-image.js";

// ---------------------------------------------------------------------------
// Pre-flight checks
// ---------------------------------------------------------------------------

const REQUIRED_VARS = [
  "PUBLIC_SUPABASE_URL",
  "SUPABASE_SERVICE_ROLE_KEY",
  "PUBLIC_SITE_KEY",
  "ANTHROPIC_API_KEY",
  "REPLICATE_API_TOKEN",
];

for (const key of REQUIRED_VARS) {
  if (!process.env[key]) {
    console.warn(`⚠️  Missing env var: ${key} — skipping image generation.`);
    process.exit(0);
  }
}

// ---------------------------------------------------------------------------
// Rate-limiter (~1.5 s between calls)
// ---------------------------------------------------------------------------

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  const supabaseUrl = process.env.PUBLIC_SUPABASE_URL!;
  const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY!;
  const siteKey = process.env.PUBLIC_SITE_KEY!;
  const supabase = createClient(supabaseUrl, serviceKey);

  // Resolve site_id
  const { data: site, error: siteErr } = await supabase
    .from("sites")
    .select("id")
    .eq("site_key", siteKey)
    .single();

  if (siteErr || !site) {
    console.error("❌ Could not resolve site_id for key:", siteKey);
    process.exit(1);
  }

  // Fetch all published articles that have no hero_image
  const { data: articles, error: fetchErr } = await supabase
    .from("articles")
    .select("id, title, slug, category, meta_description")
    .eq("site_id", site.id)
    .eq("content_stage", "published")
    .is("hero_image", null)
    .order("published_at", { ascending: false });

  if (fetchErr) {
    console.error("❌ Error fetching articles:", fetchErr.message);
    process.exit(1);
  }

  if (!articles || articles.length === 0) {
    console.log("✅ All articles already have hero images. Nothing to do.");
    return;
  }

  console.log(`\n🖼️  Found ${articles.length} article(s) without hero images.\n`);

  let success = 0;
  let failed = 0;

  for (let i = 0; i < articles.length; i++) {
    const article = articles[i];
    const label = `[${i + 1}/${articles.length}]`;

    try {
      console.log(`${label} Processing: "${article.title}"...`);

      const result = await generateArticleImage(article);

      console.log(`${label} ✅ Done → ${result.filename}`);
      success++;
    } catch (err: any) {
      console.error(`${label} ❌ Failed: ${err.message}`);
      failed++;
    }

    // Rate-limit between calls (skip after the last one)
    if (i < articles.length - 1) {
      await sleep(1500);
    }
  }

  console.log(
    `\n📊 Complete: ${success} succeeded, ${failed} failed out of ${articles.length} total.\n`
  );
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
