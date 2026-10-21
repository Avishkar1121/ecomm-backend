# ORM + user-facing Product API

The ORM mappings are aligned to the supplied `db/markethub_learning_schema.sql`. Product reads only expose `status = active`, non-deleted products in active categories, and active variants. The schema does not contain `products.features`, `products.specs`, or `product_variants.in_stock`; stock is derived from `stock_qty > 0`.

## Routes (under `/api/v1`)
- `GET /categories`
- `GET /brands?category=electronics`
- `GET /products?q=phone&category=electronics&brand=samsung&min_price=100&max_price=50000&min_rating=4&in_stock=true&sort=price_asc&page=1&page_size=12`
- `GET /products/{slug}`
- `GET /products/{slug}/related?limit=8`

The product endpoints are read-only user/catlog endpoints. Seller create/update and admin moderation should be implemented separately with role checks. Image uploads are not implemented; the API reads URLs from `product_images`.

## Important
- Keep your existing `.env`; this archive does not overwrite it.
- This is a mapping for an already-created schema. It does not run `create_all()` or alter your database. Apply your SQL schema/migrations first.
- The schema uses PostgreSQL enum types, so the `Enum(..., create_type=False)` mappings expect those types to exist already.
- The existing auth/dependency modules contain SQL queries and remain unchanged; this change ORM-maps the catlog/product path, not every table in the entire application.
- The supplied schema's `users` table does not include `avatar_url`, but the existing `app/deps.py` query selects it. Remove `u.avatar_url` from that query or add that column through a deliberate migration before testing auth.
