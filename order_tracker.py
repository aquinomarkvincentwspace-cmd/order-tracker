import streamlit as st
import pandas as pd
from datetime import datetime

st.set_page_config(page_title="Business Order Tracker", page_icon="📦", layout="centered")

st.title("📦 Propesyonal na Business Order Tracker")
st.write("Pamahalaan ang mga order, subaybayan ang kabuuang benta, at i-download ang iyong mga rekord.")

# I-initialize ang session state para sa mga orders
if 'orders' not in st.session_state:
    st.session_state.orders = []

# --- SECTION 1: MAGDAGDAG NG BAGONG ORDER ---
st.header("1. Magdagdag ng Bagong Order")
with st.form("order_form", clear_on_submit=True):
    col1, col2 = st.columns(2)
    with col1:
        customer_name = st.text_input("Pangalan ng Customer")
    with col2:
        item_name = st.text_input("Anong Inorder")
        
    price = st.number_input("Presyo (₱)", min_value=0.0, format="%.2f")
    submit_button = st.form_submit_button("I-save ang Order")
    
    if submit_button:
        if customer_name and item_name:
            st.session_state.orders.append({
                "Date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "Customer": customer_name,
                "Item": item_name,
                "Price": price,
                "Status": "Pending"
            })
            st.success(f"Tagumpay! Na-save na ang order para kay {customer_name}.")
        else:
            st.warning("Paki-fill up ang Pangalan ng Customer at Inorder.")

# Kung walang orders, itigil muna rito ang display
if len(st.session_state.orders) > 0:
    df_orders = pd.DataFrame(st.session_state.orders)

    # --- SECTION 2: DASHBOARD & METRICS ---
    st.divider()
    total_orders = len(df_orders)
    total_sales = df_orders['Price'].sum()
    
    col_m1, col_m2 = st.columns(2)
    col_m1.metric("Kabuuang Bilang ng Orders", total_orders)
    col_m2.metric("Kabuuang Benta (Total Sales)", f"₱{total_sales:,.2f}")
    st.divider()

    # --- SECTION 3: FILTER AT LISTAHAN NG MGA ORDERS ---
    st.header("2. Listahan ng mga Orders at Filter")
    
    filter_status = st.selectbox("I-filter ayon sa Status", ["Lahat", "Pending", "Packed", "Out for Delivery", "Delivered"])
    
    if filter_status != "Lahat":
        filtered_df = df_orders[df_orders['Status'] == filter_status]
    else:
        filtered_df = df_orders

    st.dataframe(filtered_df, use_container_width=True)

    # --- SECTION 4: BAGUHIN ANG STATUS ---
    st.header("3. Baguhin ang Status ng Order")
    order_index = st.selectbox(
        "Piliin ang Order na I-update", 
        range(len(st.session_state.orders)), 
        format_func=lambda x: f"{x+1}. {st.session_state.orders[x]['Customer']} - {st.session_state.orders[x]['Item']} ({st.session_state.orders[x]['Status']})"
    )
    
    new_status = st.selectbox("Bagong Status", ["Pending", "Packed", "Out for Delivery", "Delivered"])
    
    if st.button("I-update ang Status"):
        st.session_state.orders[order_index]['Status'] = new_status
        st.success("Na-update na ang status ng order!")
        st.rerun()

    # --- SECTION 5: DOWNLOAD EXCEL / CSV BUTTON ---
    st.divider()
    st.header("4. I-download ang mga Rekord")
    csv_data = df_orders.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 I-download ang Orders bilang CSV (Excel Compatible)",
        data=csv_data,
        file_name="business_orders_report.csv",
        mime="text/csv",
    )
else:
    st.info("Wala pang nakikitang orders. Magdagdag sa itaas para magsimula!")