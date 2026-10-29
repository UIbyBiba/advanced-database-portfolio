#!/usr/bin/env bash
# =====================================================================
# MIT 8103 - Portfolio 5: PostgreSQL 16 streaming replication for EkoDash
# Creates a hot-standby replica (port 5433) of the primary (port 5432).
# Run as root on a machine where the Portfolio 1 primary is running.
# =====================================================================
set -euo pipefail
PGBIN=/usr/lib/postgresql/16/bin
PRIMARY_DATA=/srv/pg/primary
REPLICA_DATA=/srv/pg/replica

echo "--- 1. Primary settings needed for replication"
su postgres -c "psql -h localhost -p 5432 -qc \"ALTER SYSTEM SET wal_level = 'replica'\""
su postgres -c "psql -h localhost -p 5432 -qc \"ALTER SYSTEM SET max_wal_senders = 5\""
su postgres -c "psql -h localhost -p 5432 -qc \"ALTER SYSTEM SET hot_standby = on\""
su postgres -c "psql -h localhost -p 5432 -qc \"CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'ekodash-repl'\"" || true
su postgres -c "psql -h localhost -p 5432 -qc \"SELECT pg_create_physical_replication_slot('ekodash_replica_1')\"" || true
su postgres -c "$PGBIN/pg_ctl -D $PRIMARY_DATA restart -m fast -l /srv/pg/primary.log -o '-p 5432'" > /dev/null

echo "--- 2. Clone the primary with pg_basebackup (-R writes standby.signal + primary_conninfo)"
rm -rf "$REPLICA_DATA"
su postgres -c "$PGBIN/pg_basebackup -h localhost -p 5432 -U replicator -D $REPLICA_DATA \
               -X stream -S ekodash_replica_1 -R -P" 2>&1 | tail -1

echo "--- 3. Start the replica on port 5433"
su postgres -c "$PGBIN/pg_ctl -D $REPLICA_DATA -l /srv/pg/replica.log -o '-p 5433' start" > /dev/null
sleep 2
su postgres -c "psql -h localhost -p 5433 -Atc 'SELECT pg_is_in_recovery()'" | sed 's/^/replica pg_is_in_recovery = /'
