import plotly.graph_objects as go
import pandas as pd
import streamlit as st

def plot_commitment_timeline(commitments):
    """Create a timeline scatter plot of commitments."""
    if not commitments:
        return None
    
    df = pd.DataFrame(commitments)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp')
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df['timestamp'],
        y=[1] * len(df),
        mode='markers+lines',
        marker=dict(
            size=12,
            color='#00FF87' if all(df['verified']) else '#FF4B4B',
            symbol='circle'
        ),
        line=dict(color='rgba(0,255,135,0.3)', width=1),
        text=df['transaction_id'],
        hovertemplate='<b>Transaction:</b> %{text}<br><b>Time:</b> %{x}<br>'
    ))
    
    fig.update_layout(
        title="Commitment Timeline",
        xaxis_title="Time",
        yaxis=dict(showticklabels=False, showgrid=False),
        height=200,
        margin=dict(l=0, r=0, t=30, b=0),
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)'
    )
    return fig

def plot_verification_rate(commitments):
    """Pie chart of verified vs unverified."""
    if not commitments:
        return None
    df = pd.DataFrame(commitments)
    verified_count = df['verified'].sum()
    unverified_count = len(df) - verified_count
    
    fig = go.Figure(data=[go.Pie(
        labels=['Verified', 'Unverified'],
        values=[verified_count, unverified_count],
        marker_colors=['#00FF87', '#FF4B4B'],
        hole=0.6
    )])
    fig.update_layout(
        title="Verification Status",
        height=250,
        margin=dict(l=20, r=20, t=40, b=20),
        showlegend=False
    )
    return fig