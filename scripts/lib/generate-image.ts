/**
 * AI image generation pipeline for article hero images.
 *
 * 1. Claude generates a detailed image prompt from article metadata.
 * 2. Claude (Sonnet) generates the image natively.
 * 3. The base64 image is uploaded to Supabase Storage.
 * 4. The article record is updated with the public URL, alt text, and filename.
 */

import Anthropic from "@anthropic-ai/sdk";
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
// Step 2 – Generate the image via Claude (native image generation)
// ---------------------------------------------------------------------------

const IMAGE_MODEL = "claude-sonnet-4-5-20250514";

export async function generateImage(
  prompt: string
): Promise<{ data: Buffer; mediaType: string }> {
  const anthropic = new Anthropic({ apiKey: requireEnv("ANTHROPIC_API_KEY") });

  const message = await anthropic.messages.create({
    model: IMAGE_MODEL,
    max_tokens: 16000,
    messages: [
      {
        role: "user",
        content: `Generate a photojournalistic editorial hero image for a legal news article. The image should be landscape orientation (16:9 aspect ratio), high quality, and visually compelling.

${prompt}

Generate the image now.`,
      },
    ],
  });

  // Find the image block in the response
  const imageBlock = message.content.find(
    (block): block is Anthropic.ImageBlock => block.type === "image"
  );

  if (!imageBlock) {
    throw new Error("Claude did not return an image");
  }

  const source = imageBlock.source as { type: string; data: string; media_type: string };
  const buffer = Buffer.from(source.data, "base64");
  return { data: buffer, mediaType: source.media_type };
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

  // Step 2: Generate image with Claude Sonnet
  const { data: imageData, mediaType } = await generateImage(prompt);

  // Derive filename extension from media type
  const ext = mediaType === "image/webp" ? "webp" : "png";
  const filename = `${article.slug}.${ext}`;

  // Step 3: Persist to Supabase Storage
  const imageUrl = await persistImage(imageData, filename, mediaType);

  // Step 4: Update the database record
  await updateArticleImage(article.id, imageUrl, altText, filename);

  return { imageUrl, altText, filename };
}
