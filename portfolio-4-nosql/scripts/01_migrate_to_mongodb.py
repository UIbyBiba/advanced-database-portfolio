"""
MIT 8103 - Portfolio 4: NoSQL and Advanced Data Models (EkoDash Logistics)
01_migrate_to_mongodb.py

Reads the relational EkoDash data (PostgreSQL, Portfolio 1) and reshapes it
into an aggregate-oriented DOCUMENT model in MongoDB 7:

  orders     - one document per delivery, with the status timeline, payment,
               rating, pickup/drop-off snapshot and rider snapshot EMBEDDED
  riders     - one document per rider with a GeoJSON current location
  customers  - one document per customer with saved addresses embedded

Only the most recent 90 days of orders are migrated: in the target design
MongoDB serves the live app (tracking, dispatch) while PostgreSQL remains
the system of record for finance and history.
"""
import os, random, datetime
import psycopg
from pymongo import MongoClient, ASCENDING, DESCENDING, GEOSPHERE

PG = os.environ.get("EKODASH_DSN", "host=localhost dbname=ekodash user=postgres")
MONGO = os.environ.get("EKODASH_MONGO", "mongodb://localhost:27017")
DAYS = 90
random.seed(8103)

order_validator = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "customer", "pickup", "dropoff", "package", "fee", "status", "timeline", "created_at", "city"],
        "properties": {
            "status": {"enum": ["PENDING", "ASSIGNED", "PICKED_UP", "DELIVERED", "CANCELLED", "FAILED"]},
            "fee": {"bsonType": "decimal", "description": "money stored as Decimal128, never as float"},
            "package": {"bsonType": "object", "required": ["size", "weight_kg"],
                        "properties": {"size": {"enum": ["S", "M", "L"]},
                                       "weight_kg": {"bsonType": "double", "minimum": 0, "maximum": 50}}},
            "pickup": {"bsonType": "object", "required": ["zone", "location"],
                       "properties": {"location": {"bsonType": "object", "required": ["type", "coordinates"]}}},
            "timeline": {"bsonType": "array", "minItems": 1},
            "rating": {"bsonType": ["object", "null"],
                       "properties": {"score": {"bsonType": "int", "minimum": 1, "maximum": 5}}},
        },
    }
}


def main():
    from bson.decimal128 import Decimal128
    db = MongoClient(MONGO)["ekodash"]
    for name in ("orders", "riders", "customers"):
        db.drop_collection(name)
    db.create_collection("orders", validator=order_validator, validationLevel="strict", validationAction="error")

    with psycopg.connect(PG) as pg:
        pg.execute("SET search_path TO ekodash")
        # ---------------- riders ----------------
        riders = []
        for rid, name, phone, vehicle, zone, city, duty, lat, lng in pg.execute("""
                SELECT r.rider_id, r.full_name, r.phone, r.vehicle_type, z.zone_name, c.city_name, r.duty_status,
                       zc.lat, zc.lng
                FROM rider r JOIN zone z ON z.zone_id = r.home_zone_id JOIN city c ON c.city_id = z.city_id
                JOIN LATERAL (SELECT avg(latitude) lat, avg(longitude) lng FROM customer_address a
                              WHERE a.zone_id = z.zone_id) zc ON true"""):
            # simulated live GPS position: within ~2 km of the rider's zone centre
            riders.append({"_id": rid, "name": name, "phone": phone, "vehicle": vehicle,
                           "zone": zone, "city": city, "duty_status": duty,
                           "location": {"type": "Point",
                                        "coordinates": [round(float(lng) + random.uniform(-0.02, 0.02), 6),
                                                        round(float(lat) + random.uniform(-0.02, 0.02), 6)]},
                           "last_seen": datetime.datetime.now(datetime.timezone.utc)})
        db.riders.insert_many(riders)
        rider_by_id = {r["_id"]: r for r in riders}

        # ---------------- customers (addresses embedded) ----------------
        cust = {}
        for cid, name, phone, email, aid, label, street, zone, lat, lng in pg.execute("""
                SELECT c.customer_id, c.full_name, c.phone, c.email, a.address_id, a.label, a.street,
                       z.zone_name, a.latitude, a.longitude
                FROM customer c JOIN customer_address a USING (customer_id) JOIN zone z USING (zone_id)
                ORDER BY c.customer_id, a.address_id"""):
            d = cust.setdefault(cid, {"_id": cid, "name": name, "phone": phone, "email": email, "addresses": []})
            d["addresses"].append({"address_id": aid, "label": label, "street": street, "zone": zone,
                                   "location": {"type": "Point", "coordinates": [float(lng), float(lat)]}})
        db.customers.insert_many(list(cust.values()))

        # ---------------- orders (aggregate: order + timeline + payment + rating) ----------------
        timelines = {}
        for oid, status, at, by in pg.execute(f"""
                SELECT h.order_id, h.status, h.changed_at, h.changed_by FROM order_status_history h
                JOIN delivery_order d USING (order_id)
                WHERE d.created_at >= now() - interval '{DAYS} days' ORDER BY h.order_id, h.changed_at"""):
            timelines.setdefault(oid, []).append({"status": status, "at": at, "by": by})

        batch, n = [], 0
        cur = pg.execute(f"""
            SELECT d.order_id, d.customer_id, d.status, d.fee_amount, d.package_size, d.weight_kg,
                   d.created_at, d.assigned_at, d.delivered_at, d.assigned_rider_id,
                   a.street, pz.zone_name, ci.city_name, a.latitude, a.longitude,
                   d.dropoff_street, dz.zone_name, d.recipient_phone,
                   p.method, p.status, p.reference, rr.score, rr.comment
            FROM delivery_order d
            JOIN customer_address a ON a.address_id = d.pickup_address_id
            JOIN zone pz ON pz.zone_id = a.zone_id JOIN city ci ON ci.city_id = pz.city_id
            JOIN zone dz ON dz.zone_id = d.dropoff_zone_id
            LEFT JOIN payment p ON p.order_id = d.order_id
            LEFT JOIN rider_rating rr ON rr.order_id = d.order_id
            WHERE d.created_at >= now() - interval '{DAYS} days'""")
        for (oid, cid, status, fee, size, kg, created, assigned, delivered, rid, street, pzone, city, lat, lng,
             dstreet, dzone, rphone, pmethod, pstatus, pref, score, comment) in cur:
            c = cust[cid]
            r = rider_by_id.get(rid)
            batch.append({
                "_id": oid, "city": city, "status": status, "fee": Decimal128(str(fee)),
                "created_at": created, "assigned_at": assigned, "delivered_at": delivered,
                "customer": {"id": cid, "name": c["name"], "phone": c["phone"]},     # snapshot
                "pickup": {"street": street, "zone": pzone,
                           "location": {"type": "Point", "coordinates": [float(lng), float(lat)]}},
                "dropoff": {"street": dstreet, "zone": dzone, "recipient_phone": rphone},
                "package": {"size": size, "weight_kg": float(kg)},
                "rider": ({"id": rid, "name": r["name"], "vehicle": r["vehicle"]} if r else None),
                "payment": ({"method": pmethod, "status": pstatus, "reference": pref} if pmethod else None),
                "rating": ({"score": int(score), "comment": comment} if score else None),
                # a few orders (created by the Portfolio 3 demos) have no history rows;
                # the $jsonSchema validator rejected them on the first run, so a minimal
                # timeline is rebuilt from the order's own timestamps instead.
                "timeline": timelines.get(oid) or [{"status": "PENDING", "at": created, "by": "SYSTEM"}],
            })
            if len(batch) == 5000:
                db.orders.insert_many(batch); n += len(batch); batch = []
        if batch:
            db.orders.insert_many(batch); n += len(batch)

    # ---------------- indexes ----------------
    db.riders.create_index([("location", GEOSPHERE)])
    db.riders.create_index([("duty_status", ASCENDING), ("vehicle", ASCENDING)])
    db.customers.create_index([("phone", ASCENDING)], unique=True)
    db.orders.create_index([("status", ASCENDING), ("created_at", ASCENDING)])
    db.orders.create_index([("pickup.location", GEOSPHERE)])
    # NOTE: the customer-history index is created later in 02_queries.py so
    #       its effect can be measured (COLLSCAN vs IXSCAN).

    print(f"riders={db.riders.count_documents({})}  customers={db.customers.count_documents({})}  orders={n}")
    stats = db.command("collstats", "orders")
    print(f"orders collection: {stats['count']} docs, avg doc size {stats['avgObjSize']} bytes, "
          f"storage {stats['storageSize']/1e6:.1f} MB")


if __name__ == "__main__":
    main()
