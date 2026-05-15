# Operator-applied schema migrations

This directory holds ALTER TABLE / DDL changes that have to be run by an
operator against the live ReciterDB MariaDB instance. There is no Alembic /
Liquibase / similar tool in use today — the daily enrichment job is a
single-writer cron, so the bar for tracked schema state is "have we run
this once and committed to that being the current state."

## File naming

`YYYY-MM-DD-short-description.sql`. Date prefix sorts naturally; description
matches the GitHub issue title where one exists.

## Required structure for each migration

Every `.sql` file in this directory must include, in order:

1. A leading comment block: issue link, motivation, blast radius (tables
   touched, expected lock duration, row counts at time of authoring).
2. **Pre-flight checks** as `SELECT` statements the operator runs first to
   confirm the migration applies to their state. Comment-out by default;
   the operator un-comments to run.
3. The forward migration (`ALTER`, `CREATE`, etc.).
4. **Post-flight verification** as `SELECT` statements to confirm the
   change landed correctly.
5. A `-- ROLLBACK` section at the end with the reverse migration, also
   commented-out. The operator can copy that block out and run it if a
   roll-back is needed.

## How to apply

```bash
# 1. Connect to ReciterDB (the operator's connection — credentials in ~/.zshrc):
mysql -h "$DB_HOST" -u "$DB_USERNAME" -p"$DB_PASSWORD" "$DB_NAME"

# 2. Read the migration file. Run the pre-flight SELECT statements first.
# 3. Apply the forward ALTER section.
# 4. Run the post-flight verification.
# 5. Note the date applied in the PR description that introduced the file,
#    or in a follow-up comment on the linked issue. Do not delete or modify
#    the migration file after application — it stays as a permanent record.
```

## Why not Alembic

The daily-enrichment writer is the only application-level writer to these
tables in production; the rest of the schema is shared with the ReCiter
Java engine and PubMed pipeline, which do not use a Python migrations
tool. Introducing Alembic here would create a second source of schema
truth that conflicts with the Java side. Operator-applied SQL keeps the
schema state authoritative in MariaDB itself.
