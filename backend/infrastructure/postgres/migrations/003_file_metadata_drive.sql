-- Extra columns for SharePoint/OneDrive re-download and debugging
ALTER TABLE file_metadata ADD COLUMN IF NOT EXISTS drive_id TEXT;
ALTER TABLE file_metadata ADD COLUMN IF NOT EXISTS site_id TEXT;
ALTER TABLE file_metadata ADD COLUMN IF NOT EXISTS mime_type TEXT;
