-- Add a 'sent' category so emails the user sent are classified/grouped separately
-- from received mail (pending_action / important / closed / spam).
ALTER TYPE email_category ADD VALUE IF NOT EXISTS 'sent';
