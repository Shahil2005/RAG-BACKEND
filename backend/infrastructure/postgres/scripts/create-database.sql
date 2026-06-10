-- Run against postgres database: psql -U postgres -f create-database.sql
SELECT 'CREATE DATABASE starbot'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'starbot')\gexec
