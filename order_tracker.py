import hmac
from contextlib import contextmanager
from datetime import datetime, date, timedelta, timezone

import pandas as pd
import psycopg2
import streamlit as st

st.set_page_config(page_title="Advanced Business Order Tracker", page_icon="📊", layout="wide")

STATUSES = ["Pending", "Packed", "Out for Delivery", "Delivered", "Cancelled"]
PAYMENTS = ["Unpaid", "Paid"]
CUSTOM = "-- Iba pa (i-type) --"
PH_TZ = timezone(timedelta(hours=8))  # Philippine time (Streamlit Cloud runs on UTC)


def now():
    return datetime.now(PH_TZ)


# ---------------------------------------------------------------
# 1. DATABASE (Supabase / PostgreSQL) - data stays even if app reboots
# ---------------------------------------------------------------
@st.cache_resource
def _connect():
    return psycopg2.connect(st.secrets["DATABASE_URL"], connect_timeout=10)


def db():
    """Return a working connection (reconnects if it was closed)."""
    conn = _connect()
    try:
        if conn.closed:
            raise psycopg2.InterfaceError("closed")
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.rollback()
    except Exception:
        _connect.clear()
        conn = _connect()
    return conn


@contextmanager
def tx():
    """Run several statements as one safe unit; saves all or nothing."""
    conn = db()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def query_df(sql, params=None):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
    finally:
        conn.rollback()
    return pd.DataFrame(rows, columns=cols)


@st.cache_resource
def init_schema():
    with tx() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY,
                date TEXT,
                customer TEXT,
                item TEXT,
                price DOUBLE PRECISION,
                status TEXT,
                quantity INTEGER DEFAULT 1,
                contact TEXT DEFAULT '',
                payment_status TEXT DEFAULT 'Unpaid',
                updated_by TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE,
                price DOUBLE PRECISION DEFAULT 0,
                cost DOUBLE PRECISION DEFAULT 0,
                stock INTEGER DEFAULT 0,
                reorder_level INTEGER DEFAULT 5
            )
            """
        )
    return True


try:
    init_schema()
except Exception as e:
    st.error("Hindi makakonekta sa database. Tingnan ang DATABASE_URL sa Streamlit Secrets.")
    st.caption(f"Detalye ng error: {e}")
    st.stop()


# ---------------------------------------------------------------
# 2. LOGIN (passwords come from Streamlit Secrets, not from code)
# ---------------------------------------------------------------
def get_users():
    try:
        users = {u: dict(v) for u, v in st.secrets["users"].items()}
        if users:
            return users, True
    except Exception:
        pass
    return {"admin": {"password": "business123", "role": "admin"}}, False


def flash(kind, msg):
    st.session_state["flash"] = (kind, msg)


def show_flash():
    if "flash" in st.session_state:
        kind, msg = st.session_state.pop("flash")
        getattr(st, kind)(msg)


if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
    st.session_state.username = ""
    st.session_state.role = "staff"

if not st.session_state.authenticated:
    st.title("🔒 Mag-login sa Order Tracker")
    users, using_secrets = get_users()
    with st.form("login_form"):
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        ok = st.form_submit_button("Mag-login")
    if ok:
        rec = users.get(u.strip())
        if rec and hmac.compare_digest(str(rec.get("password", "")).encode(), p.encode()):
            st.session_state.authenticated = True
            st.session_state.username = u.strip()
            st.session_state.role = rec.get("role", "staff")
            st.rerun()
        else:
            st.error("Maling Username o Password!")
    if not using_secrets:
        st.warning(
            "Gumagamit pa ng default na login. Mag-set ng accounts sa Streamlit Secrets "
            "(Manage app → Settings → Secrets) para maging ligtas."
        )
    st.stop()

USER = st.session_state.username
IS_ADMIN = st.session_state.role == "admin"

st.sidebar.success(f"Naka-login: {USER} ({'Admin' if IS_ADMIN else 'Staff'})")
if st.sidebar.button("Mag-logout"):
    st.session_state.authenticated = False
    st.rerun()


# ---------------------------------------------------------------
# 3. DATA HELPERS
# ---------------------------------------------------------------
def load_orders():
    df = query_df("SELECT * FROM orders ORDER BY id DESC")
    df["price"] = pd.to_numeric(df["price"])
    df["quantity"] = pd.to_numeric(df["quantity"]).fillna(1).astype(int)
    df["payment_status"] = df["payment_status"].fillna("Unpaid")
    df["total"] = df["price"] * df["quantity"]
    return df


def load_products():
    return query_df("SELECT * FROM products ORDER BY name")


def find_product(name):
    df = query_df("SELECT id, stock, price FROM products WHERE lower(name) = lower(%s)", (name,))
    if df.empty:
        return None
    r = df.iloc[0]
    return int(r["id"]), int(r["stock"]), float(r["price"])


def sync_price():
    choice = st.session_state.get("new_item", CUSTOM)
    if choice != CUSTOM:
        prod = find_product(choice)
        if prod:
            st.session_state["new_price"] = prod[2]


def save_order():
    s = st.session_state
    customer = s.get("new_customer", "").strip()
    contact = s.get("new_contact", "").strip()
    choice = s.get("new_item", CUSTOM)
    item = (s.get("new_item_custom", "") if choice == CUSTOM else choice).strip()
    qty = int(s.get("new_qty", 1))
    price = float(s.get("new_price", 0.0))
    payment = s.get("new_payment", "Unpaid")

    if not customer or not item:
        flash("warning", "Paki-fill up ang Pangalan ng Customer at Inorder.")
        return
    if price <= 0:
        flash("warning", "Dapat mas mataas sa 0 ang presyo.")
        return

    stamp = now().strftime("%Y-%m-%d %H:%M")
    try:
        with tx() as cur:
            cur.execute("SELECT id, stock FROM products WHERE lower(name) = lower(%s)", (item,))
            prod = cur.fetchone()
            if prod:
                cur.execute(
                    "UPDATE products SET stock = stock - %s WHERE id = %s AND stock >= %s",
                    (qty, prod[0], qty),
                )
                if cur.rowcount == 0:
                    flash("error", f"Kulang ang stock ng '{item}'. Natitira: {prod[1]}.")
                    return
            cur.execute(
                """INSERT INTO orders
                   (date, customer, item, price, status, quantity, contact, payment_status, updated_by, updated_at)
                   VALUES (%s, %s, %s, %s, 'Pending', %s, %s, %s, %s, %s)""",
                (stamp, customer, item, price, qty, contact, payment, USER, stamp),
            )
    except Exception as e:
        flash("error", f"Hindi na-save ang order: {e}")
        return

    flash("success", f"Na-save na ang order para kay {customer}!")
    s["new_customer"] = ""
    s["new_contact"] = ""
    s["new_item"] = CUSTOM
    s["new_item_custom"] = ""
    s["new_qty"] = 1
    s["new_price"] = 0.0
    s["new_payment"] = "Unpaid"


def update_order(order_id, new_status, new_payment):
    try:
        with tx() as cur:
            cur.execute("SELECT item, quantity, status FROM orders WHERE id = %s FOR UPDATE", (order_id,))
            row = cur.fetchone()
            if not row:
                return False, "Hindi nahanap ang order."
            item, qty, old_status = row
            qty = qty or 1

            cur.execute("SELECT id, stock FROM products WHERE lower(name) = lower(%s) FOR UPDATE", (item,))
            prod = cur.fetchone()
            if prod:
                if new_status == "Cancelled" and old_status != "Cancelled":
                    cur.execute("UPDATE products SET stock = stock + %s WHERE id = %s", (qty, prod[0]))
                elif old_status == "Cancelled" and new_status != "Cancelled":
                    if prod[1] < qty:
                        return False, f"Kulang ang stock ng '{item}' para ibalik ang order."
                    cur.execute("UPDATE products SET stock = stock - %s WHERE id = %s", (qty, prod[0]))

            cur.execute(
                "UPDATE orders SET status = %s, payment_status = %s, updated_by = %s, updated_at = %s WHERE id = %s",
                (new_status, new_payment, USER, now().strftime("%Y-%m-%d %H:%M"), order_id),
            )
        return True, "Na-update na ang order!"
    except Exception as e:
        return False, f"May error: {e}"


# ---------------------------------------------------------------
# 4. PAGE LAYOUT
# ---------------------------------------------------------------
st.title("📊 Propesyonal na Business Order Tracker & Analytics")

tab_names = ["🧾 Orders", "📦 Inventory"]
if IS_ADMIN:
    tab_names += ["📈 Dashboard", "📥 Export"]
tabs = st.tabs(tab_names)

df_orders = load_orders()
df_products = load_products()

# ================= TAB 1: ORDERS =================
with tabs[0]:
    show_flash()
    st.header("1. Magdagdag ng Bagong Order")

    product_names = df_products["name"].tolist()
    c1, c2 = st.columns(2)
    c1.text_input("Pangalan ng Customer", key="new_customer")
    c2.text_input("Contact Number (optional)", key="new_contact")

    c3, c4 = st.columns(2)
    c3.selectbox("Anong Inorder", [CUSTOM] + product_names, key="new_item", on_change=sync_price)
    if st.session_state.get("new_item", CUSTOM) == CUSTOM:
        c4.text_input("Pangalan ng Item", key="new_item_custom")

    c5, c6, c7 = st.columns(3)
    c5.number_input("Dami (Quantity)", min_value=1, step=1, value=1, key="new_qty")
    c6.number_input("Presyo bawat piraso (₱)", min_value=0.0, format="%.2f", key="new_price")
    c7.selectbox("Payment", PAYMENTS, key="new_payment")

    qty_now = st.session_state.get("new_qty", 1)
    price_now = st.session_state.get("new_price", 0.0)
    st.caption(f"Kabuuan ng order na ito: ₱{qty_now * price_now:,.2f}")
    st.button("I-save ang Order", on_click=save_order, type="primary")

    st.divider()
    st.header("2. Listahan ng mga Orders")

    if df_orders.empty:
        st.info("Wala pang orders. Magdagdag sa itaas para magsimula!")
    else:
        f1, f2, f3 = st.columns(3)
        filter_status = f1.selectbox("Status", ["Lahat"] + STATUSES)
        filter_pay = f2.selectbox("Payment", ["Lahat"] + PAYMENTS)
        search = f3.text_input("Hanapin ang customer")

        dates = pd.to_datetime(df_orders["date"], errors="coerce")
        d1, d2 = st.columns(2)
        start = d1.date_input("Mula", value=(dates.min().date() if dates.notna().any() else now().date()))
        end = d2.date_input("Hanggang", value=now().date())

        view = df_orders.copy()
        view_dates = pd.to_datetime(view["date"], errors="coerce").dt.date
        view = view[(view_dates >= start) & (view_dates <= end)]
        if filter_status != "Lahat":
            view = view[view["status"] == filter_status]
        if filter_pay != "Lahat":
            view = view[view["payment_status"] == filter_pay]
        if search:
            view = view[view["customer"].str.contains(search, case=False, na=False)]

        show_cols = ["id", "date", "customer", "contact", "item", "quantity", "price", "total",
                     "status", "payment_status", "updated_by", "updated_at"]
        st.dataframe(view[show_cols], use_container_width=True, hide_index=True)

        st.divider()
        st.header("3. Baguhin ang Order")
        labels = {
            int(r.id): f"ID {r.id}: {r.customer} - {r.item} x{r.quantity} ({r.status})"
            for r in df_orders.itertuples()
        }
        selected_id = st.selectbox(
            "Piliin ang Order", list(labels.keys()), format_func=lambda x: labels[x]
        )
        cur_row = df_orders[df_orders["id"] == selected_id].iloc[0]
        u1, u2 = st.columns(2)
        new_status = u1.selectbox(
            "Bagong Status", STATUSES,
            index=STATUSES.index(cur_row["status"]) if cur_row["status"] in STATUSES else 0,
            key=f"status_{selected_id}",
        )
        new_payment = u2.selectbox(
            "Payment Status", PAYMENTS,
            index=PAYMENTS.index(cur_row["payment_status"]) if cur_row["payment_status"] in PAYMENTS else 0,
            key=f"pay_{selected_id}",
        )
        if st.button("I-update ang Order"):
            ok, msg = update_order(int(selected_id), new_status, new_payment)
            flash("success" if ok else "error", msg)
            st.rerun()

# ================= TAB 2: INVENTORY =================
with tabs[1]:
    st.header("Inventory / Mga Produkto")
    if df_products.empty:
        st.info("Wala pang produkto. " + ("Magdagdag sa ibaba." if IS_ADMIN else "Magpa-add sa Admin."))
    else:
        low = df_products[df_products["stock"] <= df_products["reorder_level"]]
        if not low.empty:
            st.warning("⚠️ Mababa na ang stock: " + ", ".join(
                f"{r.name} ({r.stock})" for r in low.itertuples()))
        show = df_products.rename(columns={
            "name": "Produkto", "price": "Presyo", "cost": "Puhunan",
            "stock": "Stock", "reorder_level": "Reorder Level"})
        if not IS_ADMIN:
            show = show.drop(columns=["Puhunan"])
        st.dataframe(show.drop(columns=["id"]), use_container_width=True, hide_index=True)

    if IS_ADMIN:
        st.subheader("Magdagdag / Mag-update ng Produkto")
        with st.form("product_form", clear_on_submit=True):
            p1, p2 = st.columns(2)
            p_name = p1.text_input("Pangalan ng Produkto")
            p_stock = p2.number_input("Stock (kasalukuyang bilang)", min_value=0, step=1)
            p3, p4, p5 = st.columns(3)
            p_price = p3.number_input("Presyo ng benta (₱)", min_value=0.0, format="%.2f")
            p_cost = p4.number_input("Puhunan bawat piraso (₱)", min_value=0.0, format="%.2f")
            p_reorder = p5.number_input("Reorder level", min_value=0, step=1, value=5)
            if st.form_submit_button("I-save ang Produkto"):
                if p_name.strip():
                    with tx() as cur:
                        cur.execute(
                            """INSERT INTO products (name, price, cost, stock, reorder_level)
                               VALUES (%s, %s, %s, %s, %s)
                               ON CONFLICT (name) DO UPDATE SET
                                 price = EXCLUDED.price, cost = EXCLUDED.cost,
                                 stock = EXCLUDED.stock, reorder_level = EXCLUDED.reorder_level""",
                            (p_name.strip(), p_price, p_cost, int(p_stock), int(p_reorder)),
                        )
                    st.success("Na-save ang produkto!")
                    st.rerun()
                else:
                    st.warning("Ilagay ang pangalan ng produkto.")

        if not df_products.empty:
            st.subheader("Mag-restock (dagdag stock)")
            r1, r2 = st.columns(2)
            restock_name = r1.selectbox("Produkto", df_products["name"].tolist(), key="restock_name")
            restock_qty = r2.number_input("Dagdag na bilang", min_value=1, step=1, key="restock_qty")
            if st.button("Idagdag sa Stock"):
                with tx() as cur:
                    cur.execute("UPDATE products SET stock = stock + %s WHERE name = %s",
                                (int(restock_qty), restock_name))
                st.success("Na-update ang stock!")
                st.rerun()

# ================= TAB 3: DASHBOARD (admin only) =================
if IS_ADMIN:
    with tabs[2]:
        st.header("Sales Dashboard")
        valid = df_orders[df_orders["status"] != "Cancelled"].copy()
        if valid.empty:
            st.info("Wala pang datos para sa dashboard.")
        else:
            valid["day"] = pd.to_datetime(valid["date"], errors="coerce").dt.date
            period = st.selectbox("Panahon", ["Lahat", "Ngayon", "Huling 7 araw", "Huling 30 araw"])
            today = now().date()
            if period == "Ngayon":
                valid = valid[valid["day"] == today]
            elif period == "Huling 7 araw":
                valid = valid[valid["day"] >= today - timedelta(days=6)]
            elif period == "Huling 30 araw":
                valid = valid[valid["day"] >= today - timedelta(days=29)]

            costs = df_products.set_index(df_products["name"].str.lower())["cost"].to_dict()
            valid["unit_cost"] = valid["item"].str.lower().map(costs)
            known = valid.dropna(subset=["unit_cost"])
            profit = ((known["price"] - known["unit_cost"].astype(float)) * known["quantity"]).sum()

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Bilang ng Orders", len(valid))
            m2.metric("Kabuuang Benta", f"₱{valid['total'].sum():,.2f}")
            m3.metric("Hindi pa bayad", f"₱{valid[valid['payment_status'] == 'Unpaid']['total'].sum():,.2f}")
            m4.metric("Tinatayang Tubo", f"₱{profit:,.2f}")
            st.caption("Ang tubo ay kinukuwenta lang para sa mga item na nasa Inventory na may puhunan. "
                       "Hindi kasama ang Cancelled orders.")

            if valid.empty:
                st.info("Walang orders sa piniling panahon.")
            else:
                st.subheader("Benta bawat Produkto")
                st.bar_chart(valid.groupby(valid["item"].str.lower())["total"].sum())
                st.subheader("Benta bawat Araw")
                st.line_chart(valid.groupby("day")["total"].sum())

        st.subheader("Bilang ng Orders ayon sa Status")
        if not df_orders.empty:
            st.bar_chart(df_orders["status"].value_counts())

    # ================= TAB 4: EXPORT (admin only) =================
    with tabs[3]:
        st.header("I-download ang mga Rekord")
        st.download_button(
            "📥 Orders (CSV, Excel compatible)",
            data=df_orders.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"orders_{now().date()}.csv",
            mime="text/csv",
        )
        st.download_button(
            "📥 Inventory (CSV, Excel compatible)",
            data=df_products.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"inventory_{now().date()}.csv",
            mime="text/csv",
        )
        st.info("Naka-save na ang data mo sa Supabase, kaya hindi na ito mawawala kapag nag-reboot ang app. "
                "Ang CSV ay extra backup na lang.")