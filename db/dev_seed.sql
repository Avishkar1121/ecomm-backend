-- =====================================================================
-- MarketHub - DEV SAMPLE DATA (not a migration; never run in production)
-- 1 seller, 3 buyers, 5 brands, 10 products with variants/images, and reviews.
-- Run after 003. Safe to run twice (skips if already loaded).
-- Sample users have a fake password hash and cannot log in; register your own.
-- =====================================================================
DO $seed$
DECLARE
  seller uuid := '00000000-0000-0000-0000-0000000000a1';
  buyers uuid[] := ARRAY['00000000-0000-0000-0000-0000000000b1'::uuid,
                         '00000000-0000-0000-0000-0000000000b2'::uuid,
                         '00000000-0000-0000-0000-0000000000b3'::uuid];
  b uuid; r record; pid uuid; vid uuid; oid uuid; oiid uuid;
  n int := 0; k int; rating int; v_price numeric;
BEGIN
  IF EXISTS (SELECT 1 FROM users WHERE id = seller) THEN
    RAISE NOTICE 'Dev seed already loaded, skipping'; RETURN;
  END IF;

  INSERT INTO users (id, first_name, last_name, email, password_hash) VALUES
    (seller,   'Aarav', 'Mehta',  'seed.seller@markethub.dev', 'seed-not-a-real-hash'),
    (buyers[1],'Priya', 'Sharma', 'seed.buyer1@markethub.dev', 'seed-not-a-real-hash'),
    (buyers[2],'Rohan', 'Verma',  'seed.buyer2@markethub.dev', 'seed-not-a-real-hash'),
    (buyers[3],'Sneha', 'Patil',  'seed.buyer3@markethub.dev', 'seed-not-a-real-hash');
  INSERT INTO user_roles (user_id, role_id) SELECT seller, id FROM roles WHERE name = 'seller';
  INSERT INTO user_roles (user_id, role_id) SELECT x, id FROM unnest(buyers) x, roles WHERE name = 'buyer';
  INSERT INTO seller_profiles (user_id, store_name, store_slug, description, verification_status)
    VALUES (seller, 'Urban Hub Store', 'urban-hub-store', 'Quality everyday products.', 'approved');

  INSERT INTO brands (name, slug) VALUES
    ('Nova','nova'),('Atlas','atlas'),('Aurora','aurora'),('Peak','peak'),('Hearth','hearth')
  ON CONFLICT (slug) DO NOTHING;

  FOR r IN SELECT * FROM (VALUES
    ('electronics','nova',  'Nova Pulse Wireless Headphones',  'nova-pulse-wireless-headphones',  'Over-ear Bluetooth headphones with active noise cancellation and 40-hour battery life.',
       ARRAY['Active noise cancellation','40-hour battery','Fast charge: 10 min = 5 hours','Foldable design'], '{"Connectivity":"Bluetooth 5.3","Battery":"40 hours","Weight":"250 g","Warranty":"1 year"}'::jsonb, 4999.00, 2999.00, 20),
    ('electronics','atlas', 'Atlas 20000mAh Power Bank',       'atlas-20000mah-power-bank',       'High-capacity power bank with 22.5W fast charging and dual USB output.',
       ARRAY['20000 mAh capacity','22.5W fast charging','Dual USB-A + USB-C','LED power indicator'], '{"Capacity":"20000 mAh","Output":"22.5W","Ports":"2x USB-A, 1x USB-C","Warranty":"6 months"}'::jsonb, 2499.00, 1699.00, 35),
    ('electronics','nova',  'Nova Fit Smart Watch',            'nova-fit-smart-watch',            'AMOLED smartwatch with heart-rate, SpO2 and 7-day battery.',
       ARRAY['1.8 inch AMOLED','Heart-rate and SpO2','7-day battery','100+ sport modes'], '{"Display":"1.8 inch AMOLED","Battery":"7 days","Water resistance":"5 ATM","Warranty":"1 year"}'::jsonb, 6999.00, 3499.00, 0),
    ('fashion',    'aurora','Aurora Classic Cotton T-Shirt',   'aurora-classic-cotton-tshirt',    'Soft 100% combed cotton crew-neck t-shirt, regular fit.',
       ARRAY['100% combed cotton','Regular fit','Machine washable','Pre-shrunk'], '{"Material":"100% cotton","Fit":"Regular","Care":"Machine wash cold"}'::jsonb, 999.00, 599.00, 60),
    ('fashion',    'peak',  'Peak Trail Running Shoes',        'peak-trail-running-shoes',        'Lightweight running shoes with cushioned sole and breathable mesh upper.',
       ARRAY['Breathable mesh upper','Cushioned EVA sole','Slip-resistant outsole'], '{"Upper":"Mesh","Sole":"EVA","Closure":"Lace-up"}'::jsonb, 3499.00, 2299.00, 25),
    ('home',       'hearth','Hearth Ceramic Dinner Set (16 pc)','hearth-ceramic-dinner-set',      'Sixteen-piece ceramic dinner set for four, microwave and dishwasher safe.',
       ARRAY['16 pieces','Microwave safe','Dishwasher safe','Chip-resistant glaze'], '{"Pieces":"16","Material":"Ceramic","Serves":"4"}'::jsonb, 3999.00, 2799.00, 15),
    ('home',       'hearth','Hearth Memory Foam Pillow',       'hearth-memory-foam-pillow',       'Contour memory foam pillow with removable washable cover.',
       ARRAY['Contour support','Removable cover','Hypoallergenic'], '{"Fill":"Memory foam","Cover":"Cotton blend","Size":"60 x 40 cm"}'::jsonb, 1499.00, 899.00, 40),
    ('beauty',     'aurora','Aurora Vitamin C Face Serum',     'aurora-vitamin-c-serum',          'Brightening 15% vitamin C serum with hyaluronic acid, 30 ml.',
       ARRAY['15% Vitamin C','Hyaluronic acid','Fragrance free','30 ml'], '{"Volume":"30 ml","Skin type":"All","Shelf life":"12 months"}'::jsonb, 799.00, 549.00, 50),
    ('sports',     'peak',  'Peak Anti-Slip Yoga Mat',         'peak-anti-slip-yoga-mat',         '6 mm thick anti-slip yoga mat with carry strap.',
       ARRAY['6 mm cushioning','Anti-slip texture','Carry strap included'], '{"Thickness":"6 mm","Size":"183 x 61 cm","Material":"TPE"}'::jsonb, 1299.00, 799.00, 45),
    ('sports',     'peak',  'Peak Adjustable Dumbbell Set',    'peak-adjustable-dumbbell-set',    'Adjustable dumbbell pair, 2 to 10 kg per hand.',
       ARRAY['Adjustable 2-10 kg','Anti-roll design','Includes storage tray'], '{"Weight range":"2-10 kg","Material":"Steel + rubber"}'::jsonb, 5999.00, 4499.00, 10)
  ) AS t(cat, brand, title, slug, descr, feats, specs, mrp, price, stock)
  LOOP
    n := n + 1;
    INSERT INTO products (seller_id, category_id, brand_id, title, slug, description, features, specs, status)
    SELECT seller, c.id, br.id, r.title, r.slug, r.descr, r.feats, r.specs, 'active'
      FROM categories c, brands br WHERE c.slug = r.cat AND br.slug = r.brand
    RETURNING id INTO pid;

    INSERT INTO product_variants (product_id, sku, name, mrp, price, stock_qty) VALUES
      (pid, 'SKU-' || n || '-A', 'Standard', r.mrp, r.price, r.stock),
      (pid, 'SKU-' || n || '-B', 'Premium',  r.mrp, r.price + 200, CASE WHEN r.stock = 0 THEN 0 ELSE 5 END);
    INSERT INTO product_images (product_id, url, alt_text, sort_order, is_primary) VALUES
      (pid, 'https://picsum.photos/seed/' || r.slug || '-1/800/800', r.title, 0, true),
      (pid, 'https://picsum.photos/seed/' || r.slug || '-2/800/800', r.title, 1, false),
      (pid, 'https://picsum.photos/seed/' || r.slug || '-3/800/800', r.title, 2, false);

    -- reviews: skip the out-of-stock product (n = 3) and product 10 so some have none
    IF n NOT IN (3, 10) THEN
      k := 0;
      FOREACH b IN ARRAY buyers LOOP
        k := k + 1;
        rating := ((n * 7 + k * 3) % 3) + 3;           -- 3..5
        IF (n + k) % 7 = 0 THEN rating := 2; END IF;   -- an occasional low rating
        SELECT id, price INTO vid, v_price FROM product_variants WHERE product_id = pid ORDER BY price LIMIT 1;
        INSERT INTO orders (user_id, status, shipping_address, subtotal, total)
          VALUES (b, 'delivered', '{"city":"Mumbai"}'::jsonb, v_price, v_price) RETURNING id INTO oid;
        INSERT INTO order_items (order_id, seller_id, variant_id, product_title, sku, unit_price, quantity, line_total, status)
          SELECT oid, seller, pv.id, r.title, pv.sku, pv.price, 1, pv.price, 'delivered'
            FROM product_variants pv WHERE pv.id = vid RETURNING id INTO oiid;
        INSERT INTO reviews (product_id, user_id, order_item_id, rating, title, body) VALUES
          (pid, b, oiid, rating,
           (ARRAY['Great value','Good, but could be better','Exactly as described','Love it','Decent for the price'])[1 + (n + k) % 5],
           (ARRAY['Works well and arrived on time.','Build quality is good for this price.','Packaging was neat, product matches the photos.','Using it daily, no complaints so far.'])[1 + (n * k) % 4]);
      END LOOP;
    END IF;
  END LOOP;
END
$seed$;