-- 009: seed default templates for the remaining document types (quotation, report)
--
-- Migration 008 seeded estimate / job_summary / customer_email. The
-- DocumentTemplateType enum also supports `quotation` and `report`, which until
-- now had no default template -- so the chat doc-gen flow and /documents page had
-- nothing to fall back on for those types. This migration seeds one ready-to-use
-- default per type for every existing organization. Idempotent: each INSERT is
-- guarded by NOT EXISTS on (organization_id, type, is_default), matching 008.
--
-- Structure follows common professional conventions: quotations carry a quote
-- number + validity window, an itemized cost breakdown, payment terms and an
-- exclusions clause; reports follow the executive-summary -> objectives -> work
-- performed -> results -> recommendations -> next-steps flow.

-- ---------------------------------------------------------------------------
-- Quotation
-- ---------------------------------------------------------------------------
INSERT INTO document_templates (organization_id, name, type, content, variables, is_default)
SELECT
  o.id,
  'Standard Quotation',
  'quotation',
  $quo$# Quotation

**Quote #:** {{quote_number}}
**Date:** {{date}}
**Valid until:** {{valid_until}}

**Prepared for:** {{customer_name}}
**Project:** {{project_name}}
**Prepared by:** {{prepared_by}}
{{company_name}}

## Scope of Work
{{scope_of_work}}

## Cost Breakdown
{{line_items}}

| | |
| --- | --- |
| Subtotal | {{subtotal}} |
| Tax | {{tax}} |
| **Total** | **{{total}}** |

## Payment Terms
{{payment_terms}}

## Exclusions
The following are not included in this quotation:
{{exclusions}}

This quotation is valid until the date shown above and is subject to our standard terms and conditions. Please contact us with any questions or to proceed.$quo$,
  $quov$[
    {"key":"quote_number","label":"Quote number","required":false},
    {"key":"date","label":"Date","required":false},
    {"key":"valid_until","label":"Valid until","required":false},
    {"key":"customer_name","label":"Customer name","required":true},
    {"key":"project_name","label":"Project name","required":true},
    {"key":"prepared_by","label":"Prepared by","required":false},
    {"key":"company_name","label":"Company name","required":false},
    {"key":"scope_of_work","label":"Scope of work","required":true},
    {"key":"line_items","label":"Itemized costs (labor, materials, services)","required":true},
    {"key":"subtotal","label":"Subtotal","required":false},
    {"key":"tax","label":"Tax","required":false},
    {"key":"total","label":"Total","required":true},
    {"key":"payment_terms","label":"Payment terms","required":false},
    {"key":"exclusions","label":"Exclusions","required":false}
  ]$quov$::jsonb,
  TRUE
FROM organizations o
WHERE NOT EXISTS (
  SELECT 1 FROM document_templates dt
  WHERE dt.organization_id = o.id AND dt.type = 'quotation' AND dt.is_default
);

-- ---------------------------------------------------------------------------
-- Report
-- ---------------------------------------------------------------------------
INSERT INTO document_templates (organization_id, name, type, content, variables, is_default)
SELECT
  o.id,
  'Standard Report',
  'report',
  $rep$# {{report_title}}

**Client:** {{customer_name}}
**Project:** {{project_name}}
**Date:** {{date}}
**Prepared by:** {{prepared_by}}

## Executive Summary
{{executive_summary}}

## Objectives
{{objectives}}

## Work Performed
{{work_performed}}

## Results & Outcomes
{{results}}

## Challenges & Lessons Learned
{{challenges}}

## Recommendations
{{recommendations}}

## Next Steps
{{next_steps}}$rep$,
  $repv$[
    {"key":"report_title","label":"Report title","required":true},
    {"key":"customer_name","label":"Client","required":false},
    {"key":"project_name","label":"Project name","required":true},
    {"key":"date","label":"Date","required":false},
    {"key":"prepared_by","label":"Prepared by","required":false},
    {"key":"executive_summary","label":"Executive summary","required":true},
    {"key":"objectives","label":"Objectives","required":false},
    {"key":"work_performed","label":"Work performed","required":true},
    {"key":"results","label":"Results & outcomes","required":false},
    {"key":"challenges","label":"Challenges & lessons learned","required":false},
    {"key":"recommendations","label":"Recommendations","required":false},
    {"key":"next_steps","label":"Next steps","required":false}
  ]$repv$::jsonb,
  TRUE
FROM organizations o
WHERE NOT EXISTS (
  SELECT 1 FROM document_templates dt
  WHERE dt.organization_id = o.id AND dt.type = 'report' AND dt.is_default
);
