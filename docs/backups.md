# Backups & disaster recovery

MailMind's SQLite database (`$MAILMIND_DB_PATH`, `/data/mailmind.db` on Fly) is
continuously replicated to S3-compatible object storage via
[Litestream](https://litestream.io) — see `litestream.yml`. WAL writes stream
out roughly as they happen (`sync-interval: 10s`) and 7 days of point-in-time
history are retained (`retention: 168h`), so a corruption or bad write noticed
days later can still be recovered from.

## Recommended replica target: Cloudflare R2

`litestream.yml` talks to whatever endpoint `$LITESTREAM_S3_ENDPOINT` points
at — it's a generic S3-compatible config, not tied to one provider. Cloudflare
R2 is the recommended target:

- S3-compatible (works with this config unchanged)
- **Zero egress fees** — a restore pulls the entire DB back down; egress-priced
  storage (e.g. AWS S3) turns every restore, and every disaster-recovery drill
  restore, into a bill
- Generous free tier for a single-user mailbox DB

Endpoint format: `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`

AWS S3, Backblaze B2, MinIO, and Tigris all work as drop-in alternatives via
the same `$LITESTREAM_S3_ENDPOINT` / `$LITESTREAM_ACCESS_KEY_ID` /
`$LITESTREAM_SECRET_ACCESS_KEY` / `$LITESTREAM_S3_BUCKET` env vars.

## Restore procedure

To restore the latest replicated snapshot + WAL history into a database file:

```bash
litestream restore -config litestream.yml -o /path/to/restored.db $MAILMIND_DB_PATH
```

- `-config litestream.yml` — this repo's Litestream config (selects the
  replica/bucket to restore from).
- `$MAILMIND_DB_PATH` — the original DB path as it appears in `litestream.yml`'s
  `dbs[].path`; Litestream uses it to find the matching replica configuration,
  not to decide where to write.
- `-o /path/to/restored.db` — where the restored database is actually written.
  **Always point this at a scratch path**, never back at the live
  `$MAILMIND_DB_PATH`, unless you specifically intend to overwrite the running
  app's data (that's what `fly-start.sh`'s own boot-time
  `-if-replica-exists` restore does, and only because nothing is running yet).

To restore to a specific point in time instead of the latest snapshot, add
`-timestamp <RFC3339 time>`.

On Fly, run this from a one-off machine or via `fly ssh console`, with the
same `LITESTREAM_*` env vars exported so the config's env-var substitutions
resolve.

## An untested backup isn't a backup

Replication running without errors in the logs is not proof the data is
actually recoverable — schema drift, a bad retention setting, or a silent
permissions issue on the bucket can all leave you with a replica that looks
fine and restores nothing useful.

**Periodically run an actual restore into a scratch path** (e.g. monthly, or
before/after any Litestream config change) and confirm:

1. The restore command above completes without error.
2. The resulting file is a valid SQLite DB (e.g. `sqlite3 /path/to/restored.db
   'PRAGMA integrity_check;'`).
3. Row counts look sane (`sqlite3 /path/to/restored.db 'SELECT COUNT(*) FROM
   emails;'`) and roughly match what you'd expect from the live DB.

Delete the scratch file afterward — it's a full copy of mailbox data.
