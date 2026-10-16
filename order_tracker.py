"""
Order Tracker - Multi-Business & Role-Based System
Professional Edition with Complete Global Language Translation & Owner Management
"""
import hashlib
import hmac
import re
import secrets as pysecrets
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pandas as pd
import psycopg2
import streamlit as st

st.set_page_config(page_title="Business Order Management System", page_icon="📊", layout="wide")

STATUSES = ["Pending", "Packed", "Out for Delivery", "Delivered", "Cancelled"]
ACTIVE_STATUSES = ["Pending", "Packed", "Out for Delivery", "Delivered"]
PAYMENTS = ["Unpaid", "Paid"]
CUSTOM = "-- Iba pa / Custom --"
REASONS = [
    "Nagbago ang isip ng customer / Customer changed mind",
    "Maling order / Wrong item",
    "Walang stock / Out of stock",
    "Sira o may depekto / Damaged or defective",
    "Late ang delivery / Delayed delivery",
    "Hindi nakabayad / Failed payment",
    "Iba pa / Others",
]
PH_TZ = timezone(timedelta(hours=8))
MAX_ADMINS = 2
MIN_PASSWORD = 8

# --- COMPLETE MULTILINGUAL DICTIONARY ---
TEXTS = {
    "en": {
        "sys_title": "Business Order Management System",
        "portal_title": "Management Portal",
        "login_tab": "Sign In",
        "signup_tab": "Register Staff",
        "username": "Username",
        "password": "Password",
        "login_btn": "Sign In",
        "signup_btn": "Register Staff",
        "full_name": "Full Name",
        "select_biz": "Select Business",
        "orders": "Orders",
        "inventory": "Inventory",
        "cancellations": "Cancellations",
        "dashboard": "Dashboard",
        "export": "Data Export",
        "users": "User Management",
        "account": "Account Settings",
        "add_order": "Create New Order",
        "view_orders": "Order Records",
        "manage_status": "Update Status & Payment",
        "cancel_order": "Process Cancellation",
        "pending_refunds": "Pending Refunds",
        "view_inventory": "Product Catalog",
        "add_product": "Add / Update Product",
        "restock": "Stock Replenishment",
        "customer_name": "Customer Name",
        "contact_num": "Contact Number",
        "item_type": "Product or Item",
        "custom_item": "Custom Item Name",
        "qty": "Quantity",
        "unit_price": "Unit Price (₱)",
        "pay_status": "Payment Status",
        "save_order_btn": "Save Order",
        "filter_status": "Filter by Status",
        "filter_pay": "Filter by Payment",
        "search_cust": "Search Customer",
        "new_status": "New Status",
        "cancel_units": "Units to Cancel",
        "cancel_reason": "Cancellation Reason",
        "cancel_note": "Additional Note (Optional)",
        "restock_check": "Restock cancelled units",
        "refund_check": "Refund has been returned to customer",
        "cancel_btn": "Cancel Selected Units",
        "prod_name": "Product Name",
        "current_stock": "Current Stock",
        "selling_price": "Selling Price (₱)",
        "cost_price": "Cost Price (₱)",
        "reorder_lvl": "Reorder Level",
        "save_prod_btn": "Save Product",
        "add_stock_btn": "Add to Stock",
        "logout_btn": "Log Out",
        "change_pass": "Change Password",
        "cur_pass": "Current Password",
        "new_pass": "New Password",
        "confirm_pass": "Confirm Password",
        "save_pass_btn": "Save New Password",
    },
    "tl": {
        "sys_title": "Sistema ng Pamamahala ng Negosyo",
        "portal_title": "Portal ng Pamamahala",
        "login_tab": "Mag-log in",
        "signup_tab": "Magrehistro ng Staff",
        "username": "Username",
        "password": "Password",
        "login_btn": "Mag-sign In",
        "signup_btn": "Magrehistro Bilang Staff",
        "full_name": "Buong Pangalan",
        "select_biz": "Piliin ang Negosyo",
        "orders": "Mga Order",
        "inventory": "Imbentaryo",
        "cancellations": "Mga Na-cancel",
        "dashboard": "Dashboard",
        "export": "Pag-export ng Datos",
        "users": "Pamamahala ng User",
        "account": "Mga Setting ng Account",
        "add_order": "Gumawa ng Bagong Order",
        "view_orders": "Talaan ng mga Order",
        "manage_status": "Baguhin ang Status at Bayad",
        "cancel_order": "Mag-cancel ng Order",
        "pending_refunds": "Mga Nakabinbing Refund",
        "view_inventory": "Katalogo ng Produkto",
        "add_product": "Magdagdag o Mag-update ng Produkto",
        "restock": "Pagdaragdag ng Stock",
        "customer_name": "Pangalan ng Customer",
        "contact_num": "Numero ng Telepono / Contact",
        "item_type": "Uri ng Produkto o Serbisyo",
        "custom_item": "Pangalan ng Pasadyang Item",
        "qty": "Dami (Quantity)",
        "unit_price": "Presyo bawat Yunit (₱)",
        "pay_status": "Katayuan ng Bayad",
        "save_order_btn": "Itala ang Order",
        "filter_status": "Salain ayon sa Status",
        "filter_pay": "Salain ayon sa Bayad",
        "search_cust": "Hanapin ang Customer",
        "new_status": "Bagong Status",
        "cancel_units": "Ilang yunit ang kakanselahin?",
        "cancel_reason": "Dahilan ng Pag-cancel",
        "cancel_note": "Karagdagang Tala (Opsyonal)",
        "restock_check": "Ibalik sa imbentaryo ang mga yunit na ito",
        "refund_check": "Naibalik na ba ang pera sa customer?",
        "cancel_btn": "Kanselahin ang Piniling Yunit",
        "prod_name": "Pangalan ng Produkto",
        "current_stock": "Kasalukuyang Stock",
        "selling_price": "Presyo ng Pagbebenta (₱)",
        "cost_price": "Puhunan bawat Yunit (₱)",
        "reorder_lvl": "Limitasyon (Reorder Level)",
        "save_prod_btn": "I-save ang Produkto",
        "add_stock_btn": "Magdagdag sa Stock",
        "logout_btn": "Mag-log out",
        "change_pass": "Palitan ang Password",
        "cur_pass": "Kasalukuyang Password",
        "new_pass": "Bagong Password",
        "confirm_pass": "Kumpirmahin ang Bagong Password",
        "save_pass_btn": "I-save ang Pagbabago",
    }
}

ROLE_LABELS = {
    "admin": "Admin (Buong Kontrol sa Negosyo)",
    "staff": "Staff (Tagapagtala ng Benta)",
    "viewer": "Viewer (Tingin Lamang)",
}
PERMS = {
    "admin": {"add_order", "edit_order", "cancel", "refund", "inventory", "costs",
              "dashboard", "export", "users"},
    "staff": {"add_order", "edit_order", "cancel", "refund"},
    "viewer": set(),
}


def now():
    return datetime.now(PH_TZ)


def stamp():
    return now().strftime("%Y-%m-%d %H:%M")


def me():
    return st.session_state.get("username", "")


def bid():
    return st.session_state.get("business_id")


def can(perm):
    return perm in PERMS.get(st.session_state.get("role"), set())


def t(key):
    lang = st.session_state.get("lang", "Tagalog")
    lang_code = "tl" if lang == "Tagalog" else "en"
    return TEXTS.get(lang_code, TEXTS["tl"]).get(key, key)


# ---------------------------------------------------------------
# 1. DATABASE CONNECTION & SCHEMA
# ---------------------------------------------------------------
@st.cache_resource
def _connect():
    return psycopg2.connect(st.secrets["DATABASE_URL"], connect_timeout=10)


def db():
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
                name TEXT,
                price DOUBLE PRECISION DEFAULT 0,
                cost DOUBLE PRECISION DEFAULT 0,
                stock INTEGER DEFAULT 0,
                reorder_level INTEGER DEFAULT 5
            )
            """
        )
        cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS cancelled_qty INTEGER DEFAULT 0")
        cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS refund_due DOUBLE PRECISION DEFAULT 0")
        cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS refunded DOUBLE PRECISION DEFAULT 0")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS cancellations (
                id SERIAL PRIMARY KEY,
                cancelled_at TEXT,
                order_id INTEGER,
                customer TEXT,
                item TEXT,
                units INTEGER,
                unit_price DOUBLE PRECISION,
                amount DOUBLE PRECISION,
                was_paid BOOLEAN DEFAULT FALSE,
                refunded_now BOOLEAN DEFAULT FALSE,
                restocked BOOLEAN DEFAULT TRUE,
                reason TEXT DEFAULT '',
                note TEXT DEFAULT '',
                cancelled_by TEXT DEFAULT ''
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS businesses (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE,
                created_at TEXT,
                active BOOLEAN DEFAULT TRUE
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE,
                password_hash TEXT,
                full_name TEXT DEFAULT '',
                role TEXT,
                business_id INTEGER,
                active BOOLEAN DEFAULT TRUE,
                created_at TEXT
            )
            """
        )
        for table in ("orders", "products", "cancellations"):
            cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS business_id INTEGER")
        cur.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS products_name_key")
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS products_biz_name_idx ON products (business_id, name)"
        )
    return True


try:
    init_schema()
except Exception as e:
    st.error(f"Koneksyon sa database ay nabigo: {e}")
    st.stop()


# ---------------------------------------------------------------
# 2. AUTHENTICATION & SECURITY
# ---------------------------------------------------------------
def hash_password(pw):
    salt = pysecrets.token_hex(16)
    iters = 200_000
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), iters).hex()
    return f"pbkdf2${iters}${salt}${h}"


def check_password(pw, stored):
    try:
        _, iters, salt, h = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(iters)).hex()
        return hmac.compare_digest(calc, h)
    except Exception:
        return False


def check_new_password(pw, pw2=None):
    if len(pw) < MIN_PASSWORD:
        return f"Ang password ay dapat hindi bababa sa {MIN_PASSWORD} na karakter."
    if pw2 is not None and pw != pw2:
        return "Hindi magkatugma ang mga password."
    return None


def check_username(u):
    if not re.fullmatch(r"[a-z0-9._-]{3,30}", u):
        return "Ang username ay dapat 3-30 na titik (maliit), numero, o simbolo (walang espasyo)."
    return None


def users_exist():
    return int(query_df("SELECT COUNT(*) AS n FROM users").iloc[0]["n"]) > 0


def insert_user(cur, username, password, role, business_id, full_name):
    cur.execute(
        """INSERT INTO users (username, password_hash, full_name, role, business_id, active, created_at)
            VALUES (%s, %s, %s, %s, %s, TRUE, %s)""",
        (username, hash_password(password), full_name.strip(), role, business_id, stamp()),
    )


def logout():
    for k in ("authenticated", "user_id", "username", "role", "business_id", "business_name", "full_name"):
        st.session_state.pop(k, None)
    st.session_state.authenticated = False


# --- SETUP SCREEN ---
def setup_screen():
    st.title("🛠️ Unang Pagsasaayos ng Sistema (Setup)")
    st.write("Gumawa ng Master Owner account at ng unang pangunahing negosyo.")
    try:
        expected = st.secrets["SETUP_CODE"]
    except Exception:
        expected = None
    if not expected:
        st.error("Kailangan ang SETUP_CODE sa Streamlit Secrets.")
        st.stop()

    with st.form("setup_form"):
        code = st.text_input("Setup Code", type="password")
        st.subheader("Master Owner Account")
        o_user = st.text_input("Owner Username")
        o_pw = st.text_input("Owner Password", type="password")
        o_pw2 = st.text_input("Kumpirmahin ang Password", type="password")
        st.subheader("Unang Negosyo")
        b_name = st.text_input("Pangalan ng Negosyo", value="Pangunahing Tindahan")
        a_user = st.text_input("Admin Username para sa Negosyong ito")
        a_full = st.text_input("Buong Pangalan ng Admin (Opsyonal)")
        a_pw = st.text_input("Admin Password", type="password")
        a_pw2 = st.text_input("Kumpirmahin ang Admin Password", type="password")
        go = st.form_submit_button("Itatag ang Sistema")

    if go:
        o_user, a_user, b_name = o_user.strip().lower(), a_user.strip().lower(), b_name.strip()
        problem = None
        if not hmac.compare_digest(code.encode(), str(expected).encode()):
            problem = "Mali ang setup code."
        elif not b_name:
            problem = "Ilagay ang pangalan ng negosyo."
        elif o_user == a_user:
            problem = "Dapat magkaiba ang username ng Owner at Admin."
        else:
            problem = (check_username(o_user) or check_username(a_user)
                       or check_new_password(o_pw, o_pw2) or check_new_password(a_pw, a_pw2))
        if problem:
            st.error(problem)
        else:
            try:
                with tx() as cur:
                    cur.execute(
                        "INSERT INTO businesses (name, created_at, active) VALUES (%s, %s, TRUE) RETURNING id",
                        (b_name, stamp()),
                    )
                    new_bid = cur.fetchone()[0]
                    insert_user(cur, o_user, o_pw, "owner", None, "Master Owner")
                    insert_user(cur, a_user, a_pw, "admin", new_bid, a_full)
                    for table in ("orders", "products", "cancellations"):
                        cur.execute(f"UPDATE {table} SET business_id = %s WHERE business_id IS NULL", (new_bid,))
                st.success("Matagumpay na naitatag ang sistema! Mangyaring mag-log in.")
                st.rerun()
            except Exception as e:
                st.error(f"May error: {e}")


if not users_exist():
    setup_screen()
    st.stop()


# --- LOGIN PORTAL ---
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False


def try_login(username, password):
    df = query_df(
        """SELECT u.id, u.username, u.password_hash, u.full_name, u.role, u.business_id, u.active,
                  b.name AS business_name, b.active AS business_active
           FROM users u LEFT JOIN businesses b ON b.id = u.business_id
           WHERE u.username = %s""",
        (username,),
    )
    if df.empty or not check_password(password, df.iloc[0]["password_hash"]):
        return None, "Mali ang username o password."
    r = df.iloc[0]
    if not bool(r["active"]):
        return None, "Ang account na ito ay naka-deactivate."
    if r["role"] != "owner" and not bool(r["business_active"]):
        return None, "Ang negosyong ito ay kasalukuyang naka-deactivate."
    return r, None


if not st.session_state.authenticated:
    st.title(f"🔐 {t('portal_title')}")
    
    if "lang" not in st.session_state:
        st.session_state["lang"] = "Tagalog"
    
    lang_choice = st.selectbox("Language / Wika", ["Tagalog", "English"], key="login_lang_selector")
    st.session_state["lang"] = lang_choice

    tab_login, tab_signup = st.tabs([t("login_tab"), t("signup_tab")])

    with tab_login:
        with st.form("login_form"):
            u = st.text_input(t("username"))
            p = st.text_input(t("password"), type="password")
            ok = st.form_submit_button(t("login_btn"))
        if ok:
            rec, err = try_login(u.strip().lower(), p)
            if err:
                st.warning(err)
            else:
                st.session_state.authenticated = True
                st.session_state.user_id = int(rec["id"])
                st.session_state.username = rec["username"]
                st.session_state.full_name = rec["full_name"] or rec["username"]
                st.session_state.role = rec["role"]
                st.session_state.business_id = None if rec["role"] == "owner" else int(rec["business_id"])
                st.session_state.business_name = rec["business_name"] if rec["role"] != "owner" else ""
                st.rerun()

    with tab_signup:
        active_biz = query_df("SELECT id, name FROM businesses WHERE active = TRUE ORDER BY name")
        with st.form("signup_form"):
            new_u = st.text_input(t("username") + " (3-30 karakter)")
            new_fn = st.text_input(t("full_name"))
            new_p1 = st.text_input(t("password"), type="password")
            new_p2 = st.text_input(t("confirm_pass"), type="password")
            if active_biz.empty:
                st.info("Walang aktibong negosyo sa ngayon.")
                biz_choice = None
            else:
                biz_dict = {int(r.id): r.name for r in active_biz.itertuples()}
                biz_choice = st.selectbox(t("select_biz"), list(biz_dict.keys()), format_func=lambda x: biz_dict[x])
            reg_ok = st.form_submit_button(t("signup_btn"))

        if reg_ok:
            new_u = new_u.strip().lower()
            problem = check_username(new_u) or check_new_password(new_p1, new_p2)
            if not biz_choice:
                problem = "Pumili ng wastong negosyo."
            if problem:
                st.warning(problem)
            else:
                try:
                    with tx() as cur:
                        insert_user(cur, new_u, new_p1, "staff", int(biz_choice), new_fn)
                    st.success("Matagumpay na nakapagrehistro! Maaari nang mag-log in.")
                except psycopg2.IntegrityError:
                    st.warning("Ang username na ito ay ginagamit na.")
                except Exception as e:
                    st.error(f"Error: {e}")
    st.stop()

# Recheck status
_chk = query_df(
    """SELECT u.role, u.active, u.business_id, b.name AS business_name, b.active AS business_active
       FROM users u LEFT JOIN businesses b ON b.id = u.business_id WHERE u.id = %s""",
    (st.session_state.get("user_id", -1),),
)
if _chk.empty or not bool(_chk.iloc[0]["active"]):
    logout()
    st.rerun()

st.session_state.role = _chk.iloc[0]["role"]
if st.session_state.role != "owner":
    st.session_state.business_id = int(_chk.iloc[0]["business_id"])
    st.session_state.business_name = _chk.iloc[0]["business_name"]

ROLE = st.session_state.role

# Sidebar Global Language Switcher
st.sidebar.selectbox("Language / Wika", ["Tagalog", "English"], key="lang", index=0 if st.session_state.get("lang","Tagalog")=="Tagalog" else 1)
if ROLE == "owner":
    st.sidebar.success("Master Owner Account")
else:
    st.sidebar.success(f"Negosyo: {st.session_state.business_name}")
    st.sidebar.write(f"👤 {me()} ({ROLE.capitalize()})")

if st.sidebar.button(t("logout_btn")):
    logout()
    st.rerun()


# ---------------------------------------------------------------
# 3. CORE FUNCTIONS
# ---------------------------------------------------------------
def load_orders():
    df = query_df("SELECT * FROM orders WHERE business_id = %s ORDER BY id DESC", (bid(),))
    df["price"] = pd.to_numeric(df["price"])
    df["quantity"] = pd.to_numeric(df["quantity"]).fillna(1).astype(int)
    df["cancelled_qty"] = pd.to_numeric(df["cancelled_qty"]).fillna(0).astype(int)
    df["refund_due"] = pd.to_numeric(df["refund_due"]).fillna(0.0)
    df["refunded"] = pd.to_numeric(df["refunded"]).fillna(0.0)
    df["payment_status"] = df["payment_status"].fillna("Unpaid")
    df["active_qty"] = df["quantity"] - df["cancelled_qty"]
    df["total"] = df["price"] * df["active_qty"]
    df["refund_pending"] = (df["refund_due"] - df["refunded"]).clip(lower=0)
    return df


def load_products():
    return query_df("SELECT * FROM products WHERE business_id = %s ORDER BY name", (bid(),))


def find_product(name):
    df = query_df(
        "SELECT id, stock, price FROM products WHERE business_id = %s AND lower(name) = lower(%s)",
        (bid(), name),
    )
    if df.empty:
        return None
    r = df.iloc[0]
    return int(r["id"]), int(r["stock"]), float(r["price"])


# ---------------------------------------------------------------
# 4. OWNER PANEL
# ---------------------------------------------------------------
def owner_panel():
    st.title("🏢 Dashboard ng Master Owner")
    t_biz, t_users, t_acc = st.tabs(["Pamamahala ng Negosyo", "Pag-rehistro ng Admin/Users", t("account")])

    with t_biz:
        biz = query_df(
            """SELECT b.id, b.name, b.active, b.created_at,
                     (SELECT COUNT(*) FROM users u WHERE u.business_id = b.id AND u.active) AS active_users,
                     (SELECT COUNT(*) FROM orders o WHERE o.business_id = b.id) AS orders
               FROM businesses b ORDER BY b.id"""
        )
        st.subheader("Listahan ng mga Negosyo")
        st.dataframe(biz, use_container_width=True, hide_index=True)

        st.subheader("Magdagdag ng Bagong Negosyo at Admin")
        with st.form("new_biz_form", clear_on_submit=True):
            n_name = st.text_input("Pangalan ng Negosyo")
            n_user = st.text_input("Admin Username")
            n_full = st.text_input("Buong Pangalan ng Admin (Opsyonal)")
            n_pw = st.text_input("Admin Password", type="password")
            go_biz = st.form_submit_button("Lumikha ng Negosyo")
        if go_biz:
            n_name, n_user = n_name.strip(), n_user.strip().lower()
            if not n_name:
                st.warning("Ilagay ang pangalan ng negosyo.")
            else:
                try:
                    with tx() as cur:
                        cur.execute(
                            "INSERT INTO businesses (name, created_at, active) VALUES (%s, %s, TRUE) RETURNING id",
                            (n_name, stamp()),
                        )
                        new_id = cur.fetchone()[0]
                        insert_user(cur, n_user, n_pw, "admin", new_id, n_full)
                    st.success(f"Matagumpay na nalikha ang negosyong '{n_name}'.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

    with t_users:
        st.subheader("Direktang Pagtatalaga ng User / Admin sa mga Negosyo")
        all_biz = query_df("SELECT id, name FROM businesses WHERE active = TRUE")
        if all_biz.empty:
            st.info("Wala pang negosyong nakarehistro.")
        else:
            biz_dict = {int(r.id): r.name for r in all_biz.itertuples()}
            with st.form("owner_add_user_form", clear_on_submit=True):
                sel_b = st.selectbox("Pumili ng Negosyo", list(biz_dict.keys()), format_func=lambda x: biz_dict[x])
                u_name = st.text_input("Username ng User")
                u_full = st.text_input("Buong Pangalan")
                u_role = st.selectbox("Tungkulin (Role)", ["admin", "staff", "viewer"], format_func=lambda x: ROLE_LABELS.get(x, x))
                u_pw = st.text_input("Password", type="password")
                go_u = st.form_submit_button("Lumikha ng User Account")
            
            if go_u:
                u_name = u_name.strip().lower()
                prob = check_username(u_name) or check_new_password(u_pw)
                if prob:
                    st.warning(prob)
                else:
                    try:
                        with tx() as cur:
                            insert_user(cur, u_name, u_pw, u_role, sel_b, u_full)
                        st.success(f"Matagumpay na naidagdag si '{u_name}' bilang {u_role} sa negosyong ito.")
                    except Exception as e:
                        st.error(f"Error: {e}")

    with t_acc:
        st.subheader(t("change_pass"))
        with st.form("owner_pw"):
            cpw = st.text_input(t("cur_pass"), type="password")
            npw = st.text_input(t("new_pass"), type="password")
            if st.form_submit_button(t("save_pass_btn")):
                row = query_df("SELECT password_hash FROM users WHERE id = %s", (st.session_state.user_id,))
                if row.empty or not check_password(cpw, row.iloc[0]["password_hash"]):
                    st.warning("Mali ang kasalukuyang password.")
                else:
                    prob = check_new_password(npw)
                    if prob:
                        st.warning(prob)
                    else:
                        with tx() as cur:
                            cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hash_password(npw), st.session_state.user_id))
                        st.success("Matagumpay na napalitan ang password.")


if ROLE == "owner":
    owner_panel()
    st.stop()


# ---------------------------------------------------------------
# 5. MAIN APPLICATION LAYOUT & TRANSLATED CATEGORIZED TABS
# ---------------------------------------------------------------
st.title(f"📊 {st.session_state.business_name} - {t('sys_title')}")

TAB_ORDERS = "🧾 " + t("orders")
TAB_INV = "📦 " + t("inventory")
TAB_CANC = "🚫 " + t("cancellations")
TAB_DASH = "📈 " + t("dashboard")
TAB_EXPORT = "📥 " + t("export")
TAB_USERS = "👥 " + t("users")
TAB_ACC = "🔑 " + t("account")

names = [TAB_ORDERS, TAB_INV, TAB_CANC]
if can("dashboard"):
    names.append(TAB_DASH)
if can("export"):
    names.append(TAB_EXPORT)
if can("users"):
    names.append(TAB_USERS)
names.append(TAB_ACC)

T = dict(zip(names, st.tabs(names)))
df_orders = load_orders()
df_products = load_products()

if ROLE == "viewer":
    st.info("👀 Ang account na ito ay may pahintulot na TINGIN LAMANG (View-Only).")


# ================= ORDERS MODULE =================
with T[TAB_ORDERS]:
    order_sub_action = st.radio(
        "Kategorya:",
        [t("add_order"), t("view_orders"), t("manage_status"), t("cancel_order"), t("pending_refunds")],
        horizontal=True
    )
    st.divider()

    if order_sub_action == t("add_order") and can("add_order"):
        st.subheader(t("add_order"))
        product_names = df_products["name"].tolist()
        
        # Ligtas na pagkuha ng presyo nang walang callback error sa loob ng form
        selected_item = st.selectbox(t("item_type"), [CUSTOM] + product_names, key="new_item")
        default_price = 0.0
        if selected_item != CUSTOM:
            prod_info = find_product(selected_item)
            if prod_info:
                default_price = prod_info[2]

        with st.form("order_entry_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            c1.text_input(t("customer_name"), key="new_customer")
            c2.text_input(t("contact_num"), key="new_contact")

            c3, c4 = st.columns(2)
            c3.write(f"Napiling Item: **{selected_item}**")
            if selected_item == CUSTOM:
                c4.text_input(t("custom_item"), key="new_item_custom")

            c5, c6, c7 = st.columns(3)
            c5.number_input(t("qty"), min_value=1, step=1, value=1, key="new_qty")
            c6.number_input(t("unit_price"), min_value=0.0, value=default_price, format="%.2f", key="new_price")
            c7.selectbox(t("pay_status"), PAYMENTS, key="new_payment")

            submitted_order = st.form_submit_button(t("save_order_btn"), type="primary")

        if submitted_order:
            s = st.session_state
            cust = s.get("new_customer", "").strip()
            final_item = (s.get("new_item_custom", "") if selected_item == CUSTOM else selected_item).strip()
            qty = int(s.get("new_qty", 1))
            price = float(s.get("new_price", 0.0))
            payment = s.get("new_payment", "Unpaid")
            contact = s.get("new_contact", "").strip()

            if not cust or not final_item:
                st.warning("Pakisagutan ang pangalan ng customer at detalye ng produkto.")
            elif price <= 0:
                st.warning("Ang presyo ay dapat mas mataas sa zero.")
            else:
                try:
                    with tx() as cur:
                        cur.execute(
                            "SELECT id, stock FROM products WHERE business_id = %s AND lower(name) = lower(%s)",
                            (bid(), final_item),
                        )
                        prod = cur.fetchone()
                        if prod:
                            cur.execute(
                                "UPDATE products SET stock = stock - %s WHERE id = %s AND stock >= %s",
                                (qty, prod[0], qty),
                            )
                            if cur.rowcount == 0:
                                st.warning(f"Kulang ang stock para sa '{final_item}'.")
                                st.stop()
                        cur.execute(
                            """INSERT INTO orders
                               (date, customer, item, price, status, quantity, contact, payment_status,
                                updated_by, updated_at, cancelled_qty, refund_due, refunded, business_id)
                               VALUES (%s, %s, %s, %s, 'Pending', %s, %s, %s, %s, %s, 0, 0, 0, %s)""",
                            (stamp(), cust, final_item, price, qty, contact, payment, me(), stamp(), bid()),
                        )
                    st.success(f"Matagumpay na naitala ang order para kay {cust}!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

    elif order_sub_action == t("view_orders"):
        st.subheader(t("view_orders"))
        if df_orders.empty:
            st.info("Walang rehistradong mga order sa kasalukuyan.")
        else:
            f1, f2, f3 = st.columns(3)
            f_status = f1.selectbox(t("filter_status"), ["Lahat"] + STATUSES)
            f_pay = f2.selectbox(t("filter_pay"), ["Lahat"] + PAYMENTS)
            search_query = f3.text_input(t("search_cust"))

            view_df = df_orders.copy()
            if f_status != "Lahat":
                view_df = view_df[view_df["status"] == f_status]
            if f_pay != "Lahat":
                view_df = view_df[view_df["payment_status"] == f_pay]
            if search_query:
                view_df = view_df[view_df["customer"].str.contains(search_query, case=False, na=False)]

            st.dataframe(view_df[["id", "date", "customer", "contact", "item", "active_qty", "price", "total", "status", "payment_status"]], use_container_width=True, hide_index=True)

    elif order_sub_action == t("manage_status") and can("edit_order"):
        st.subheader(t("manage_status"))
        editable = df_orders[df_orders["active_qty"] > 0]
        if editable.empty:
            st.info("Walang order na maaaring baguhin.")
        else:
            labels = {int(r.id): f"ID {r.id}: {r.customer} - {r.item} ({r.status})" for r in editable.itertuples()}
            sel_id = st.selectbox("Pumili ng Order", list(labels.keys()), format_func=lambda x: labels[x])
            c_row = editable[editable["id"] == sel_id].iloc[0]

            with st.form("update_status_form"):
                ns = st.selectbox(t("new_status"), ACTIVE_STATUSES, index=ACTIVE_STATUSES.index(c_row["status"]) if c_row["status"] in ACTIVE_STATUSES else 0)
                np = st.selectbox(t("pay_status"), PAYMENTS, index=PAYMENTS.index(c_row["payment_status"]) if c_row["payment_status"] in PAYMENTS else 0)
                sub_up = st.form_submit_button("I-update ang Status")
            
            if sub_up:
                try:
                    with tx() as cur:
                        cur.execute("UPDATE orders SET status = %s, payment_status = %s, updated_by = %s, updated_at = %s WHERE id = %s AND business_id = %s", (ns, np, me(), stamp(), int(sel_id), bid()))
                    st.success("Matagumpay na na-update ang order.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

    elif order_sub_action == t("cancel_order") and can("cancel"):
        st.subheader(t("cancel_order"))
        editable = df_orders[df_orders["active_qty"] > 0]
        if editable.empty:
            st.info("Walang order na maaaring i-cancel.")
        else:
            labels_c = {int(r.id): f"ID {r.id}: {r.customer} - {r.item} (Natitira: {r.active_qty})" for r in editable.itertuples()}
            cid = st.selectbox("Pumili ng Order para Kanselahin", list(labels_c.keys()), format_func=lambda x: labels_c[x])
            crow = editable[editable["id"] == cid].iloc[0]
            max_u = int(crow["active_qty"])

            with st.form("cancel_form"):
                units = st.number_input(t("cancel_units"), min_value=1, max_value=max_u, value=1)
                reason = st.selectbox(t("cancel_reason"), REASONS)
                note = st.text_input(t("cancel_note"))
                restock = st.checkbox(t("restock_check"), value=True)
                
                ref_now = False
                if crow["payment_status"] == "Paid":
                    ref_amt = float(units) * float(crow["price"])
                    st.warning(f"Ang order na ito ay BAYAD na. Kinakailangang magbalik ng refund na nagkakahalaga ng ₱{ref_amt:,.2f}.")
                    ref_now = st.checkbox(t("refund_check"), value=False)
                
                sub_cancel = st.form_submit_button(t("cancel_btn"), type="primary")

            if sub_cancel:
                try:
                    with tx() as cur:
                        price = float(crow["price"])
                        pay = crow["payment_status"]
                        cust = crow["customer"]
                        itm = crow["item"]
                        new_cancelled = int(crow["cancelled_qty"]) + units
                        refund_add = units * price if pay == "Paid" else 0.0
                        refunded_add = refund_add if ref_now else 0.0
                        fully_cancelled = new_cancelled >= int(crow["quantity"])

                        if restock:
                            cur.execute("UPDATE products SET stock = stock + %s WHERE business_id = %s AND lower(name) = lower(%s)", (units, bid(), itm))

                        cur.execute(
                            """UPDATE orders SET cancelled_qty = %s, refund_due = COALESCE(refund_due, 0) + %s,
                               refunded = COALESCE(refunded, 0) + %s, status = CASE WHEN %s THEN 'Cancelled' ELSE status END,
                               updated_by = %s, updated_at = %s WHERE id = %s AND business_id = %s""",
                            (new_cancelled, refund_add, refunded_add, fully_cancelled, me(), stamp(), int(cid), bid())
                        )
                        cur.execute(
                            """INSERT INTO cancellations (cancelled_at, order_id, customer, item, units, unit_price, amount,
                               was_paid, refunded_now, restocked, reason, note, cancelled_by, business_id)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                            (stamp(), int(cid), cust, itm, units, price, units * price, pay == "Paid", bool(ref_now), bool(restock), reason, note, me(), bid())
                        )
                    st.success("Matagumpay na nakansela ang order.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

    elif order_sub_action == t("pending_refunds") and can("refund"):
        st.subheader(t("pending_refunds"))
        pending_ref = df_orders[df_orders["refund_pending"] > 0.009]
        if pending_ref.empty:
            st.success("Walang nakabinbing refund sa kasalukuyan.")
        else:
            st.dataframe(pending_ref[["id", "customer", "contact", "item", "refund_pending"]], use_container_width=True, hide_index=True)
            ref_labels = {int(r.id): f"ID {r.id}: {r.customer} - ₱{r.refund_pending:,.2f}" for r in pending_ref.itertuples()}
            r_id = st.selectbox("Pumili ng Order", list(ref_labels.keys()), format_func=lambda x: ref_labels[x])
            if st.button("Markahan bilang Naibalik na ang Refund"):
                try:
                    with tx() as cur:
                        cur.execute("UPDATE orders SET refunded = refund_due, updated_by = %s, updated_at = %s WHERE id = %s AND business_id = %s", (me(), stamp(), int(r_id), bid()))
                    st.success("Naitala na ang refund bilang naibalik.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")


# ================= INVENTORY MODULE =================
with T[TAB_INV]:
    inv_sub = st.radio("Kategorya:", [t("view_inventory"), t("add_product"), t("restock")], horizontal=True)
    st.divider()

    if inv_sub == t("view_inventory"):
        if df_products.empty:
            st.info("Walang nakatalang produkto.")
        else:
            low_stock = df_products[df_products["stock"] <= df_products["reorder_level"]]
            if not low_stock.empty:
                st.warning("⚠️ Babala: Mababa na ang stock para sa mga sumusunod na produkto: " + ", ".join(f"{r.name} ({r.stock})" for r in low_stock.itertuples()))
            
            show_p = df_products.rename(columns={"name": "Produkto", "price": "Presyo", "cost": "Puhunan", "stock": "Stock", "reorder_level": "Limitasyon"})
            if not can("costs"):
                show_p = show_p.drop(columns=["Puhunan"], errors="ignore")
            st.dataframe(show_p.drop(columns=["id", "business_id"], errors="ignore"), use_container_width=True, hide_index=True)

    elif inv_sub == t("add_product") and can("inventory"):
        with st.form("add_prod_form", clear_on_submit=True):
            p1, p2 = st.columns(2)
            p_name = p1.text_input(t("prod_name"))
            p_stock = p2.number_input(t("current_stock"), min_value=0, step=1)
            p3, p4, p5 = st.columns(3)
            p_price = p3.number_input(t("selling_price"), min_value=0.0, format="%.2f")
            p_cost = p4.number_input(t("cost_price"), min_value=0.0, format="%.2f")
            p_reorder = p5.number_input(t("reorder_lvl"), min_value=0, value=5, step=1)
            sub_prod = st.form_submit_button(t("save_prod_btn"))
        
        if sub_prod:
            if not p_name.strip():
                st.warning("Ilagay ang pangalan ng produkto.")
            else:
                try:
                    with tx() as cur:
                        cur.execute(
                            """INSERT INTO products (name, price, cost, stock, reorder_level, business_id)
                               VALUES (%s, %s, %s, %s, %s, %s)
                               ON CONFLICT (business_id, name) DO UPDATE SET
                                 price = EXCLUDED.price, cost = EXCLUDED.cost,
                                 stock = EXCLUDED.stock, reorder_level = EXCLUDED.reorder_level""",
                            (p_name.strip(), p_price, p_cost, int(p_stock), int(p_reorder), bid())
                        )
                    st.success("Matagumpay na nai-save ang produkto.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

    elif inv_sub == t("restock") and can("inventory"):
        if df_products.empty:
            st.info("Walang produkto para lagyan ng stock.")
        else:
            with st.form("restock_form"):
                r_name = st.selectbox(t("prod_name"), df_products["name"].tolist())
                r_qty = st.number_input(t("qty"), min_value=1, step=1)
                sub_res = st.form_submit_button(t("add_stock_btn"))
            if sub_res:
                try:
                    with tx() as cur:
                        cur.execute("UPDATE products SET stock = stock + %s WHERE name = %s AND business_id = %s", (int(r_qty), r_name, bid()))
                    st.success("Nadagdagan na ang stock ng produkto.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")


# ================= CANCELLATIONS MODULE =================
with T[TAB_CANC]:
    st.subheader(t("cancellations"))
    df_c = query_df("SELECT * FROM cancellations WHERE business_id = %s ORDER BY id DESC", (bid(),))
    if df_c.empty:
        st.info("Walang nakatalang kanselasyon.")
    else:
        df_c["units"] = pd.to_numeric(df_c["units"]).fillna(0).astype(int)
        df_c["amount"] = pd.to_numeric(df_c["amount"]).fillna(0.0)

        m1, m2, m3 = st.columns(3)
        m1.metric("Kabuuang Bilang", len(df_c))
        m2.metric("Na-cancel na Yunit", int(df_c["units"].sum()))
        m3.metric("Kabuuang Halaga", f"₱{df_c['amount'].sum():,.2f}")

        st.divider()
        st.subheader("Visualization ng mga Dahilan")
        reason_counts = df_c["reason"].replace("", "Walang Tukoy na Dahilan").value_counts()
        st.bar_chart(reason_counts)

        st.dataframe(df_c, use_container_width=True, hide_index=True)


# ================= DASHBOARD MODULE =================
if TAB_DASH in T:
    with T[TAB_DASH]:
        st.subheader(t("dashboard"))
        valid_orders = df_orders[df_orders["active_qty"] > 0].copy()
        if valid_orders.empty:
            st.info("Walang sapat na datos para sa dashboard.")
        else:
            valid_orders["day"] = pd.to_datetime(valid_orders["date"], errors="coerce").dt.date
            
            costs = df_products.set_index(df_products["name"].str.lower())["cost"].to_dict()
            valid_orders["unit_cost"] = valid_orders["item"].str.lower().map(costs)
            known_costs = valid_orders.dropna(subset=["unit_cost"])
            est_profit = ((known_costs["price"] - known_costs["unit_cost"].astype(float)) * known_costs["active_qty"]).sum()

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Orders", len(valid_orders))
            col2.metric("Benta (Gross)", f"₱{valid_orders['total'].sum():,.2f}")
            col3.metric("Tubo (Net Profit)", f"₱{est_profit:,.2f}")
            col4.metric("Refunds", f"₱{df_orders['refund_pending'].sum():,.2f}")

            st.divider()
            st.subheader("Trend ng Benta")
            daily_sales = valid_orders.groupby("day")["total"].sum()
            st.line_chart(daily_sales)


# ================= EXPORT MODULE =================
if TAB_EXPORT in T:
    with T[TAB_EXPORT]:
        st.subheader(t("export"))
        st.download_button("I-download ang mga Order (CSV)", data=df_orders.to_csv(index=False).encode("utf-8-sig"), file_name=f"orders_{now().date()}.csv", mime="text/csv")
        st.download_button("I-download ang Imbentaryo (CSV)", data=df_products.to_csv(index=False).encode("utf-8-sig"), file_name=f"inventory_{now().date()}.csv", mime="text/csv")


# ================= USERS MODULE =================
if TAB_USERS in T:
    with T[TAB_USERS]:
        st.subheader(t("users"))
        users_df = query_df("SELECT id, username, full_name, role, active, created_at FROM users WHERE business_id = %s ORDER BY id", (bid(),))
        st.dataframe(users_df, use_container_width=True, hide_index=True)


# ================= ACCOUNT MODULE =================
with T[TAB_ACC]:
    st.subheader(t("account"))
    with st.form("change_acc_pw"):
        c_pw = st.text_input(t("cur_pass"), type="password")
        n_pw = st.text_input(t("new_pass"), type="password")
        n_pw2 = st.text_input(t("confirm_pass"), type="password")
        if st.form_submit_button(t("save_pass_btn")):
            r = query_df("SELECT password_hash FROM users WHERE id = %s", (st.session_state.user_id,))
            if r.empty or not check_password(c_pw, r.iloc[0]["password_hash"]):
                st.warning("Mali ang kasalukuyang password.")
            else:
                err_p = check_new_password(n_pw, n_pw2)
                if err_p:
                    st.warning(err_p)
                else:
                    with tx() as cur:
                        cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hash_password(n_pw), st.session_state.user_id))
                    st.success("Matagumpay na nabago ang iyong password.")