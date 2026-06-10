CREATE TABLE projects (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT,
  custom_instructions TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE project_files (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  file_name TEXT NOT NULL,
  storage_path TEXT NOT NULL,
  mime_type TEXT,
  size_bytes BIGINT,
  is_indexed BOOLEAN NOT NULL DEFAULT FALSE,
  indexed_at TIMESTAMPTZ,
  index_reason TEXT,
  chunk_count INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE chat_sessions
  ADD COLUMN project_id UUID REFERENCES projects(id) ON DELETE CASCADE;

CREATE INDEX idx_projects_user_org ON projects(user_id, organization_id);
CREATE INDEX idx_chat_sessions_project ON chat_sessions(project_id);
CREATE INDEX idx_project_files_project ON project_files(project_id);

ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_files ENABLE ROW LEVEL SECURITY;

CREATE POLICY projects_policy ON projects FOR ALL
  USING (user_id = auth.uid() AND user_in_organization(organization_id));

CREATE POLICY project_files_policy ON project_files FOR ALL
  USING (
    EXISTS (
      SELECT 1 FROM projects p
      WHERE p.id = project_id AND p.user_id = auth.uid()
    )
  );
