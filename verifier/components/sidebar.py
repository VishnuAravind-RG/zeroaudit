import streamlit as st

def render_sidebar():
    """Render the auditor dashboard sidebar."""
    with st.sidebar:
        st.image("https://via.placeholder.com/150x150.png?text=ZEROAUDIT", width=150)
        st.markdown("## Auditor Console")
        
        # Auto-refresh option
        auto_refresh = st.checkbox("Auto-refresh (5s)", value=True)
        if auto_refresh:
            st.experimental_rerun()
        
        st.markdown("---")
        st.markdown("### Filters")
        time_filter = st.selectbox(
            "Time Range",
            ["Last 5 minutes", "Last hour", "Last 24 hours", "All time"]
        )
        
        st.markdown("---")
        st.markdown("### System Status")
        st.markdown("🟢 PostgreSQL: **Connected**")
        st.markdown("🟢 Kafka: **Connected**")
        st.markdown("🟢 Prover API: **Running**")
        
        return auto_refresh, time_filter