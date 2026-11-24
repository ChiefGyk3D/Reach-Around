-- Issue #772: the database states which database it is. See worker/migrations/0020_db_identity.sql.
-- This folder writes "leads"; the analytics folder writes "analytics". The Worker refuses to serve
-- leads unless each binding reads back its own role and only that role.

CREATE TABLE db_identity (
  role TEXT NOT NULL PRIMARY KEY CHECK (role IN ('analytics', 'leads'))
) WITHOUT ROWID, STRICT;
INSERT INTO db_identity (role) VALUES ('leads');
