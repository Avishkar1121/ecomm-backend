-- Run ONCE against the existing database BEFORE deploying the new cart code.
-- Makes cart_items.variant_id mandatory and unique per cart.
BEGIN;

-- 1. Old rows added without a variant: pin them to the cheapest active variant.
UPDATE cart_items ci
   SET variant_id = (SELECT pv.id FROM product_variants pv
                      WHERE pv.product_id = ci.product_id AND pv.is_active
                      ORDER BY pv.price, pv.id LIMIT 1)
 WHERE ci.variant_id IS NULL;

-- 2. Products with no active variant cannot be bought any more: drop those lines.
DELETE FROM cart_items WHERE variant_id IS NULL;
DELETE FROM cart_items WHERE quantity <= 0;

-- 3. Merge duplicate (cart, variant) lines: keep the oldest, add up the quantities.
WITH d AS (
    SELECT cart_id, variant_id,
           (array_agg(id ORDER BY created_at, id))[1] AS keep_id,
           sum(quantity) AS total
      FROM cart_items GROUP BY cart_id, variant_id HAVING count(*) > 1)
UPDATE cart_items ci SET quantity = LEAST(999, d.total) FROM d WHERE ci.id = d.keep_id;

DELETE FROM cart_items ci
 USING (SELECT cart_id, variant_id,
               (array_agg(id ORDER BY created_at, id))[1] AS keep_id
          FROM cart_items GROUP BY cart_id, variant_id HAVING count(*) > 1) d
 WHERE ci.cart_id = d.cart_id AND ci.variant_id = d.variant_id AND ci.id <> d.keep_id;

-- 4. Constraints.
ALTER TABLE cart_items ALTER COLUMN variant_id SET NOT NULL;
ALTER TABLE cart_items ADD CONSTRAINT uq_cart_items_cart_variant UNIQUE (cart_id, variant_id);
ALTER TABLE cart_items ADD CONSTRAINT ck_cart_items_quantity_positive CHECK (quantity > 0);

COMMIT;

-- Role fix (only if your DB already assigned a 'customer' role to users):
--   INSERT INTO user_roles (user_id, role_id)
--   SELECT ur.user_id, (SELECT id FROM roles WHERE name = 'buyer')
--     FROM user_roles ur JOIN roles r ON r.id = ur.role_id AND r.name = 'customer'
--   ON CONFLICT DO NOTHING;
