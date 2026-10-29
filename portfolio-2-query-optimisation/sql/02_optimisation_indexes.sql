-- =====================================================================
-- Portfolio 2 - 02_optimisation_indexes.sql
-- Indexes designed for the queries in 01_business_queries.sql.
-- Each index is chosen for one access path; reasons are in README.md.
-- =====================================================================
SET search_path TO ekodash;

-- Q1: equality on customer_id + ORDER BY created_at DESC LIMIT 20
--     -> composite B-tree in the same sort order. For a customer with many
--        orders the planner can walk the index in order and stop after 20 rows;
--        for a customer with few orders it may prefer a bitmap scan + a tiny sort
--        (that is what the measured plan for customer 1234, 5 orders, shows).
CREATE INDEX ix_order_customer_created ON delivery_order (customer_id, created_at DESC);

-- Q2: only ~0.1% of orders are PENDING at any time.
--     A PARTIAL index stores just those rows, so it stays tiny and hot.
CREATE INDEX ix_order_pending_zone ON delivery_order (dropoff_zone_id, created_at)
    WHERE status = 'PENDING';

-- Q3: equality on rider + range on delivered_at; INCLUDE fee_amount
--     so the SUM can be answered by an index-only scan (no heap visits)
CREATE INDEX ix_order_rider_delivered ON delivery_order (assigned_rider_id, delivered_at)
    INCLUDE (fee_amount);

-- Q4: time-range scan. Orders are inserted in created_at order, so the
--     physical order of the table follows time -> a BRIN index fits well
--     and is a fraction of the size of a B-tree.
CREATE INDEX ix_order_created_brin ON delivery_order USING brin (created_at) WITH (pages_per_range = 32);

-- Q5: the predicate is lower(email), so a plain index on email cannot be used.
--     An EXPRESSION index on lower(email) matches the predicate exactly.
CREATE INDEX ix_customer_email_lower ON customer (lower(email));

-- Q6: rating look-ups go through delivery_order.assigned_rider_id; the Q3
--     index already covers that column as its leading key.

-- Refresh planner statistics after creating indexes
VACUUM ANALYZE delivery_order;
VACUUM ANALYZE customer;
