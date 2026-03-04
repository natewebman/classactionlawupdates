import { defineConfig } from 'astro/config';
import tailwind from '@astrojs/tailwind';
import cloudflare from '@astrojs/cloudflare';

export default defineConfig({
  site: 'https://classactionlawupdates.com',
  integrations: [tailwind()],
  output: 'hybrid',
  adapter: cloudflare(),
});
