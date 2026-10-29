-- =====================================================================
-- Portfolio 1 - 02_seed_data.sql
-- Generates a realistic, repeatable data set for EkoDash Logistics.
-- setseed() makes the random data identical on every run.
-- Volumes: 3 cities, 20 zones, 50 000 customers, ~80 000 addresses,
--          1 500 riders, 600 000 orders (+ history, payments, wallets)
-- =====================================================================
SET search_path TO ekodash;
\o /dev/null
SELECT setseed(0.8103);

INSERT INTO city VALUES
 (1,'Lagos','Lagos'), (2,'Abuja','FCT'), (3,'Port Harcourt','Rivers');

-- zone centre coordinates are used to scatter addresses realistically
CREATE TEMP TABLE zone_centre (zone_id SMALLINT, lat NUMERIC, lng NUMERIC, weight INT);
INSERT INTO zone (zone_id, city_id, zone_name, base_fee) VALUES
 (1,1,'Yaba',1500),(2,1,'Surulere',1500),(3,1,'Ikeja',1700),(4,1,'Lekki Phase 1',2500),
 (5,1,'Victoria Island',2500),(6,1,'Ajah',2200),(7,1,'Ikorodu',1800),(8,1,'Festac',1600),
 (9,1,'Maryland',1600),(10,1,'Gbagada',1600),(11,1,'Apapa',2000),(12,1,'Oshodi',1500),
 (13,2,'Wuse',2000),(14,2,'Garki',2000),(15,2,'Maitama',2600),(16,2,'Gwarinpa',1800),(17,2,'Kubwa',1700),
 (18,3,'GRA Phase 2',1900),(19,3,'Rumuokoro',1500),(20,3,'Trans-Amadi',1700);
INSERT INTO zone_centre VALUES
 (1,6.5095,3.3711,9),(2,6.4997,3.3540,8),(3,6.6018,3.3515,10),(4,6.4478,3.4723,9),
 (5,6.4281,3.4219,8),(6,6.4698,3.5852,6),(7,6.6194,3.5105,5),(8,6.4664,3.2833,5),
 (9,6.5710,3.3660,5),(10,6.5539,3.3901,5),(11,6.4489,3.3590,4),(12,6.5560,3.3430,6),
 (13,9.0765,7.4686,6),(14,9.0333,7.4833,5),(15,9.0868,7.4938,3),(16,9.1050,7.4100,4),(17,9.1561,7.3400,3),
 (18,4.8156,7.0050,4),(19,4.8710,6.9990,3),(20,4.8040,7.0300,3);

-- weighted zone picker: busier zones get more customers and orders
CREATE TEMP TABLE zone_pick AS
SELECT zone_id, row_number() OVER () AS slot
FROM zone_centre, generate_series(1, weight);

CREATE TEMP TABLE first_names(n TEXT[]);
INSERT INTO first_names VALUES (ARRAY['Adaeze','Tunde','Chinedu','Aisha','Emeka','Ngozi','Ibrahim','Funmilayo',
 'Yusuf','Kemi','Obinna','Zainab','Segun','Halima','Ifeanyi','Bisola','Musa','Amaka','Femi','Hauwa',
 'Uche','Bukola','Abubakar','Chioma','Kunle','Fatima','Nnamdi','Temitope','Sani','Ebere']);
CREATE TEMP TABLE last_names(n TEXT[]);
INSERT INTO last_names VALUES (ARRAY['Okafor','Adeyemi','Bello','Eze','Ogunleye','Abdullahi','Nwosu','Lawal',
 'Okonkwo','Ibrahim','Adebayo','Musa','Obi','Balogun','Usman','Chukwu','Akinola','Danjuma','Ikenna','Oladipo']);

-- ---------------- customers ----------------
INSERT INTO customer (full_name, phone, email, status, created_at)
SELECT f.n[1 + floor(random()*30)::int] || ' ' || l.n[1 + floor(random()*20)::int],
       '+23480' || lpad(g::text, 8, '0'),
       CASE WHEN random() < 0.85 THEN 'user' || g || '@mail.ng' END,
       CASE WHEN random() < 0.97 THEN 'ACTIVE' WHEN random() < 0.5 THEN 'SUSPENDED' ELSE 'CLOSED' END,
       now() - (random() * interval '730 days')
FROM generate_series(1, 50000) g, first_names f, last_names l;

-- ---------------- addresses (1-2 per customer) ----------------
INSERT INTO customer_address (customer_id, zone_id, label, street, latitude, longitude)
SELECT c.customer_id, zc.zone_id,
       CASE WHEN k = 1 THEN 'Home' ELSE 'Office' END,
       (1 + floor(random()*120))::int || ' ' ||
       (ARRAY['Herbert Macaulay Way','Allen Avenue','Admiralty Way','Adeola Odeku St','Ikorodu Road',
              'Awolowo Road','Aminu Kano Crescent','Ademola Adetokunbo Cres','Aba Road','Olu Obasanjo Rd'])[1+floor(random()*10)::int],
       round(zc.lat + ((random()-0.5)*0.03)::numeric, 6), round(zc.lng + ((random()-0.5)*0.03)::numeric, 6)
FROM customer c
CROSS JOIN LATERAL generate_series(1, CASE WHEN c.customer_id % 5 < 3 THEN 2 ELSE 1 END) k
-- draw the random slot number first, then join on it (one draw per address)
CROSS JOIN LATERAL (SELECT 1 + floor(random() * (SELECT count(*) FROM zone_pick) + k * 0)::int AS slot) pick
JOIN zone_pick zp ON zp.slot = pick.slot
JOIN zone_centre zc ON zc.zone_id = zp.zone_id;

-- ---------------- riders ----------------
INSERT INTO rider (full_name, phone, vehicle_type, plate_number, home_zone_id, duty_status, joined_on)
SELECT f.n[1 + floor(random()*30)::int] || ' ' || l.n[1 + floor(random()*20)::int],
       '+23490' || lpad(g::text, 8, '0'),
       CASE WHEN random() < 0.80 THEN 'BIKE' WHEN random() < 0.6 THEN 'TRICYCLE' ELSE 'VAN' END,
       'EKD-' || lpad(g::text, 5, '0'),
       1 + (g % 20),
       CASE WHEN random() < 0.45 THEN 'ON_DUTY' WHEN random() < 0.95 THEN 'OFF_DUTY' ELSE 'SUSPENDED' END,
       CURRENT_DATE - (random()*900)::int
FROM generate_series(1, 1500) g, first_names f, last_names l;

INSERT INTO admin_user (full_name, email, role) VALUES
 ('Halima Yusuf','halima.y@ekodash.ng','SUPERADMIN'),
 ('Tunde Bello','tunde.b@ekodash.ng','DISPATCHER'),
 ('Ngozi Eze','ngozi.e@ekodash.ng','FINANCE'),
 ('Femi Lawal','femi.l@ekodash.ng','SUPPORT');

-- ---------------- orders ----------------
-- staging table: decide all random attributes once, then insert consistently
CREATE TEMP TABLE o AS
SELECT g AS n,
       a.customer_id, a.address_id, a.zone_id AS pickup_zone,
       CASE WHEN random() < 0.6 THEN a.zone_id ELSE 1 + floor(random()*20)::int END AS drop_zone,
       now() - (random()^1.3 * interval '365 days') AS created_at,
       (ARRAY['S','S','S','M','M','L'])[1+floor(random()*6)::int] AS size,
       round((0.3 + random()*12)::numeric, 2) AS kg,
       random() AS r_status,
       random() AS r_rider,
       (12 + random()*35) AS assign_min,
       (25 + random()*140) AS deliver_min
FROM (SELECT g, 1 + floor(random() * (SELECT max(address_id) FROM customer_address))::bigint AS addr
      FROM generate_series(1, 600000) g) pick
JOIN customer_address a ON a.address_id = pick.addr;

-- delivery zones in other cities are not allowed: keep drop zone in same city
UPDATE o SET drop_zone = pickup_zone
WHERE (SELECT city_id FROM zone WHERE zone_id = drop_zone) <> (SELECT city_id FROM zone WHERE zone_id = pickup_zone);

INSERT INTO delivery_order (customer_id, pickup_address_id, dropoff_street, dropoff_zone_id, recipient_phone,
       package_size, weight_kg, fee_amount, status, assigned_rider_id, created_at, assigned_at, delivered_at)
SELECT o.customer_id, o.address_id,
       (1 + o.n % 200) || ' Delivery Close', o.drop_zone,
       '+23470' || lpad((o.n % 99999999)::text, 8, '0'),
       o.size, o.kg,
       z.base_fee + CASE o.size WHEN 'M' THEN 500 WHEN 'L' THEN 1200 ELSE 0 END
                  + CASE WHEN o.drop_zone <> o.pickup_zone THEN 800 ELSE 0 END,
       s.status,
       CASE WHEN s.status IN ('PENDING','CANCELLED') THEN NULL
            ELSE COALESCE(NULLIF(20 * floor(o.r_rider * 75)::int + (o.pickup_zone - 1), 0), 1500) END,  -- rider whose home zone = pickup zone
       o.created_at,
       CASE WHEN s.status IN ('PENDING','CANCELLED') THEN NULL ELSE o.created_at + o.assign_min * interval '1 minute' END,
       CASE WHEN s.status = 'DELIVERED' THEN o.created_at + o.deliver_min * interval '1 minute' END
FROM o
JOIN zone z ON z.zone_id = o.pickup_zone
CROSS JOIN LATERAL (SELECT CASE
        WHEN o.created_at > now() - interval '3 hours' THEN
             CASE WHEN o.r_status < 0.4 THEN 'PENDING' WHEN o.r_status < 0.7 THEN 'ASSIGNED' ELSE 'PICKED_UP' END
        WHEN o.r_status < 0.06 THEN 'CANCELLED'
        WHEN o.r_status < 0.09 THEN 'FAILED'
        ELSE 'DELIVERED' END AS status) s
ORDER BY o.created_at;          -- insert in time order, like a real system

-- ---------------- status history (derived from the orders) ----------------
INSERT INTO order_status_history (order_id, status, changed_at, changed_by)
SELECT order_id, 'PENDING', created_at, 'CUSTOMER' FROM delivery_order
UNION ALL SELECT order_id, 'ASSIGNED', assigned_at, 'SYSTEM' FROM delivery_order WHERE assigned_at IS NOT NULL
UNION ALL SELECT order_id, 'PICKED_UP', assigned_at + interval '10 minutes', 'RIDER'
          FROM delivery_order WHERE status IN ('PICKED_UP','DELIVERED','FAILED')
UNION ALL SELECT order_id, status, COALESCE(delivered_at, created_at + interval '2 hours'),
                 CASE status WHEN 'CANCELLED' THEN 'CUSTOMER' ELSE 'RIDER' END
          FROM delivery_order WHERE status IN ('DELIVERED','CANCELLED','FAILED');

-- ---------------- payments ----------------
INSERT INTO payment (order_id, method, amount, status, reference, paid_at)
SELECT d.order_id,
       (ARRAY['CARD','TRANSFER','TRANSFER','WALLET','CASH'])[1 + (d.order_id % 5)::int],
       d.fee_amount,
       CASE d.status WHEN 'CANCELLED' THEN 'REFUNDED' WHEN 'FAILED' THEN 'FAILED' ELSE 'SUCCESS' END,
       'EKD' || to_char(d.created_at, 'YYMMDD') || lpad(d.order_id::text, 9, '0'),
       CASE WHEN d.status <> 'FAILED' THEN d.created_at + interval '1 minute' END
FROM delivery_order d;

-- ---------------- wallets ----------------
INSERT INTO wallet (customer_id, balance) SELECT customer_id, round((random()*30000)::numeric, 2) FROM customer;
INSERT INTO wallet (rider_id, balance)    SELECT rider_id,    round((random()*80000)::numeric, 2) FROM rider;

-- ---------------- ratings for ~70% of delivered orders ----------------
INSERT INTO rider_rating (order_id, score, comment, rated_at)
SELECT order_id,
       (ARRAY[5,5,5,4,4,4,3,2,1])[1 + floor(random()*9)::int],
       (ARRAY[NULL,'Fast delivery','Rider was polite','Package arrived safely','Late but fine',NULL])[1+floor(random()*6)::int],
       delivered_at + interval '30 minutes'
FROM delivery_order WHERE status = 'DELIVERED' AND random() < 0.7;

ANALYZE;
