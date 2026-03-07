#!/usr/bin/env node
/**
 * SEO Validation Script
 *
 * Checks the Astro source files for required SEO elements:
 * 1. Article pages include data-article-id attribute
 * 2. Settlement pages include SettlementDataBlock component
 * 3. Article pages emit Article/NewsArticle JSON-LD
 * 4. No duplicate primary schema types per page
 *
 * Usage: node scripts/validate-seo.mjs
 * Exit code 0 = pass, 1 = fail
 */

import { readFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = resolve(__dirname, '..');

let errors = 0;
let warnings = 0;
let checks = 0;

function pass(msg) {
  checks++;
  console.log(`  \x1b[32m✓\x1b[0m ${msg}`);
}

function fail(msg) {
  checks++;
  errors++;
  console.log(`  \x1b[31m✗\x1b[0m ${msg}`);
}

function warn(msg) {
  warnings++;
  console.log(`  \x1b[33m!\x1b[0m ${msg}`);
}

// ── Helpers ──

function readSource(relativePath) {
  return readFileSync(resolve(root, relativePath), 'utf-8');
}

// ── Check Settlement Detail Page ──

console.log('\n📄 Settlement Detail Page (src/pages/settlements/[slug].astro)');

const settlementPage = readSource('src/pages/settlements/[slug].astro');

if (settlementPage.includes('data-article-id')) {
  pass('Has data-article-id attribute');
} else {
  fail('Missing data-article-id attribute on <article> element');
}

if (settlementPage.includes('SettlementDataBlock')) {
  pass('Includes SettlementDataBlock component');
} else {
  fail('Missing SettlementDataBlock component');
}

if (settlementPage.includes('getSettlementArticle')) {
  pass('Uses getSettlementArticle() for Article JSON-LD');
} else {
  fail('Missing Article JSON-LD (getSettlementArticle)');
}

if (settlementPage.includes('SettlementFAQ')) {
  pass('Includes SettlementFAQ component (FAQPage JSON-LD)');
} else {
  warn('Missing SettlementFAQ component');
}

// Check for conflicting schema types (should not use BOTH settlement and news schemas)
const hasSettlementSchema = settlementPage.includes('getSettlementArticle');
const hasNewsSchemaOnSettlement = settlementPage.includes('getNewsArticle');
if (hasSettlementSchema && !hasNewsSchemaOnSettlement) {
  pass('No conflicting Article schema types');
} else if (hasSettlementSchema && hasNewsSchemaOnSettlement) {
  fail('Conflicting Article + NewsArticle schemas on same page');
} else {
  warn('Unexpected schema configuration');
}

// ── Check News Detail Page ──

console.log('\n📄 News Detail Page (src/pages/news/[slug].astro)');

const newsPage = readSource('src/pages/news/[slug].astro');

if (newsPage.includes('data-article-id')) {
  pass('Has data-article-id attribute');
} else {
  fail('Missing data-article-id attribute on <article> element');
}

if (newsPage.includes('getNewsArticle')) {
  pass('Uses getNewsArticle() for NewsArticle JSON-LD');
} else {
  fail('Missing NewsArticle JSON-LD (getNewsArticle)');
}

const hasNewsSchema = newsPage.includes('getNewsArticle');
const hasSettlementSchemaOnNews = newsPage.includes('getSettlementArticle');
if (hasNewsSchema && !hasSettlementSchemaOnNews) {
  pass('No conflicting Article schema types');
} else if (hasNewsSchema && hasSettlementSchemaOnNews) {
  fail('Conflicting NewsArticle + Article schemas on same page');
} else {
  warn('Unexpected schema configuration');
}

// ── Check Structured Data Module ──

console.log('\n📄 Structured Data Module (src/lib/structured-data.ts)');

const structuredData = readSource('src/lib/structured-data.ts');

if (structuredData.includes('articleSection')) {
  pass('Article schemas include articleSection');
} else {
  fail('Missing articleSection in Article schemas');
}

if (structuredData.includes("'logo'")) {
  pass('Organization schema includes logo');
} else {
  fail('Missing logo in Organization schema');
}

if (structuredData.includes("'sameAs'")) {
  pass('Organization schema includes sameAs');
} else {
  warn('Missing sameAs in Organization schema');
}

if (structuredData.includes('FAQPage')) {
  pass('FAQPage schema function exists');
} else {
  fail('Missing FAQPage schema function');
}

// ── Check BaseLayout ──

console.log('\n📄 BaseLayout (src/layouts/BaseLayout.astro)');

const layout = readSource('src/layouts/BaseLayout.astro');

if (layout.includes('getOrganization')) {
  pass('Includes site-wide Organization schema');
} else {
  fail('Missing site-wide Organization schema in BaseLayout');
}

if (layout.includes('getBreadcrumbList')) {
  pass('Includes BreadcrumbList schema support');
} else {
  fail('Missing BreadcrumbList schema support');
}

// ── Check SettlementDataBlock ──

console.log('\n📄 SettlementDataBlock (src/components/SettlementDataBlock.astro)');

const dataBlock = readSource('src/components/SettlementDataBlock.astro');

if (dataBlock.includes('deadlineBadge')) {
  pass('Has deadline proximity logic');
} else {
  fail('Missing deadline proximity logic');
}

if (dataBlock.includes('Claim Deadline Passed')) {
  pass('Shows "Claim Deadline Passed" badge');
} else {
  fail('Missing "Claim Deadline Passed" badge');
}

if (dataBlock.includes('Claim Deadline Approaching')) {
  pass('Shows "Claim Deadline Approaching" badge');
} else {
  fail('Missing "Claim Deadline Approaching" badge');
}

// ── Check Sitemap & Robots ──

console.log('\n📄 Sitemap & Robots');

try {
  const robots = readSource('public/robots.txt');
  if (robots.includes('Sitemap:')) {
    pass('robots.txt references sitemap');
  } else {
    fail('robots.txt missing Sitemap directive');
  }
} catch {
  fail('robots.txt not found');
}

try {
  readSource('src/pages/sitemap-articles.xml.ts');
  pass('Dynamic article sitemap exists');
} catch {
  fail('Dynamic article sitemap missing');
}

// ── Check Content Stats API ──

console.log('\n📄 Content Stats API');

try {
  const contentStats = readSource('src/pages/api/content-stats.ts');
  if (contentStats.includes('total_articles')) {
    pass('/api/content-stats endpoint exists');
  } else {
    fail('/api/content-stats endpoint missing expected fields');
  }
} catch {
  fail('/api/content-stats endpoint not found');
}

// ── Hero Image Fallback ──

console.log('\n🖼️  Hero Image Fallback');

// Settlement page must have an else branch for hero image (gradient placeholder)
if (settlementPage.includes('heroImage ?') || settlementPage.includes('meta.heroImage ?')) {
  pass('Settlement page has hero image if/else (not conditional skip)');
} else {
  fail('Settlement page conditionally skips hero image — needs fallback placeholder');
}

if (settlementPage.includes('bg-gradient-to-br from-navy')) {
  pass('Settlement page has gradient placeholder for missing images');
} else {
  fail('Settlement page missing gradient placeholder fallback');
}

// News page must have an else branch for hero image
if (newsPage.includes('heroImage ?')) {
  pass('News page has hero image if/else (not conditional skip)');
} else {
  fail('News page conditionally skips hero image — needs fallback placeholder');
}

if (newsPage.includes('bg-gradient-to-br from-navy')) {
  pass('News page has gradient placeholder for missing images');
} else {
  fail('News page missing gradient placeholder fallback');
}

// ── Crawl Structure & Internal Linking ──

console.log('\n🔗 Crawl Structure & Internal Linking');

// Settlement pages must link back to their category
if (settlementPage.includes('/category/')) {
  pass('Settlement pages link to category pages');
} else {
  fail('Settlement pages missing link to category page');
}

// Settlement pages include SimilarSettlements (prevents orphan pages)
if (settlementPage.includes('SimilarSettlements')) {
  pass('Settlement pages include SimilarSettlements cross-links');
} else {
  fail('Settlement pages missing SimilarSettlements component');
}

// Settlement pages include CategoryCrossLinks
if (settlementPage.includes('CategoryCrossLinks')) {
  pass('Settlement pages include CategoryCrossLinks');
} else {
  fail('Settlement pages missing CategoryCrossLinks component');
}

// News pages include CategoryCrossLinks
if (newsPage.includes('CategoryCrossLinks')) {
  pass('News pages include CategoryCrossLinks');
} else {
  fail('News pages missing CategoryCrossLinks component');
}

// Category hub page checks
console.log('\n📄 Category Hub Page (src/pages/category/[slug].astro)');

const categoryPage = readSource('src/pages/category/[slug].astro');

if (categoryPage.includes('ArticleCard')) {
  pass('Category pages link to multiple articles via ArticleCard');
} else {
  fail('Category pages missing ArticleCard components');
}

if (categoryPage.includes('CategoryIntro')) {
  pass('Category pages include CategoryIntro section');
} else {
  fail('Category pages missing CategoryIntro component');
}

if (categoryPage.includes('LargestSettlements')) {
  pass('Category pages include LargestSettlements table');
} else {
  fail('Category pages missing LargestSettlements component');
}

if (categoryPage.includes('getCollectionPage')) {
  pass('Category pages include CollectionPage JSON-LD');
} else {
  fail('Category pages missing CollectionPage JSON-LD');
}

if (categoryPage.includes('CategoryCrossLinks')) {
  pass('Category pages include CategoryCrossLinks');
} else {
  fail('Category pages missing CategoryCrossLinks component');
}

if (categoryPage.includes('mainEntity')) {
  pass('CollectionPage schema includes mainEntity references');
} else {
  warn('CollectionPage schema missing mainEntity references');
}

// Settlement amount parser
console.log('\n📄 Settlement Amount Parser (src/lib/settlement-amount.ts)');

try {
  const amountParser = readSource('src/lib/settlement-amount.ts');
  if (amountParser.includes('parseSettlementAmount')) {
    pass('parseSettlementAmount function exists');
  } else {
    fail('parseSettlementAmount function missing');
  }
  if (amountParser.includes('sortBySettlementAmount')) {
    pass('sortBySettlementAmount function exists');
  } else {
    fail('sortBySettlementAmount function missing');
  }
} catch {
  fail('Settlement amount parser module not found');
}

// Sitemap priority checks
console.log('\n📄 Sitemap Priority Configuration');

const sitemapArticles = readSource('src/pages/sitemap-articles.xml.ts');
if (sitemapArticles.includes('0.6')) {
  pass('Article sitemap uses correct priority (0.6)');
} else {
  warn('Article sitemap priority may need adjustment');
}

try {
  const astroConfig = readSource('astro.config.mjs');
  if (astroConfig.includes('/category/')) {
    pass('Astro sitemap config includes category page rules');
  } else {
    warn('Astro sitemap config missing category page priority rules');
  }
} catch {
  warn('Could not read astro.config.mjs');
}

// ── Phase 7: High-Intent Landing Pages ──

console.log('\n🎯 High-Intent Landing Pages');

// Open Settlements page
try {
  const openPage = readSource('src/pages/open-class-action-settlements.astro');
  if (openPage.includes('getOpenSettlements')) {
    pass('Open Settlements page uses getOpenSettlements query');
  } else {
    fail('Open Settlements page missing getOpenSettlements query');
  }
  if (openPage.includes('getItemList')) {
    pass('Open Settlements page includes ItemList schema');
  } else {
    fail('Open Settlements page missing ItemList schema');
  }
  if (openPage.includes('CategoryCrossLinks')) {
    pass('Open Settlements page includes CategoryCrossLinks');
  } else {
    warn('Open Settlements page missing CategoryCrossLinks');
  }
} catch {
  fail('Open Settlements page not found (src/pages/open-class-action-settlements.astro)');
}

// Brand pages
try {
  const brandPage = readSource('src/pages/brand/[slug].astro');
  if (brandPage.includes('extractBrands')) {
    pass('Brand pages use extractBrands for data');
  } else {
    fail('Brand pages missing extractBrands logic');
  }
  if (brandPage.includes('getCollectionPage')) {
    pass('Brand pages include CollectionPage schema');
  } else {
    fail('Brand pages missing CollectionPage schema');
  }
  if (brandPage.includes('ArticleCard')) {
    pass('Brand pages display articles via ArticleCard');
  } else {
    fail('Brand pages missing ArticleCard components');
  }
} catch {
  fail('Brand page template not found (src/pages/brand/[slug].astro)');
}

// State pages
try {
  const statePage = readSource('src/pages/state/[slug].astro');
  if (statePage.includes('extractStates') || statePage.includes('US_STATES')) {
    pass('State pages use state extraction for data');
  } else {
    fail('State pages missing state extraction logic');
  }
  if (statePage.includes('getCollectionPage')) {
    pass('State pages include CollectionPage schema');
  } else {
    fail('State pages missing CollectionPage schema');
  }
  if (statePage.includes('ArticleCard')) {
    pass('State pages display articles via ArticleCard');
  } else {
    fail('State pages missing ArticleCard components');
  }
} catch {
  fail('State page template not found (src/pages/state/[slug].astro)');
}

// Brand/state extraction module
try {
  const extraction = readSource('src/lib/brand-state-extraction.ts');
  if (extraction.includes('extractBrand') && extraction.includes('extractBrands')) {
    pass('Brand extraction functions exist');
  } else {
    fail('Brand extraction functions missing');
  }
  if (extraction.includes('extractState') && extraction.includes('extractStates')) {
    pass('State extraction functions exist');
  } else {
    fail('State extraction functions missing');
  }
  if (extraction.includes('US_STATES')) {
    pass('US states lookup map exists');
  } else {
    fail('US states lookup map missing');
  }
} catch {
  fail('Brand/state extraction module not found (src/lib/brand-state-extraction.ts)');
}

// Structured data: ItemList schema
if (structuredData.includes('ItemList')) {
  pass('ItemList schema function exists');
} else {
  fail('ItemList schema function missing');
}

// Settlement pages link to brand/state pages
if (settlementPage.includes('/brand/') || settlementPage.includes('brandSlug')) {
  pass('Settlement pages link to brand pages');
} else {
  fail('Settlement pages missing brand page links');
}

if (settlementPage.includes('/state/') || settlementPage.includes('stateSlug')) {
  pass('Settlement pages link to state pages');
} else {
  fail('Settlement pages missing state page links');
}

if (settlementPage.includes('open-class-action-settlements')) {
  pass('Settlement pages link to open settlements page');
} else {
  warn('Settlement pages missing open settlements link');
}

// Sitemap includes brand/state/open URLs
if (sitemapArticles.includes('extractBrands')) {
  pass('Sitemap includes brand page URLs');
} else {
  fail('Sitemap missing brand page URLs');
}

if (sitemapArticles.includes('extractStates')) {
  pass('Sitemap includes state page URLs');
} else {
  fail('Sitemap missing state page URLs');
}

if (sitemapArticles.includes('open-class-action-settlements')) {
  pass('Sitemap includes open settlements page URL');
} else {
  fail('Sitemap missing open settlements page URL');
}

// Footer / site config includes open settlements link
try {
  const siteConfig = readSource('src/site.config.ts');
  if (siteConfig.includes('open-class-action-settlements')) {
    pass('Site config quickLinks includes open settlements');
  } else {
    fail('Site config quickLinks missing open settlements link');
  }
} catch {
  warn('Could not read site.config.ts');
}

// ── Summary ──

console.log('\n' + '─'.repeat(50));
console.log(
  `\n${checks} checks: \x1b[32m${checks - errors} passed\x1b[0m` +
    (errors > 0 ? `, \x1b[31m${errors} failed\x1b[0m` : '') +
    (warnings > 0 ? `, \x1b[33m${warnings} warnings\x1b[0m` : '')
);

if (errors > 0) {
  console.log('\n\x1b[31mValidation FAILED\x1b[0m\n');
  process.exit(1);
} else {
  console.log('\n\x1b[32mValidation PASSED\x1b[0m\n');
  process.exit(0);
}
