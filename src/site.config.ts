// Site-specific configuration for classactionlawupdates.com
// This file is the single source of truth for site identity, branding, and feature flags.

export const siteConfig = {
  // Identity
  siteKey: 'classactionlawupdates',
  name: 'Class Action News & Settlements',
  shortName: 'Class Action News',
  domain: 'classactionlawupdates.com',
  tagline: 'Stay informed about settlements and class actions that affect you.',
  description:
    'Your trusted source for the latest class action lawsuits, settlements, and legal news. Stay informed and claim what you are owed.',

  // Contact
  contact: {
    address: 'PO Box 195546',
    city: 'Winter Springs',
    state: 'FL',
    zip: '32719',
  },

  // Navigation categories
  settlementCategories: [
    { name: 'All Settlements', slug: 'all' },
    { name: 'Stocks', slug: 'stocks' },
    { name: 'Personal Injury', slug: 'personal-injury' },
    { name: 'Product Recalls', slug: 'product-recalls' },
    { name: 'Drugs & Pharmacy', slug: 'drugs-pharmacy' },
    { name: 'Financial', slug: 'financial' },
    { name: 'Online/Privacy', slug: 'online-privacy' },
  ],

  newsCategories: [
    { name: 'All News', slug: 'all' },
    { name: 'Case Filings', slug: 'case-filings' },
    { name: 'Settlement News', slug: 'settlement-news' },
  ],

  // Signup form topics (checkboxes)
  signupTopics: [
    'Stocks',
    'Personal Injury',
    'Product Recalls',
    'Drugs & Pharmacy',
    'Financial',
    'Online/Privacy',
  ],

  // Social media
  social: {
    twitter: '',
    facebook: '',
    linkedin: '',
  },

  // Footer links
  quickLinks: [
    { name: 'Home', href: '/' },
    { name: 'Browse Settlements', href: '/settlements' },
    { name: 'Latest News', href: '/news' },
    { name: 'Join as Member', href: '/signup' },
    { name: 'For Attorneys', href: '/attorney-portal' },
  ],
} as const;
