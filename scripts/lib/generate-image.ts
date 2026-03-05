/**
 * AI image generation pipeline for article hero images.
 *
 * 1. Claude Haiku generates a detailed image prompt from article metadata.
 * 2. OpenAI DALL-E 3 generates the image.
 * 3. The image is downloaded and uploaded to Supabase Storage.
 * 4. The article record is updated with the public URL, alt text, and filename.
 */

import Anthropic from "@anthropic-ai/sdk";
import OpenAI from "openai";
import { createClient } from "@supabase/supabase-js";

// ---------------------------------------------------------------------------
// Environment helpers
// ---------------------------------------------------------------------------

function requireEnv(key: string): string {
  const val = process.env[key];
  if (!val) throw new Error(`Missing required env var: ${key}`);
  return val;
}

// ---------------------------------------------------------------------------
// Step 1 – Generate an image prompt via Claude Haiku
// ---------------------------------------------------------------------------

export async function generateImagePrompt(article: {
  title: string;
  category: string | null;
  meta_description: string | null;
}): Promise<string> {
  const anthropic = new Anthropic({ apiKey: requireEnv("ANTHROPIC_API_KEY") });

  const message = await anthropic.messages.create({
    model: "claude-haiku-4-5-20251001",
    max_tokens: 300,
    messages: [
      {
        role: "user",
        content: `You are an expert editorial photo director. Given the article details below, write a single, detailed image-generation prompt (2-3 sentences) for a photojournalistic, editorial-style hero image.

Rules:
- Photorealistic, high-quality, dramatic lighting
- NO text, logos, watermarks, or identifiable human faces
- Evoke the legal/financial/consumer-protection theme
- Include specific compositional details (camera angle, depth of field, lighting)
- Suitable for a legal news website

Article title: ${article.title}
Category: ${article.category ?? "General"}
Summary: ${article.meta_description ?? article.title}

Respond with ONLY the image prompt, nothing else.`,
      },
    ],
  });

  // Extract text from the response
  const block = message.content[0];
  if (block.type !== "text") throw new Error("Unexpected response type");
  return block.text.trim();
}

// ---------------------------------------------------------------------------
// Step 2 – Generate the image via OpenAI DALL-E 3
// ---------------------------------------------------------------------------

export async function generateImage(
  prompt: string
): Promise<{ data: Buffer; mediaType: string }> {
  const openai = new OpenAI({ apiKey: requireEnv("OPENAI_API_KEY") });

  const response = await openai.images.generate({
    model: "dall-e-3",
    prompt: `Photojournalistic editorial hero image for a legal news article. NO text, NO logos, NO watermarks, NO identifiable human faces. ${prompt}`,
    n: 1,
    size: "1792x1024", // Landscape, closest to 16:9
    quality: "standard",
    response_format: "b64_json",
  });

  const imageData = response.data[0];
  if (!imageData?.b64_json) {
    throw new Error("OpenAI did not return an image");
  }

  const buffer = Buffer.from(imageData.b64_json, "base64");
  return { data: buffer, mediaType: "image/png" };
}

// ---------------------------------------------------------------------------
// Step 3 – Upload image to Supabase Storage
// ---------------------------------------------------------------------------

const STORAGE_BUCKET = "article-images";

export async function persistImage(
  imageData: Buffer,
  filename: string,
  contentType: string
): Promise<string> {
  const supabaseUrl = requireEnv("PUBLIC_SUPABASE_URL");
  const serviceKey = requireEnv("SUPABASE_SERVICE_ROLE_KEY");
  const supabase = createClient(supabaseUrl, serviceKey);

  // Upload to Supabase Storage (upsert to allow re-runs)
  const { error: uploadError } = await supabase.storage
    .from(STORAGE_BUCKET)
    .upload(filename, imageData, {
      contentType,
      upsert: true,
    });

  if (uploadError) {
    throw new Error(`Storage upload failed: ${uploadError.message}`);
  }

  // Build the public URL
  const { data } = supabase.storage
    .from(STORAGE_BUCKET)
    .getPublicUrl(filename);

  return data.publicUrl;
}

// ---------------------------------------------------------------------------
// Step 4 – Update the article record in Supabase
// ---------------------------------------------------------------------------

export async function updateArticleImage(
  articleId: string,
  imageUrl: string,
  altText: string,
  filename: string
): Promise<void> {
  const supabaseUrl = requireEnv("PUBLIC_SUPABASE_URL");
  const serviceKey = requireEnv("SUPABASE_SERVICE_ROLE_KEY");
  const supabase = createClient(supabaseUrl, serviceKey);

  const { error } = await supabase
    .from("articles")
    .update({
      hero_image: imageUrl,
      hero_image_alt: altText,
      hero_image_filename: filename,
    })
    .eq("id", articleId);

  if (error) {
    throw new Error(`Failed to update article ${articleId}: ${error.message}`);
  }
}

// ---------------------------------------------------------------------------
// Convenience – full pipeline for a single article
// ---------------------------------------------------------------------------

export async function generateArticleImage(article: {
  id: string;
  title: string;
  slug: string;
  category: string | null;
  meta_description: string | null;
}): Promise<{ imageUrl: string; altText: string; filename: string }> {
  // Derive alt text from title
  const altText = `${article.title} - Class Action Law Updates`;

  // Step 1: Generate prompt with Claude Haiku
  const prompt = await generateImagePrompt(article);

  // Step 2: Generate image with OpenAI DALL-E 3
  const { data: imageData, mediaType } = await generateImage(prompt);

  // Filename is always .png with DALL-E 3
  const filename = `${article.slug}.png`;

  // Step 3: Persist to Supabase Storage
  const imageUrl = await persistImage(imageData, filename, mediaType);

  // Step 4: Update the database record
  await updateArticleImage(article.id, imageUrl, altText, filename);

  return { imageUrl, altText, filename };
}
