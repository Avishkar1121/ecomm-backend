-- =====================================================================
-- MarketHub - learning schema (20 tables, PostgreSQL 14+)
-- A simplified subset of MarketHub v1.2. Run on an empty database.
--   psql -d markethub_dev -f markethub_learning_schema.sql
-- Conventions: uuid keys, timestamptz, numeric(12,2) money (INR).
-- Never store card numbers; payments hold only a gateway reference.
-- =====================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS citext;

-- ---------------------------------------------------------------------
-- ENUMS
-- ---------------------------------------------------------------------
CREATE TYPE seller_verification_status AS ENUM ('pending','approved','rejected','suspended');
CREATE TYPE product_status  AS ENUM ('draft','active','archived');
CREATE TYPE order_status    AS ENUM ('pending','processing','shipped','out_for_delivery','delivered','cancelled');
CREATE TYPE payment_method  AS ENUM ('cod','card','upi');
CREATE TYPE payment_status  AS ENUM ('pending','paid','failed','refunded','partially_refunded');
CREATE TYPE shipment_status AS ENUM ('pending','shipped','out_for_delivery','delivered','failed','returned');
CREATE TYPE refund_status   AS ENUM ('pending','processed','failed');

-- ---------------------------------------------------------------------
-- SHARED FUNCTIONS
-- ---------------------------------------------------------------------
CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;

CREATE SEQUENCE order_number_seq;

-- ---------------------------------------------------------------------
-- 1. USERS, ROLES, ADDRESSES (5 tables)
-- ---------------------------------------------------------------------
CREATE TABLE states (
  code      char(2)      PRIMARY KEY,
  name      varchar(100) NOT NULL UNIQUE,
  gst_code  char(2)      NOT NULL UNIQUE
);

CREATE TABLE users (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  first_name     varchar(80)  NOT NULL,
  last_name      varchar(80)  NOT NULL DEFAULT '',
  email          citext       NOT NULL,
  password_hash  text         NOT NULL,          -- bcrypt/argon2 hash, never plain text
  phone          varchar(20),
  is_active      boolean      NOT NULL DEFAULT true,
  last_login_at  timestamptz,
  created_at     timestamptz  NOT NULL DEFAULT now(),
  updated_at     timestamptz  NOT NULL DEFAULT now(),
  deleted_at     timestamptz,                    -- soft delete
  CONSTRAINT chk_users_phone CHECK (phone IS NULL OR phone ~ '^\+?[0-9]{10,15}$')
);
-- Unique only among non-deleted users, so an email can be reused after deletion
CREATE UNIQUE INDEX uq_users_email_active ON users (email) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX uq_users_phone_active ON users (phone) WHERE phone IS NOT NULL AND deleted_at IS NULL;
CREATE TRIGGER trg_users_updated BEFORE UPDATE ON users
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE roles (
  id           smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name         varchar(50) NOT NULL UNIQUE,
  description  varchar(200)
);

CREATE TABLE user_roles (
  user_id  uuid     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role_id  smallint NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  PRIMARY KEY (user_id, role_id)
);

CREATE TABLE addresses (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  label       varchar(30)  NOT NULL DEFAULT 'Home',
  full_name   varchar(150) NOT NULL,
  phone       varchar(20)  NOT NULL,
  line1       varchar(200) NOT NULL,
  line2       varchar(200),
  city        varchar(100) NOT NULL,
  state_code  char(2)      NOT NULL REFERENCES states(code),
  zip         varchar(6)   NOT NULL CHECK (zip ~ '^[0-9]{6}$'),
  is_default  boolean      NOT NULL DEFAULT false,
  created_at  timestamptz  NOT NULL DEFAULT now(),
  updated_at  timestamptz  NOT NULL DEFAULT now(),
  deleted_at  timestamptz
);
CREATE INDEX idx_addresses_user ON addresses (user_id) WHERE deleted_at IS NULL;
-- At most one default address per user
CREATE UNIQUE INDEX uq_addresses_default ON addresses (user_id) WHERE is_default AND deleted_at IS NULL;
CREATE TRIGGER trg_addresses_updated BEFORE UPDATE ON addresses
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------
-- 2. SELLERS (1 table)
-- ---------------------------------------------------------------------
CREATE TABLE seller_profiles (
  user_id              uuid PRIMARY KEY REFERENCES users(id) ON DELETE RESTRICT,
  store_name           varchar(150) NOT NULL,
  store_slug           varchar(160) NOT NULL UNIQUE,
  description          text,
  logo_url             text,
  gstin                varchar(15),
  verification_status  seller_verification_status NOT NULL DEFAULT 'pending',
  commission_rate      numeric(5,2) NOT NULL DEFAULT 10.00 CHECK (commission_rate BETWEEN 0 AND 100),
  created_at           timestamptz  NOT NULL DEFAULT now(),
  updated_at           timestamptz  NOT NULL DEFAULT now(),
  CONSTRAINT chk_gstin CHECK (gstin IS NULL OR gstin ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$')
);
CREATE TRIGGER trg_seller_profiles_updated BEFORE UPDATE ON seller_profiles
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------
-- 3. CATALOG (5 tables)
-- ---------------------------------------------------------------------
CREATE TABLE categories (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  parent_id   uuid REFERENCES categories(id) ON DELETE RESTRICT,   -- NULL = top level
  name        varchar(100) NOT NULL,
  slug        varchar(120) NOT NULL UNIQUE,
  sort_order  int          NOT NULL DEFAULT 0,
  is_active   boolean      NOT NULL DEFAULT true,
  CHECK (parent_id IS DISTINCT FROM id)
);
CREATE INDEX idx_categories_parent ON categories (parent_id);

CREATE TABLE brands (
  id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name      varchar(100) NOT NULL UNIQUE,
  slug      varchar(120) NOT NULL UNIQUE,
  logo_url  text
);

CREATE TABLE products (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  seller_id    uuid NOT NULL REFERENCES seller_profiles(user_id) ON DELETE RESTRICT,
  category_id  uuid NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
  brand_id     uuid REFERENCES brands(id) ON DELETE SET NULL,
  title        varchar(250) NOT NULL,
  slug         varchar(280) NOT NULL UNIQUE,
  description  text,
  status       product_status NOT NULL DEFAULT 'draft',
  avg_rating   numeric(3,2)   NOT NULL DEFAULT 0 CHECK (avg_rating BETWEEN 0 AND 5),
  review_count int            NOT NULL DEFAULT 0 CHECK (review_count >= 0),
  created_at   timestamptz    NOT NULL DEFAULT now(),
  updated_at   timestamptz    NOT NULL DEFAULT now(),
  deleted_at   timestamptz
);
CREATE INDEX idx_products_seller   ON products (seller_id);
CREATE INDEX idx_products_category ON products (category_id) WHERE status = 'active' AND deleted_at IS NULL;
CREATE INDEX idx_products_brand    ON products (brand_id);
CREATE TRIGGER trg_products_updated BEFORE UPDATE ON products
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- A variant is the thing you actually buy (size/colour). Price and stock live here.
CREATE TABLE product_variants (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id   uuid NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  sku          varchar(64) NOT NULL UNIQUE,
  name         varchar(120),                         -- e.g. "Red / L"
  mrp          numeric(12,2) NOT NULL CHECK (mrp >= 0),
  price        numeric(12,2) NOT NULL CHECK (price >= 0),
  stock_qty    int           NOT NULL DEFAULT 0 CHECK (stock_qty >= 0),  -- DB refuses negative stock
  is_active    boolean       NOT NULL DEFAULT true,
  created_at   timestamptz   NOT NULL DEFAULT now(),
  updated_at   timestamptz   NOT NULL DEFAULT now(),
  CHECK (price <= mrp)
);
CREATE INDEX idx_variants_product ON product_variants (product_id);
CREATE TRIGGER trg_variants_updated BEFORE UPDATE ON product_variants
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE product_images (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id  uuid NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  url         text NOT NULL,
  alt_text    varchar(200),
  sort_order  int  NOT NULL DEFAULT 0,
  is_primary  boolean NOT NULL DEFAULT false
);
CREATE INDEX idx_product_images_product ON product_images (product_id, sort_order);
CREATE UNIQUE INDEX uq_product_primary_image ON product_images (product_id) WHERE is_primary;

-- ---------------------------------------------------------------------
-- 4. CART (2 tables)
-- ---------------------------------------------------------------------
CREATE TABLE carts (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,  -- one cart per user
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_carts_updated BEFORE UPDATE ON carts
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE cart_items (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cart_id     uuid NOT NULL REFERENCES carts(id) ON DELETE CASCADE,
  variant_id  uuid NOT NULL REFERENCES product_variants(id) ON DELETE CASCADE,
  quantity    int  NOT NULL CHECK (quantity > 0),
  added_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (cart_id, variant_id)                       -- same variant = increase quantity, not a new row
);

-- ---------------------------------------------------------------------
-- 5. ORDERS (3 tables)
-- Orders are SNAPSHOTS: copy name, price and address at purchase time so
-- later catalog or address edits never change history.
-- ---------------------------------------------------------------------
CREATE TABLE orders (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  order_number      varchar(20) NOT NULL UNIQUE
                    DEFAULT ('MH' || to_char(now(),'YYMM') || lpad(nextval('order_number_seq')::text, 6, '0')),
  user_id           uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  status            order_status  NOT NULL DEFAULT 'pending',
  shipping_address  jsonb         NOT NULL,          -- copy of the address at checkout
  subtotal          numeric(12,2) NOT NULL CHECK (subtotal >= 0),
  shipping_fee      numeric(12,2) NOT NULL DEFAULT 0 CHECK (shipping_fee >= 0),
  discount          numeric(12,2) NOT NULL DEFAULT 0 CHECK (discount >= 0),
  total             numeric(12,2) NOT NULL CHECK (total >= 0),
  placed_at         timestamptz   NOT NULL DEFAULT now(),
  cancelled_at      timestamptz,
  updated_at        timestamptz   NOT NULL DEFAULT now(),
  CHECK (total = subtotal + shipping_fee - discount)
);
CREATE INDEX idx_orders_user   ON orders (user_id, placed_at DESC);
CREATE INDEX idx_orders_status ON orders (status);
CREATE TRIGGER trg_orders_updated BEFORE UPDATE ON orders
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE order_items (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id     uuid NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  seller_id    uuid NOT NULL REFERENCES seller_profiles(user_id) ON DELETE RESTRICT,
  variant_id   uuid NOT NULL REFERENCES product_variants(id) ON DELETE RESTRICT,
  product_title varchar(250) NOT NULL,               -- snapshot
  sku          varchar(64)   NOT NULL,               -- snapshot
  unit_price   numeric(12,2) NOT NULL CHECK (unit_price >= 0),   -- snapshot
  quantity     int           NOT NULL CHECK (quantity > 0),
  line_total   numeric(12,2) NOT NULL CHECK (line_total >= 0),
  status       order_status  NOT NULL DEFAULT 'pending',          -- each seller ships their own items
  CHECK (line_total = unit_price * quantity)
);
CREATE INDEX idx_order_items_order  ON order_items (order_id);
CREATE INDEX idx_order_items_seller ON order_items (seller_id, status);
CREATE INDEX idx_order_items_variant ON order_items (variant_id);

CREATE TABLE order_status_history (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  order_id    uuid        NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  status      order_status NOT NULL,
  changed_by  uuid REFERENCES users(id) ON DELETE SET NULL,
  note        text,
  changed_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_order_history_order ON order_status_history (order_id, changed_at);

-- Automatically record every status change (and the initial status)
CREATE FUNCTION log_order_status() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' OR NEW.status IS DISTINCT FROM OLD.status THEN
    INSERT INTO order_status_history (order_id, status, changed_by)
    VALUES (NEW.id, NEW.status, NULLIF(current_setting('app.user_id', true), '')::uuid);
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_orders_status_log AFTER INSERT OR UPDATE OF status ON orders
  FOR EACH ROW EXECUTE FUNCTION log_order_status();

-- ---------------------------------------------------------------------
-- 6. PAYMENTS AND REFUNDS (2 tables)
-- ---------------------------------------------------------------------
CREATE TABLE payments (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id      uuid NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
  method        payment_method NOT NULL,
  status        payment_status NOT NULL DEFAULT 'pending',
  amount        numeric(12,2)  NOT NULL CHECK (amount > 0),
  gateway_ref   varchar(100),                        -- e.g. Razorpay/Stripe payment id
  paid_at       timestamptz,
  created_at    timestamptz    NOT NULL DEFAULT now(),
  updated_at    timestamptz    NOT NULL DEFAULT now()
);
CREATE INDEX idx_payments_order ON payments (order_id);
CREATE UNIQUE INDEX uq_payments_gateway_ref ON payments (gateway_ref) WHERE gateway_ref IS NOT NULL;
CREATE TRIGGER trg_payments_updated BEFORE UPDATE ON payments
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE refunds (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  payment_id     uuid NOT NULL REFERENCES payments(id) ON DELETE RESTRICT,
  order_item_id  uuid REFERENCES order_items(id) ON DELETE RESTRICT,   -- NULL = whole-order refund
  amount         numeric(12,2) NOT NULL CHECK (amount > 0),
  status         refund_status NOT NULL DEFAULT 'pending',
  reason         text,
  gateway_ref    varchar(100),
  created_at     timestamptz   NOT NULL DEFAULT now(),
  processed_at   timestamptz
);
CREATE INDEX idx_refunds_payment ON refunds (payment_id);

-- ---------------------------------------------------------------------
-- 7. SHIPPING (1 table)
-- One shipment per seller per order. Simplified: no shipment_items table.
-- ---------------------------------------------------------------------
CREATE TABLE shipments (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id         uuid NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
  seller_id        uuid NOT NULL REFERENCES seller_profiles(user_id) ON DELETE RESTRICT,
  carrier          varchar(80),
  tracking_number  varchar(100),
  status           shipment_status NOT NULL DEFAULT 'pending',
  shipped_at       timestamptz,
  delivered_at     timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (order_id, seller_id)
);
CREATE INDEX idx_shipments_seller ON shipments (seller_id, status);
CREATE TRIGGER trg_shipments_updated BEFORE UPDATE ON shipments
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------
-- 8. REVIEWS (1 table)
-- Tied to an order_item so only real buyers can review (verified purchase),
-- and each purchased item can be reviewed once.
-- ---------------------------------------------------------------------
CREATE TABLE reviews (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id     uuid NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  order_item_id  uuid NOT NULL UNIQUE REFERENCES order_items(id) ON DELETE RESTRICT,
  rating         smallint NOT NULL CHECK (rating BETWEEN 1 AND 5),
  title          varchar(150),
  body           text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_reviews_product ON reviews (product_id, created_at DESC);
CREATE INDEX idx_reviews_user    ON reviews (user_id);
CREATE TRIGGER trg_reviews_updated BEFORE UPDATE ON reviews
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------
-- SEED DATA (minimal; extend as needed)
-- ---------------------------------------------------------------------
INSERT INTO roles (name, description) VALUES
  ('buyer','Customer access'), ('seller','Seller access'), ('admin','Administrative access');

INSERT INTO states (code, name, gst_code) VALUES
  ('MH','Maharashtra','27'), ('DL','Delhi','07'), ('KA','Karnataka','29'),
  ('GJ','Gujarat','24'),     ('TN','Tamil Nadu','33'), ('UP','Uttar Pradesh','09'),
  ('WB','West Bengal','19'), ('TS','Telangana','36');

INSERT INTO categories (name, slug, sort_order) VALUES
  ('Electronics','electronics',1), ('Fashion','fashion',2), ('Home','home',3),
  ('Beauty','beauty',4), ('Sports','sports',5), ('Grocery','grocery',6);

COMMIT;