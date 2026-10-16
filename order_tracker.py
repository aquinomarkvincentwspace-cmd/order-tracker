"""
Order Tracker - multi-business version

ROLES
  owner  : (ikaw) nagdadagdag ng mga negosyo at nagre-reset ng password. Hindi nakikita ang data ng negosyo.
  admin  : (1-2 bawat negosyo) lahat ng nasa negosyo: dashboard, inventory, export, mga user.
  staff  : nag-i-input ng orders, nag-uupdate ng status/payment, nag-cancel at nag-refund.
  viewer : tingin lang (orders, inventory na walang puhunan, listahan ng cancel).
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

st.set_page_config(page_title="Advanced Business Order Tracker", page_icon="📊", layout="wide")

STATUSES = ["Pending", "Packed", "Out for Delivery", "Delivered", "Cancelled"]
ACTIVE_STATUSES = ["Pending", "Packed", "Out for Delivery", "Delivered"]
PAYMENTS = ["Unpaid", "Paid"]
CUSTOM = "-- Iba pa (i-type) --"
REASONS = [
    "Nagbago ang isip ng customer",
    "Maling order / maling item",
    "Walang stock / walang supply",
    "Sira o may depekto",
    "Late ang delivery",
    "Hindi nakabayad",
    "Iba pa",
]
PH_TZ = timezone(timedelta(hours=8))  # Philippine time (Streamlit Cloud runs on UTC)
MAX_ADMINS = 2                        # pinakamaraming admin bawat negosyo
MIN_PASSWORD = 8

ROLE_LABELS = {
    "admin": "Admin (nakikita at kontrolado lahat)",
    "staff": "Staff (nag-i-input ng benta)",
    "viewer": "Viewer (tingin lang)",
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


# ---------------------------------------------------------------
# 1. DATABASE (Supabase / PostgreSQL)
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
            "UPDATE orders SET cancelled_qty = quantity "
            "WHERE status = 'Cancelled' AND COALESCE(cancelled_qty, 0) = 0"
        )
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

        # --- multi-business tables ---
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
        # product names only need to be unique inside one business
        cur.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS products_name_key")
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS products_biz_name_idx ON products (business_id, name)"
        )

        # add older cancellations (made before the list existed) once
        cur.execute(
            """
            INSERT INTO cancellations
                (cancelled_at, order_id, customer, item, units, unit_price, amount,
                 was_paid, refunded_now, restocked, reason, note, cancelled_by, business_id)
            SELECT COALESCE(NULLIF(o.updated_at, ''), o.date), o.id, o.customer, o.item,
                   o.cancelled_qty, o.price, o.cancelled_qty * o.price,
                   (o.payment_status = 'Paid'), FALSE, TRUE,
                   'Hindi naitala (lumang cancel)', '', COALESCE(o.updated_by, ''), o.business_id
            FROM orders o
            WHERE COALESCE(o.cancelled_qty, 0) > 0
              AND NOT EXISTS (SELECT 1 FROM cancellations c WHERE c.order_id = o.id)
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
# 2. PASSWORDS AND ACCOUNTS
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
        return f"Dapat hindi bababa sa {MIN_PASSWORD} ang haba ng password."
    if pw2 is not None and pw != pw2:
        return "Hindi magkapareho ang dalawang password."
    return None


def check_username(u):
    if not re.fullmatch(r"[a-z0-9._-]{3,30}", u):
        return "Ang username ay 3-30 na letra (maliit), numero, tuldok, gitling o underscore, walang space."
    return None


def users_exist():
    return int(query_df("SELECT COUNT(*) AS n FROM users").iloc[0]["n"]) > 0


def insert_user(cur, username, password, role, business_id, full_name):
    cur.execute(
        """INSERT INTO users (username, password_hash, full_name, role, business_id, active, created_at)
            VALUES (%s, %s, %s, %s, %s, TRUE, %s)""",
        (username, hash_password(password), full_name.strip(), role, business_id, stamp()),
    )


def flash(kind, msg):
    st.session_state["flash"] = (kind, msg)


def show_flash():
    if "flash" in st.session_state:
        kind, msg = st.session_state.pop("flash")
        getattr(st, kind)(msg)


def logout():
    for k in ("authenticated", "user_id", "username", "role", "business_id", "business_name", "full_name"):
        st.session_state.pop(k, None)
    st.session_state.authenticated = False


# ---------- first-time setup ----------
def setup_screen():
    st.title("🛠️ Unang Setup")
    st.write(
        "Ginagawa ito nang isang beses lang. Gagawa ka ng **Owner account** (para sa pag-add ng mga negosyo) "
        "at ng **unang negosyo** na may sarili nitong Admin. Mananatili ang mga kasalukuyang orders at produkto mo "
        "sa unang negosyo."
    )
    try:
        expected = st.secrets["SETUP_CODE"]
    except Exception:
        expected = None
    if not expected:
        st.error("Kailangan muna ng SETUP_CODE sa Streamlit Secrets para hindi makapag-setup ang ibang tao.")
        st.write("Pumunta sa Manage app → Settings → Secrets at idagdag ang linyang ito (palitan ang laman):")
        st.code('SETUP_CODE = "ilagay-dito-ang-sarili-mong-lihim-na-code"')
        st.write("I-save, i-refresh ang page na ito, at ilagay ulit ang code sa ibaba.")
        st.stop()

    with st.form("setup_form"):
        code = st.text_input("Setup code (yung nasa Secrets)", type="password")
        st.subheader("Owner account (ikaw)")
        o_user = st.text_input("Owner username (hal. owner)")
        o_pw = st.text_input("Owner password", type="password")
        o_pw2 = st.text_input("Ulitin ang owner password", type="password")
        st.subheader("Unang negosyo")
        b_name = st.text_input("Pangalan ng negosyo", value="Aking Negosyo")
        a_user = st.text_input("Admin username ng negosyo (hal. admin)")
        a_full = st.text_input("Buong pangalan ng admin (optional)")
        a_pw = st.text_input("Admin password", type="password")
        a_pw2 = st.text_input("Ulitin ang admin password", type="password")
        go = st.form_submit_button("Gawin ang Setup")

    if go:
        o_user, a_user, b_name = o_user.strip().lower(), a_user.strip().lower(), b_name.strip()
        problem = None
        if not hmac.compare_digest(code.encode(), str(expected).encode()):
            problem = "Mali ang setup code."
        elif not b_name:
            problem = "Ilagay ang pangalan ng negosyo."
        elif o_user == a_user:
            problem = "Dapat magkaiba ang owner username at admin username."
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
                    insert_user(cur, o_user, o_pw, "owner", None, "Owner")
                    insert_user(cur, a_user, a_pw, "admin", new_bid, a_full)
                    for table in ("orders", "products", "cancellations"):
                        cur.execute(f"UPDATE {table} SET business_id = %s WHERE business_id IS NULL", (new_bid,))
                st.success("Tapos na ang setup! Mag-login na gamit ang admin (o owner) account.")
                st.rerun()
            except Exception as e:
                st.error(f"May error: {e}")


if not users_exist():
    setup_screen()
    st.stop()


# ---------- login & self-registration ----------
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
        return None, "Maling Username o Password!"
    r = df.iloc[0]
    if not bool(r["active"]):
        return None, "Naka-disable ang account na ito. Makipag-ugnayan sa admin."
    if r["role"] != "owner" and not bool(r["business_active"]):
        return None, "Naka-disable ang negosyong ito. Makipag-ugnayan sa may-ari ng app."
    return r, None


if not st.session_state.authenticated:
    st.title("🔒 Order Tracker - Portal")
    if st.session_state.get("lock_until", 0) > time.time():
        st.error("Masyadong maraming maling subok. Maghintay ng isang minuto bago ulit mag-login.")
        st.stop()
        
    tab_login, tab_signup = st.tabs(["Mag-login", "Mag-register (Bagong Account)"])
    
    with tab_login:
        with st.form("login_form"):
            u = st.text_input("Username")
            p = st.text_input("Password", type="password")
            ok = st.form_submit_button("Mag-login")
        if ok:
            rec, err = try_login(u.strip().lower(), p)
            if err:
                st.session_state["fails"] = st.session_state.get("fails", 0) + 1
                if st.session_state["fails"] >= 5:
                    st.session_state["lock_until"] = time.time() + 60
                    st.session_state["fails"] = 0
                st.error(err)
            else:
                st.session_state["fails"] = 0
                st.session_state.authenticated = True
                st.session_state.user_id = int(rec["id"])
                st.session_state.username = rec["username"]
                st.session_state.full_name = rec["full_name"] or rec["username"]
                st.session_state.role = rec["role"]
                st.session_state.business_id = None if rec["role"] == "owner" else int(rec["business_id"])
                st.session_state.business_name = rec["business_name"] if rec["role"] != "owner" else ""
                st.rerun()

    with tab_signup:
        st.subheader("Gumawa ng sariling account")
        active_biz = query_df("SELECT id, name FROM businesses WHERE active = TRUE ORDER BY name")
        
        with st.form("self_signup_form"):
            new_u = st.text_input("Pumili ng Username (3-30 na letra/numero)")
            new_fn = st.text_input("Buong Pangalan")
            new_p1 = st.text_input("Password", type="password")
            new_p2 = st.text_input("Ulitin ang Password", type="password")
            
            if active_biz.empty:
                st.warning("Wala pang nakarehistrong negosyo.")
                biz_choice = None
            else:
                biz_dict = {int(r.id): r.name for r in active_biz.itertuples()}
                biz_choice = st.selectbox("Piliin ang Negosyo", list(biz_dict.keys()), format_func=lambda x: biz_dict[x])
                
            reg_ok = st.form_submit_button("Mag-register")
            
        if reg_ok:
            new_u = new_u.strip().lower()
            problem = check_username(new_u) or check_new_password(new_p1, new_p2)
            if not biz_choice:
                problem = "Pumili ng valid na negosyo."
            
            if problem:
                st.error(problem)
            else:
                try:
                    with tx() as cur:
                        insert_user(cur, new_u, new_p1, "staff", int(biz_choice), new_fn)
                    st.success("Tagumpay! Nagawa na ang iyong account. Pwede ka nang lumipat sa 'Mag-login' tab para pumasok.")
                except psycopg2.IntegrityError:
                    st.error("May gumagamit na ng username na iyan. Pumili ng iba.")
                except Exception as e:
                    st.error(f"May error: {e}")
    st.stop()

# re-check on every run, so a disabled or changed account takes effect right away
_chk = query_df(
    """SELECT u.role, u.active, u.business_id, b.name AS business_name, b.active AS business_active
       FROM users u LEFT JOIN businesses b ON b.id = u.business_id WHERE u.id = %s""",
    (st.session_state.get("user_id", -1),),
)
if _chk.empty or not bool(_chk.iloc[0]["active"]) or (
    _chk.iloc[0]["role"] != "owner" and not bool(_chk.iloc[0]["business_active"])
):
    logout()
    st.rerun()
st.session_state.role = _chk.iloc[0]["role"]
if st.session_state.role != "owner":
    st.session_state.business_id = int(_chk.iloc[0]["business_id"])
    st.session_state.business_name = _chk.iloc[0]["business_name"]

ROLE = st.session_state.role
if ROLE == "owner":
    st.sidebar.success(f"Naka-login: {me()} (Owner)")
else:
    st.sidebar.success(f"{st.session_state.business_name}")
    st.sidebar.write(f"👤 {me()} · {ROLE.capitalize()}")
if st.sidebar.button("Mag-logout"):
    logout()
    st.rerun()


# ---------------------------------------------------------------
# 3. SHARED: CHANGE OWN PASSWORD
# ---------------------------------------------------------------
def change_password_ui():
    st.header("Palitan ang Password")
    with st.form("change_pw", clear_on_submit=True):
        cur_pw = st.text_input("Kasalukuyang password", type="password")
        new_pw = st.text_input("Bagong password", type="password")
        new_pw2 = st.text_input("Ulitin ang bagong password", type="password")
        go = st.form_submit_button("I-save ang bagong password")
    if go:
        row = query_df("SELECT password_hash FROM users WHERE id = %s", (st.session_state.user_id,))
        if row.empty or not check_password(cur_pw, row.iloc[0]["password_hash"]):
            st.error("Mali ang kasalukuyang password.")
        else:
            problem = check_new_password(new_pw, new_pw2)
            if problem:
                st.error(problem)
            else:
                with tx() as cur:
                    cur.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                                (hash_password(new_pw), st.session_state.user_id))
                st.success("Napalitan na ang password mo.")


# ---------------------------------------------------------------
# 4. OWNER PANEL (mga negosyo)
# ---------------------------------------------------------------
def owner_panel():
    st.title("🏢 Owner Panel")
    t_biz, t_acc = st.tabs(["🏢 Mga Negosyo", "🔑 Account"])

    with t_biz:
        show_flash()
        biz = query_df(
            """SELECT b.id, b.name, b.active, b.created_at,
                     (SELECT COUNT(*) FROM users u WHERE u.business_id = b.id AND u.active) AS active_users,
                     (SELECT COUNT(*) FROM orders o WHERE o.business_id = b.id) AS orders
               FROM businesses b ORDER BY b.id"""
        )
        st.subheader("Mga Negosyo")
        st.dataframe(
            biz.rename(columns={"id": "ID", "name": "Negosyo", "active": "Aktibo?", "created_at": "Ginawa",
                                "active_users": "Aktibong user", "orders": "Bilang ng orders"}),
            use_container_width=True, hide_index=True,
        )

        st.subheader("Magdagdag ng Bagong Negosyo")
        with st.form("new_business", clear_on_submit=True):
            n_name = st.text_input("Pangalan ng negosyo")
            n_user = st.text_input("Username ng unang Admin")
            n_full = st.text_input("Buong pangalan ng Admin (optional)")
            n_pw = st.text_input("Password ng Admin", type="password")
            go = st.form_submit_button("Gawin ang negosyo")
        if go:
            n_name, n_user = n_name.strip(), n_user.strip().lower()
            problem = None
            if not n_name:
                problem = "Ilagay ang pangalan ng negosyo."
            else:
                problem = check_username(n_user) or check_new_password(n_pw)
            if problem:
                st.error(problem)
            else:
                try:
                    with tx() as cur:
                        cur.execute(
                            "INSERT INTO businesses (name, created_at, active) VALUES (%s, %s, TRUE) RETURNING id",
                            (n_name, stamp()),
                        )
                        new_id = cur.fetchone()[0]
                        insert_user(cur, n_user, n_pw, "admin", new_id, n_full)
                    flash("success", f"Nagawa na ang '{n_name}'. Ibigay sa admin ang username at password.")
                    st.rerun()
                except psycopg2.IntegrityError:
                    st.error("May ganitong pangalan ng negosyo o username na. Pumili ng iba.")
                except Exception as e:
                    st.error(f"May error: {e}")

        if not biz.empty:
            st.subheader("Pamahalaan ang Negosyo")
            labels = {int(r.id): f"{r.name} ({'aktibo' if r.active else 'naka-disable'})" for r in biz.itertuples()}
            sel = st.selectbox("Piliin ang negosyo", list(labels.keys()), format_func=lambda x: labels[x])
            is_active = bool(biz[biz["id"] == sel].iloc[0]["active"])
            if st.button("I-disable ang negosyo" if is_active else "I-enable ang negosyo"):
                with tx() as cur:
                    cur.execute("UPDATE businesses SET active = %s WHERE id = %s", (not is_active, int(sel)))
                flash("success", "Na-update na ang negosyo.")
                st.rerun()

            ub = query_df(
                "SELECT id, username, full_name, role, active FROM users WHERE business_id = %s ORDER BY id",
                (int(sel),),
            )
            st.write("Mga user ng negosyong ito:")
            st.dataframe(ub.rename(columns={"id": "ID", "username": "Username", "full_name": "Pangalan",
                                            "role": "Role", "active": "Aktibo?"}),
                         use_container_width=True, hide_index=True)
            if not ub.empty:
                ulabels = {int(r.id): f"{r.username} ({r.role})" for r in ub.itertuples()}
                usel = st.selectbox("I-reset ang password ng user", list(ulabels.keys()),
                                    format_func=lambda x: ulabels[x], key="owner_reset_user")
                with st.form("owner_reset_pw", clear_on_submit=True):
                    npw = st.text_input("Bagong password", type="password")
                    go2 = st.form_submit_button("I-reset ang password")
                if go2:
                    problem = check_new_password(npw)
                    if problem:
                        st.error(problem)
                    else:
                        with tx() as cur:
                            cur.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                                        (hash_password(npw), int(usel)))
                        st.success("Na-reset na ang password.")

    with t_acc:
        change_password_ui()


if ROLE == "owner":
    owner_panel()
    st.stop()


# ---------------------------------------------------------------
# 5. BUSINESS DATA HELPERS (laging naka-filter sa sariling negosyo)
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


def sync_price():
    choice = st.session_state.get("new_item", CUSTOM)
    if choice != CUSTOM:
        prod = find_product(choice)
        if prod:
            st.session_state["new_price"] = prod[2]


def save_order():
    if not can("add_order"):
        flash("error", "Walang pahintulot na magdagdag ng order.")
        return
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

    try:
        with tx() as cur:
            cur.execute(
                "SELECT id, stock FROM products WHERE business_id = %s AND lower(name) = lower(%s)",
                (bid(), item),
            )
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
                   (date, customer, item, price, status, quantity, contact, payment_status,
                    updated_by, updated_at, cancelled_qty, refund_due, refunded, business_id)
                   VALUES (%s, %s, %s, %s, 'Pending', %s, %s, %s, %s, %s, 0, 0, 0, %s)""",
                (stamp(), customer, item, price, qty, contact, payment, me(), stamp(), bid()),
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
    if not can("edit_order"):
        return False, "Walang pahintulot na mag-update ng order."
    try:
        with tx() as cur:
            cur.execute(
                "UPDATE orders SET status = %s, payment_status = %s, updated_by = %s, updated_at = %s "
                "WHERE id = %s AND business_id = %s",
                (new_status, new_payment, me(), stamp(), order_id, bid()),
            )
        return True, "Na-update na ang order!"
    except Exception as e:
        return False, f"May error: {e}"


def cancel_units(order_id, units, restock, refunded_now, reason="", note=""):
    """Cancel some (or all) pieces of an order and record it in the cancellations list."""
    if not can("cancel"):
        return False, "Walang pahintulot na mag-cancel ng order."
    try:
        with tx() as cur:
            cur.execute(
                "SELECT item, price, quantity, cancelled_qty, payment_status, customer "
                "FROM orders WHERE id = %s AND business_id = %s FOR UPDATE",
                (order_id, bid()),
            )
            row = cur.fetchone()
            if not row:
                return False, "Hindi nahanap ang order."
            item, price, qty, cancelled, pay, customer = row
            qty = qty or 1
            cancelled = cancelled or 0
            active = qty - cancelled
            if units < 1 or units > active:
                return False, f"Maaari lang mag-cancel ng 1 hanggang {active} piraso."

            new_cancelled = cancelled + units
            refund_add = units * price if pay == "Paid" else 0.0
            refunded_add = refund_add if refunded_now else 0.0
            fully_cancelled = new_cancelled >= qty

            if restock:
                cur.execute(
                    "UPDATE products SET stock = stock + %s WHERE business_id = %s AND lower(name) = lower(%s)",
                    (units, bid(), item),
                )

            cur.execute(
                """UPDATE orders SET
                        cancelled_qty = %s,
                        refund_due = COALESCE(refund_due, 0) + %s,
                        refunded = COALESCE(refunded, 0) + %s,
                        status = CASE WHEN %s THEN 'Cancelled' ELSE status END,
                        updated_by = %s,
                        updated_at = %s
                   WHERE id = %s AND business_id = %s""",
                (new_cancelled, refund_add, refunded_add, fully_cancelled, me(), stamp(), order_id, bid()),
            )
            cur.execute(
                """INSERT INTO cancellations
                        (cancelled_at, order_id, customer, item, units, unit_price, amount,
                         was_paid, refunded_now, restocked, reason, note, cancelled_by, business_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (stamp(), order_id, customer, item, units, price, units * price, pay == "Paid",
                 bool(refunded_now), bool(restock), reason, note.strip(), me(), bid()),
            )
        msg = f"Na-cancel ang {units} piraso."
        if refund_add > 0 and not refunded_now:
            msg += f" May refund na ₱{refund_add:,.2f} na dapat ibalik sa customer."
        return True, msg
    except Exception as e:
        return False, f"May error: {e}"


def mark_refund_done(order_id):
    if not can("refund"):
        return False, "Walang pahintulot."
    try:
        with tx() as cur:
            cur.execute(
                "UPDATE orders SET refunded = refund_due, updated_by = %s, updated_at = %s "
                "WHERE id = %s AND business_id = %s",
                (me(), stamp(), order_id, bid()),
            )
        return True, "Naitala na ang refund bilang naibalik."
    except Exception as e:
        return False, f"May error: {e}"


def count_admins(exclude_id=None):
    df = query_df(
        "SELECT id FROM users WHERE business_id = %s AND role = 'admin' AND active", (bid(),)
    )
    ids = [int(i) for i in df["id"].tolist()]
    return len([i for i in ids if i != exclude_id])


# ---------------------------------------------------------------
# 6. PAGE LAYOUT
# ---------------------------------------------------------------
st.title(f"📊 {st.session_state.business_name} - Order Tracker")

TAB_ORDERS, TAB_INV, TAB_CANC = "🧾 Orders", "📦 Inventory", "🚫 Cancellations"
TAB_DASH, TAB_EXPORT, TAB_USERS, TAB_ACC = "📈 Dashboard", "📥 Export", "👥 Mga User", "🔑 Account"

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
    st.info("👀 Tingin lang ang account na ito. Hindi ka makakapagdagdag o makakapagbago ng datos.")

# ================= ORDERS =================
with T[TAB_ORDERS]:
    show_flash()

    if can("add_order"):
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
        st.info("Wala pang orders.")
    else:
        pending_refunds = df_orders[df_orders["refund_pending"] > 0.009]
        if not pending_refunds.empty:
            st.warning(
                f"💸 May {len(pending_refunds)} order na may refund na hindi pa naibalik sa customer "
                f"(kabuuang ₱{pending_refunds['refund_pending'].sum():,.2f})."
            )

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

        show_cols = ["id", "date", "customer", "contact", "item", "quantity", "cancelled_qty",
                     "active_qty", "price", "total", "status", "payment_status", "refund_pending",
                     "updated_by", "updated_at"]
        st.dataframe(view[show_cols], use_container_width=True, hide_index=True)
        st.caption("quantity = orihinal na dami · cancelled_qty = na-cancel · active_qty = natitirang binili · "
                   "total = presyo × active_qty · refund_pending = refund na hindi pa naibalik")

        editable = df_orders[df_orders["active_qty"] > 0]

        # ---------- Section 3: change status / payment ----------
        if can("edit_order"):
            st.divider()
            st.header("3. Baguhin ang Status at Payment")
            if editable.empty:
                st.info("Walang aktibong order na mababago.")
            else:
                labels = {
                    int(r.id): f"ID {r.id}: {r.customer} - {r.item} x{r.active_qty} ({r.status}, {r.payment_status})"
                    for r in editable.itertuples()
                }
                selected_id = st.selectbox(
                    "Piliin ang Order", list(labels.keys()), format_func=lambda x: labels[x], key="edit_id"
                )
                cur_row = editable[editable["id"] == selected_id].iloc[0]
                u1, u2 = st.columns(2)
                new_status = u1.selectbox(
                    "Bagong Status", ACTIVE_STATUSES,
                    index=ACTIVE_STATUSES.index(cur_row["status"]) if cur_row["status"] in ACTIVE_STATUSES else 0,
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

        # ---------- Section 4: cancel / return ----------
        if can("cancel"):
            st.divider()
            st.header("4. Mag-cancel / Mag-return ng Order")
            st.caption("Pwedeng i-cancel ang ilang piraso lang (halimbawa 1 sa 3), hindi kailangang lahat.")
            if editable.empty:
                st.info("Walang aktibong order na maaaring i-cancel.")
            else:
                labels_c = {
                    int(r.id): (f"ID {r.id}: {r.customer} - {r.item} "
                                f"(natitira {r.active_qty} sa {r.quantity}) [{r.payment_status}]")
                    for r in editable.itertuples()
                }
                cid = st.selectbox(
                    "Piliin ang order na i-cancel", list(labels_c.keys()),
                    format_func=lambda x: labels_c[x], key="cancel_id",
                )
                crow = editable[editable["id"] == cid].iloc[0]
                max_units = int(crow["active_qty"])
                tag = f"{cid}_{max_units}"
                units = st.number_input(
                    "Ilang piraso ang i-cancel", min_value=1, max_value=max_units,
                    value=1, step=1, key=f"cancel_units_{tag}",
                )
                rc1, rc2 = st.columns(2)
                reason = rc1.selectbox("Dahilan ng pag-cancel", REASONS, key=f"reason_{tag}")
                note = rc2.text_input("Dagdag na tala (optional)", key=f"note_{tag}")
                restock = st.checkbox(
                    "Ibalik sa stock ang na-cancel na piraso (i-uncheck kung sira o hindi na maibebenta)",
                    value=True, key=f"restock_{tag}",
                )
                refund_now = False
                if crow["payment_status"] == "Paid":
                    refund_amt = float(units) * float(crow["price"])
                    st.warning(
                        f"Bayad na ang order na ito. Kapag na-cancel ang {int(units)} piraso, "
                        f"₱{refund_amt:,.2f} ang dapat ibalik sa customer."
                    )
                    refund_now = st.checkbox(
                        "Naibalik ko na ang pera sa customer", value=False, key=f"refund_now_{tag}"
                    )
                if st.button("I-cancel ang piniling dami", type="primary"):
                    ok, msg = cancel_units(int(cid), int(units), restock, refund_now, reason, note)
                    flash("success" if ok else "error", msg)
                    st.rerun()

        # ---------- Section 5: refunds not yet returned ----------
        if can("refund"):
            st.divider()
            st.header("5. Mga Refund na Hindi Pa Naibalik")
            if pending_refunds.empty:
                st.success("Walang nakabinbing refund.")
            else:
                st.dataframe(
                    pending_refunds[["id", "customer", "contact", "item", "cancelled_qty", "refund_pending"]],
                    use_container_width=True, hide_index=True,
                )
                ref_labels = {
                    int(r.id): f"ID {r.id}: {r.customer} - ₱{r.refund_pending:,.2f}"
                    for r in pending_refunds.itertuples()
                }
                ref_id = st.selectbox(
                    "Piliin ang order", list(ref_labels.keys()),
                    format_func=lambda x: ref_labels[x], key="refund_id",
                )
                if st.button("Naibalik na ang refund"):
                    ok, msg = mark_refund_done(int(ref_id))
                    flash("success" if ok else "error", msg)
                    st.rerun()

# ================= INVENTORY =================
with T[TAB_INV]:
    st.header("Inventory / Mga Produkto")
    if df_products.empty:
        st.info("Wala pang produkto. " + ("Magdagdag sa ibaba." if can("inventory") else "Magpa-add sa Admin."))
    else:
        low = df_products[df_products["stock"] <= df_products["reorder_level"]]
        if not low.empty:
            st.warning("⚠️ Mababa na ang stock: " + ", ".join(
                f"{r.name} ({r.stock})" for r in low.itertuples()))
        show = df_products.rename(columns={
            "name": "Produkto", "price": "Presyo", "cost": "Puhunan",
            "stock": "Stock", "reorder_level": "Reorder Level"})
        if not can("costs"):
            show = show.drop(columns=["Puhunan"])
        st.dataframe(show.drop(columns=["id", "business_id"]), use_container_width=True, hide_index=True)

    if can("inventory"):
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
                            """INSERT INTO products (name, price, cost, stock, reorder_level, business_id)
                               VALUES (%s, %s, %s, %s, %s, %s)
                               ON CONFLICT (business_id, name) DO UPDATE SET
                                 price = EXCLUDED.price, cost = EXCLUDED.cost,
                                 stock = EXCLUDED.stock, reorder_level = EXCLUDED.reorder_level""",
                            (p_name.strip(), p_price, p_cost, int(p_stock), int(p_reorder), bid()),
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
                    cur.execute(
                        "UPDATE products SET stock = stock + %s WHERE name = %s AND business_id = %s",
                        (int(restock_qty), restock_name, bid()),
                    )
                st.success("Na-update ang stock!")
                st.rerun()

# ================= CANCELLATIONS =================
with T[TAB_CANC]:
    st.header("Listahan ng mga Na-cancel")
    df_c = query_df("SELECT * FROM cancellations WHERE business_id = %s ORDER BY id DESC", (bid(),))
    if df_c.empty:
        st.info("Wala pang na-cancel na order.")
    else:
        df_c["units"] = pd.to_numeric(df_c["units"]).fillna(0).astype(int)
        df_c["amount"] = pd.to_numeric(df_c["amount"]).fillna(0.0)
        df_c["key"] = df_c["item"].str.lower()

        k1, k2, k3 = st.columns(3)
        k1.metric("Bilang ng Pag-cancel", len(df_c))
        k2.metric("Kabuuang Piraso na Na-cancel", int(df_c["units"].sum()))
        k3.metric("Halaga ng Na-cancel", f"₱{df_c['amount'].sum():,.2f}")

        by_item = df_c.groupby("key").agg(
            Produkto=("item", "first"),
            Beses=("id", "count"),
            Piraso=("units", "sum"),
            Halaga=("amount", "sum"),
        )
        ordered = df_orders.assign(key=df_orders["item"].str.lower()).groupby("key")["quantity"].sum()
        by_item["Naorder"] = ordered.reindex(by_item.index).fillna(0).astype(int)
        by_item["Cancel rate (%)"] = (
            by_item["Piraso"] / by_item["Naorder"].where(by_item["Naorder"] > 0) * 100
        ).round(1)
        by_item = by_item.sort_values("Piraso", ascending=False)

        st.subheader("Mga Produktong Pinakamaraming Na-cancel")
        st.dataframe(by_item.reset_index(drop=True), use_container_width=True, hide_index=True)
        st.bar_chart(by_item.set_index("Produkto")["Piraso"])
        st.caption("Cancel rate = piraso na na-cancel ÷ kabuuang piraso na na-order sa produktong iyon.")

        st.subheader("Mga Dahilan ng Pag-cancel")
        st.bar_chart(df_c["reason"].replace("", "Walang dahilan").value_counts())

        st.subheader("Buong Listahan")
        prod_filter = st.selectbox("I-filter ayon sa Produkto", ["Lahat"] + sorted(by_item["Produkto"].tolist()))
        log_view = df_c if prod_filter == "Lahat" else df_c[df_c["key"] == prod_filter.lower()]
        log_view = log_view.rename(columns={
            "cancelled_at": "Petsa", "order_id": "Order ID", "customer": "Customer", "item": "Produkto",
            "units": "Piraso", "unit_price": "Presyo", "amount": "Halaga", "was_paid": "Bayad na?",
            "refunded_now": "Refund agad?", "restocked": "Ibinalik sa stock?", "reason": "Dahilan",
            "note": "Tala", "cancelled_by": "Ni-cancel ni",
        })
        cols = ["Petsa", "Order ID", "Customer", "Produkto", "Piraso", "Presyo", "Halaga", "Bayad na?",
                "Refund agad?", "Ibinalik sa stock?", "Dahilan", "Tala", "Ni-cancel ni"]
        st.dataframe(log_view[cols], use_container_width=True, hide_index=True)

# ================= DASHBOARD (admin) =================
if TAB_DASH in T:
    with T[TAB_DASH]:
        st.header("Sales Dashboard")
        valid = df_orders[df_orders["active_qty"] > 0].copy()
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
            profit = ((known["price"] - known["unit_cost"].astype(float)) * known["active_qty"]).sum()

            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Bilang ng Orders", len(valid))
            m2.metric("Kabuuang Benta", f"₱{valid['total'].sum():,.2f}")
            m3.metric("Hindi pa bayad", f"₱{valid[valid['payment_status'] == 'Unpaid']['total'].sum():,.2f}")
            m4.metric("Tinatayang Tubo", f"₱{profit:,.2f}")
            m5.metric("Refund na dapat ibalik", f"₱{df_orders['refund_pending'].sum():,.2f}")
            st.caption("Ang benta at tubo ay para lang sa mga piraso na hindi na-cancel. "
                       "Ang tubo ay kinukuwenta lang para sa mga item na nasa Inventory na may puhunan.")

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

# ================= EXPORT (admin) =================
if TAB_EXPORT in T:
    with T[TAB_EXPORT]:
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
        st.download_button(
            "📥 Mga Na-cancel (CSV, Excel compatible)",
            data=query_df("SELECT * FROM cancellations WHERE business_id = %s ORDER BY id DESC", (bid(),))
            .to_csv(index=False).encode("utf-8-sig"),
            file_name=f"cancellations_{now().date()}.csv",
            mime="text/csv",
        )
        st.info("Naka-save na ang data mo sa Supabase, kaya hindi na ito mawawala kapag nag-reboot ang app. "
                "Ang CSV ay extra backup na lang.")

# ================= USERS (admin) =================
if TAB_USERS in T:
    with T[TAB_USERS]:
        show_flash()
        st.header("Mga User ng Negosyo")
        st.caption(f"Hanggang {MAX_ADMINS} Admin lang bawat negosyo. Ang Staff ay nag-i-input ng benta; "
                   "ang Viewer ay tingin lang.")
        df_u = query_df(
            "SELECT id, username, full_name, role, active, created_at FROM users "
            "WHERE business_id = %s ORDER BY id", (bid(),),
        )
        st.dataframe(
            df_u.rename(columns={"id": "ID", "username": "Username", "full_name": "Pangalan", "role": "Role",
                                   "active": "Aktibo?", "created_at": "Ginawa"}),
            use_container_width=True, hide_index=True,
        )

        st.subheader("Magdagdag ng User")
        with st.form("new_user", clear_on_submit=True):
            nu1, nu2 = st.columns(2)
            nu_user = nu1.text_input("Username (maliit na letra/numero, walang space)")
            nu_full = nu2.text_input("Buong pangalan (optional)")
            nu3, nu4 = st.columns(2)
            nu_role = nu3.selectbox("Role", list(ROLE_LABELS.keys()), format_func=lambda x: ROLE_LABELS[x])
            nu_pw = nu4.text_input(f"Password (hindi bababa sa {MIN_PASSWORD} karakter)", type="password")
            go = st.form_submit_button("Idagdag ang User")
        if go:
            nu_user = nu_user.strip().lower()
            problem = check_username(nu_user) or check_new_password(nu_pw)
            if not problem and nu_role == "admin" and count_admins() >= MAX_ADMINS:
                problem = f"Mayroon nang {MAX_ADMINS} admin ang negosyo."
            if problem:
                st.error(problem)
            else:
                try:
                    with tx() as cur:
                        insert_user(cur, nu_user, nu_pw, nu_role, bid(), nu_full)
                    flash("success", f"Nadagdag na si {nu_user}.")
                    st.rerun()
                except psycopg2.IntegrityError:
                    st.error("May gumagamit na ng username na iyan. Pumili ng iba.")
                except Exception as e:
                    st.error(f"May error: {e}")

        others = df_u[df_u["id"] != st.session_state.user_id]
        if not others.empty:
            st.subheader("Baguhin ang User")
            ulabels = {
                int(r.id): f"{r.username} - {r.role} ({'aktibo' if r.active else 'naka-disable'})"
                for r in others.itertuples()
            }
            uid = st.selectbox("Piliin ang user", list(ulabels.keys()),
                               format_func=lambda x: ulabels[x], key="manage_user")
            urow = others[others["id"] == uid].iloc[0]
            roles = list(ROLE_LABELS.keys())
            mu1, mu2 = st.columns(2)
            m_role = mu1.selectbox(
                "Role", roles, index=roles.index(urow["role"]) if urow["role"] in roles else 1,
                format_func=lambda x: ROLE_LABELS[x], key=f"mrole_{uid}",
            )
            m_active = mu2.checkbox("Aktibo (pwedeng mag-login)", value=bool(urow["active"]), key=f"mact_{uid}")
            m_pw = st.text_input("Bagong password (iwanang blangko kung hindi papalitan)",
                                 type="password", key=f"mpw_{uid}")
            if st.button("I-save ang pagbabago"):
                problem = None
                if m_pw:
                    problem = check_new_password(m_pw)
                if not problem and m_role == "admin" and m_active and count_admins(exclude_id=int(uid)) >= MAX_ADMINS:
                    problem = f"Mayroon nang {MAX_ADMINS} admin ang negosyo."
                if problem:
                    st.error(problem)
                else:
                    with tx() as cur:
                        cur.execute(
                            "UPDATE users SET role = %s, active = %s WHERE id = %s AND business_id = %s",
                            (m_role, m_active, int(uid), bid()),
                        )
                        if m_pw:
                            cur.execute(
                                "UPDATE users SET password_hash = %s WHERE id = %s AND business_id = %s",
                                (hash_password(m_pw), int(uid), bid()),
                            )
                    flash("success", "Na-update na ang user.")
                    st.rerun()
        st.caption("Para sa sarili mong password, gamitin ang 🔑 Account tab.")

# ================= ACCOUNT (lahat) =================
with T[TAB_ACC]:
    change_password_ui()