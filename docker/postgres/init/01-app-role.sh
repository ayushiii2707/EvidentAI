#!/bin/sh
# Runs once when the data volume is first created.
# evidentai_owner (POSTGRES_USER) owns the schema and runs migrations.
# evidentai_app is what the API and workers connect as: not an owner, not a superuser,
# and without BYPASSRLS, so row-level security policies apply to it.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_password="$EVIDENTAI_APP_PASSWORD" <<'SQL'
CREATE ROLE evidentai_app LOGIN PASSWORD :'app_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
SQL
