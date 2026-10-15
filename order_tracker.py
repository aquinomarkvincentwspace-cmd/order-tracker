import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime

st.set_page_config(page_title="Advanced Business Order Tracker", page_icon="📊", layout="wide")

# --- 1. SETUP DATABASE (SQLite) ---
def init_db():
    conn = sqlite3.connect("orders.db", check_same_thread=False)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            customer TEXT,
            item TEXT,
            price REAL,
            status TEXT
        )
    ''')
    conn.commit()
    return conn

conn = init_db()

# --- 2. LOGIN SYSTEM (Authentication) ---
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

def check_login():
    username = st.session_state.get("user", "")
    password = st.session_state.get("pass", "")
    # Pwede mong palitan dito ang username at password mo
    if username == "admin" and password == "business123":
        st.session_state.authenticated = True
    else:
        st.error("Maling Username o Password!")

if not st.session_state.authenticated:
    st.title("🔒 Mag-login sa Order Tracker")
    with st.form("login_form"):
        st.text_input("Username", key="user")
        st.text_input("Password", type="password", key="pass")
        st.form_submit_button("Mag-login", on_click=check_login)
    st.stop() # Pigilan munang lumabas ang app hanggang hindi naka-login

# Kung naka-login na:
st.sidebar.success("Naka-login bilang Admin")
if st.sidebar.button("Mag-logout"):
    st.session_state.authenticated = False
    st.rerun()

# --- 3. MAIN APP INTERFACE ---
st.title("📊 Propesyonal na Business Order Tracker & Analytics")
st.write("Kumpletong sistema na may database, login security, at visual sales analytics.")

# Kuhanin ang lahat ng data mula sa SQLite database
def load_data():
    return pd.read_sql("SELECT * FROM orders", conn)

df_orders = load_data()

# --- SECTION A: MAGDAGDAG NG BAGONG ORDER ---
st.header("1. Magdagdag ng Bagong Order")
with st.form("order_form", clear_on_submit=True):
    col1, col2 = st.columns(2)
    with col1:
        customer_name = st.text_input("Pangalan ng Customer")
    with col2:
        item_name = st.text_input("Anong Inorder")
        
    price = st.number_input("Presyo (₱)", min_value=0.0, format="%.2f")
    submit_button = st.form_submit_button("I-save ang Order sa Database")
    
    if submit_button:
        if customer_name and item_name:
            c = conn.cursor()
            c.execute("INSERT INTO orders (date, customer, item, price, status) VALUES (?, ?, ?, ?, ?)",
                      (datetime.now().strftime("%Y-%m-%d %H:%M"), customer_name, item_name, price, "Pending"))
            conn.commit()
            st.success(f"Na-save na ang order para kay {customer_name}!")
            st.rerun()
        else:
            st.warning("Paki-fill up ang Pangalan ng Customer at Inorder.")

if not df_orders.empty:
    # --- SECTION B: DASHBOARD & METRICS ---
    st.divider()
    total_orders = len(df_orders)
    total_sales = df_orders['Price'].sum()
    
    col_m1, col_m2 = st.columns(2)
    col_m1.metric("Kabuuang Bilang ng Orders", total_orders)
    col_m2.metric("Kabuuang Benta (Total Sales)", f"₱{total_sales:,.2f}")
    st.divider()

    # --- SECTION C: VISUAL SALES ANALYTICS (CHARTS) ---
    st.header("2. Sales Analytics & Charts")
    if 'Price' in df_orders.columns and len(df_orders) > 0:
        chart_data = df_orders.groupby('item')['Price'].sum().reset_index()
        st.subheader("Benta bawat Produkto / Item")
        st.bar_chart(chart_data.set_index('item'))

    # --- SECTION D: FILTER AT LISTAHAN NG MGA ORDERS ---
    st.header("3. Listahan ng mga Orders at Filter")
    
    filter_status = st.selectbox("I-filter ayon sa Status", ["Lahat", "Pending", "Packed", "Out for Delivery", "Delivered"])
    
    if filter_status != "Lahat":
        filtered_df = df_orders[df_orders['status'] == filter_status]
    else:
        filtered_df = df_orders

    st.dataframe(filtered_df, use_container_width=True)

    # --- SECTION E: BAGUHIN ANG STATUS ---
    st.header("4. Baguhin ang Status ng Order")
    order_ids = df_orders['id'].tolist()
    selected_id = st.selectbox(
        "Piliin ang Order ID na I-update", 
        order_ids,
        format_func=lambda x: f"ID {x}: {df_orders[df_orders['id'] == x]['customer'].values[0]} - {df_orders[df_orders['id'] == x]['item'].values[0]}"
    )
    
    new_status = st.selectbox("Bagong Status", ["Pending", "Packed", "Out for Delivery", "Delivered"])
    
    if st.button("I-update ang Status"):
        c = conn.cursor()
        c.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, selected_id))
        conn.commit()
        st.success("Na-update na ang status ng order sa database!")
        st.rerun()

    # --- SECTION F: DOWNLOAD EXCEL / CSV BUTTON ---
    st.divider()
    st.header("5. I-download ang mga Rekord")
    csv_data = df_orders.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 I-download ang Orders bilang CSV (Excel Compatible)",
        data=csv_data,
        file_name="business_orders_report.csv",
        mime="text/csv",
    )
else:
    st.info("Wala pang nakikitang orders sa database. Magdagdag sa itaas para magsimula!")