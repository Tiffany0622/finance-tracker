#!/bin/sh
set -eu
# psql reads the environment without printing the password in command arguments.
psql --username "$POSTGRES_USER" --dbname postgres --set ON_ERROR_STOP=1 <<'SQL'
\getenv app_password APP_DB_PASSWORD
SELECT format('CREATE ROLE finance LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', :'app_password') \gexec
CREATE DATABASE finance OWNER finance;
REVOKE ALL ON DATABASE finance FROM PUBLIC;
GRANT CONNECT ON DATABASE finance TO finance;
SQL
