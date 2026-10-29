-- =====================================================================
-- Portfolio 1 - 03_constraint_tests.sql
-- Each statement below breaks one business rule on purpose.
-- Expected result: PostgreSQL rejects every one of them.
-- Run with:  psql -d ekodash -f 03_constraint_tests.sql
-- =====================================================================
SET search_path TO ekodash;
\set ON_ERROR_STOP off
\echo '--- Test 1: phone must be Nigerian +234 format (CHECK) ---'
INSERT INTO customer (full_name, phone) VALUES ('Test User', '08031234567');

\echo '--- Test 2: duplicate phone number (UNIQUE) ---'
INSERT INTO customer (full_name, phone) SELECT 'Copy Cat', phone FROM customer WHERE customer_id = 1;

\echo '--- Test 3: order for a customer that does not exist (FOREIGN KEY) ---'
INSERT INTO delivery_order (customer_id, pickup_address_id, dropoff_street, dropoff_zone_id,
                            recipient_phone, package_size, weight_kg, fee_amount)
VALUES (999999, 1, '1 Nowhere St', 1, '+2348000000001', 'S', 1.0, 1500);

\echo '--- Test 4: order marked ASSIGNED without a rider (table CHECK) ---'
UPDATE delivery_order SET status = 'ASSIGNED' WHERE status = 'PENDING' AND order_id = (SELECT min(order_id) FROM delivery_order WHERE status='PENDING');

\echo '--- Test 5: DELIVERED without a delivered_at time (table CHECK) ---'
UPDATE delivery_order SET delivered_at = NULL WHERE order_id = (SELECT min(order_id) FROM delivery_order WHERE status='DELIVERED');

\echo '--- Test 6: wallet balance cannot go negative (CHECK) ---'
UPDATE wallet SET balance = balance - 10000000 WHERE wallet_id = 1;

\echo '--- Test 7: a wallet must belong to exactly one owner (exclusive-arc CHECK) ---'
INSERT INTO wallet (customer_id, rider_id, balance) VALUES (NULL, NULL, 0);

\echo '--- Test 8: rating score outside 1-5 (CHECK) ---'
INSERT INTO rider_rating (order_id, score)
SELECT order_id, 7 FROM delivery_order WHERE status='DELIVERED'
  AND order_id NOT IN (SELECT order_id FROM rider_rating) LIMIT 1;

\echo '--- Test 9: a second payment for the same order (UNIQUE = 1:1) ---'
INSERT INTO payment (order_id, method, amount, status, reference) VALUES (1, 'CARD', 1500, 'SUCCESS', 'DUPLICATE-REF-1');

\echo '--- Test 10: deleting a rider who has orders (FOREIGN KEY RESTRICT) ---'
DELETE FROM rider WHERE rider_id = (SELECT assigned_rider_id FROM delivery_order WHERE assigned_rider_id IS NOT NULL LIMIT 1);

\echo '--- Control: a valid order is accepted ---'
BEGIN;
INSERT INTO delivery_order (customer_id, pickup_address_id, dropoff_street, dropoff_zone_id,
                            recipient_phone, package_size, weight_kg, fee_amount)
SELECT customer_id, address_id, '14 Tejuosho St', zone_id, '+2348011122233', 'M', 2.5, 2000
FROM customer_address WHERE address_id = 1
RETURNING order_id, status, created_at;
ROLLBACK;   -- keep the data set unchanged
