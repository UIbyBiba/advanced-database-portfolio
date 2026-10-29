-- =====================================================================
-- MIT 8103 Advanced Database Systems - Portfolio 1
-- Case study : EkoDash Logistics Ltd (simulated last-mile delivery company)
-- Author     : Abdullahi Habiba (2026/A/MIT/0437)
-- DBMS       : PostgreSQL 16
-- File       : 01_schema.sql  - relational schema, keys and constraints
-- =====================================================================

DROP SCHEMA IF EXISTS ekodash CASCADE;
CREATE SCHEMA ekodash;
SET search_path TO ekodash;

-- ---------------------------------------------------------------------
-- Reference data: where EkoDash operates
-- ---------------------------------------------------------------------
CREATE TABLE city (
    city_id     SMALLINT     PRIMARY KEY,
    city_name   VARCHAR(60)  NOT NULL UNIQUE,
    state_name  VARCHAR(60)  NOT NULL
);

-- A zone is a pricing / dispatch area inside a city (e.g. Yaba, Lekki Phase 1)
CREATE TABLE zone (
    zone_id     SMALLINT      PRIMARY KEY,
    city_id     SMALLINT      NOT NULL REFERENCES city(city_id),
    zone_name   VARCHAR(60)   NOT NULL,
    base_fee    NUMERIC(10,2) NOT NULL CHECK (base_fee > 0),
    CONSTRAINT uq_zone_per_city UNIQUE (city_id, zone_name)
);

-- ---------------------------------------------------------------------
-- People who use the system
-- ---------------------------------------------------------------------
CREATE TABLE customer (
    customer_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    full_name    VARCHAR(120) NOT NULL,
    phone        VARCHAR(20)  NOT NULL UNIQUE
                 CHECK (phone ~ '^\+234[0-9]{10}$'),           -- Nigerian E.164 format
    email        VARCHAR(160) UNIQUE,
    status       VARCHAR(10)  NOT NULL DEFAULT 'ACTIVE'
                 CHECK (status IN ('ACTIVE','SUSPENDED','CLOSED')),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE customer_address (
    address_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id  BIGINT       NOT NULL REFERENCES customer(customer_id) ON DELETE CASCADE,
    zone_id      SMALLINT     NOT NULL REFERENCES zone(zone_id),
    label        VARCHAR(30)  NOT NULL DEFAULT 'Home',
    street       VARCHAR(200) NOT NULL,
    latitude     NUMERIC(9,6) NOT NULL CHECK (latitude  BETWEEN 4 AND 14),   -- Nigeria bounding box
    longitude    NUMERIC(9,6) NOT NULL CHECK (longitude BETWEEN 2 AND 15)
);

CREATE TABLE rider (
    rider_id      INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    full_name     VARCHAR(120) NOT NULL,
    phone         VARCHAR(20)  NOT NULL UNIQUE CHECK (phone ~ '^\+234[0-9]{10}$'),
    vehicle_type  VARCHAR(10)  NOT NULL CHECK (vehicle_type IN ('BIKE','TRICYCLE','VAN')),
    plate_number  VARCHAR(12)  NOT NULL UNIQUE,
    home_zone_id  SMALLINT     NOT NULL REFERENCES zone(zone_id),
    duty_status   VARCHAR(10)  NOT NULL DEFAULT 'OFF_DUTY'
                  CHECK (duty_status IN ('ON_DUTY','OFF_DUTY','SUSPENDED')),
    joined_on     DATE         NOT NULL DEFAULT CURRENT_DATE
);

CREATE TABLE admin_user (
    admin_id   SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    full_name  VARCHAR(120) NOT NULL,
    email      VARCHAR(160) NOT NULL UNIQUE,
    role       VARCHAR(12)  NOT NULL CHECK (role IN ('DISPATCHER','FINANCE','SUPPORT','SUPERADMIN'))
);

-- ---------------------------------------------------------------------
-- Core business process: a delivery order
-- ---------------------------------------------------------------------
CREATE TABLE delivery_order (
    order_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id         BIGINT       NOT NULL REFERENCES customer(customer_id),
    pickup_address_id   BIGINT       NOT NULL REFERENCES customer_address(address_id),
    dropoff_street      VARCHAR(200) NOT NULL,
    dropoff_zone_id     SMALLINT     NOT NULL REFERENCES zone(zone_id),
    recipient_phone     VARCHAR(20)  NOT NULL CHECK (recipient_phone ~ '^\+234[0-9]{10}$'),
    package_size        CHAR(1)      NOT NULL CHECK (package_size IN ('S','M','L')),
    weight_kg           NUMERIC(5,2) NOT NULL CHECK (weight_kg > 0 AND weight_kg <= 50),
    -- fee is a snapshot of the price at order time, so later changes to
    -- zone.base_fee do not rewrite history (intentional, not redundancy)
    fee_amount          NUMERIC(10,2) NOT NULL CHECK (fee_amount > 0),
    status              VARCHAR(12)  NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN ('PENDING','ASSIGNED','PICKED_UP','DELIVERED','CANCELLED','FAILED')),
    assigned_rider_id   INT          REFERENCES rider(rider_id),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    assigned_at         TIMESTAMPTZ,
    delivered_at        TIMESTAMPTZ,
    -- business rules expressed as table constraints
    CONSTRAINT chk_rider_when_assigned
        CHECK (status IN ('PENDING','CANCELLED') OR assigned_rider_id IS NOT NULL),
    CONSTRAINT chk_delivered_time
        CHECK ((status = 'DELIVERED') = (delivered_at IS NOT NULL)),
    CONSTRAINT chk_time_order
        CHECK (delivered_at IS NULL OR delivered_at >= created_at)
);

-- Audit trail of every status change (one order -> many history rows)
CREATE TABLE order_status_history (
    history_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id     BIGINT      NOT NULL REFERENCES delivery_order(order_id) ON DELETE CASCADE,
    status       VARCHAR(12) NOT NULL,
    changed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    changed_by   VARCHAR(10) NOT NULL CHECK (changed_by IN ('CUSTOMER','RIDER','ADMIN','SYSTEM'))
);

CREATE TABLE payment (
    payment_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id     BIGINT        NOT NULL UNIQUE REFERENCES delivery_order(order_id),   -- 1:1
    method       VARCHAR(8)    NOT NULL CHECK (method IN ('CARD','TRANSFER','WALLET','CASH')),
    amount       NUMERIC(10,2) NOT NULL CHECK (amount > 0),
    status       VARCHAR(8)    NOT NULL CHECK (status IN ('PENDING','SUCCESS','FAILED','REFUNDED')),
    reference    VARCHAR(32)   NOT NULL UNIQUE,
    paid_at      TIMESTAMPTZ
);

-- One wallet per customer OR per rider (exclusive arc enforced with CHECK)
CREATE TABLE wallet (
    wallet_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id  BIGINT UNIQUE REFERENCES customer(customer_id),
    rider_id     INT    UNIQUE REFERENCES rider(rider_id),
    balance      NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (balance >= 0),  -- no overdraft
    updated_at   TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT chk_wallet_owner CHECK (num_nonnulls(customer_id, rider_id) = 1)
);

-- Double-entry style ledger: every balance change is recorded
CREATE TABLE wallet_transaction (
    txn_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    wallet_id   BIGINT        NOT NULL REFERENCES wallet(wallet_id),
    order_id    BIGINT        REFERENCES delivery_order(order_id),
    entry_type  VARCHAR(6)    NOT NULL CHECK (entry_type IN ('CREDIT','DEBIT')),
    amount      NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- Customer rates the rider after delivery (weak entity, identified by the order)
CREATE TABLE rider_rating (
    order_id    BIGINT   PRIMARY KEY REFERENCES delivery_order(order_id),
    score       SMALLINT NOT NULL CHECK (score BETWEEN 1 AND 5),
    comment     VARCHAR(300),
    rated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes that support foreign keys and the core access paths.
-- (Performance indexes are added and measured in Portfolio 2.)
CREATE INDEX ix_address_customer ON customer_address(customer_id);
CREATE INDEX ix_history_order    ON order_status_history(order_id);
CREATE INDEX ix_wtxn_wallet      ON wallet_transaction(wallet_id);
