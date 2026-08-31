# Backup and restore operations

`backup.py` is a small Python 3.11+ and Docker Compose helper for the private
release. It writes snapshots with mode `0700` on directories and `0600` on
files. A snapshot is committed by an atomic directory rename, and the manifest
contains a SHA-256 and byte size for every artifact.

## Scope

The snapshot contains only these product volumes:

- `seo_config`
- `seo_state`
- `seo_reports`
- `seo_history`
- `seo_evidence`

The PostgreSQL part is a logical `pg_dump` from `crawlseo-db`, database
`crawlseo`. The raw `crawlseo_db` volume is not copied.

The manifest explicitly records these excluded non-sensitive categories:

- `deploy/secrets`, including API tokens, passwords, and Compose secret files;
- `deploy/bindings`, including device IDs and agent binding records;
- `agent_zero_usr`, including Agent Zero data, provider credentials, and
  sessions;
- raw PostgreSQL volume files, because only the logical dump is included.

The helper never reads those paths as backup inputs. It rejects traversal,
symlinks, duplicate archive paths, hard links, device files, and unexpected
snapshot files during verification and restore staging.

## Create and verify

Choose a backup location outside the release directory. The default is
`~/.local/share/extella-seo-employee/backups`; `--backup-dir` can select an
encrypted or otherwise protected host location.

On a host with more than one Compose project, always pass the exact project
name used to deploy this release. This makes every Compose metadata, service,
database, and volume operation address only that project. On CT160 the v1
release project is `extella-seo-release`:

`--project-name` does not create Docker networks or change Docker daemon
configuration. If Docker reports exhausted default address pools, provision the
release-specific networks with approved free subnets before deployment; do not
change the global Docker address-pool configuration as part of backup work.

```sh
python3 deploy/backup.py create \
  --backup-dir /secure/extella-backups \
  --compose-file deploy/compose.yaml \
  --project-name extella-seo-release
python3 deploy/backup.py verify --backup-dir /secure/extella-backups <snapshot-id>
```

During `create`, currently running Compose services are quiesced, the database
is dumped, and the five product volumes are archived. The initial running
service set is started again in a `finally` path, including after a failed
snapshot. If `crawlseo-db` was stopped but has an existing container, it is
started only for the dump and then returned to stopped state.

## Restore check

The default `restore-check` verifies hashes and safely extracts every volume
archive into a temporary directory. It does not call Docker, write a Docker
volume, run `psql`, or mutate production. The temporary directory is removed
after the check.

```sh
python3 deploy/backup.py restore-check --backup-dir /secure/extella-backups <snapshot-id>
```

Production restore is a separate explicit action. `--apply` is required. The
script verifies the manifest first, stages all archives in a temporary area,
then stops the running product services, restores the logical database and
product volumes, and restores the initial service state.

```sh
python3 deploy/backup.py restore-check \
  --backup-dir /secure/extella-backups \
  --compose-file deploy/compose.yaml \
  --project-name extella-seo-release \
  --apply <snapshot-id>
```

Cross-volume rollback is not transactional. Keep an independent backup and
confirm the target snapshot before using `--apply`.

## Prune

Pruning verifies snapshots and keeps the newest `7` by default. The default is
always a dry-run:

```sh
python3 deploy/backup.py prune --backup-dir /secure/extella-backups --keep 7
```

Review `planned` before explicitly deleting the planned snapshots:

```sh
python3 deploy/backup.py prune --backup-dir /secure/extella-backups --keep 7 --apply
```

Malformed or unsafe snapshots are retained and reported as
`invalid_retained`; they are never selected for deletion.
