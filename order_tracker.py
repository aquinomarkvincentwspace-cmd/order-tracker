import hmac
import sqlite3
from datetime import datetime, date, timedelta

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Advanced Business Order Tracker", page_icon="📊", layout="wide")

DB_PATH = "orders.db"
STATUSES = ["Pending", "Packed", "Out for Delivery", "Delivered", "Cancelled"]
PAYMENTS = ["Unpaid", "Paid"]
CUSTOM = "-- Iba pa (i-type) --"


# ---------------------------------------------------------------
# 1. DATABASE
# ---------------------------------------------------------------
def add_column_if_missing(conn, table, col, ddl):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


@st.cache_resource
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            customer TEXT,
            item TEXT,
            price REAL,
            status TEXT
        )
        """
    )
    # New columns (safe for your existing data: old orders get quantity = 1)
    add_column_if_missing(conn, "orders", "quantity", "INTEGER DEFAULT 1")
    add_column_if_missing(conn, "orders", "contact", "TEXT DEFAULT ''")
    add_column_if_missing(conn, "orders", "payment_status", "TEXT DEFAULT 'Unpaid'")
    add_column_if_missing(conn, "orders", "updated_by", "TEXT DEFAULT ''")
    add_column_if_missing(conn, "orders", "updated_at", "TEXT DEFAULT ''")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            price REAL DEFAULT 0,
            cost REAL DEFAULT 0,
            stock INTEGER DEFAULT 0,
            reorder_level INTEGER DEFAULT 5
        )
        """
    )
    conn.commit()
    return conn


conn = get_conn()


# ---------------------------------------------------------------
# 2. LOGIN (passwords come from Streamlit Secrets, not from code)
# ---------------------------------------------------------------
def get_users():
    """
    Reads accounts from Streamlit Secrets. Example secrets:

    [users.admin]
    password = "your-strong-password"
    role = "admin"

    [users.staff1]
    password = "another-password"
    role = "staff"
    """
    try:
        users = {u: dict(v) for u, v in st.secrets["users"].items()}
        if users:
            return users, True
    except Exception:
        pass
    # Fallback so the app still opens before you set up secrets
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
    df = pd.read_sql("SELECT * FROM orders ORDER BY id DESC", conn)
    df["quantity"] = df["quantity"].fillna(1).astype(int)
    df["payment_status"] = df["payment_status"].fillna("Unpaid")
    df["total"] = df["price"] * df["quantity"]
    return df


def load_products():
    return pd.read_sql("SELECT * FROM products ORDER BY name", conn)


def find_product(name):
    return conn.execute(
        "SELECT id, stock, price FROM products WHERE lower(name) = lower(?)", (name,)
    ).fetchone()


def sync_price():
    """Auto-fill the price when a product is chosen."""
    choice = st.session_state.get("new_item", CUSTOM)
    if choice != CUSTOM:
        prod = find_product(choice)
        if prod:
            st.session_state["new_price"] = float(prod[2])


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

    prod = find_product(item)
    if prod and prod[1] < qty:
        flash("error", f"Kulang ang stock ng '{item}'. Natitira: {prod[1]}.")
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn.execute(
        """INSERT INTO orders
           (date, customer, item, price, status, quantity, contact, payment_status, updated_by, updated_at)
           VALUES (?, ?, ?, ?, 'Pending', ?, ?, ?, ?, ?)""",
        (now, customer, item, price, qty, contact, payment, USER, now),
    )
    if prod:
        conn.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (qty, prod[0]))
    conn.commit()
    flash("success", f"Na-save na ang order para kay {customer}!")

    s["new_customer"] = ""
    s["new_contact"] = ""
    s["new_item"] = CUSTOM
    s["new_item_custom"] = ""
    s["new_qty"] = 1
    s["new_price"] = 0.0
    s["new_payment"] = "Unpaid"


def update_order(order_id, new_status, new_payment):
    row = conn.execute(
        "SELECT item, quantity, status FROM orders WHERE id = ?", (order_id,)
    ).fetchone()
    if not row:
        return False, "Hindi nahanap ang order."
    item, qty, old_status = row
    qty = qty or 1
    prod = find_product(item)

    # Return stock when cancelled; take it again if un-cancelled
    if prod:
        if new_status == "Cancelled" and old_status != "Cancelled":
            conn.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (qty, prod[0]))
        elif old_status == "Cancelled" and new_status != "Cancelled":
            if prod[1] < qty:
                return False, f"Kulang ang stock ng '{item}' para ibalik ang order."
            conn.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (qty, prod[0]))

    conn.execute(
        "UPDATE orders SET status = ?, payment_status = ?, updated_by = ?, updated_at = ? WHERE id = ?",
        (new_status, new_payment, USER, datetime.now().strftime("%Y-%m-%d %H:%M"), order_id),
    )
    conn.commit()
    return True, "Na-update na ang order!"


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
        start = d1.date_input("Mula", value=(dates.min().date() if dates.notna().any() else date.today()))
        end = d2.date_input("Hanggang", value=date.today())

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
        cur = df_orders[df_orders["id"] == selected_id].iloc[0]
        u1, u2 = st.columns(2)
        new_status = u1.selectbox(
            "Bagong Status", STATUSES,
            index=STATUSES.index(cur["status"]) if cur["status"] in STATUSES else 0,
            key=f"status_{selected_id}",
        )
        new_payment = u2.selectbox(
            "Payment Status", PAYMENTS,
            index=PAYMENTS.index(cur["payment_status"]) if cur["payment_status"] in PAYMENTS else 0,
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
                    conn.execute(
                        """INSERT INTO products (name, price, cost, stock, reorder_level)
                           VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT(name) DO UPDATE SET
                             price = excluded.price, cost = excluded.cost,
                             stock = excluded.stock, reorder_level = excluded.reorder_level""",
                        (p_name.strip(), p_price, p_cost, int(p_stock), int(p_reorder)),
                    )
                    conn.commit()
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
                conn.execute("UPDATE products SET stock = stock + ? WHERE name = ?",
                             (int(restock_qty), restock_name))
                conn.commit()
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
            today = date.today()
            if period == "Ngayon":
                valid = valid[valid["day"] == today]
            elif period == "Huling 7 araw":
                valid = valid[valid["day"] >= today - timedelta(days=6)]
            elif period == "Huling 30 araw":
                valid = valid[valid["day"] >= today - timedelta(days=29)]

            # Profit: only for items that exist in the product list with a cost
            costs = df_products.set_index(df_products["name"].str.lower())["cost"].to_dict()
            valid["unit_cost"] = valid["item"].str.lower().map(costs)
            known = valid.dropna(subset=["unit_cost"])
            profit = ((known["price"] - known["unit_cost"]) * known["quantity"]).sum()

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
        st.bar_chart(df_orders["status"].value_counts())

    # ================= TAB 4: EXPORT (admin only) =================
    with tabs[3]:
        st.header("I-download ang mga Rekord")
        st.download_button(
            "📥 Orders (CSV, Excel compatible)",
            data=df_orders.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"orders_{date.today()}.csv",
            mime="text/csv",
        )
        st.download_button(
            "📥 Inventory (CSV, Excel compatible)",
            data=df_products.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"inventory_{date.today()}.csv",
            mime="text/csv",
        )
        st.info("Mag-download nang regular bilang backup, lalo na habang SQLite pa ang gamit mo.")