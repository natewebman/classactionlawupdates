import type { Article } from './supabase';

// ---------- Title Tag Optimization ----------

/**
 * Extract a company/product name from the article title or case_name.
 * Strips common legal suffixes like "Settlement", "Class Action", etc.
 */
function extractCompany(article: Article): string {
  const source = article.case_name || article.title;
  // Try to extract company name before common legal terms
  const match = source.match(/^(.+?)\s+(?:Settlement|Class Action|Lawsuit|v\.|vs\.)/i);
  if (match) return match[1].trim();
  // Fallback: use first few words
  return source.split(/\s+/).slice(0, 3).join(' ');
}

/**
 * Extract year from article published_at or claim_deadline.
 */
function extractYear(article: Article): string {
  const dateStr = article.published_at || article.created_at;
  const year = new Date(dateStr).getFullYear();
  return String(year);
}

/**
 * Generate an SEO-optimized title for settlement articles.
 * Pattern: [Company] Settlement [Year]: Eligibility, Claim Deadline & How to File
 * Truncated to 60 characters.
 */
export function settlementTitle(article: Article): string {
  const company = extractCompany(article);
  const year = extractYear(article);
  const full = `${company} Settlement ${year}: Eligibility, Claim Deadline & How to File`;
  if (full.length <= 60) return full;

  // Try shorter suffix
  const shorter = `${company} Settlement ${year}: How to File a Claim`;
  if (shorter.length <= 60) return shorter;

  // Minimal
  const minimal = `${company} Settlement ${year}`;
  if (minimal.length <= 60) return minimal;

  // Truncate company name if still too long
  return `${company.slice(0, 40)}... Settlement ${year}`;
}

/**
 * Generate an SEO-optimized title for news articles.
 * Pattern: [Company] Class Action Lawsuit [Year]: What [Affected Group] Should Know
 * Truncated to 60 characters.
 */
export function newsTitle(article: Article): string {
  const company = extractCompany(article);
  const year = extractYear(article);
  const full = `${company} Class Action ${year}: What You Should Know`;
  if (full.length <= 60) return full;

  // Try shorter
  const shorter = `${company} Class Action Lawsuit ${year}`;
  if (shorter.length <= 60) return shorter;

  // Minimal
  const minimal = `${company} Class Action ${year}`;
  if (minimal.length <= 60) return minimal;

  return `${company.slice(0, 35)}... Class Action ${year}`;
}

// ---------- Date Formatting ----------

/**
 * Format a date string for display and datetime attributes.
 * Returns both a human-readable display string and ISO format.
 */
export function formatDate(dateStr: string | null): { display: string; iso: string } {
  if (!dateStr) {
    return { display: '', iso: '' };
  }
  const date = new Date(dateStr);
  const display = date.toLocaleDateString('en-US', {
    month: 'long',
    day: 'numeric',
    year: 'numeric',
  });
  const iso = date.toISOString();
  return { display, iso };
}

// ---------- Heading Utilities (for Table of Contents) ----------

/**
 * Create a URL-safe slug from heading text.
 */
function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '');
}

/**
 * Add id attributes to H2 tags in HTML content for anchor linking.
 * Handles duplicate headings by appending a counter suffix.
 */
export function addHeadingIds(html: string): string {
  const usedIds = new Map<string, number>();

  return html.replace(/<h2([^>]*)>(.*?)<\/h2>/gi, (_match, attrs: string, innerHtml: string) => {
    // Strip HTML tags from inner content to get plain text for the slug
    const plainText = innerHtml.replace(/<[^>]*>/g, '').trim();
    let id = slugify(plainText);

    // Handle duplicate IDs
    const count = usedIds.get(id) ?? 0;
    usedIds.set(id, count + 1);
    if (count > 0) {
      id = `${id}-${count}`;
    }

    // Preserve existing attributes but add/replace id
    const cleanAttrs = attrs.replace(/\s*id="[^"]*"/g, '');
    return `<h2${cleanAttrs} id="${id}">${innerHtml}</h2>`;
  });
}

/**
 * Extract H2 headings and their IDs from processed HTML content.
 * Should be called on HTML that has already been processed by addHeadingIds().
 */
export function extractHeadings(html: string): Array<{ id: string; text: string }> {
  const headings: Array<{ id: string; text: string }> = [];
  const regex = /<h2[^>]*id="([^"]*)"[^>]*>(.*?)<\/h2>/gi;
  let match;

  while ((match = regex.exec(html)) !== null) {
    const id = match[1];
    const text = match[2].replace(/<[^>]*>/g, '').trim();
    headings.push({ id, text });
  }

  return headings;
}
