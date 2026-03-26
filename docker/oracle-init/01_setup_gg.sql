-- 01_setup_gg.sql
-- Initializes Oracle Free Database for GoldenGate stress testing
-- Creates 10 source tables + 10 matching target tables

-- 1. Enable GoldenGate Replication at CDB level
ALTER SESSION SET CONTAINER=CDB$ROOT;
ALTER SYSTEM SET enable_goldengate_replication=true SCOPE=BOTH;
ALTER DATABASE ADD SUPPLEMENTAL LOG DATA;

-- 2. Switch to PDB for all schema work
ALTER SESSION SET CONTAINER=FREEPDB1;

-- Create GoldenGate Admin user
CREATE USER ggadmin IDENTIFIED BY GGMCP_Admin123;
GRANT CONNECT, RESOURCE, DBA TO ggadmin;
GRANT OGG_CAPTURE TO ggadmin;
GRANT OGG_APPLY TO ggadmin;

-- ============================================================
-- 3. Source Schema (GG_SRC) — 10 tables
-- ============================================================
CREATE USER gg_src IDENTIFIED BY GGMCP_Admin123;
GRANT CONNECT, RESOURCE, UNLIMITED TABLESPACE TO gg_src;

-- Table 1: customers (high-volume OLTP)
CREATE TABLE gg_src.customers (
    id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name VARCHAR2(100) NOT NULL,
    email VARCHAR2(100) NOT NULL,
    region VARCHAR2(50) DEFAULT 'US-EAST',
    tier VARCHAR2(20) DEFAULT 'STANDARD',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table 2: orders (high-volume transactional)
CREATE TABLE gg_src.orders (
    id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id NUMBER NOT NULL,
    amount NUMBER(12,2) NOT NULL,
    currency VARCHAR2(3) DEFAULT 'USD',
    status VARCHAR2(20) DEFAULT 'PENDING',
    order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table 3: order_items (child of orders, very high volume)
CREATE TABLE gg_src.order_items (
    id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id NUMBER NOT NULL,
    product_id NUMBER NOT NULL,
    quantity NUMBER(10) DEFAULT 1,
    unit_price NUMBER(10,2) NOT NULL,
    discount_pct NUMBER(5,2) DEFAULT 0
);

-- Table 4: products (catalog, moderate updates)
CREATE TABLE gg_src.products (
    id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sku VARCHAR2(50) NOT NULL,
    name VARCHAR2(200) NOT NULL,
    category VARCHAR2(100),
    price NUMBER(10,2) NOT NULL,
    stock_qty NUMBER(10) DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table 5: inventory_events (append-only, high throughput)
CREATE TABLE gg_src.inventory_events (
    id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id NUMBER NOT NULL,
    event_type VARCHAR2(30) NOT NULL,
    quantity_delta NUMBER(10) NOT NULL,
    warehouse VARCHAR2(50),
    event_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table 6: payments (financial, moderate volume)
CREATE TABLE gg_src.payments (
    id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id NUMBER NOT NULL,
    method VARCHAR2(30) NOT NULL,
    amount NUMBER(12,2) NOT NULL,
    status VARCHAR2(20) DEFAULT 'PROCESSING',
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table 7: audit_log (append-only, very high volume)
CREATE TABLE gg_src.audit_log (
    id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_type VARCHAR2(50) NOT NULL,
    entity_id NUMBER NOT NULL,
    action VARCHAR2(30) NOT NULL,
    actor VARCHAR2(100),
    details VARCHAR2(4000),
    logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table 8: user_sessions (high churn, frequent updates)
CREATE TABLE gg_src.user_sessions (
    id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id NUMBER NOT NULL,
    session_token VARCHAR2(128) NOT NULL,
    ip_address VARCHAR2(45),
    user_agent VARCHAR2(500),
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active NUMBER(1) DEFAULT 1
);

-- Table 9: notifications (queue-like, insert-heavy)
CREATE TABLE gg_src.notifications (
    id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id NUMBER NOT NULL,
    channel VARCHAR2(20) NOT NULL,
    subject VARCHAR2(200),
    body VARCHAR2(4000),
    status VARCHAR2(20) DEFAULT 'QUEUED',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMP
);

-- Table 10: metrics_raw (time-series, very high insert rate)
CREATE TABLE gg_src.metrics_raw (
    id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    metric_name VARCHAR2(100) NOT NULL,
    metric_value NUMBER(15,4) NOT NULL,
    tags VARCHAR2(500),
    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Enable supplemental logging for all source tables
ALTER TABLE gg_src.customers ADD SUPPLEMENTAL LOG DATA (ALL) COLUMNS;
ALTER TABLE gg_src.orders ADD SUPPLEMENTAL LOG DATA (ALL) COLUMNS;
ALTER TABLE gg_src.order_items ADD SUPPLEMENTAL LOG DATA (ALL) COLUMNS;
ALTER TABLE gg_src.products ADD SUPPLEMENTAL LOG DATA (ALL) COLUMNS;
ALTER TABLE gg_src.inventory_events ADD SUPPLEMENTAL LOG DATA (ALL) COLUMNS;
ALTER TABLE gg_src.payments ADD SUPPLEMENTAL LOG DATA (ALL) COLUMNS;
ALTER TABLE gg_src.audit_log ADD SUPPLEMENTAL LOG DATA (ALL) COLUMNS;
ALTER TABLE gg_src.user_sessions ADD SUPPLEMENTAL LOG DATA (ALL) COLUMNS;
ALTER TABLE gg_src.notifications ADD SUPPLEMENTAL LOG DATA (ALL) COLUMNS;
ALTER TABLE gg_src.metrics_raw ADD SUPPLEMENTAL LOG DATA (ALL) COLUMNS;

-- Seed source data
INSERT INTO gg_src.products (sku, name, category, price, stock_qty)
SELECT 'SKU-' || LPAD(LEVEL, 4, '0'),
       'Product ' || LEVEL,
       CASE MOD(LEVEL, 4) WHEN 0 THEN 'Electronics' WHEN 1 THEN 'Books'
            WHEN 2 THEN 'Clothing' ELSE 'Home' END,
       ROUND(DBMS_RANDOM.VALUE(5, 500), 2),
       FLOOR(DBMS_RANDOM.VALUE(0, 1000))
FROM DUAL CONNECT BY LEVEL <= 200;

INSERT INTO gg_src.customers (name, email, region, tier)
SELECT 'Customer ' || LEVEL,
       'cust' || LEVEL || '@example.com',
       CASE MOD(LEVEL, 4) WHEN 0 THEN 'US-EAST' WHEN 1 THEN 'US-WEST'
            WHEN 2 THEN 'EU-WEST' ELSE 'APAC' END,
       CASE MOD(LEVEL, 3) WHEN 0 THEN 'PREMIUM' WHEN 1 THEN 'STANDARD'
            ELSE 'BASIC' END
FROM DUAL CONNECT BY LEVEL <= 500;
COMMIT;

-- ============================================================
-- 4. Target Schema (GG_TGT) — 10 matching tables (no IDENTITY)
-- ============================================================
CREATE USER gg_tgt IDENTIFIED BY GGMCP_Admin123;
GRANT CONNECT, RESOURCE, UNLIMITED TABLESPACE TO gg_tgt;

CREATE TABLE gg_tgt.customers (
    id NUMBER PRIMARY KEY, name VARCHAR2(100) NOT NULL,
    email VARCHAR2(100) NOT NULL, region VARCHAR2(50),
    tier VARCHAR2(20), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE gg_tgt.orders (
    id NUMBER PRIMARY KEY, customer_id NUMBER NOT NULL,
    amount NUMBER(12,2) NOT NULL, currency VARCHAR2(3),
    status VARCHAR2(20), order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE gg_tgt.order_items (
    id NUMBER PRIMARY KEY, order_id NUMBER NOT NULL,
    product_id NUMBER NOT NULL, quantity NUMBER(10),
    unit_price NUMBER(10,2) NOT NULL, discount_pct NUMBER(5,2)
);
CREATE TABLE gg_tgt.products (
    id NUMBER PRIMARY KEY, sku VARCHAR2(50) NOT NULL,
    name VARCHAR2(200) NOT NULL, category VARCHAR2(100),
    price NUMBER(10,2) NOT NULL, stock_qty NUMBER(10),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE gg_tgt.inventory_events (
    id NUMBER PRIMARY KEY, product_id NUMBER NOT NULL,
    event_type VARCHAR2(30) NOT NULL, quantity_delta NUMBER(10) NOT NULL,
    warehouse VARCHAR2(50), event_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE gg_tgt.payments (
    id NUMBER PRIMARY KEY, order_id NUMBER NOT NULL,
    method VARCHAR2(30) NOT NULL, amount NUMBER(12,2) NOT NULL,
    status VARCHAR2(20), processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE gg_tgt.audit_log (
    id NUMBER PRIMARY KEY, entity_type VARCHAR2(50) NOT NULL,
    entity_id NUMBER NOT NULL, action VARCHAR2(30) NOT NULL,
    actor VARCHAR2(100), details VARCHAR2(4000),
    logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE gg_tgt.user_sessions (
    id NUMBER PRIMARY KEY, customer_id NUMBER NOT NULL,
    session_token VARCHAR2(128) NOT NULL, ip_address VARCHAR2(45),
    user_agent VARCHAR2(500), started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP, is_active NUMBER(1)
);
CREATE TABLE gg_tgt.notifications (
    id NUMBER PRIMARY KEY, customer_id NUMBER NOT NULL,
    channel VARCHAR2(20) NOT NULL, subject VARCHAR2(200),
    body VARCHAR2(4000), status VARCHAR2(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, sent_at TIMESTAMP
);
CREATE TABLE gg_tgt.metrics_raw (
    id NUMBER PRIMARY KEY, metric_name VARCHAR2(100) NOT NULL,
    metric_value NUMBER(15,4) NOT NULL, tags VARCHAR2(500),
    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
