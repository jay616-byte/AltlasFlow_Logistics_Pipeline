\connect atlasflow_ops

CREATE TABLE public.customers (
    customer_id       VARCHAR(24) PRIMARY KEY,
    customer_name     VARCHAR(120) NOT NULL,
    customer_segment  VARCHAR(32) NOT NULL,
    country_code      CHAR(2) NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE public.orders (
    order_id              VARCHAR(24) PRIMARY KEY,
    external_order_ref    VARCHAR(40) NOT NULL UNIQUE,
    customer_id           VARCHAR(24) NOT NULL REFERENCES public.customers(customer_id),
    status                VARCHAR(24) NOT NULL,
    product_sku           VARCHAR(40) NOT NULL,
    quantity              INTEGER NOT NULL CHECK (quantity > 0),
    order_value           NUMERIC(12,2) NOT NULL CHECK (order_value >= 0),
    currency              CHAR(3) NOT NULL,
    order_created_at      TIMESTAMPTZ NOT NULL,
    promised_delivery_at  TIMESTAMPTZ NOT NULL,
    destination_country   CHAR(2) NOT NULL,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_orders_customer ON public.orders(customer_id);

CREATE TABLE public.inventory_balances (
    warehouse_id       VARCHAR(24) NOT NULL,
    warehouse_name     VARCHAR(120) NOT NULL,
    region             VARCHAR(32) NOT NULL,
    product_sku        VARCHAR(40) NOT NULL,
    on_hand_quantity   INTEGER NOT NULL CHECK (on_hand_quantity >= 0),
    reserved_quantity  INTEGER NOT NULL CHECK (reserved_quantity >= 0),
    damaged_quantity   INTEGER NOT NULL DEFAULT 0 CHECK (damaged_quantity >= 0),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (warehouse_id, product_sku),
    CHECK (reserved_quantity + damaged_quantity <= on_hand_quantity)
);

CREATE TABLE public.carriers (
    carrier_id    VARCHAR(24) PRIMARY KEY,
    carrier_name  VARCHAR(120) NOT NULL,
    service_level VARCHAR(32) NOT NULL,
    home_region   VARCHAR(32) NOT NULL,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE public.shipments (
    shipment_id          VARCHAR(24) PRIMARY KEY,
    order_id             VARCHAR(24) NOT NULL REFERENCES public.orders(order_id),
    carrier_id           VARCHAR(24) NOT NULL REFERENCES public.carriers(carrier_id),
    origin_warehouse_id  VARCHAR(24) NOT NULL,
    status               VARCHAR(24) NOT NULL,
    planned_arrival_at   TIMESTAMPTZ,
    actual_delivery_at   TIMESTAMPTZ,
    source_version       INTEGER NOT NULL DEFAULT 1 CHECK (source_version > 0),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_shipments_order ON public.shipments(order_id);

CREATE TABLE public.tracking_events (
    event_id       VARCHAR(48) PRIMARY KEY,
    shipment_id    VARCHAR(24) NOT NULL REFERENCES public.shipments(shipment_id),
    event_type     VARCHAR(40) NOT NULL,
    event_time     TIMESTAMPTZ NOT NULL,
    location       VARCHAR(120) NOT NULL,
    delay_minutes  INTEGER NOT NULL DEFAULT 0,
    source         VARCHAR(32) NOT NULL DEFAULT 'synthetic-generator'
);
CREATE INDEX idx_tracking_shipment_time ON public.tracking_events(shipment_id, event_time);

CREATE TABLE public.disruptions (
    disruption_id          VARCHAR(48) PRIMARY KEY,
    shipment_id            VARCHAR(24) REFERENCES public.shipments(shipment_id),
    disruption_type        VARCHAR(48) NOT NULL,
    severity               VARCHAR(16) NOT NULL,
    location               VARCHAR(120) NOT NULL,
    started_at             TIMESTAMPTZ NOT NULL,
    expected_delay_minutes INTEGER NOT NULL DEFAULT 0,
    active                 BOOLEAN NOT NULL DEFAULT TRUE,
    description            TEXT NOT NULL);
CREATE INDEX idx_disruptions_shipment_active ON public.disruptions(shipment_id, active);

INSERT INTO public.customers (customer_id, customer_name, customer_segment, country_code) VALUES
('CUST-001', 'Northstar Retail Group', 'enterprise', 'US'),
('CUST-002', 'Rhine Valley Stores', 'enterprise', 'DE'),
('CUST-003', 'Merlion Supply Co', 'mid_market', 'SG'),
('CUST-004', 'Lowlands Market', 'mid_market', 'NL'),
('CUST-005', 'Southern Cross Retail', 'small_business', 'AU'),
('CUST-006', 'Maple Distribution', 'mid_market', 'CA');

INSERT INTO public.orders (
    order_id, external_order_ref, customer_id, status, product_sku, quantity,
    order_value, currency, order_created_at, promised_delivery_at, destination_country) VALUES
('ORD-001', 'EXT-10001', 'CUST-001', 'shipped',    'SKU-LAPTOP-A',   40, 35960.00, 'USD', now() - interval '4 days', now() + interval '2 days', 'US'),
('ORD-002', 'EXT-10002', 'CUST-002', 'shipped',    'SKU-DOCK-A',     80, 10320.00, 'EUR', now() - interval '6 days', now() + interval '1 day', 'DE'),
('ORD-003', 'EXT-10003', 'CUST-003', 'processing', 'SKU-MONITOR-A',  30,  7470.00, 'SGD', now() - interval '2 days', now() + interval '4 days', 'SG'),
('ORD-004', 'EXT-10004', 'CUST-004', 'delivered',  'SKU-LAPTOP-A',   25, 22475.00, 'EUR', now() - interval '9 days', now() - interval '2 days', 'NL'),
('ORD-005', 'EXT-10005', 'CUST-005', 'shipped',    'SKU-HEADSET-A',  50,  3950.00, 'AUD', now() - interval '5 days', now() - interval '1 day', 'AU'),
('ORD-006', 'EXT-10006', 'CUST-001', 'confirmed',  'SKU-KEYBOARD-A', 60,  3540.00, 'USD', now() - interval '1 day', now() + interval '5 days', 'US'),
('ORD-007', 'EXT-10007', 'CUST-006', 'processing', 'SKU-DOCK-A',     35,  4515.00, 'CAD', now() - interval '3 days', now() + interval '3 days', 'CA'),
('ORD-008', 'EXT-10008', 'CUST-002', 'shipped',    'SKU-LAPTOP-A',   35, 31465.00, 'EUR', now() - interval '3 days', now() + interval '1 day', 'DE');


INSERT INTO public.inventory_balances (
    warehouse_id, warehouse_name, region, product_sku,
    on_hand_quantity, reserved_quantity, damaged_quantity) VALUES
('WH-US-EAST', 'East Coast Distribution Center', 'NA-EAST', 'SKU-LAPTOP-A',   120, 85, 2),
('WH-US-EAST', 'East Coast Distribution Center', 'NA-EAST', 'SKU-KEYBOARD-A', 200, 145, 1),
('WH-US-EAST', 'East Coast Distribution Center', 'NA-EAST', 'SKU-DOCK-A',     150, 50, 0),
('WH-EU-WEST', 'Western Europe Distribution Center', 'EU-WEST', 'SKU-LAPTOP-A', 100, 20, 5),
('WH-EU-WEST', 'Western Europe Distribution Center', 'EU-WEST', 'SKU-DOCK-A',   160, 90, 4),
('WH-EU-WEST', 'Western Europe Distribution Center', 'EU-WEST', 'SKU-HEADSET-A', 90, 35, 10),
('WH-APAC-SG', 'Singapore Regional Distribution Center', 'APAC', 'SKU-MONITOR-A', 70, 42, 3),
('WH-APAC-SG', 'Singapore Regional Distribution Center', 'APAC', 'SKU-LAPTOP-A', 45, 20, 0);

INSERT INTO public.carriers (carrier_id, carrier_name, service_level, home_region) VALUES
('CARR-001', 'Carrier Alpha', 'express', 'NA'),
('CARR-002', 'Carrier Beta', 'standard', 'EU'),
('CARR-003', 'Carrier Gamma', 'premium', 'GLOBAL'),
('CARR-004', 'Carrier Delta', 'economy', 'APAC');

INSERT INTO public.shipments (
    shipment_id, order_id, carrier_id, origin_warehouse_id, status,
    planned_arrival_at, actual_delivery_at, source_version
) VALUES
('SHP-001', 'ORD-001', 'CARR-003', 'WH-US-EAST', 'in_transit', now() + interval '2 days', NULL, 1),
('SHP-002', 'ORD-002', 'CARR-002', 'WH-EU-WEST', 'delayed',    now() - interval '1 day', NULL, 2),
('SHP-003', 'ORD-003', 'CARR-004', 'WH-APAC-SG',  'booked',     now() + interval '4 days', NULL, 1),
('SHP-004', 'ORD-004', 'CARR-002', 'WH-EU-WEST',  'delivered',  now() - interval '2 days', now() - interval '2 days', 2),
('SHP-005', 'ORD-005', 'CARR-001', 'WH-EU-WEST',  'in_transit', now() - interval '1 day', NULL, 1),
('SHP-006', 'ORD-006', 'CARR-003', 'WH-US-EAST',  'planned',    now() + interval '5 days', NULL, 1),
('SHP-007', 'ORD-007', 'CARR-004', 'WH-EU-WEST',  'in_transit', now() + interval '3 days', NULL, 1),
('SHP-008', 'ORD-008', 'CARR-002', 'WH-EU-WEST',  'in_transit', now() + interval '1 day', NULL, 1);

GRANT CONNECT ON DATABASE atlasflow_ops TO atlasflow_cdc;
GRANT USAGE ON SCHEMA public TO atlasflow_cdc;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO atlasflow_cdc;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO atlasflow_cdc;
