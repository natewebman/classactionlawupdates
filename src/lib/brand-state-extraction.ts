/**
 * Brand & State Extraction
 *
 * Extracts brand/company names and US state locations from settlement data
 * for generating dynamic /brand/[slug] and /state/[slug] pages.
 */

import type { Article } from './supabase';

// ── US States ──

export const US_STATES: Record<string, string> = {
  'alabama': 'Alabama',
  'alaska': 'Alaska',
  'arizona': 'Arizona',
  'arkansas': 'Arkansas',
  'california': 'California',
  'colorado': 'Colorado',
  'connecticut': 'Connecticut',
  'delaware': 'Delaware',
  'florida': 'Florida',
  'georgia': 'Georgia',
  'hawaii': 'Hawaii',
  'idaho': 'Idaho',
  'illinois': 'Illinois',
  'indiana': 'Indiana',
  'iowa': 'Iowa',
  'kansas': 'Kansas',
  'kentucky': 'Kentucky',
  'louisiana': 'Louisiana',
  'maine': 'Maine',
  'maryland': 'Maryland',
  'massachusetts': 'Massachusetts',
  'michigan': 'Michigan',
  'minnesota': 'Minnesota',
  'mississippi': 'Mississippi',
  'missouri': 'Missouri',
  'montana': 'Montana',
  'nebraska': 'Nebraska',
  'nevada': 'Nevada',
  'new-hampshire': 'New Hampshire',
  'new-jersey': 'New Jersey',
  'new-mexico': 'New Mexico',
  'new-york': 'New York',
  'north-carolina': 'North Carolina',
  'north-dakota': 'North Dakota',
  'ohio': 'Ohio',
  'oklahoma': 'Oklahoma',
  'oregon': 'Oregon',
  'pennsylvania': 'Pennsylvania',
  'rhode-island': 'Rhode Island',
  'south-carolina': 'South Carolina',
  'south-dakota': 'South Dakota',
  'tennessee': 'Tennessee',
  'texas': 'Texas',
  'utah': 'Utah',
  'vermont': 'Vermont',
  'virginia': 'Virginia',
  'washington': 'Washington',
  'west-virginia': 'West Virginia',
  'wisconsin': 'Wisconsin',
  'wyoming': 'Wyoming',
  'district-of-columbia': 'District of Columbia',
};

/**
 * Normalize a string into a URL-safe slug.
 */
export function toSlug(str: string): string {
  return str
    .toLowerCase()
    .replace(/['']/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

// ── Brand Extraction ──

// Common words to exclude from brand detection
const STOP_WORDS = new Set([
  'the', 'a', 'an', 'and', 'or', 'of', 'in', 'on', 'at', 'to', 'for',
  'is', 'are', 'was', 'were', 'be', 'been', 'has', 'have', 'had',
  'class', 'action', 'lawsuit', 'settlement', 'case', 'claims',
  'claim', 'filed', 'against', 'over', 'with', 'from', 'how', 'what',
  'who', 'when', 'where', 'why', 'can', 'may', 'will', 'new', 'all',
  'your', 'you', 'get', 'file', 'pay', 'paid', 'money', 'up',
  'million', 'billion', 'dollars', 'dollar', 'per', 'each',
  'data', 'breach', 'privacy', 'product', 'recall', 'injury',
  'securities', 'fraud', 'stock', 'investor', 'investors',
  'consumers', 'consumer', 'customers', 'customer', 'users', 'user',
  'eligibility', 'deadline', 'qualifying', 'qualify',
  'alleged', 'alleges', 'alleging', 'update', 'updates',
  'could', 'should', 'would', 'about', 'this', 'that', 'these', 'those',
  'not', 'but', 'its', 'their', 'they', 'them', 'his', 'her',
  'class-action', 'multi', 'district', 'litigation', 'preliminary',
  'final', 'approval', 'approved', 'pending', 'know', 'need',
  // Common title verbs that follow brand names
  'hit', 'hits', 'faces', 'facing', 'settles', 'settled', 'settling',
  'sparks', 'reaches', 'reached', 'sues', 'sued', 'suing',
  'wins', 'pays', 'drops', 'seeks', 'dumps', 'dumping',
  // Generic nouns/products that aren't brand names
  'contamination', 'chain', 'clinic',
  'airbag', 'airbags', 'listeria', 'order', 'flow',
]);

/** Strip common legal entity suffixes from a brand name. */
function cleanBrandSuffix(brand: string): string {
  return brand.replace(/,?\s*(Inc|LLC|Corp|Ltd|Co|LP|L\.?P\.?|et\s+al)\.?\s*$/i, '').trim();
}

/**
 * Extract a brand/company name from a text source using proper-noun detection.
 * Returns the first 1-3 consecutive capitalized non-stop-words.
 * Preserves "&" within words (e.g., "AT&T").
 */
function extractProperNouns(text: string, maxWords = 3): string | null {
  const words = text.split(/\s+/);
  const buffer: string[] = [];

  for (const word of words) {
    // Keep "&" when embedded (AT&T) but not as standalone
    const clean = word.replace(/[^a-zA-Z0-9'&-]/g, '');
    const isCapitalized = /^[A-Z]/.test(clean);
    const isStopWord = STOP_WORDS.has(clean.toLowerCase());

    if (isCapitalized && !isStopWord && clean.length >= 2) {
      buffer.push(clean);
      if (buffer.length >= maxWords) break;
    } else {
      if (buffer.length > 0) break;
    }
  }

  if (buffer.length === 0) return null;
  const result = buffer.join(' ');
  return result.length >= 2 && result.length <= 60 ? result : null;
}

/**
 * Extract a brand/company name from an article.
 * Tries case_name patterns first, falls back to title.
 * Returns null if no brand can be extracted.
 */
export function extractBrand(article: Article): string | null {
  if (article.case_name) {
    // Strategy 1a: "Smith v. CompanyName" format
    const vsMatch = article.case_name.match(/\bv\.?\s+(.+?)(?:\s*,|\s*$)/i);
    if (vsMatch) {
      const brand = cleanBrandSuffix(vsMatch[1]);
      if (brand.length >= 2 && brand.length <= 60) return brand;
    }

    // Strategy 1b: "In re: CompanyName ..." format
    // Brand names in "In re" cases are typically 1-2 words (e.g., "Kaiser Permanente", "Robinhood")
    const inReMatch = article.case_name.match(/In\s+re:?\s+(.+)/i);
    if (inReMatch) {
      const brand = extractProperNouns(inReMatch[1], 2);
      if (brand) return cleanBrandSuffix(brand);
    }

    // Strategy 1c: Other case_names — extract first proper noun phrase
    if (!article.case_name.match(/\bv\.?\s/i) && !article.case_name.match(/In\s+re/i)) {
      const brand = extractProperNouns(article.case_name, 3);
      if (brand) return cleanBrandSuffix(brand);
    }
  }

  // Strategy 2: Title — extract first proper noun phrase (max 3 words)
  const brand = extractProperNouns(article.title, 3);
  return brand;
}

/**
 * Extract brands from a list of articles.
 * Returns a map of slug → { name, articles }.
 */
export function extractBrands(articles: Article[]): Map<string, { name: string; articles: Article[] }> {
  const brands = new Map<string, { name: string; articles: Article[] }>();

  for (const article of articles) {
    const brand = extractBrand(article);
    if (!brand) continue;

    const slug = toSlug(brand);
    if (slug.length < 2) continue;

    const existing = brands.get(slug);
    if (existing) {
      existing.articles.push(article);
    } else {
      brands.set(slug, { name: brand, articles: [article] });
    }
  }

  return brands;
}

// ── State Extraction ──

// Build a reverse lookup: "California" → "california", "New York" → "new-york"
const stateNameToSlug = new Map<string, string>();
for (const [slug, name] of Object.entries(US_STATES)) {
  stateNameToSlug.set(name.toLowerCase(), slug);
}

// State abbreviation → slug
const STATE_ABBREVS: Record<string, string> = {
  'AL': 'alabama', 'AK': 'alaska', 'AZ': 'arizona', 'AR': 'arkansas',
  'CA': 'california', 'CO': 'colorado', 'CT': 'connecticut', 'DE': 'delaware',
  'FL': 'florida', 'GA': 'georgia', 'HI': 'hawaii', 'ID': 'idaho',
  'IL': 'illinois', 'IN': 'indiana', 'IA': 'iowa', 'KS': 'kansas',
  'KY': 'kentucky', 'LA': 'louisiana', 'ME': 'maine', 'MD': 'maryland',
  'MA': 'massachusetts', 'MI': 'michigan', 'MN': 'minnesota', 'MS': 'mississippi',
  'MO': 'missouri', 'MT': 'montana', 'NE': 'nebraska', 'NV': 'nevada',
  'NH': 'new-hampshire', 'NJ': 'new-jersey', 'NM': 'new-mexico', 'NY': 'new-york',
  'NC': 'north-carolina', 'ND': 'north-dakota', 'OH': 'ohio', 'OK': 'oklahoma',
  'OR': 'oregon', 'PA': 'pennsylvania', 'RI': 'rhode-island', 'SC': 'south-carolina',
  'SD': 'south-dakota', 'TN': 'tennessee', 'TX': 'texas', 'UT': 'utah',
  'VT': 'vermont', 'VA': 'virginia', 'WA': 'washington', 'WV': 'west-virginia',
  'WI': 'wisconsin', 'WY': 'wyoming', 'DC': 'district-of-columbia',
};

/**
 * Extract a state slug from an article's location field.
 * Returns null if no state match found.
 */
export function extractState(article: Article): string | null {
  if (!article.location) return null;

  const location = article.location.trim();

  // Check full state name match
  for (const [name, slug] of stateNameToSlug) {
    if (location.toLowerCase().includes(name)) {
      return slug;
    }
  }

  // Check state abbreviation (e.g., "CA", "NY", "Los Angeles, CA")
  const abbrevMatch = location.match(/\b([A-Z]{2})\b/);
  if (abbrevMatch && STATE_ABBREVS[abbrevMatch[1]]) {
    return STATE_ABBREVS[abbrevMatch[1]];
  }

  return null;
}

/**
 * Extract states from a list of articles.
 * Returns a map of slug → { name, articles }.
 */
export function extractStates(articles: Article[]): Map<string, { name: string; articles: Article[] }> {
  const states = new Map<string, { name: string; articles: Article[] }>();

  for (const article of articles) {
    const stateSlug = extractState(article);
    if (!stateSlug) continue;

    const stateName = US_STATES[stateSlug];
    if (!stateName) continue;

    const existing = states.get(stateSlug);
    if (existing) {
      existing.articles.push(article);
    } else {
      states.set(stateSlug, { name: stateName, articles: [article] });
    }
  }

  return states;
}
