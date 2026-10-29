"""
MIT 8103 - Portfolio 4 (part 2): KEY-VALUE model with Redis 8 for EkoDash

Data that changes every few seconds and is only needed "right now" does not
belong in the system of record. Three EkoDash use cases are modelled as keys:

  K1  riders:geo:lagos          GEO set   - live rider GPS pings, nearest-rider search
  K2  eta:order:<id>            STRING+TTL- cached ETA shown on the tracking page
  K3  ratelimit:otp:<phone>     counter   - max 3 OTP SMS per phone per 10 minutes
  K4  rider:<id>:session        HASH      - rider app session / last heartbeat

Usage: python3 03_redis_keyvalue.py
"""
import time, json, random
import redis
from pymongo import MongoClient

r = redis.Redis(host="localhost", port=6379, decode_responses=True)
mongo = MongoClient("mongodb://localhost:27017")["ekodash"]
random.seed(8103)


def cmd(text, result):
    print(f"redis> {text}\n{json.dumps(result, default=str) if not isinstance(result, str) else result}")


def main():
    r.flushdb()
    print("=" * 74 + "\nK1  Live rider locations - GEOADD / GEOSEARCH\n" + "=" * 74)
    lagos = list(mongo.riders.find({"city": "Lagos", "duty_status": "ON_DUTY"}, {"location": 1}))
    pipe = r.pipeline()
    for d in lagos:
        lng, lat = d["location"]["coordinates"]
        pipe.geoadd("riders:geo:lagos", (lng, lat, f"rider:{d['_id']}"))
    pipe.execute()
    cmd("ZCARD riders:geo:lagos", r.zcard("riders:geo:lagos"))
    # a GPS ping moves one rider: same command overwrites the position (O(log N))
    r.geoadd("riders:geo:lagos", (3.3720, 6.5101, f"rider:{lagos[0]['_id']}"))
    cmd(f"GEOADD riders:geo:lagos 3.3720 6.5101 rider:{lagos[0]['_id']}   (position update)", "OK")
    t = time.perf_counter()
    near = r.geosearch("riders:geo:lagos", longitude=3.3711, latitude=6.5095, radius=1.5, unit="km",
                       sort="ASC", count=5, withdist=True)
    ms = (time.perf_counter() - t) * 1000
    cmd("GEOSEARCH riders:geo:lagos FROMLONLAT 3.3711 6.5095 BYRADIUS 1.5 km ASC COUNT 5 WITHDIST", near)
    print(f"(round trip {ms:.2f} ms)")

    print("\n" + "=" * 74 + "\nK2  Cached ETA with time-to-live\n" + "=" * 74)
    r.set("eta:order:598715", json.dumps({"eta_min": 14, "rider": 1480}), ex=30)
    cmd('SET eta:order:598715 \'{"eta_min":14,"rider":1480}\' EX 30', "OK")
    cmd("GET eta:order:598715", r.get("eta:order:598715"))
    cmd("TTL eta:order:598715", r.ttl("eta:order:598715"))
    print("-> after 30 s the key disappears by itself; the app recomputes the ETA on the next request.")

    print("\n" + "=" * 74 + "\nK3  OTP rate limit - atomic INCR + EXPIRE (fixed window)\n" + "=" * 74)
    key = "ratelimit:otp:+2348031234567"
    for attempt in range(1, 6):
        pipe = r.pipeline()                     # MULTI/EXEC: both commands run atomically
        pipe.incr(key); pipe.expire(key, 600, nx=True)
        n, _ = pipe.execute()
        print(f"OTP request {attempt}: INCR -> {n}  => {'SEND SMS' if n <= 3 else 'BLOCKED (limit 3 per 10 min)'}")
    cmd(f"TTL {key}", r.ttl(key))

    print("\n" + "=" * 74 + "\nK4  Rider app session - HASH\n" + "=" * 74)
    r.hset("rider:1480:session", mapping={"device": "Android 14", "app_version": "3.8.1",
                                          "status": "ON_DUTY", "last_heartbeat": int(time.time())})
    r.expire("rider:1480:session", 120)
    cmd("HGETALL rider:1480:session", r.hgetall("rider:1480:session"))
    cmd("HINCRBY rider:1480:session jobs_today 1", r.hincrby("rider:1480:session", "jobs_today", 1))

    print("\n" + "=" * 74 + "\nMemory footprint\n" + "=" * 74)
    cmd("MEMORY USAGE riders:geo:lagos", f"{r.memory_usage('riders:geo:lagos')} bytes for {r.zcard('riders:geo:lagos')} riders")
    cmd("DBSIZE", r.dbsize())


if __name__ == "__main__":
    main()
