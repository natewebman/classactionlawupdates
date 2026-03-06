import type { APIRoute } from 'astro';
import { getArticles } from '../../lib/supabase';
import { siteConfig } from '../../site.config';

export const prerender = false;

export const GET: APIRoute = async () => {
  const allArticles = await getArticles({ limit: 5000 });

  const settlements = allArticles.filter(
    (a) => a.category && a.category !== 'General'
  );
  const news = allArticles.filter(
    (a) => !a.category || a.category === 'General'
  );

  // Category counts
  const categoryCounts: Record<string, number> = {};
  for (const cat of siteConfig.settlementCategories) {
    if (cat.slug === 'all') continue;
    categoryCounts[cat.name] = 0;
  }
  for (const article of settlements) {
    const catName = article.category ?? 'Uncategorized';
    categoryCounts[catName] = (categoryCounts[catName] ?? 0) + 1;
  }

  // Latest 10 articles
  const latest = allArticles.slice(0, 10).map((a) => ({
    id: a.id,
    title: a.title,
    slug: a.slug,
    category: a.category,
    news_type: a.news_type,
    published_at: a.published_at,
    updated_at: a.updated_at,
  }));

  const stats = {
    total_articles: allArticles.length,
    total_settlements: settlements.length,
    total_news: news.length,
    categories: categoryCounts,
    latest_articles: latest,
  };

  return new Response(JSON.stringify(stats, null, 2), {
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': 'public, max-age=300',
    },
  });
};
