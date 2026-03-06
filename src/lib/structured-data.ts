import { siteConfig } from '../site.config';
import type { Article } from './supabase';

const SITE_URL = `https://${siteConfig.domain}`;

// ---------- Organization ----------

export function getOrganization() {
  return {
    '@type': 'Organization',
    'name': siteConfig.name,
    'url': SITE_URL,
    'description': siteConfig.description,
    'logo': `${SITE_URL}/images/class-action-law-updates-logo-dark.svg`,
    'sameAs': [],
    'address': {
      '@type': 'PostalAddress',
      'postOfficeBoxNumber': siteConfig.contact.address.replace('PO Box ', ''),
      'addressLocality': siteConfig.contact.city,
      'addressRegion': siteConfig.contact.state,
      'postalCode': siteConfig.contact.zip,
      'addressCountry': 'US',
    },
  };
}

// ---------- Author ----------

export function getAuthor() {
  return {
    '@type': 'Person',
    'name': 'Editorial Team',
    'url': `${SITE_URL}/about`,
  };
}

// ---------- BreadcrumbList ----------

export function getBreadcrumbList(items: Array<{ name: string; url: string }>) {
  return {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    'itemListElement': items.map((item, i) => ({
      '@type': 'ListItem',
      'position': i + 1,
      'name': item.name,
      'item': `${SITE_URL}${item.url}`,
    })),
  };
}

// ---------- WebSite (homepage) ----------

export function getWebSite() {
  return {
    '@context': 'https://schema.org',
    '@type': 'WebSite',
    'name': siteConfig.name,
    'url': SITE_URL,
    'description': siteConfig.description,
    'publisher': getOrganization(),
  };
}

// ---------- NewsArticle ----------

interface NewsArticleParams {
  article: Article;
  pageUrl: string;
}

export function getNewsArticle({ article, pageUrl }: NewsArticleParams) {
  return {
    '@context': 'https://schema.org',
    '@type': 'NewsArticle',
    'headline': article.title,
    'description': article.meta_description,
    'datePublished': article.published_at,
    'dateModified': article.updated_at,
    'author': getAuthor(),
    'publisher': getOrganization(),
    'articleSection': article.category ?? article.news_type ?? 'News',
    'mainEntityOfPage': {
      '@type': 'WebPage',
      '@id': pageUrl,
    },
    ...(article.hero_image ? { image: article.hero_image } : {}),
  };
}

// ---------- Settlement Article ----------

interface SettlementArticleParams {
  article: Article;
  pageUrl: string;
}

export function getSettlementArticle({ article, pageUrl }: SettlementArticleParams) {
  const additionalProperty: Array<{ '@type': string; name: string; value: string }> = [];

  if (article.case_name) {
    additionalProperty.push({ '@type': 'PropertyValue', name: 'Case Name', value: article.case_name });
  }
  if (article.settlement_amount) {
    additionalProperty.push({ '@type': 'PropertyValue', name: 'Settlement Amount', value: article.settlement_amount });
  }
  if (article.claim_deadline) {
    additionalProperty.push({ '@type': 'PropertyValue', name: 'Claim Deadline', value: article.claim_deadline });
  }
  if (article.case_status) {
    additionalProperty.push({ '@type': 'PropertyValue', name: 'Case Status', value: article.case_status });
  }
  if (article.potential_reward) {
    additionalProperty.push({ '@type': 'PropertyValue', name: 'Potential Reward', value: article.potential_reward });
  }
  if (article.proof_required !== null && article.proof_required !== undefined) {
    additionalProperty.push({
      '@type': 'PropertyValue',
      name: 'Proof Required',
      value: article.proof_required ? 'Yes' : 'No',
    });
  }
  if (article.location) {
    additionalProperty.push({ '@type': 'PropertyValue', name: 'Location', value: article.location });
  }
  if (article.claim_url) {
    additionalProperty.push({ '@type': 'PropertyValue', name: 'Claim URL', value: article.claim_url });
  }

  return {
    '@context': 'https://schema.org',
    '@type': 'Article',
    'headline': article.title,
    'description': article.meta_description,
    'datePublished': article.published_at,
    'dateModified': article.updated_at,
    'author': getAuthor(),
    'publisher': getOrganization(),
    'articleSection': article.category ?? 'Settlement',
    'mainEntityOfPage': {
      '@type': 'WebPage',
      '@id': pageUrl,
    },
    ...(article.hero_image ? { image: article.hero_image } : {}),
    ...(additionalProperty.length > 0 ? { additionalProperty } : {}),
  };
}

// ---------- FAQPage ----------

export function getFAQPage(items: Array<{ question: string; answer: string }>) {
  return {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    'mainEntity': items.map((item) => ({
      '@type': 'Question',
      'name': item.question,
      'acceptedAnswer': {
        '@type': 'Answer',
        'text': item.answer,
      },
    })),
  };
}

// ---------- ItemList ----------

interface ItemListParams {
  name: string;
  description?: string;
  items: Array<{ url: string; name: string; position: number }>;
}

export function getItemList({ name, description, items }: ItemListParams) {
  return {
    '@context': 'https://schema.org',
    '@type': 'ItemList',
    'name': name,
    ...(description ? { description } : {}),
    'numberOfItems': items.length,
    'itemListElement': items.map((item) => ({
      '@type': 'ListItem',
      'position': item.position,
      'name': item.name,
      'url': item.url,
    })),
  };
}

// ---------- CollectionPage ----------

interface CollectionPageParams {
  name: string;
  description: string;
  url: string;
  numberOfItems?: number;
  mainEntity?: Array<{ '@type': string; url: string }>;
}

export function getCollectionPage({ name, description, url, numberOfItems, mainEntity }: CollectionPageParams) {
  return {
    '@context': 'https://schema.org',
    '@type': 'CollectionPage',
    'name': name,
    'description': description,
    'url': url,
    'publisher': getOrganization(),
    ...(numberOfItems !== undefined ? { numberOfItems } : {}),
    ...(mainEntity && mainEntity.length > 0 ? { mainEntity } : {}),
  };
}
