import type { APIRoute } from 'astro';
import { getArticles, getAllSettlements } from '../lib/supabase';
import { extractBrands, extractStates } from '../lib/brand-state-extraction';

export const prerender = false;

export const GET: APIRoute = async () => {
  const allArticles = await getArticles({ limit: 5000 });
  const today = new Date().toISOString().split('T')[0];

  // ── Article URLs ──
  const articleUrls = allArticles.map((article) => {
    const isSettlement = article.news_type === 'settlement' || (article.category && article.category !== 'General');
    const path = isSettlement ? `/settlements/${article.slug}` : `/news/${article.slug}`;
    const lastmod = article.updated_at || article.published_at || article.created_at;
    const lastmodDate = new Date(lastmod).toISOString().split('T')[0];

    return `  <url>
    <loc>https://classactionlawupdates.com${path}</loc>
    <lastmod>${lastmodDate}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.6</priority>
  </url>`;
  });

  // ── High-intent landing pages ──
  const landingPages: string[] = [];

  // Open settlements page
  landingPages.push(`  <url>
    <loc>https://classactionlawupdates.com/open-class-action-settlements</loc>
    <lastmod>${today}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.8</priority>
  </url>`);

  // Claim deadlines ending soon page
  landingPages.push(`  <url>
    <loc>https://classactionlawupdates.com/claim-deadlines-ending-soon</loc>
    <lastmod>${today}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.8</priority>
  </url>`);

  // Settlement database page
  landingPages.push(`  <url>
    <loc>https://classactionlawupdates.com/class-action-settlements-database</loc>
    <lastmod>${today}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>`);

  // Brand pages
  const allSettlements = await getAllSettlements({ limit: 500 });
  const brands = extractBrands(allSettlements);
  for (const [slug] of brands) {
    landingPages.push(`  <url>
    <loc>https://classactionlawupdates.com/brand/${slug}</loc>
    <lastmod>${today}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.8</priority>
  </url>`);
  }

  // State pages
  const states = extractStates(allSettlements);
  for (const [slug] of states) {
    landingPages.push(`  <url>
    <loc>https://classactionlawupdates.com/state/${slug}</loc>
    <lastmod>${today}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.8</priority>
  </url>`);
  }

  const allUrls = [...landingPages, ...articleUrls];

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${allUrls.join('\n')}
</urlset>`;

  return new Response(xml, {
    headers: {
      'Content-Type': 'application/xml; charset=utf-8',
      'Cache-Control': 'public, max-age=3600',
    },
  });
};
