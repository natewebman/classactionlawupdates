import { defineConfig } from 'astro/config';
import tailwind from '@astrojs/tailwind';
import cloudflare from '@astrojs/cloudflare';
import sitemap from '@astrojs/sitemap';

export default defineConfig({
  site: 'https://classactionlawupdates.com',
  integrations: [
    tailwind(),
    sitemap({
      filter: (page) =>
        !page.includes('/login') &&
        !page.includes('/signup') &&
        !page.includes('/subscribe'),
      serialize(item) {
        // Homepage
        if (item.url === 'https://classactionlawupdates.com/') {
          item.priority = 1.0;
          item.changefreq = 'daily';
        }
        // Category pages (hub pages — high priority)
        else if (item.url.includes('/category/')) {
          item.priority = 0.8;
          item.changefreq = 'daily';
        }
        // Static pages
        else {
          item.priority = 0.3;
          item.changefreq = 'monthly';
        }
        return item;
      },
    }),
  ],
  output: 'static',
  adapter: cloudflare(),
});
