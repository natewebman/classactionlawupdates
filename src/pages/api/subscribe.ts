import type { APIRoute } from 'astro';
import { addSubscriber } from '../../lib/supabase';

export const prerender = false;

export const POST: APIRoute = async ({ request }) => {
  try {
    const body = await request.json();
    const { email, source, utm_source, utm_campaign } = body;

    if (!email || typeof email !== 'string') {
      return new Response(JSON.stringify({ success: false, error: 'Email is required' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    const result = await addSubscriber({
      email,
      source: source ?? 'sticky_banner',
      utm_source: utm_source ?? undefined,
      utm_campaign: utm_campaign ?? undefined,
    });

    return new Response(JSON.stringify(result), {
      status: result.success ? 200 : 500,
      headers: { 'Content-Type': 'application/json' },
    });
  } catch {
    return new Response(JSON.stringify({ success: false, error: 'Invalid request' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    });
  }
};
