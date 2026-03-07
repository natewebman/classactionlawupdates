-- Add unsubscribe support to subscribers table
-- The existing `status` column (text, default 'active') is already used for subscribe state.
-- We add `unsubscribed_at` to track when a user unsubscribed.

-- Add unsubscribed_at column
ALTER TABLE subscribers
  ADD COLUMN IF NOT EXISTS unsubscribed_at timestamptz DEFAULT NULL;

-- Backfill: ensure all existing rows have status = 'active' (should already be the case)
UPDATE subscribers SET status = 'active' WHERE status IS NULL;

-- Allow anon role to update subscribers (needed for client-side unsubscribe)
-- The existing RLS policy should already allow inserts; add update permission
-- scoped to the status and unsubscribed_at columns only.
-- NOTE: Run this in Supabase SQL Editor if RLS policies need adjustment.
