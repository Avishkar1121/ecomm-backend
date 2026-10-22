import threading
import uuid

from sqlalchemy import text

from app.core.database import engine

C = "/api/v1/cart"


def add(client, h, pid, vid=None, qty=1):
    body = {"product_id": str(pid), "quantity": qty}
    if vid:
        body["variant_id"] = str(vid)
    return client.post(f"{C}/items", json=body, headers=h)


def test_new_buyer_can_use_cart_and_wishlist(client, buyer):
    h, *_ = buyer
    assert client.get(C, headers=h).status_code == 200            # was 403: role "customer" vs "buyer"
    assert client.get("/api/v1/wishlist", headers=h).status_code == 200


def test_single_variant_product_resolves_concrete_variant(client, buyer, catalog):
    h, *_ = buyer
    pid, (vid,) = catalog.product([(90, 10)])
    r = add(client, h, pid)
    assert r.status_code == 200, r.text
    line = r.json()["items"][0]
    assert line["variant_id"] == str(vid)                         # never NULL any more
    assert r.json()["subtotal"] == 90.0 and r.json()["item_count"] == 1


def test_multi_variant_requires_variant(client, buyer, catalog):
    h, *_ = buyer
    pid, (v1, v2) = catalog.product([(90, 5), (120, 5)])
    r = add(client, h, pid)
    assert r.status_code == 400 and r.json()["code"] == "VARIANT_REQUIRED"
    assert add(client, h, pid, v2).status_code == 200


def test_same_variant_merges_into_one_line_and_respects_stock(client, buyer, catalog):
    h, *_ = buyer
    pid, (vid,) = catalog.product([(50, 5)])
    add(client, h, pid, vid, 3)
    r = add(client, h, pid, vid, 2)
    assert r.status_code == 200 and len(r.json()["items"]) == 1 and r.json()["items"][0]["quantity"] == 5
    r = add(client, h, pid, vid, 1)
    assert r.status_code == 409 and r.json()["code"] == "INSUFFICIENT_STOCK"
    assert client.get(C, headers=h).json()["items"][0]["quantity"] == 5   # unchanged after rejection


def test_out_of_stock_and_unsellable_products_rejected(client, buyer, catalog, db):
    h, *_ = buyer
    pid, _ = catalog.product([(50, 0)])
    r = add(client, h, pid)
    assert r.status_code == 409 and r.json()["code"] == "OUT_OF_STOCK"
    draft, _ = catalog.product([(50, 5)], status="draft")
    assert add(client, h, draft).json()["code"] == "PRODUCT_NOT_FOUND"
    gone, _ = catalog.product([(50, 5)])
    db.execute(text("UPDATE products SET deleted_at = now() WHERE id = :i"), {"i": gone})
    assert add(client, h, gone).status_code == 404
    # unapproved seller
    s2 = uuid.uuid4()
    db.execute(text("INSERT INTO users(id,first_name,email,password_hash) VALUES (:i,'S',:e,'x')"), {"i": s2, "e": f"{s2.hex[:8]}@x.com"})
    db.execute(text("INSERT INTO seller_profiles(user_id,store_name,store_slug,verification_status) VALUES (:i,'S',:s,'suspended')"), {"i": s2, "s": s2.hex[:10]})
    bad, _ = catalog.product([(50, 5)], seller=s2)
    assert add(client, h, bad).json()["code"] == "PRODUCT_NOT_FOUND"


def test_variant_of_another_product_rejected(client, buyer, catalog):
    h, *_ = buyer
    p1, _ = catalog.product([(50, 5)])
    _, (other_v,) = catalog.product([(60, 5)])
    r = add(client, h, p1, other_v)
    assert r.status_code == 404 and r.json()["code"] == "VARIANT_NOT_FOUND"


def test_prices_are_live_and_flagged(client, buyer, catalog, db):
    h, *_ = buyer
    pid, (vid,) = catalog.product([(100, 5)])
    add(client, h, pid, vid, 2)
    db.execute(text("UPDATE product_variants SET price = 80, mrp = 120 WHERE id = :i"), {"i": vid})
    c = client.get(C, headers=h).json()
    line = c["items"][0]
    assert line["unit_price"] == 80.0 and line["price_when_added"] == 100.0 and line["price_changed"] is True
    assert c["subtotal"] == 160.0 and line["line_total"] == 160.0


def test_stock_drop_and_unavailable_flagged_and_excluded_from_totals(client, buyer, catalog, db):
    h, *_ = buyer
    p1, (v1,) = catalog.product([(100, 10)])
    p2, (v2,) = catalog.product([(50, 10)])
    add(client, h, p1, v1, 4)
    add(client, h, p2, v2, 1)
    db.execute(text("UPDATE product_variants SET stock_qty = 2 WHERE id = :i"), {"i": v1})
    c = client.get(C, headers=h).json()
    by = {i["variant_id"]: i for i in c["items"]}
    assert by[str(v1)]["issue"] == "INSUFFICIENT_STOCK" and c["has_issues"] is True
    db.execute(text("UPDATE products SET status = 'archived' WHERE id = :i"), {"i": p2})
    c = client.get(C, headers=h).json()
    by = {i["variant_id"]: i for i in c["items"]}
    assert by[str(v2)]["issue"] == "UNAVAILABLE" and by[str(v2)]["is_available"] is False
    assert c["subtotal"] == 400.0                                  # only the still-sellable line (live price x qty)
    # user can still DECREASE the over-stock line, but not increase it
    item_id = by[str(v1)]["id"]
    assert client.patch(f"{C}/items/{item_id}", json={"quantity": 5}, headers=h).status_code == 409
    r = client.patch(f"{C}/items/{item_id}", json={"quantity": 2}, headers=h)
    assert r.status_code == 200
    fixed = next(i for i in r.json()["items"] if i["variant_id"] == str(v1))
    assert fixed["quantity"] == 2 and fixed["issue"] is None


def test_update_remove_clear_and_404_codes(client, buyer, catalog):
    h, *_ = buyer
    pid, (vid,) = catalog.product([(10, 9)])
    item = add(client, h, pid, vid, 2).json()["items"][0]["id"]
    assert client.patch(f"{C}/items/{item}", json={"quantity": 3}, headers=h).json()["items"][0]["quantity"] == 3
    assert client.patch(f"{C}/items/{item}", json={"quantity": 0}, headers=h).json()["items"] == []
    r = client.patch(f"{C}/items/{uuid.uuid4()}", json={"quantity": 1}, headers=h)
    assert r.status_code == 404 and r.json()["code"] == "CART_ITEM_NOT_FOUND"
    assert client.delete(f"{C}/items/{uuid.uuid4()}", headers=h).json()["code"] == "CART_ITEM_NOT_FOUND"
    add(client, h, pid, vid, 1)
    assert client.delete(C, headers=h).status_code == 204 and client.get(C, headers=h).json()["items"] == []


def test_money_is_exact(client, buyer, catalog):
    h, *_ = buyer
    pid, (vid,) = catalog.product([(0.10, 100)])
    add(client, h, pid, vid, 3)
    assert client.get(C, headers=h).json()["total"] == 0.3         # float math would give 0.30000000000000004


def test_concurrent_first_requests_and_adds_do_not_duplicate(client, buyer, catalog):
    h, uid, _ = buyer
    pid, (vid,) = catalog.product([(10, 100)])
    results = []

    def worker():
        from fastapi.testclient import TestClient
        from app.main import app
        results.append(add(TestClient(app), h, pid, vid, 1).status_code)

    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert results == [200] * 8, results
    with engine.begin() as c:
        rows = c.execute(text("SELECT count(*), sum(quantity) FROM cart_items ci JOIN carts ca ON ca.id=ci.cart_id WHERE ca.user_id=:u"), {"u": uid}).one()
    assert tuple(rows) == (1, 8)


def test_db_rejects_duplicate_and_null_variant_rows(catalog, buyer, client):
    import sqlalchemy
    h, uid, _ = buyer
    pid, (vid,) = catalog.product([(10, 5)])
    cart = client.get(C, headers=h).json()["id"]
    ins = "INSERT INTO cart_items(cart_id,product_id,variant_id,quantity,unit_price) VALUES (:c,:p,{v},{q},10)"
    with engine.begin() as c:
        c.execute(text(ins.format(v=":v", q=1)), {"c": cart, "p": pid, "v": vid})
    for v, q in ((":v", 1), ("NULL", 1), (":v", 0)):          # duplicate, null variant, zero quantity
        try:
            with engine.begin() as c:
                c.execute(text(ins.format(v=v, q=q)), {"c": cart, "p": pid, "v": vid})
            raise AssertionError(f"constraint missing for variant={v} qty={q}")
        except sqlalchemy.exc.IntegrityError:
            pass
