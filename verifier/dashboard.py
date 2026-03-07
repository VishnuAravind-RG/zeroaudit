import streamlit as st
import pandas as pd
from verifier.kafka_client import consumer
from verifier.verifier import verify_commitment, verify_commitment_chain
from verifier.components.sidebar import render_sidebar
from verifier.components.charts import plot_commitment_timeline, plot_verification_rate

# Page config
st.set_page_config(
    page_title="ZEROAUDIT Auditor",
    page_icon="🔐",
    layout="wide"
)

# Custom CSS for dark theme
st.markdown("""
<style>
    .main-header {
        font-size: 3rem;
        font-weight: 700;
        background: linear-gradient(90deg, #00FF87 0%, #60EFFF 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0px;
    }
    .sub-header {
        color: #888;
        font-size: 1.2rem;
        margin-bottom: 2rem;
    }
    .stat-card {
        background: #1E1E1E;
        border-radius: 10px;
        padding: 20px;
        border-left: 4px solid #00FF87;
    }
    .verified-badge {
        background: #00FF87;
        color: black;
        padding: 2px 10px;
        border-radius: 20px;
        font-weight: bold;
    }
    .warning-badge {
        background: #FF4B4B;
        color: white;
        padding: 2px 10px;
        border-radius: 20px;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

# Header
st.markdown('<p class="main-header">ZEROAUDIT</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">Zero-Knowledge Proof Auditor • Prove Compliance • Reveal Nothing</p>', unsafe_allow_html=True)

# Sidebar
auto_refresh, time_filter = render_sidebar()

# Fetch commitments
commitments = consumer.get_commitments(limit=100, time_filter=time_filter)

# Verify each commitment (add 'verified' flag if not present)
for c in commitments:
    if 'verified' not in c:
        c['verified'] = verify_commitment(c.get('commitment', ''))

# Overall chain integrity
chain_ok = verify_commitment_chain(commitments)

# Metrics row
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Total Commitments", len(commitments))
with col2:
    verified = sum(1 for c in commitments if c.get('verified'))
    st.metric("Verified", verified, delta=f"{verified/len(commitments)*100:.1f}%" if commitments else "0%")
with col3:
    st.metric("Chain Integrity", "✅ Valid" if chain_ok else "❌ Broken")
with col4:
    st.metric("Last Update", pd.Timestamp.now().strftime("%H:%M:%S"))

# Visualizations
if commitments:
    col_left, col_right = st.columns([3, 1])
    with col_left:
        fig_timeline = plot_commitment_timeline(commitments)
        if fig_timeline:
            st.plotly_chart(fig_timeline, use_container_width=True)
    with col_right:
        fig_pie = plot_verification_rate(commitments)
        if fig_pie:
            st.plotly_chart(fig_pie, use_container_width=True)
else:
    st.info("No commitments found. Waiting for transactions...")

# Transactions table
st.markdown("---")
st.markdown("### 📋 Verified Commitments")

if commitments:
    df = pd.DataFrame(commitments)
    # Format columns
    df['commitment_short'] = df['commitment'].apply(lambda x: x[:16] + '...' if x else 'N/A')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # Apply styling
    def highlight_verified(val):
        color = '#00FF87' if val else '#FF4B4B'
        return f'background-color: {color}; color: black'
    
    styled_df = df[['transaction_id', 'timestamp', 'commitment_short', 'verified']].style.applymap(
        highlight_verified, subset=['verified']
    )
    st.dataframe(styled_df, use_container_width=True)
else:
    st.info("No commitments to display.")

# Footer
st.markdown("---")
st.markdown(
    "<div style='text-align: center; color: #888;'>"
    "ZEROAUDIT • Mathematical Proof • Zero Data Exposure • Series-A Grade RegTech"
    "</div>",
    unsafe_allow_html=True
)