import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime
from kafka_client import consumer
from verifier.verify import verify_commitment, verify_commitment_chain
from verifier.components.sidebar import render_sidebar
from verifier.components.charts import plot_commitment_timeline, plot_verification_rate
from verifier.anomaly_detector import AnomalyDetector

# Page config
st.set_page_config(
    page_title="ZEROAUDIT · Zero-Knowledge Auditor",
    page_icon="🔐",
    layout="wide",
    initial_sidebar_state="expanded"
)

# -------------------- CUSTOM CSS FOR ULTRA-MODERN DARK THEME --------------------
st.markdown("""
<style>
    /* Import Google Fonts */
    @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&display=swap');
    
    /* Global styles */
    html, body, [class*="css"] {
        font-family: 'Space Grotesk', sans-serif;
    }
    
    /* Main header with gradient animation */
    .main-header {
        font-size: 4rem;
        font-weight: 700;
        background: linear-gradient(135deg, #00ff87 0%, #60efff 50%, #0061ff 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-size: 200% 200%;
        animation: gradient 5s ease infinite;
        margin-bottom: 0;
        line-height: 1.2;
    }
    @keyframes gradient {
        0% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
        100% { background-position: 0% 50%; }
    }
    
    /* Subheader with glow */
    .sub-header {
        color: rgba(255,255,255,0.7);
        font-size: 1.2rem;
        letter-spacing: 1px;
        margin-bottom: 2rem;
        text-shadow: 0 0 10px rgba(0,255,135,0.3);
    }
    
    /* Metric cards */
    .metric-card {
        background: rgba(20, 20, 30, 0.8);
        backdrop-filter: blur(10px);
        border-radius: 20px;
        padding: 20px;
        border: 1px solid rgba(255,255,255,0.05);
        box-shadow: 0 8px 32px 0 rgba(0,0,0,0.37);
        transition: transform 0.3s ease, box-shadow 0.3s ease;
    }
    .metric-card:hover {
        transform: translateY(-5px);
        box-shadow: 0 12px 48px 0 rgba(0,255,135,0.2);
        border-color: rgba(0,255,135,0.3);
    }
    .metric-label {
        color: rgba(255,255,255,0.6);
        font-size: 0.9rem;
        text-transform: uppercase;
        letter-spacing: 2px;
    }
    .metric-value {
        font-size: 2.5rem;
        font-weight: 700;
        background: linear-gradient(135deg, #fff, #aaa);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .metric-delta {
        font-size: 0.9rem;
        color: #00ff87;
    }
    
    /* Badges */
    .verified-badge {
        background: linear-gradient(135deg, #00ff87, #00d68f);
        color: black;
        padding: 4px 12px;
        border-radius: 30px;
        font-weight: 600;
        font-size: 0.8rem;
        box-shadow: 0 0 15px rgba(0,255,135,0.5);
    }
    .warning-badge {
        background: linear-gradient(135deg, #ff4b4b, #ff1a1a);
        color: white;
        padding: 4px 12px;
        border-radius: 30px;
        font-weight: 600;
        font-size: 0.8rem;
        box-shadow: 0 0 15px rgba(255,75,75,0.5);
    }
    .anomaly-badge {
        background: linear-gradient(135deg, #ff8c00, #ff5e00);
        color: white;
        padding: 2px 8px;
        border-radius: 20px;
        font-size: 0.7rem;
        font-weight: 600;
    }
    
    /* Table styling */
    .dataframe {
        background: rgba(20,20,30,0.6) !important;
        backdrop-filter: blur(5px);
        border-radius: 20px !important;
        border: 1px solid rgba(255,255,255,0.05) !important;
    }
    .dataframe th {
        background: rgba(0,255,135,0.1) !important;
        color: #00ff87 !important;
        font-weight: 600 !important;
        text-transform: uppercase !important;
        font-size: 0.8rem !important;
        letter-spacing: 1px !important;
        border: none !important;
    }
    .dataframe td {
        color: white !important;
        border: none !important;
        border-bottom: 1px solid rgba(255,255,255,0.05) !important;
    }
    
    /* Section headers */
    .section-header {
        font-size: 1.8rem;
        font-weight: 600;
        background: linear-gradient(135deg, #fff, #aaa);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin: 1rem 0;
        letter-spacing: -0.5px;
    }
    
    /* Pulse animation for live indicator */
    .live-indicator {
        display: inline-block;
        width: 10px;
        height: 10px;
        background: #00ff87;
        border-radius: 50%;
        box-shadow: 0 0 10px #00ff87;
        animation: pulse 2s infinite;
        margin-right: 8px;
    }
    @keyframes pulse {
        0% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.5; transform: scale(1.2); }
        100% { opacity: 1; transform: scale(1); }
    }
    
    /* Footer */
    .footer {
        text-align: center;
        color: rgba(255,255,255,0.4);
        font-size: 0.8rem;
        margin-top: 3rem;
        padding-top: 1rem;
        border-top: 1px solid rgba(255,255,255,0.05);
    }
</style>
""", unsafe_allow_html=True)

# -------------------- INITIALIZATION --------------------
@st.cache_resource
def get_detector():
    return AnomalyDetector()

detector = get_detector()

# -------------------- HEADER SECTION --------------------
col_logo, col_title = st.columns([1, 5])
with col_logo:
    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
    st.image("https://img.icons8.com/fluency/96/lock--v1.png", width=80)
with col_title:
    st.markdown('<p class="main-header">ZEROAUDIT</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Zero-Knowledge Proof Auditor · Prove Compliance · Reveal Nothing</p>', unsafe_allow_html=True)

# Sidebar (already defined)
auto_refresh, time_filter = render_sidebar()

# -------------------- DATA FETCH --------------------
commitments = consumer.get_commitments(limit=100, time_filter=time_filter)

for c in commitments:
    if 'verified' not in c:
        c['verified'] = verify_commitment(c.get('commitment', ''))
chain_ok = verify_commitment_chain(commitments)

# -------------------- LIVE INDICATOR --------------------
st.markdown(f"""
<div style="display: flex; align-items: center; gap: 10px; margin-bottom: 20px;">
    <span class="live-indicator"></span>
    <span style="color: #00ff87; font-weight: 500;">LIVE · Real-time updates</span>
    <span style="color: rgba(255,255,255,0.3); margin-left: auto;">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</span>
</div>
""", unsafe_allow_html=True)

# -------------------- METRICS ROW (CUSTOM CARDS) --------------------
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">Total Commitments</div>
        <div class="metric-value">{len(commitments)}</div>
        <div class="metric-delta">+{len([c for c in commitments if c.get('timestamp', '') > (datetime.now() - pd.Timedelta(minutes=5)).isoformat()])} in last 5m</div>
    </div>
    """, unsafe_allow_html=True)

with col2:
    verified = sum(1 for c in commitments if c.get('verified'))
    pct = f"{verified/len(commitments)*100:.1f}%" if commitments else "0%"
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">Verified</div>
        <div class="metric-value">{verified}</div>
        <div class="metric-delta">{pct} of total</div>
    </div>
    """, unsafe_allow_html=True)

with col3:
    status = "✅ Valid" if chain_ok else "❌ Broken"
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">Chain Integrity</div>
        <div class="metric-value" style="font-size:1.8rem;">{status}</div>
        <div class="metric-delta">Cryptographically sound</div>
    </div>
    """, unsafe_allow_html=True)

with col4:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">Last Update</div>
        <div class="metric-value" style="font-size:1.8rem;">{datetime.now().strftime('%H:%M:%S')}</div>
        <div class="metric-delta">Auto-refresh every 5s</div>
    </div>
    """, unsafe_allow_html=True)

# Manual refresh button (styled)
if st.button("🔄 Refresh Now", use_container_width=True):
    st.rerun()

# -------------------- PRIVACY-PRESERVING INSIGHTS --------------------
st.markdown('<p class="section-header">🔍 Privacy-Preserving Insights</p>', unsafe_allow_html=True)

if len(commitments) > 5:
    anomaly_indices = detector.detect(commitments)
    for i, c in enumerate(commitments):
        c['anomaly'] = i in anomaly_indices

    col_i1, col_i2, col_i3, col_i4 = st.columns(4)
    with col_i1:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">Anomalies Detected</div>
            <div class="metric-value">{len(anomaly_indices)}</div>
            <div class="metric-delta">{len(anomaly_indices)/len(commitments)*100:.1f}% of transactions</div>
        </div>
        """, unsafe_allow_html=True)
    with col_i2:
        accounts = [c.get('account_id') for c in commitments if c.get('account_id')]
        if accounts:
            most_active = pd.Series(accounts).mode()[0]
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-label">Most Active Account</div>
                <div class="metric-value" style="font-size:1.5rem;">{most_active}</div>
                <div class="metric-delta">Highest transaction volume</div>
            </div>
            """, unsafe_allow_html=True)
    with col_i3:
        timestamps = [c.get('timestamp') for c in commitments if c.get('timestamp')]
        if timestamps:
            ts_series = pd.to_datetime(pd.Series(timestamps))
            peak_hour = ts_series.dt.hour.mode()[0]
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-label">Peak Hour</div>
                <div class="metric-value" style="font-size:1.5rem;">{peak_hour}:00</div>
                <div class="metric-delta">Busiest transaction time</div>
            </div>
            """, unsafe_allow_html=True)
    with col_i4:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">Risk Score</div>
            <div class="metric-value" style="font-size:1.5rem;">{len(anomaly_indices)*10}</div>
            <div class="metric-delta">Anomaly-based</div>
        </div>
        """, unsafe_allow_html=True)
else:
    st.info("📊 Collecting more data for insights...")

# -------------------- VISUALIZATIONS --------------------
st.markdown('<p class="section-header">📈 Live Audit Trail</p>', unsafe_allow_html=True)

if commitments:
    col_left, col_right = st.columns([3, 1])
    with col_left:
        fig_timeline = plot_commitment_timeline(commitments)
        if fig_timeline:
            # Apply dark theme to plotly figure
            fig_timeline.update_layout(
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
                font_color='white',
                title_font_color='white'
            )
            st.plotly_chart(fig_timeline, use_container_width=True)
    with col_right:
        fig_pie = plot_verification_rate(commitments)
        if fig_pie:
            fig_pie.update_layout(
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
                font_color='white',
                title_font_color='white'
            )
            st.plotly_chart(fig_pie, use_container_width=True)
else:
    st.info("⏳ No commitments yet. Waiting for transactions...")

# -------------------- TRANSACTIONS TABLE --------------------
st.markdown('<p class="section-header">📋 Verified Commitments (with Anomaly Highlight)</p>', unsafe_allow_html=True)

if commitments:
    df = pd.DataFrame(commitments)
    df['commitment_short'] = df['commitment'].apply(lambda x: x[:16] + '...' if x else 'N/A')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    if 'anomaly' not in df.columns:
        df['anomaly'] = False

    # Add anomaly badge column
    df['anomaly_flag'] = df['anomaly'].apply(lambda x: '<span class="anomaly-badge">⚠️ ANOMALY</span>' if x else '')

    display_cols = ['transaction_id', 'account_id', 'transaction_type', 'timestamp', 'commitment_short', 'verified', 'anomaly_flag']
    display_cols = [col for col in display_cols if col in df.columns]

    # Convert verified to badge
    df['verified_badge'] = df['verified'].apply(lambda x: '<span class="verified-badge">VERIFIED</span>' if x else '<span class="warning-badge">UNVERIFIED</span>')

    # Replace verified column with badge
    if 'verified' in display_cols:
        display_cols[display_cols.index('verified')] = 'verified_badge'

    # Render as HTML for custom styling
    html_table = df[display_cols].to_html(escape=False, index=False)
    st.markdown(f"""
    <div style="overflow-x: auto; border-radius: 20px; background: rgba(20,20,30,0.6); backdrop-filter: blur(5px); padding: 10px;">
        {html_table}
    </div>
    """, unsafe_allow_html=True)
else:
    st.info("No commitments to display.")

# -------------------- FOOTER --------------------
st.markdown("""
<div class="footer">
    ZEROAUDIT · Mathematical Proof · Zero Data Exposure · Series-A Grade RegTech<br>
    Built with 🔐 by Team INFERNO · MIT Chromepet
</div>
""", unsafe_allow_html=True)