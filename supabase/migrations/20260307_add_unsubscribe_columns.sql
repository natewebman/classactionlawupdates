-- Create subscribers table (if it doesn't exist) and add unsubscribe support.
-- Fields match what src/lib/supabase.ts addSubscriber() and unsubscribeEmail() expect.

CREATE TABLE IF NOT EXISTS subscribers (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  site_id uuid NOT NULL REFERENCES sites(id),
  email text NOT NULL,
  name text,
  source text DEFAULT 'website_form',
  utm_source text,
  utm_campaign text,
  status text DEFAULT 'active',
  unsubscribed_at timestamptz,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  UNIQUE (site_id, email)
);

-- Enable RLS
ALTER TABLE subscribers ENABLE ROW LEVEL SECURITY;

-- Allow anon to insert (subscribe)
CREATE POLICY "Allow anon to insert subscribers"
  ON subscribers
  FOR INSERT
  TO anon
  WITH CHECK (true);

-- Allow anon to select (needed for unsubscribe lookup)
CREATE POLICY "Allow anon to select subscribers"
  ON subscribers
  FOR SELECT
  TO anon
  USING (true);

-- Allow anon to update (needed for unsubscribe status change)
CREATE POLICY "Allow anon to update subscribers"
  ON subscribers
  FOR UPDATE
  TO anon
  USING (true)
  WITH CHECK (true);

-- Grant permissions to anon role
GRANT SELECT, INSERT, UPDATE ON subscribers TO anon;
