-- Project sectors: named subgroups of a project's knowledge base.
-- Uploaded files belong to a sector; project chat can scope retrieval to one.
CREATE TABLE IF NOT EXISTS project_sectors (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (project_id, name)
);

ALTER TABLE project_files
  ADD COLUMN IF NOT EXISTS sector_id UUID REFERENCES project_sectors(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_project_sectors_project ON project_sectors(project_id);
CREATE INDEX IF NOT EXISTS idx_project_files_sector ON project_files(sector_id);
