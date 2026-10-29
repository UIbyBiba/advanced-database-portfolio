// =====================================================================
// MIT 8103 - Portfolio 4: MongoDB queries for EkoDash Logistics
// Run:  mongosh --quiet ekodash 02_mongodb_queries.js
// =====================================================================
const line = (t) => print("\n" + "=".repeat(74) + "\n" + t + "\n" + "=".repeat(74));
const sample = db.orders.findOne({ status: "DELIVERED", rating: { $ne: null } }, { _id: 1, "customer.id": 1 });
const ORDER_ID = sample._id, CUSTOMER_ID = sample.customer.id;

// ---------------------------------------------------------------- N1
line("N1  Order tracking screen - ONE read returns the whole aggregate");
const doc = db.orders.findOne({ _id: ORDER_ID });
printjson(doc);
const e1 = db.orders.find({ _id: ORDER_ID }).explain("executionStats");
print(`plan stage: ${e1.queryPlanner.winningPlan.stage} | docsExamined: ${e1.executionStats.totalDocsExamined}` +
      ` | time: ${e1.executionStats.executionTimeMillis} ms`);
print("Relational equivalent needs 7 tables joined (order, address, 2x zone, city, rider, payment, rating, history).");

// ---------------------------------------------------------------- N2
line("N2  Customer order history - effect of a compound index");
const q2 = () => db.orders.find({ "customer.id": CUSTOMER_ID }, { status: 1, fee: 1, created_at: 1 })
                          .sort({ created_at: -1 }).limit(20);
try { db.orders.dropIndex("customer.id_1_created_at_-1"); } catch (e) { /* first run: index does not exist yet */ }
let e2 = q2().explain("executionStats");
const stages = (p) => { let s = [], n = p; while (n) { s.push(n.stage + (n.indexName ? `(${n.indexName})` : "")); n = n.inputStage; } return s.join(" <- "); };
print(`BEFORE  stages: ${stages(e2.queryPlanner.winningPlan)}`);
print(`        docsExamined=${e2.executionStats.totalDocsExamined} keysExamined=${e2.executionStats.totalKeysExamined}` +
      ` nReturned=${e2.executionStats.nReturned} time=${e2.executionStats.executionTimeMillis} ms`);
db.orders.createIndex({ "customer.id": 1, created_at: -1 });
e2 = q2().explain("executionStats");
print(`AFTER   stages: ${stages(e2.queryPlanner.winningPlan)}`);
print(`        docsExamined=${e2.executionStats.totalDocsExamined} keysExamined=${e2.executionStats.totalKeysExamined}` +
      ` nReturned=${e2.executionStats.nReturned} time=${e2.executionStats.executionTimeMillis} ms`);
print("Note: equality field first, sort field second (ESR rule) -> no in-memory SORT stage.");

// ---------------------------------------------------------------- N3
line("N3  Dispatch - 5 nearest ON_DUTY bikes within 3 km of a Yaba pickup (2dsphere + $geoNear)");
const pickup = { type: "Point", coordinates: [3.3711, 6.5095] };   // Yaba, Lagos
db.riders.aggregate([
  { $geoNear: { near: pickup, distanceField: "distance_m", maxDistance: 3000, spherical: true,
                query: { duty_status: "ON_DUTY", vehicle: "BIKE" } } },
  { $limit: 5 },
  { $project: { _id: 1, name: 1, zone: 1, distance_m: { $round: ["$distance_m", 0] } } }
]).forEach(printjson);
const e3 = db.riders.explain("executionStats").aggregate([
  { $geoNear: { near: pickup, distanceField: "d", maxDistance: 3000, spherical: true,
                query: { duty_status: "ON_DUTY", vehicle: "BIKE" } } }, { $limit: 5 }]);
const s3 = e3.stages ? e3.stages[0].$geoNearCursor : e3;
print(`index used: ${JSON.stringify(s3.queryPlanner ? s3.queryPlanner.winningPlan.inputStage?.indexName || s3.queryPlanner.winningPlan.inputStage?.inputStage?.indexName : "see explain")}`);

// ---------------------------------------------------------------- N4
line("N4  Analytics - revenue and average delivery time per zone, last 7 days (aggregation pipeline)");
db.orders.aggregate([
  { $match: { status: "DELIVERED", created_at: { $gte: new Date(Date.now() - 7 * 864e5) } } },
  { $group: { _id: { city: "$city", zone: "$pickup.zone" },
              orders: { $sum: 1 },
              revenue: { $sum: "$fee" },
              avg_minutes: { $avg: { $dateDiff: { startDate: "$created_at", endDate: "$delivered_at", unit: "minute" } } } } },
  { $sort: { revenue: -1 } },
  { $limit: 8 },
  { $project: { _id: 0, city: "$_id.city", zone: "$_id.zone", orders: 1,
                revenue: { $toDouble: "$revenue" }, avg_minutes: { $round: ["$avg_minutes", 1] } } }
]).forEach((r) => print(JSON.stringify(r)));

// ---------------------------------------------------------------- N5
line("N5  Rider picks up a parcel - status change + timeline append in ONE atomic document update");
const live = db.orders.findOne({ status: "ASSIGNED" }, { _id: 1 });
const res = db.orders.updateOne(
  { _id: live._id, status: "ASSIGNED" },                      // compare-and-set, like Portfolio 3 D4
  { $set: { status: "PICKED_UP" }, $push: { timeline: { status: "PICKED_UP", at: new Date(), by: "RIDER" } } });
printjson(res);
printjson(db.orders.findOne({ _id: live._id }, { status: 1, timeline: 1 }));
const again = db.orders.updateOne({ _id: live._id, status: "ASSIGNED" }, { $set: { status: "PICKED_UP" } });
print(`second rider tries the same transition -> matchedCount = ${again.matchedCount} (already picked up)`);

// ---------------------------------------------------------------- N6
line("N6  $jsonSchema validation - invalid documents are rejected by the server");
try {
  db.orders.insertOne({ _id: 999999999, city: "Lagos", status: "LOST", fee: 1500.5,
                        customer: { id: 1 }, pickup: { zone: "Yaba", location: pickup },
                        dropoff: { zone: "Yaba" }, package: { size: "XL", weight_kg: 80 },
                        timeline: [], created_at: new Date() });
} catch (err) {
  print(`REJECTED: code ${err.code} ${err.errmsg || err.message}`);
  const d = err.errInfo?.details?.schemaRulesNotSatisfied?.[0]?.propertiesNotSatisfied || [];
  d.forEach((p) => print(`  - field '${p.propertyName}' failed: ${p.details.map((x) => x.reason || ("nested fields failed: " + (x.propertiesNotSatisfied || []).map((q) => q.propertyName).join(", "))).join("; ")}`));
}

// ---------------------------------------------------------------- sizes
line("Storage summary");
["orders", "riders", "customers"].forEach((c) => {
  const s = db.getCollection(c).stats();
  print(`${c.padEnd(10)} docs=${String(s.count).padStart(7)}  avgDoc=${String(s.avgObjSize).padStart(5)} B  ` +
        `data=${(s.size / 1e6).toFixed(1)} MB  indexes=${s.nindexes} (${(s.totalIndexSize / 1e6).toFixed(1)} MB)`);
});
