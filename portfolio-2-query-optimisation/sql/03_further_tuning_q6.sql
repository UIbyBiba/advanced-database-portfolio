-- =====================================================================
-- Portfolio 2 - 03_further_tuning_q6.sql
-- Q6b still reads every rating (it is an aggregate over the whole history).
-- Two further techniques are measured here:
--   (a) session work_mem: the plan showed "Batches: 4 ... temp written",
--       i.e. the hash table spilled to disk with the 4 MB default.
--   (b) a MATERIALIZED VIEW: the leaderboard only needs to be fresh once
--       per hour, so the aggregate is pre-computed and refreshed on schedule.
-- =====================================================================
SET search_path TO ekodash;
SET max_parallel_workers_per_gather = 0;

\echo '=== (a) Q6b with work_mem = 64MB (default is 4MB) ==='
SET work_mem = '64MB';
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT r.rider_id, r.full_name, round(avg(rr.score), 3) AS avg_score, count(*) AS ratings
FROM rider r
JOIN zone z            ON z.zone_id = r.home_zone_id AND z.city_id = 1
JOIN delivery_order d  ON d.assigned_rider_id = r.rider_id
JOIN rider_rating rr   ON rr.order_id = d.order_id
GROUP BY r.rider_id, r.full_name
HAVING count(*) >= 50
ORDER BY avg_score DESC, ratings DESC
LIMIT 10;
RESET work_mem;

\echo '=== (b) Materialized view for the leaderboard ==='
DROP MATERIALIZED VIEW IF EXISTS mv_rider_rating_summary;
CREATE MATERIALIZED VIEW mv_rider_rating_summary AS
SELECT d.assigned_rider_id AS rider_id, count(*) AS ratings, avg(rr.score) AS avg_score
FROM delivery_order d JOIN rider_rating rr ON rr.order_id = d.order_id
GROUP BY d.assigned_rider_id;
CREATE UNIQUE INDEX ux_mv_rider ON mv_rider_rating_summary (rider_id);   -- needed for CONCURRENTLY refresh
ANALYZE mv_rider_rating_summary;

EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT r.rider_id, r.full_name, round(m.avg_score, 3) AS avg_score, m.ratings
FROM mv_rider_rating_summary m
JOIN rider r ON r.rider_id = m.rider_id
JOIN zone z  ON z.zone_id = r.home_zone_id AND z.city_id = 1
WHERE m.ratings >= 50
ORDER BY avg_score DESC, m.ratings DESC
LIMIT 10;

\echo '=== Same top-10 result as Q6b? (should return 0 rows of difference) ==='
(SELECT r.rider_id FROM rider r JOIN zone z ON z.zone_id = r.home_zone_id AND z.city_id = 1
   JOIN delivery_order d ON d.assigned_rider_id = r.rider_id JOIN rider_rating rr ON rr.order_id = d.order_id
   GROUP BY r.rider_id HAVING count(*) >= 50 ORDER BY round(avg(rr.score),3) DESC, count(*) DESC LIMIT 10)
EXCEPT
(SELECT m.rider_id FROM mv_rider_rating_summary m JOIN rider r ON r.rider_id = m.rider_id
   JOIN zone z ON z.zone_id = r.home_zone_id AND z.city_id = 1
   WHERE m.ratings >= 50 ORDER BY round(m.avg_score,3) DESC, m.ratings DESC LIMIT 10);

\echo '=== Cost of keeping it fresh: REFRESH ... CONCURRENTLY (readers are not blocked) ==='
\timing on
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_rider_rating_summary;
\timing off
