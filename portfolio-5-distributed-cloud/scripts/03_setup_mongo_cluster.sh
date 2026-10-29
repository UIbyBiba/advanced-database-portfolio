#!/usr/bin/env bash
# =====================================================================
# MIT 8103 - Portfolio 5: MongoDB 7 topologies for EkoDash (all on one host,
# separate processes and ports - the same commands work across real servers)
#
#  A) Replica set "ekors"       : 3 members  27101 27102 27103
#  B) Sharded cluster           : config RS "cfgrs" 27201
#                                 shard "shardLagos"  (RS) 27301
#                                 shard "shardAbujaPH"  (RS) 27302  (Abuja + Port Harcourt)
#                                 mongos router 27200
# =====================================================================
set -euo pipefail
MONGOD=/opt/mongo/bin/mongod
MONGOS=/opt/mongo/bin/mongos
BASE=/srv/mongo
start() {  # name port extra-args...
  local name=$1 port=$2; shift 2
  mkdir -p "$BASE/$name"
  $MONGOD --dbpath "$BASE/$name" --port "$port" --bind_ip 127.0.0.1 --fork \
          --logpath "$BASE/$name.log" --wiredTigerCacheSizeGB 0.25 "$@" > /dev/null
  echo "started mongod $name on :$port"
}

echo "--- A) replica set"
for p in 27101 27102 27103; do start "rs-$p" $p --replSet ekors; done
mongosh --quiet --port 27101 --eval '
  rs.initiate({_id: "ekors", members: [
    {_id: 0, host: "127.0.0.1:27101", priority: 2},
    {_id: 1, host: "127.0.0.1:27102", priority: 1},
    {_id: 2, host: "127.0.0.1:27103", priority: 1}]});
  while (!db.hello().isWritablePrimary) { sleep(200); }
  print("replica set ekors initiated, primary = " + db.hello().primary);'

echo "--- B) sharded cluster"
start cfg-27201 27201 --configsvr --replSet cfgrs
start shard-lagos 27301 --shardsvr --replSet shardLagos
start shard-abujaph 27302 --shardsvr --replSet shardAbujaPH
for spec in "27201 cfgrs" "27301 shardLagos" "27302 shardAbujaPH"; do
  set -- $spec
  mongosh --quiet --port $1 --eval "rs.initiate({_id: '$2', members: [{_id: 0, host: '127.0.0.1:$1'}]});
     while (!db.hello().isWritablePrimary) { sleep(200); }"
done
$MONGOS --configdb cfgrs/127.0.0.1:27201 --port 27200 --bind_ip 127.0.0.1 --fork --logpath $BASE/mongos.log > /dev/null
echo "started mongos router on :27200"
mongosh --quiet --port 27200 --eval '
  sh.addShard("shardLagos/127.0.0.1:27301");
  sh.addShard("shardAbujaPH/127.0.0.1:27302");
  printjson(db.adminCommand({listShards: 1}).shards.map(s => ({_id: s._id, host: s.host})));'
