-- Case candidates table for article backlog / discovery pipeline
-- Stores discovered cases from Perplexity for dedup and generation queue

CREATE TABLE IF NOT EXISTS case_candidates (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id UUID NOT NULL REFERENCES sites(id),
  case_title TEXT NOT NULL,
  defendant TEXT,
  court TEXT,
  filing_date DATE,
  docket_number TEXT,
  source_url TEXT,
  category TEXT,
  content_type TEXT,
  research_summary TEXT,
  discovered_at TIMESTAMPTZ DEFAULT now(),
  processed BOOLEAN DEFAULT false,
  status TEXT DEFAULT 'discovered',
  retry_count INTEGER DEFAULT 0,
  processed_at TIMESTAMPTZ,
  article_id UUID REFERENCES articles(id)
);

-- Index matching the candidate queue selection query
CREATE INDEX idx_case_candidates_queue
  ON case_candidates (site_id, status, category, content_type, discovered_at)
  WHERE processed = false;

-- Database-level uniqueness safeguards (safety net beyond application-level dedup)
CREATE UNIQUE INDEX idx_case_candidates_site_docket_unique
  ON case_candidates (site_id, docket_number)
  WHERE docket_number IS NOT NULL;

CREATE UNIQUE INDEX idx_case_candidates_site_case_title_date_unique
  ON case_candidates (site_id, case_title, filing_date)
  WHERE filing_date IS NOT NULL;

-- RLS
ALTER TABLE case_candidates ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow service role full access on case_candidates"
  ON case_candidates FOR ALL
  USING (true) WITH CHECK (true);
