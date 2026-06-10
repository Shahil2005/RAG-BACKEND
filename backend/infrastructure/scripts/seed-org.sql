-- Run after user signs in via Microsoft OAuth
-- Replace USER_ID with users.id from: SELECT id, email FROM users;

INSERT INTO organizations (name, slug)
VALUES ('Acme Corp', 'acme')
ON CONFLICT (slug) DO NOTHING;

INSERT INTO organization_members (organization_id, user_id, role)
SELECT o.id, 'USER_ID'::uuid, 'owner'
FROM organizations o
WHERE o.slug = 'acme'
ON CONFLICT (organization_id, user_id) DO NOTHING;
