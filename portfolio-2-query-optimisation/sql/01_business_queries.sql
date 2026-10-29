-- =====================================================================
-- MIT 8103 - Portfolio 2: Query Processing and Optimisation
-- EkoDash Logistics - the six queries the business runs most often.
-- Each query is shown with the plain-English business question it answers.
-- =====================================================================
SET search_path TO ekodash;

-- Q1  Customer app "My orders" screen: last 20 orders of one customer
SELECT order_id, status, fee_amount, created_at
FROM delivery_order
WHERE customer_id = 1234
ORDER BY created_at DESC
LIMIT 20;

-- Q2  Dispatcher queue: unassigned (PENDING) orders in Lekki Phase 1, oldest first
SELECT order_id, customer_id, package_size, created_at
FROM delivery_order
WHERE status = 'PENDING' AND dropoff_zone_id = 4
ORDER BY created_at;

-- Q3  Rider earnings statement: deliveries and total fees for one rider in a month
SELECT count(*) AS deliveries, sum(fee_amount) AS gross_fees
FROM delivery_order
WHERE assigned_rider_id = 616
  AND delivered_at >= date_trunc('month', now()) - interval '1 month'
  AND delivered_at <  date_trunc('month', now());

-- Q4  Management report: revenue per zone for the last 7 days
SELECT z.zone_name, count(*) AS orders, sum(d.fee_amount) AS revenue
FROM delivery_order d
JOIN zone z ON z.zone_id = d.dropoff_zone_id
WHERE d.created_at >= now() - interval '7 days'
  AND d.status = 'DELIVERED'
GROUP BY z.zone_name
ORDER BY revenue DESC;

-- Q5  Support desk: find a customer by e-mail, whatever case the agent types
SELECT customer_id, full_name, phone, status
FROM customer
WHERE lower(email) = lower('USER4321@Mail.NG');

-- Q6  Rider leaderboard: top 10 Lagos riders by average rating (min 50 ratings)
--     6a = first draft (correlated sub-queries), 6b = rewritten with one join + GROUP BY
-- 6a
SELECT r.rider_id, r.full_name,
       (SELECT avg(rr.score) FROM rider_rating rr JOIN delivery_order d ON d.order_id = rr.order_id
         WHERE d.assigned_rider_id = r.rider_id) AS avg_score,
       (SELECT count(*) FROM rider_rating rr JOIN delivery_order d ON d.order_id = rr.order_id
         WHERE d.assigned_rider_id = r.rider_id) AS ratings
FROM rider r JOIN zone z ON z.zone_id = r.home_zone_id
WHERE z.city_id = 1
  AND (SELECT count(*) FROM rider_rating rr JOIN delivery_order d ON d.order_id = rr.order_id
        WHERE d.assigned_rider_id = r.rider_id) >= 50
ORDER BY avg_score DESC, ratings DESC
LIMIT 10;
-- 6b
SELECT r.rider_id, r.full_name, round(avg(rr.score), 3) AS avg_score, count(*) AS ratings
FROM rider r
JOIN zone z            ON z.zone_id = r.home_zone_id AND z.city_id = 1
JOIN delivery_order d  ON d.assigned_rider_id = r.rider_id
JOIN rider_rating rr   ON rr.order_id = d.order_id
GROUP BY r.rider_id, r.full_name
HAVING count(*) >= 50
ORDER BY avg_score DESC, ratings DESC
LIMIT 10;
