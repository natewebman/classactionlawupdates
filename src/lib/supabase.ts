import { createClient } from '@supabase/supabase-js';

const supabaseUrl = import.meta.env.PUBLIC_SUPABASE_URL;
const supabaseAnonKey = import.meta.env.PUBLIC_SUPABASE_ANON_KEY;
const siteKey = import.meta.env.PUBLIC_SITE_KEY;

export const supabase = createClient(supabaseUrl, supabaseAnonKey);

// ---------- Helper: resolve site_id from site_key ----------
let _siteId: string | null = null;

async function getSiteId(): Promise<string> {
  if (_siteId) return _siteId;
  const { data, error } = await supabase
    .from('sites')
    .select('id')
    .eq('site_key', siteKey)
    .single();
  if (error || !data) throw new Error(`Site not found for key: ${siteKey}`);
  _siteId = data.id;
  return _siteId;
}

// ---------- Articles ----------

export interface Article {
  id: string;
  title: string;
  slug: string;
  content: string | null;
  meta_description: string | null;
  category: string | null;
  news_type: string | null;
  content_stage: string;
  published_at: string | null;
  created_at: string;
  updated_at: string;
  // Image fields
  hero_image: string | null;
  hero_image_alt: string | null;
  hero_image_filename: string | null;
  // Settlement-specific fields
  case_status: string | null;
  claim_deadline: string | null;
  claim_url: string | null;
  proof_required: boolean | null;
  potential_reward: string | null;
  settlement_amount: string | null;
  location: string | null;
  case_name: string | null;
  settlement_website: string | null;
  claims_administrator: string | null;
  class_counsel: string | null;
}

/**
 * Fetch published articles, optionally filtered by category or news_type.
 */
export async function getArticles(opts?: {
  category?: string;
  newsType?: string;
  limit?: number;
  offset?: number;
}): Promise<Article[]> {
  const siteId = await getSiteId();
  let query = supabase
    .from('articles')
    .select('*')
    .eq('site_id', siteId)
    .eq('content_stage', 'published')
    .order('published_at', { ascending: false });

  if (opts?.category && opts.category !== 'all') {
    query = query.eq('category', opts.category);
  }
  if (opts?.newsType && opts.newsType !== 'all') {
    query = query.eq('news_type', opts.newsType);
  }
  if (opts?.limit) {
    query = query.limit(opts.limit);
  }
  if (opts?.offset) {
    query = query.range(opts.offset, opts.offset + (opts.limit ?? 10) - 1);
  }

  const { data, error } = await query;
  if (error) {
    console.error('Error fetching articles:', error);
    return [];
  }
  return data ?? [];
}

/**
 * Fetch a single article by slug.
 */
export async function getArticleBySlug(slug: string): Promise<Article | null> {
  const siteId = await getSiteId();
  const { data, error } = await supabase
    .from('articles')
    .select('*')
    .eq('site_id', siteId)
    .eq('slug', slug)
    .eq('content_stage', 'published')
    .single();

  if (error) {
    console.error('Error fetching article:', error);
    return null;
  }
  return data;
}

/**
 * Fetch all published article slugs (for static path generation).
 */
export async function getAllArticleSlugs(): Promise<string[]> {
  const siteId = await getSiteId();
  const { data, error } = await supabase
    .from('articles')
    .select('slug')
    .eq('site_id', siteId)
    .eq('content_stage', 'published');

  if (error) {
    console.error('Error fetching slugs:', error);
    return [];
  }
  return (data ?? []).map((a) => a.slug);
}

/**
 * Fetch open settlements — active status or claim_deadline >= today.
 * Sorted by nearest deadline first, then newest.
 */
export async function getOpenSettlements(opts?: { limit?: number }): Promise<Article[]> {
  const siteId = await getSiteId();
  const today = new Date().toISOString().split('T')[0];

  // Settlements where status is active-like or deadline hasn't passed
  const { data, error } = await supabase
    .from('articles')
    .select('*')
    .eq('site_id', siteId)
    .eq('content_stage', 'published')
    .or(`case_status.in.(settled,approved,paying),claim_deadline.gte.${today}`)
    .order('claim_deadline', { ascending: true, nullsFirst: false })
    .order('published_at', { ascending: false })
    .limit(opts?.limit ?? 100);

  if (error) {
    console.error('Error fetching open settlements:', error);
    return [];
  }
  return data ?? [];
}

/**
 * Fetch all published settlements (news_type = 'settlement' OR category is non-null and non-General).
 * Used for brand/state extraction at build time.
 */
export async function getAllSettlements(opts?: { limit?: number }): Promise<Article[]> {
  const siteId = await getSiteId();
  const { data, error } = await supabase
    .from('articles')
    .select('*')
    .eq('site_id', siteId)
    .eq('content_stage', 'published')
    .not('category', 'is', null)
    .order('published_at', { ascending: false })
    .limit(opts?.limit ?? 500);

  if (error) {
    console.error('Error fetching all settlements:', error);
    return [];
  }
  return data ?? [];
}

// ---------- Subscribers ----------

export async function addSubscriber(fields: {
  email: string;
  name?: string;
  source?: string;
  utm_source?: string;
  utm_campaign?: string;
}): Promise<{ success: boolean; error?: string }> {
  const siteId = await getSiteId();
  const { error } = await supabase.from('subscribers').upsert(
    {
      site_id: siteId,
      email: fields.email,
      name: fields.name ?? null,
      source: fields.source ?? 'website_form',
      utm_source: fields.utm_source ?? null,
      utm_campaign: fields.utm_campaign ?? null,
      status: 'active',
      unsubscribed_at: null,
    },
    { onConflict: 'site_id,email' }
  );

  if (error) {
    console.error('Error adding subscriber:', error);
    return { success: false, error: error.message };
  }
  return { success: true };
}

export async function unsubscribeEmail(
  email: string
): Promise<{ success: boolean; error?: string; alreadyUnsubscribed?: boolean }> {
  const siteId = await getSiteId();

  // Look up the subscriber
  const { data: subscriber, error: lookupErr } = await supabase
    .from('subscribers')
    .select('id, status')
    .eq('site_id', siteId)
    .eq('email', email)
    .single();

  if (lookupErr || !subscriber) {
    return { success: false, error: "We couldn't find that email address in our system." };
  }

  if (subscriber.status === 'unsubscribed') {
    return { success: true, alreadyUnsubscribed: true };
  }

  // Update status to unsubscribed
  const { error: updateErr } = await supabase
    .from('subscribers')
    .update({ status: 'unsubscribed', unsubscribed_at: new Date().toISOString() })
    .eq('id', subscriber.id);

  if (updateErr) {
    console.error('Error unsubscribing:', updateErr);
    return { success: false, error: 'Something went wrong. Please try again.' };
  }

  return { success: true };
}

// ---------- Submissions ----------

export async function addSubmission(fields: {
  form_name: string;
  data: Record<string, unknown>;
  utm_source?: string;
  utm_medium?: string;
  utm_campaign?: string;
  utm_term?: string;
  utm_content?: string;
  landing_page?: string;
  referrer?: string;
  gclid?: string;
  fbclid?: string;
}): Promise<{ success: boolean; error?: string }> {
  const siteId = await getSiteId();
  const { error } = await supabase.from('submissions').insert({
    site_id: siteId,
    form_name: fields.form_name,
    data: fields.data,
    utm_source: fields.utm_source ?? null,
    utm_medium: fields.utm_medium ?? null,
    utm_campaign: fields.utm_campaign ?? null,
    utm_term: fields.utm_term ?? null,
    utm_content: fields.utm_content ?? null,
    landing_page: fields.landing_page ?? null,
    referrer: fields.referrer ?? null,
    gclid: fields.gclid ?? null,
    fbclid: fields.fbclid ?? null,
  });

  if (error) {
    console.error('Error adding submission:', error);
    return { success: false, error: error.message };
  }
  return { success: true };
}

// ---------- Auth helpers (Supabase Auth) ----------

export async function signUp(email: string, password: string) {
  return supabase.auth.signUp({ email, password });
}

export async function signIn(email: string, password: string) {
  return supabase.auth.signInWithPassword({ email, password });
}

export async function signOut() {
  return supabase.auth.signOut();
}

export async function getSession() {
  return supabase.auth.getSession();
}
