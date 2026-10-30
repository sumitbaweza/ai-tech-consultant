#!/bin/sh
# Runs once, when the Postgres volume is first created (docker-entrypoint-initdb.d).
# It never runs again on an existing volume, so all three tasks are set up here, on Day 1.
#
# Each task gets its own login role and a schema owned by that role. A new schema grants
# nothing to other roles, so the inbox and scope roles cannot read the Brain's tables:
# Tasks 2 and 3 have to use the Brain's API. Each schema has the same name as its role,
# so Postgres's default search_path ("$user", public) finds the task's own tables first
# and pgvector's types in public.
#
# No `set -u` here: if this file is not executable, the Postgres image sources it into
# its own entrypoint shell, where `-u` would leak into the entrypoint.
set -e

# Roles belong to the whole server, not one database, so they are created once.
# psql's :'name' syntax inserts the variable as a safely quoted string.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v brain_pw="$BRAIN_DB_PASSWORD" \
  -v inbox_pw="$INBOX_DB_PASSWORD" \
  -v scope_pw="$SCOPE_DB_PASSWORD" <<'EOSQL'
CREATE ROLE brain LOGIN PASSWORD :'brain_pw';
CREATE ROLE inbox LOGIN PASSWORD :'inbox_pw';
CREATE ROLE scope LOGIN PASSWORD :'scope_pw';
CREATE DATABASE consultant_test;
EOSQL

# The extension and the schemas live inside each database: the main one and the test one.
for db in "$POSTGRES_DB" consultant_test; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" <<'EOSQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA brain AUTHORIZATION brain;
CREATE SCHEMA inbox AUTHORIZATION inbox;
CREATE SCHEMA scope AUTHORIZATION scope;
EOSQL
done
