-- Issue #772: the database states which database it is.
--
-- A D1 binding exposes no database id to the Worker (prepare, batch, exec, dump and withSession are
-- the whole surface), so the Worker cannot compare ids. Instead each database carries one identity
-- row, written by its own migration folder: this one says "analytics", worker/leads-migrations says
-- "leads". The Worker (src/kit/separation.ts) reads the row through each binding and refuses to touch
-- leads unless LEADS_DB answers "leads" and DB answers "analytics". Two names bound to one database
-- answer the same thing, so one of the two checks fails. Additive: the previous Worker ignores it.

CREATE TABLE db_identity (
  role TEXT NOT NULL PRIMARY KEY CHECK (role IN ('analytics', 'leads'))
) WITHOUT ROWID, STRICT;
INSERT INTO db_identity (role) VALUES ('analytics');
