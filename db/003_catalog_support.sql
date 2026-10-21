-- =====================================================================
-- MarketHub - migration 003: catlog support
-- Keeps products.avg_rating / review_count in sync with reviews (automatic),
-- and adds a trigram index so search by title is fast.
-- Run after 002. Safe to run twice.
-- =====================================================================
BEGIN;

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_products_title_trgm ON products USING gin (title gin_trgm_ops);

CREATE OR REPLACE FUNCTION refresh_product_rating() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE pid uuid;
BEGIN
  FOR pid IN
    SELECT DISTINCT x FROM unnest(ARRAY[
      CASE WHEN TG_OP <> 'INSERT' THEN OLD.product_id END,
      CASE WHEN TG_OP <> 'DELETE' THEN NEW.product_id END
    ]) AS x WHERE x IS NOT NULL
  LOOP
    UPDATE products p
       SET avg_rating   = COALESCE((SELECT round(avg(rating)::numeric, 2) FROM reviews WHERE product_id = pid), 0),
           review_count = (SELECT count(*) FROM reviews WHERE product_id = pid)
     WHERE p.id = pid;
  END LOOP;
  RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS trg_reviews_rating ON reviews;
CREATE TRIGGER trg_reviews_rating
  AFTER INSERT OR DELETE OR UPDATE OF rating, product_id ON reviews
  FOR EACH ROW EXECUTE FUNCTION refresh_product_rating();

-- one-time backfill for reviews that already exist
UPDATE products p
   SET avg_rating   = COALESCE((SELECT round(avg(rating)::numeric, 2) FROM reviews WHERE product_id = p.id), 0),
       review_count = (SELECT count(*) FROM reviews WHERE product_id = p.id);

INSERT INTO schema_migrations (version) VALUES ('003_catlog_support') ON CONFLICT DO NOTHING;
COMMIT;