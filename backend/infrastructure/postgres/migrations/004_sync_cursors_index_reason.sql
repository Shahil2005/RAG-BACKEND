-- Delta sync cursors per user/drive/source
CREATE TABLE IF NOT EXISTS sync_cursors (
  organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  drive_id TEXT NOT NULL DEFAULT '',
  delta_link TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (organization_id, user_id, source, drive_id)
);

-- Track how each file was indexed (full text vs metadata-only)
ALTER TABLE file_metadata ADD COLUMN IF NOT EXISTS index_reason TEXT;
