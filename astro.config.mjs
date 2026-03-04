import { defineConfig } from 'astro/config';
import tailwind from '@astrojs/tailwind';
import cloudflare from '@astrojs/cloudflare';

export default defineConfig({
    site: 'https://classactionlawupdates.com',
    integrations: [tailwind()],
    output: 'hybrid',
    adapter: cloudflare(),
});
```

**File 2: `src/pages/news/index.astro`** — add this one line right after the opening `---`:
```
export const prerender = false;
```

**File 3: `src/pages/news/[slug].astro`** — same thing, add right after the opening `---`:
```
export const prerender = false;
```

**File 4: `package.json`** — add the adapter dependency. Run this in your terminal or add it manually:
```
npm install @astrojs/cloudflare
