"""
MIT 8103 - Portfolio 5: MongoDB replication and sharding experiments (EkoDash)

Replica set "ekors" (3 members)
  M1  member states
  M2  write concern: w:1 vs w:"majority" latency
  M3  read from a secondary (read scaling) and read concern "majority"
  M4  multi-document ACID transaction (order + wallet in 2 collections)
  M5  automatic failover: primary is killed, election, client keeps writing

Sharded cluster (mongos :27200, shards shardLagos / shardAbujaPH)
  S1  shard key + zone sharding by city (data locality)
  S2  data distribution after loading the 90-day order set
  S3  targeted query (shard key in filter) vs scatter-gather query
"""
import os, time, signal, subprocess, statistics, datetime
from pymongo import MongoClient, ReadPreference, WriteConcern
from pymongo.read_concern import ReadConcern
from pymongo.errors import PyMongoError
from bson.decimal128 import Decimal128

RS_URI = "mongodb://127.0.0.1:27101,127.0.0.1:27102,127.0.0.1:27103/?replicaSet=ekors"
MONGOS = "mongodb://127.0.0.1:27200"
SOURCE = "mongodb://127.0.0.1:27017"     # Portfolio 4 database (90 days of orders)


def h(t):
    print("\n" + "=" * 74 + f"\n{t}\n" + "=" * 74, flush=True)


def members(client):
    st = client.admin.command("replSetGetStatus")
    return {m["name"]: m["stateStr"] for m in st["members"]}


def replica_set_tests():
    client = MongoClient(RS_URI, retryWrites=True, serverSelectionTimeoutMS=20000)
    db = client.ekodash
    db.orders.drop(); db.wallets.drop()

    h("M1  Replica set members")
    for name, state in members(client).items():
        print(f"{name:18s} {state}")

    h("M2  Write concern latency (500 inserts each)")
    docs = list(MongoClient(SOURCE).ekodash.orders.find().limit(1000))
    for label, wc in (("w:1         (ack by primary only)", WriteConcern(w=1)),
                      ("w:majority  (ack by 2 of 3 members)", WriteConcern(w="majority"))):
        coll = db.get_collection("orders", write_concern=wc)
        lat = []
        for d in (docs[:500] if "w:1" in label else docs[500:]):
            t0 = time.perf_counter(); coll.insert_one(d); lat.append((time.perf_counter() - t0) * 1000)
        print(f"{label}: median {statistics.median(lat):.3f} ms  p95 {sorted(lat)[474]:.3f} ms")
    print("w:majority survives the loss of the primary; w:1 can be rolled back if the primary dies before replicating.")

    h("M3  Reading from a SECONDARY with readConcern majority")
    sec = db.get_collection("orders", read_preference=ReadPreference.SECONDARY, read_concern=ReadConcern("majority"))
    t0 = time.perf_counter()
    n = sec.count_documents({"status": "DELIVERED"})
    served_by = sec.database.client.admin.command("hello")  # metadata only
    cur = sec.find({"status": "DELIVERED"}).limit(1); doc = next(cur); addr = cur.address
    print(f"count DELIVERED = {n} in {(time.perf_counter() - t0) * 1000:.1f} ms, cursor served by {addr[0]}:{addr[1]} "
          f"({members(client)[f'{addr[0]}:{addr[1]}']})")

    h("M4  Multi-document transaction: create order AND debit customer wallet atomically")
    db.wallets.insert_one({"_id": "cust:1", "balance": Decimal128("3000.00")})
    def pay(session, order_id, fee):
        w = db.wallets.find_one({"_id": "cust:1"}, session=session)
        if w["balance"].to_decimal() < fee:
            raise ValueError(f"insufficient balance {w['balance']} < {fee}")
        db.wallets.update_one({"_id": "cust:1"}, {"$inc": {"balance": Decimal128(str(-fee))}}, session=session)
        db.orders.insert_one({"_id": order_id, "status": "PENDING", "fee": Decimal128(str(fee)),
                              "created_at": datetime.datetime.now(datetime.timezone.utc)}, session=session)
    for oid, fee in ((900001, 2000), (900002, 2500)):
        with client.start_session() as s:
            try:
                s.with_transaction(lambda ss: pay(ss, oid, fee),
                                   read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority"))
                print(f"order {oid} fee {fee}: COMMITTED")
            except ValueError as e:
                print(f"order {oid} fee {fee}: ABORTED ({e})")
        bal = db.wallets.find_one({'_id': 'cust:1'})['balance']
        print(f"   wallet = {bal}, order exists = {db.orders.count_documents({'_id': oid}) == 1}")

    h("M5  Failover: kill -9 the PRIMARY while the app keeps writing")
    primary = client.admin.command("hello")["primary"]
    port = primary.split(":")[1]
    pid = int(subprocess.run(["pgrep", "-f", f"rs-{port} "], capture_output=True, text=True).stdout.split()[0])
    print(f"primary = {primary} (pid {pid}); sending SIGKILL")
    os.kill(pid, signal.SIGKILL)
    t_kill = time.perf_counter()
    ok, failed = 0, 0
    first_ok_after = None
    for i in range(40):
        try:
            db.orders.insert_one({"_id": 910000 + i, "status": "PENDING", "note": "written during failover"})
            ok += 1
            if first_ok_after is None:
                first_ok_after = time.perf_counter() - t_kill
        except PyMongoError as e:
            failed += 1
        time.sleep(0.25)
    new_primary = client.admin.command("hello")["primary"]
    print(f"new primary elected: {new_primary}")
    print(f"first successful write {first_ok_after:.2f} s after the kill (driver retried); ok={ok} failed={failed}")
    for name, state in members(client).items():
        print(f"   {name:18s} {state}")
    print(f"restarting old node {primary} ...")
    subprocess.run(["/opt/mongo/bin/mongod", "--dbpath", f"/srv/mongo/rs-{port}", "--port", port, "--bind_ip", "127.0.0.1",
                    "--fork", "--logpath", f"/srv/mongo/rs-{port}.log", "--wiredTigerCacheSizeGB", "0.25",
                    "--replSet", "ekors"], capture_output=True)
    for _ in range(60):
        st = members(client).get(primary)
        if st in ("SECONDARY", "PRIMARY"):
            break
        time.sleep(1)
    print(f"old node rejoined as: {members(client)[primary]}")
    for _ in range(60):          # priority 2 member takes back the primary role once caught up
        if client.admin.command("hello").get("primary") == primary:
            break
        time.sleep(1)
    for name, state in members(client).items():
        print(f"   {name:18s} {state}")
    print(f"writes made during failover present after recovery: {db.orders.count_documents({'_id': {'$gte': 910000}})}")


def sharding_tests():
    c = MongoClient(MONGOS)
    admin = c.admin
    db = c.ekodash
    db.orders.drop()

    h("S1  Shard key {city: 1, _id: 1} with ZONE sharding (each city's data lives on a chosen shard)")
    admin.command("enableSharding", "ekodash")
    admin.command("addShardToZone", "shardLagos", zone="LAGOS")
    admin.command("addShardToZone", "shardAbujaPH", zone="ABUJA_PH")
    from bson.min_key import MinKey
    from bson.max_key import MaxKey
    for t in list(c.config.tags.find({"ns": "ekodash.orders"})):          # clean re-runs
        admin.command("updateZoneKeyRange", "ekodash.orders", min=t["min"], max=t["max"], zone=None)
    # LESSON LEARNT (first run): ranges declared per city *with gaps between them*
    # left empty "gap" chunks on the other shard. A query on city = 'Abuja' covers
    # the key range [{Abuja,MinKey}, {Abuja,MaxKey}] INCLUSIVE, which touches the
    # first key of the next gap chunk, so mongos contacted both shards.
    # Fix: zone ranges that tile the whole key space with no gaps.
    lo = {"city": MinKey(), "_id": MinKey()}
    lagos_start = {"city": "Lagos", "_id": MinKey()}
    ph_start = {"city": "Port Harcourt", "_id": MinKey()}
    hi = {"city": MaxKey(), "_id": MaxKey()}
    admin.command("updateZoneKeyRange", "ekodash.orders", min=lo, max=lagos_start, zone="ABUJA_PH")   # ... Abuja ...
    admin.command("updateZoneKeyRange", "ekodash.orders", min=lagos_start, max=ph_start, zone="LAGOS")  # Lagos
    admin.command("updateZoneKeyRange", "ekodash.orders", min=ph_start, max=hi, zone="ABUJA_PH")        # Port Harcourt ...
    db.orders.create_index([("city", 1), ("_id", 1)])
    admin.command("shardCollection", "ekodash.orders", key={"city": 1, "_id": 1})
    print("zone ranges (contiguous, no gaps):")
    for t in c.config.tags.find({"ns": "ekodash.orders"}).sort("min", 1):
        print(f"   {str(t['min']):48s} -> {str(t['max']):48s} zone {t['tag']}")

    h("S2  Load 203 899 orders through mongos, then inspect distribution")
    src = MongoClient(SOURCE).ekodash.orders
    batch, t0 = [], time.perf_counter()
    for d in src.find():
        batch.append(d)
        if len(batch) == 5000:
            db.orders.insert_many(batch, ordered=False); batch = []
    if batch:
        db.orders.insert_many(batch, ordered=False)
    print(f"loaded in {time.perf_counter() - t0:.1f} s")
    time.sleep(5)
    for shard, port in (("shardLagos", 27301), ("shardAbujaPH", 27302)):
        sc = MongoClient(f"mongodb://127.0.0.1:{port}/?directConnection=true").ekodash.orders
        by_city = {r["_id"]: r["n"] for r in sc.aggregate([{"$group": {"_id": "$city", "n": {"$sum": 1}}}])}
        print(f"{shard:13s} holds {sc.estimated_document_count():7d} docs  by city: {by_city}")
    chunks = list(c.config.chunks.aggregate([{"$group": {"_id": "$shard", "chunks": {"$sum": 1}}}]))
    print("chunks per shard:", {x["_id"]: x["chunks"] for x in chunks})

    # secondary index for the customer-history access path (prefix = shard key field)
    db.orders.create_index([("city", 1), ("customer.id", 1), ("created_at", -1)])

    h("S3  Query routing: targeted vs scatter-gather")
    cid = db.orders.find_one({"city": "Abuja"})["customer"]["id"]
    for label, flt in (("WITH shard key   {city:'Abuja', customer.id}", {"city": "Abuja", "customer.id": cid}),
                       ("WITHOUT shard key {customer.id}            ", {"customer.id": cid})):
        ex = db.command("explain", {"find": "orders", "filter": flt}, verbosity="executionStats")
        wp = ex["queryPlanner"]["winningPlan"]
        shards = [s["shardName"] for s in wp.get("shards", [])]
        print(f"{label}: stage={wp['stage']:<13s} shards contacted={shards} "
              f"docsExamined={ex['executionStats']['totalDocsExamined']}")
    print("Targeted queries touch one shard; queries without the shard key must ask every shard (scatter-gather).")


if __name__ == "__main__":
    import sys
    parts = sys.argv[1:] or ["rs", "shard"]
    if "rs" in parts:
        replica_set_tests()
    if "shard" in parts:
        sharding_tests()
