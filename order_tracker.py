import streamlit as st

st.title("📦 Business Order Tracker")
st.write("Isang simpleng web app para sa pamamahala ng mga order.")

# Gamitin ang session_state para hindi mawala ang data habang ginagamit
if 'orders' not in st.session_state:
    st.session_state.orders = []

# Section 1: Magdagdag ng Bagong Order
st.header("1. Magdagdag ng Bagong Order")
with st.form("order_form"):
    customer_name = st.text_input("Pangalan ng Customer")
    item_name = st.text_input("Anong Inorder")
    submit_button = st.form_submit_button("I-save ang Order")
    
    if submit_button:
        if customer_name and item_name:
            st.session_state.orders.append({
                "name": customer_name,
                "item": item_name,
                "status": "Pending"
            })
            st.success(f"Na-save na ang order para kay {customer_name}!")
        else:
            st.warning("Paki-fill up ang lahat ng Detalye.")

# Section 2: Tingnan ang Listahan ng Orders
st.header("2. Listahan ng mga Orders")
if len(st.session_state.orders) == 0:
    st.info("Wala pang nakikitang orders sa ngayon.")
else:
    for i, order in enumerate(st.session_state.orders, 1):
        st.write(f"**{i}. Customer:** {order['name']} | **Item:** {order['item']} | **Status:** `{order['status']}`")

# Section 3: Baguhin ang Status
if len(st.session_state.orders) > 0:
    st.header("3. Baguhin ang Status ng Order")
    order_index = st.selectbox("Piliin ang Order na I-update", range(len(st.session_state.orders)), format_func=lambda x: f"{x+1}. {st.session_state.orders[x]['name']} - {st.session_state.orders[x]['item']}")
    
    new_status = st.selectbox("Bagong Status", ["Pending", "Packed", "Out for Delivery", "Delivered"])
    
    if st.button("I-update ang Status"):
        st.session_state.orders[order_index]['status'] = new_status
        st.success("Na-update na ang status ng order!")
        st.rerun()