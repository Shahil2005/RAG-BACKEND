-- 008: document template authoring columns + default seed templates
--
-- The document_templates table (002) ships empty. This migration adds the
-- authoring columns used by the create API and seeds one ready-to-use default
-- template per supported type (estimate, job_summary, customer_email) for every
-- existing organization, so the chat doc-gen flow and /documents page work out
-- of the box.

ALTER TABLE document_templates
  ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS is_default BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_document_templates_org_type
  ON document_templates(organization_id, type);

-- ---------------------------------------------------------------------------
-- Estimate
-- ---------------------------------------------------------------------------
INSERT INTO document_templates (organization_id, name, type, content, variables, is_default)
SELECT
  o.id,
  'Standard Estimate',
  'estimate',
  $est$# Project Estimate

**Prepared for:** {{customer_name}}
**Project:** {{project_name}}
**Date:** {{date}}

## Scope of Work
{{scope_of_work}}

## Cost Breakdown
- Labor: {{labor_cost}}
- Materials: {{materials_cost}}
- Estimated timeline: {{timeline}}

**Total estimate: {{total_cost}}**

This estimate is valid for 30 days from the date above. Please reach out with any questions.$est$,
  $estv$[
    {"key":"customer_name","label":"Customer name","required":true},
    {"key":"project_name","label":"Project name","required":true},
    {"key":"date","label":"Date","required":false},
    {"key":"scope_of_work","label":"Scope of work","required":true},
    {"key":"labor_cost","label":"Labor cost","required":false},
    {"key":"materials_cost","label":"Materials cost","required":false},
    {"key":"timeline","label":"Estimated timeline","required":false},
    {"key":"total_cost","label":"Total estimate","required":true}
  ]$estv$::jsonb,
  TRUE
FROM organizations o
WHERE NOT EXISTS (
  SELECT 1 FROM document_templates dt
  WHERE dt.organization_id = o.id AND dt.type = 'estimate' AND dt.is_default
);

-- ---------------------------------------------------------------------------
-- Job summary
-- ---------------------------------------------------------------------------
INSERT INTO document_templates (organization_id, name, type, content, variables, is_default)
SELECT
  o.id,
  'Standard Job Summary',
  'job_summary',
  $job$# Job Summary

**Customer:** {{customer_name}}
**Job:** {{job_title}}
**Date completed:** {{date}}
**Technician:** {{technician_name}}

## Work Performed
{{work_performed}}

## Materials Used
{{materials_used}}

## Notes & Recommendations
{{notes}}$job$,
  $jobv$[
    {"key":"customer_name","label":"Customer name","required":true},
    {"key":"job_title","label":"Job title","required":true},
    {"key":"date","label":"Date completed","required":false},
    {"key":"technician_name","label":"Technician","required":false},
    {"key":"work_performed","label":"Work performed","required":true},
    {"key":"materials_used","label":"Materials used","required":false},
    {"key":"notes","label":"Notes & recommendations","required":false}
  ]$jobv$::jsonb,
  TRUE
FROM organizations o
WHERE NOT EXISTS (
  SELECT 1 FROM document_templates dt
  WHERE dt.organization_id = o.id AND dt.type = 'job_summary' AND dt.is_default
);

-- ---------------------------------------------------------------------------
-- Customer-facing email
-- ---------------------------------------------------------------------------
INSERT INTO document_templates (organization_id, name, type, content, variables, is_default)
SELECT
  o.id,
  'Customer Email',
  'customer_email',
  $eml$Subject: {{subject}}

Hi {{customer_name}},

{{body}}

If you have any questions, please don't hesitate to reach out.

Best regards,
{{sender_name}}
{{company_name}}$eml$,
  $emlv$[
    {"key":"subject","label":"Subject","required":true},
    {"key":"customer_name","label":"Customer name","required":true},
    {"key":"body","label":"Message body","required":true},
    {"key":"sender_name","label":"Your name","required":false},
    {"key":"company_name","label":"Company name","required":false}
  ]$emlv$::jsonb,
  TRUE
FROM organizations o
WHERE NOT EXISTS (
  SELECT 1 FROM document_templates dt
  WHERE dt.organization_id = o.id AND dt.type = 'customer_email' AND dt.is_default
);
