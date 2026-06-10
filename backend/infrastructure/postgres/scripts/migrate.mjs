/**
 * Run PostgreSQL migrations without psql.
 * From repo root: node infrastructure/postgres/scripts/migrate.mjs
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { createRequire } from 'module';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(__dirname, '..', '..', '..');
const require = createRequire(join(repoRoot, 'apps', 'api', 'package.json'));
const pg = require('pg');

function loadEnv() {
  const paths = [join(repoRoot, '.env'), join(repoRoot, 'apps', 'api', '.env')];
  for (const p of paths) {
    if (!existsSync(p)) continue;
    const text = readFileSync(p, 'utf8');
    for (const line of text.split('\n')) {
      const m = line.match(/^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$/);
      if (!m || process.env[m[1]]) continue;
      process.env[m[1]] = m[2].replace(/^["']|["']$/g, '').trim();
    }
  }
}

loadEnv();

const config = {
  host: process.env.DATABASE_HOST || 'localhost',
  port: Number(process.env.DATABASE_PORT || 5432),
  user: process.env.DATABASE_USER || 'postgres',
  password: process.env.DATABASE_PASSWORD ?? '',
  database: 'postgres',
};

const targetDb = process.env.DATABASE_NAME || 'starbot';

async function runSql(client, filePath) {
  const sql = readFileSync(filePath, 'utf8');
  console.log(`Running ${filePath}...`);
  await client.query(sql);
}

async function main() {
  const admin = new pg.Client(config);
  await admin.connect();

  const exists = await admin.query(
    `SELECT 1 FROM pg_database WHERE datname = $1`,
    [targetDb],
  );
  if (exists.rowCount === 0) {
    console.log(`Creating database "${targetDb}"...`);
    await admin.query(`CREATE DATABASE ${targetDb}`);
  } else {
    console.log(`Database "${targetDb}" already exists.`);
  }
  await admin.end();

  const app = new pg.Client({ ...config, database: targetDb });
  await app.connect();

  const migrationsDir = join(__dirname, '..', 'migrations');
  const hasUsers = await app.query(
    `SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'users'`,
  );
  if (hasUsers.rowCount === 0) {
    await runSql(app, join(migrationsDir, '001_auth_core.sql'));
    await runSql(app, join(migrationsDir, '002_enterprise_schema.sql'));
  } else {
    console.log('Skipping 001/002 (database already initialized).');
  }
  for (const name of [
    '003_file_metadata_drive.sql',
    '004_sync_cursors_index_reason.sql',
    '005_projects.sql',
    '006_project_sectors.sql',
    '007_email_sent_category.sql',
    '008_document_templates_seed.sql',
  ]) {
    const file = join(migrationsDir, name);
    if (existsSync(file)) {
      await runSql(app, file);
    }
  }

  const tables = await app.query(`
    SELECT table_name FROM information_schema.tables
    WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
    ORDER BY table_name
  `);
  console.log('\nTables in database:');
  for (const row of tables.rows) {
    console.log(`  - ${row.table_name}`);
  }

  await app.end();
  console.log('\nMigrations complete.');
}

main().catch((err) => {
  console.error('Migration failed:', err.message);
  if (err.code === 'ECONNREFUSED') {
    console.error('\nPostgreSQL is not running or not reachable.');
    console.error('Install PostgreSQL, start the service, then set DATABASE_* in .env');
  }
  if (err.code === '28P01') {
    console.error('\nWrong password. Set DATABASE_PASSWORD in .env');
  }
  process.exit(1);
});
