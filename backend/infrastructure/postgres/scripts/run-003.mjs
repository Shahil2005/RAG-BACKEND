/**
 * Apply pending migration 003 only (safe on existing DB).
 * Usage: node infrastructure/postgres/scripts/run-003.mjs
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { createRequire } from 'module';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(__dirname, '..', '..', '..');
const require = createRequire(join(repoRoot, 'apps', 'api', 'package.json'));
const pg = require('pg');

for (const p of [join(repoRoot, '.env'), join(repoRoot, 'apps', 'api', '.env')]) {
  if (!existsSync(p)) continue;
  for (const line of readFileSync(p, 'utf8').split('\n')) {
    const m = line.match(/^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$/);
    if (m && !process.env[m[1]]) {
      process.env[m[1]] = m[2].replace(/^["']|["']$/g, '').trim();
    }
  }
}

const client = new pg.Client({
  host: process.env.DATABASE_HOST || 'localhost',
  port: Number(process.env.DATABASE_PORT || 5432),
  user: process.env.DATABASE_USER || 'postgres',
  password: process.env.DATABASE_PASSWORD ?? '',
  database: process.env.DATABASE_NAME || 'starbot',
});

const sql = readFileSync(
  join(__dirname, '..', 'migrations', '003_file_metadata_drive.sql'),
  'utf8',
);

await client.connect();
await client.query(sql);
const cols = await client.query(`
  SELECT column_name FROM information_schema.columns
  WHERE table_name = 'file_metadata'
    AND column_name IN ('drive_id', 'site_id', 'mime_type')
  ORDER BY column_name
`);
console.log('Applied 003_file_metadata_drive.sql');
console.log('Columns:', cols.rows.map((r) => r.column_name).join(', ') || '(none)');
await client.end();
