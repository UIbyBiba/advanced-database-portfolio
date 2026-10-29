"""
MIT 8103 - Portfolio 5: PostgreSQL replication experiments (EkoDash)

  R1  replication status on the primary (pg_stat_replication)
  R2  a new order written on the primary - how long until the replica sees it?
  R3  the replica refuses writes (hot standby is read-only)
  R4  read scaling - management report served by the replica
  R5  lag under a burst of writes (bytes and time behind)
  R6  asynchronous vs synchronous commit - the latency price of zero data loss
  R7  failover - primary crashes, replica is promoted and accepts writes
"""
import time, statistics, subprocess
import psycopg

PRIMARY = "host=localhost port=5432 dbname=ekodash user=postgres"
REPLICA = "host=localhost port=5433 dbname=ekodash user=postgres"
PGBIN = "/usr/lib/postgresql/16/bin"


def h(t):
    print("\n" + "=" * 74 + f"\n{t}\n" + "=" * 74, flush=True)


def table(cur):
    cols = [d.name for d in cur.description]
    rows = cur.fetchall()
    w = [max(len(str(c)), *(len(str(r[i])) for r in rows)) for i, c in enumerate(cols)] if rows else [len(c) for c in cols]
    print(" | ".join(str(c).ljust(w[i]) for i, c in enumerate(cols)))
    print("-+-".join("-" * x for x in w))
    for r in rows:
        print(" | ".join(str(v).ljust(w[i]) for i, v in enumerate(r)))


def pg(cmd):
    return subprocess.run(["su", "postgres", "-c", cmd], capture_output=True, text=True)


def main():
    p = psycopg.connect(PRIMARY, autocommit=True)
    r = psycopg.connect(REPLICA, autocommit=True)

    h("R1  Replication status (run on the PRIMARY)")
    table(p.execute("""SELECT application_name, client_addr, state, sync_state,
                              pg_current_wal_lsn() AS primary_lsn, replay_lsn,
                              pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS bytes_behind
                       FROM pg_stat_replication"""))
    print("replica pg_is_in_recovery():", r.execute("SELECT pg_is_in_recovery()").fetchone()[0])

    h("R2  Write on primary -> visible on replica")
    oid = p.execute("""INSERT INTO ekodash.delivery_order (customer_id, pickup_address_id, dropoff_street,
                          dropoff_zone_id, recipient_phone, package_size, weight_kg, fee_amount)
                       VALUES (1, 1, '9 Replication Road', 1, '+2348000000999', 'S', 1, 1500)
                       RETURNING order_id""").fetchone()[0]
    t0 = time.perf_counter()
    while not r.execute("SELECT 1 FROM ekodash.delivery_order WHERE order_id = %s", (oid,)).fetchone():
        time.sleep(0.0005)
    r2_order = oid
    print(f"order {oid} committed on primary; visible on replica after {(time.perf_counter() - t0) * 1000:.1f} ms")

    h("R3  Attempt to write on the replica")
    try:
        r.execute("UPDATE ekodash.zone SET base_fee = 1 WHERE zone_id = 1")
    except psycopg.errors.ReadOnlySqlTransaction as e:
        print(f"ERROR {e.sqlstate}: {e}")

    h("R4  Read scaling - weekly zone revenue report executed on the REPLICA")
    t0 = time.perf_counter()
    cur = r.execute("""SELECT z.zone_name, count(*) orders, sum(d.fee_amount) revenue
                       FROM ekodash.delivery_order d JOIN ekodash.zone z ON z.zone_id = d.dropoff_zone_id
                       WHERE d.created_at >= now() - interval '7 days' AND d.status = 'DELIVERED'
                       GROUP BY 1 ORDER BY 3 DESC LIMIT 5""")
    table(cur)
    print(f"(served by replica in {(time.perf_counter() - t0) * 1000:.1f} ms - no load on the primary)")

    h("R5  Replication lag during a burst of 300 000 history rows")
    p.execute("CREATE TABLE IF NOT EXISTS ekodash.lag_probe AS SELECT * FROM ekodash.order_status_history WITH NO DATA")
    samples = []
    import threading
    def burst():
        with psycopg.connect(PRIMARY, autocommit=True) as c:
            c.execute("INSERT INTO ekodash.lag_probe SELECT * FROM ekodash.order_status_history LIMIT 300000")
    t = threading.Thread(target=burst); t.start()
    while t.is_alive() or (samples and samples[-1][1] > 0):
        row = p.execute("""SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn), replay_lag
                           FROM pg_stat_replication""").fetchone()
        samples.append((time.perf_counter(), int(row[0] or 0), row[1]))
        time.sleep(0.05)
        if len(samples) > 400: break
    t.join()
    peak = max(samples, key=lambda s: s[1])
    print(f"samples taken: {len(samples)}; peak bytes behind = {peak[1]:,} ({peak[1]/1e6:.1f} MB), "
          f"replay_lag at peak = {peak[2]}")
    print(f"final bytes behind = {samples[-1][1]:,}  -> replica caught up")
    p.execute("DROP TABLE ekodash.lag_probe")

    h("R6  Commit latency: asynchronous vs synchronous replication (200 single-row commits each)")
    results = {}
    for mode, names in (("async  (synchronous_commit=local)", ""), ("sync   (synchronous_commit=remote_apply)", "*")):
        p.execute(f"ALTER SYSTEM SET synchronous_standby_names = '{names}'"); p.execute("SELECT pg_reload_conf()")
        time.sleep(1)
        lat = []
        with psycopg.connect(PRIMARY) as c:
            c.execute("SET synchronous_commit = " + ("remote_apply" if names else "local")); c.commit()
            for i in range(200):
                t0 = time.perf_counter()
                c.execute("UPDATE ekodash.wallet SET updated_at = now() WHERE wallet_id = %s", (i + 1,))
                c.commit()
                lat.append((time.perf_counter() - t0) * 1000)
        results[mode] = lat
        print(f"{mode}: median {statistics.median(lat):.3f} ms   p95 {sorted(lat)[189]:.3f} ms")
    sync_state = p.execute("SELECT sync_state FROM pg_stat_replication").fetchone()[0]
    print(f"pg_stat_replication.sync_state while synchronous = '{sync_state}'")
    p.execute("ALTER SYSTEM SET synchronous_standby_names = ''"); p.execute("SELECT pg_reload_conf()")

    h("R7  Failover: primary crashes, replica is promoted")
    p.close()
    out = pg(f"{PGBIN}/pg_ctl -D /srv/pg/primary stop -m immediate")
    print("pg_ctl stop -m immediate (simulated crash):", out.stdout.strip().splitlines()[-1])
    try:
        psycopg.connect(PRIMARY, connect_timeout=2)
    except psycopg.OperationalError:
        print("primary unreachable: connection refused")
    t0 = time.perf_counter()
    out = pg(f"{PGBIN}/pg_ctl -D /srv/pg/replica promote -w")
    print("pg_ctl promote:", out.stdout.strip().splitlines()[-1], f"({(time.perf_counter() - t0):.2f} s)")
    r.close()
    with psycopg.connect(REPLICA, autocommit=True) as nr:
        print("new primary pg_is_in_recovery():", nr.execute("SELECT pg_is_in_recovery()").fetchone()[0])
        oid = nr.execute("""INSERT INTO ekodash.delivery_order (customer_id, pickup_address_id, dropoff_street,
                              dropoff_zone_id, recipient_phone, package_size, weight_kg, fee_amount)
                            VALUES (1, 1, '1 Failover Avenue', 1, '+2348000000998', 'S', 1, 1500)
                            RETURNING order_id""").fetchone()[0]
        print(f"write accepted on promoted node: order {oid}")
        print(f"order {r2_order} written in R2 (before the crash) is present on the new primary:",
              bool(nr.execute("SELECT 1 FROM ekodash.delivery_order WHERE order_id = %s", (r2_order,)).fetchone()))
        print(f"identity gap {r2_order} -> {oid}: PostgreSQL logs sequence values to WAL in batches of 32, "
              "so pre-allocated values are skipped after a crash/promotion (expected, not data loss)")


if __name__ == "__main__":
    main()
