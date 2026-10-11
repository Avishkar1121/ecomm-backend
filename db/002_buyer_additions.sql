-- =====================================================================
-- MarketHub - migration 002: buyer-flow additions
-- Run AFTER markethub_learning_schema.sql (001). Safe to run twice.
-- Adds: auth_tokens, wishlist_items, coupons, coupon_redemptions,
--       notifications, v_rating_breakdown, and new columns on
--       users, products, carts, orders.
-- =====================================================================

BEGIN;

-- Tiny migration tracker (so you always know what was applied)
CREATE TABLE IF NOT EXISTS schema_migrations (
  version     text PRIMARY KEY,
  applied_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO schema_migrations (version) VALUES ('001_learning_schema') ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------
-- Enums (guarded so re-running does not fail)
-- ---------------------------------------------------------------------
DO $$ BEGIN
  CREATE TYPE token_type AS ENUM ('refresh','password_reset','email_verify');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE discount_type AS ENUM ('percent','fixed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE notification_type AS ENUM ('order','payment','shipping','promotion','review','system');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------------------------------------------------------------------
-- Profile page: date of birth and avatar
-- ---------------------------------------------------------------------
ALTER TABLE users ADD COLUMN IF NOT EXISTS date_of_birth date;
ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url    text;

-- ---------------------------------------------------------------------
-- Product detail page: feature list and specification table
-- ---------------------------------------------------------------------
ALTER TABLE products ADD COLUMN IF NOT EXISTS features text[] NOT NULL DEFAULT '{}';
-- specs example: {"Display": "6.1 inch OLED", "Battery": "4000 mAh"}
ALTER TABLE products ADD COLUMN IF NOT EXISTS specs    jsonb  NOT NULL DEFAULT '{}'::jsonb;

-- ---------------------------------------------------------------------
-- Auth: refresh tokens, password reset, email verification
-- Store only a HASH of the token; the raw token goes to the user once.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS auth_tokens (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type        token_type  NOT NULL,
  token_hash  text        NOT NULL UNIQUE,
  user_agent  text,
  ip_address  inet,
  expires_at  timestamptz NOT NULL,
  used_at     timestamptz,
  revoked_at  timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_user_type ON auth_tokens (user_id, type);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_expiry    ON auth_tokens (expires_at);

-- ---------------------------------------------------------------------
-- Wishlist
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wishlist_items (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
  product_id  uuid NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, product_id)          -- adding twice is a no-op, not a duplicate
);
CREATE INDEX IF NOT EXISTS idx_wishlist_user ON wishlist_items (user_id, created_at DESC);

-- ---------------------------------------------------------------------
-- Coupons
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS coupons (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code             citext        NOT NULL UNIQUE,            -- case-insensitive: SAVE10 = save10
  description      varchar(200),
  discount_type    discount_type NOT NULL,
  value            numeric(12,2) NOT NULL CHECK (value > 0),
  max_discount     numeric(12,2) CHECK (max_discount IS NULL OR max_discount > 0),  -- cap for percent coupons
  min_order_amount numeric(12,2) NOT NULL DEFAULT 0 CHECK (min_order_amount >= 0),
  starts_at        timestamptz,
  expires_at       timestamptz,
  usage_limit      int CHECK (usage_limit IS NULL OR usage_limit > 0),      -- total uses, NULL = unlimited
  per_user_limit   int NOT NULL DEFAULT 1 CHECK (per_user_limit > 0),
  used_count       int NOT NULL DEFAULT 0 CHECK (used_count >= 0),
  is_active        boolean NOT NULL DEFAULT true,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CHECK (discount_type <> 'percent' OR value <= 100),
  CHECK (expires_at IS NULL OR starts_at IS NULL OR expires_at > starts_at)
);

DROP TRIGGER IF EXISTS trg_coupons_updated ON coupons;
CREATE TRIGGER trg_coupons_updated BEFORE UPDATE ON coupons
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- One row per order that used a coupon (also enforces per-user limits in code)
CREATE TABLE IF NOT EXISTS coupon_redemptions (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  coupon_id        uuid NOT NULL REFERENCES coupons(id) ON DELETE RESTRICT,
  user_id          uuid NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
  order_id         uuid NOT NULL UNIQUE REFERENCES orders(id) ON DELETE CASCADE,
  discount_amount  numeric(12,2) NOT NULL CHECK (discount_amount >= 0),
  redeemed_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_coupon_redemptions_coupon_user ON coupon_redemptions (coupon_id, user_id);

-- Coupon currently applied to a cart (cleared when the order is placed)
ALTER TABLE carts ADD COLUMN IF NOT EXISTS coupon_id uuid REFERENCES coupons(id) ON DELETE SET NULL;

-- ---------------------------------------------------------------------
-- Orders: delivery estimate, coupon snapshot, safe retries
-- ---------------------------------------------------------------------
ALTER TABLE orders ADD COLUMN IF NOT EXISTS estimated_delivery_date date;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS coupon_code             varchar(40);   -- snapshot, survives coupon edits
ALTER TABLE orders ADD COLUMN IF NOT EXISTS idempotency_key         varchar(100);  -- from the Idempotency-Key header
-- Same key from the same user = same order, so a double-click cannot create two orders
CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_idempotency
  ON orders (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

-- ---------------------------------------------------------------------
-- Notifications (bell icon)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notifications (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type        notification_type NOT NULL DEFAULT 'system',
  title       varchar(150) NOT NULL,
  body        text,
  data        jsonb NOT NULL DEFAULT '{}'::jsonb,       -- e.g. {"order_id": "..."} for deep links
  read_at     timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_notifications_user   ON notifications (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications (user_id) WHERE read_at IS NULL;

-- ---------------------------------------------------------------------
-- Rating breakdown view (the 5-star to 1-star bars on product detail)
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_rating_breakdown AS
SELECT product_id,
       count(*)                                  AS total_reviews,
       round(avg(rating)::numeric, 2)            AS avg_rating,
       count(*) FILTER (WHERE rating = 5)        AS five_star,
       count(*) FILTER (WHERE rating = 4)        AS four_star,
       count(*) FILTER (WHERE rating = 3)        AS three_star,
       count(*) FILTER (WHERE rating = 2)        AS two_star,
       count(*) FILTER (WHERE rating = 1)        AS one_star
  FROM reviews
 GROUP BY product_id;

-- ---------------------------------------------------------------------
-- Sample coupons for testing
-- ---------------------------------------------------------------------
INSERT INTO coupons (code, description, discount_type, value, max_discount, min_order_amount, per_user_limit) VALUES
  ('WELCOME10', '10% off, up to 200',        'percent', 10, 200, 499, 1),
  ('FLAT100',   'Flat 100 off above 999',    'fixed',  100, NULL, 999, 3)
ON CONFLICT (code) DO NOTHING;

INSERT INTO schema_migrations (version) VALUES ('002_buyer_additions') ON CONFLICT DO NOTHING;

COMMIT;