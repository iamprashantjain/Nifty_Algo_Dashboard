import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime
import json
import scipy.stats as stats
import warnings
import paramiko
import sqlite3
import tempfile
import os
warnings.filterwarnings('ignore')

# Set page config
st.set_page_config(
    page_title="Trading Dashboard",
    layout="wide",
    initial_sidebar_state="expanded"
)

INITIAL_CAPITAL = 1000000

# Custom CSS
st.markdown("""
    <style>
    .big-font { font-size: 20px !important; font-weight: bold; }
    .metric-card { background-color: #f0f2f6; border-radius: 10px; padding: 15px; margin: 10px 0; }
    </style>
""", unsafe_allow_html=True)

def calculate_tax_and_charges(option_entry_price, option_exit_price, quantity):
    """Calculate all charges for a single trade"""
    gross_pnl = (option_exit_price - option_entry_price) * quantity
    turnover = (option_entry_price + option_exit_price) * quantity
    
    brokerage = 40  # ₹20 per trade x 2
    stt = (option_exit_price * quantity) * 0.0005  # 0.05% on sell side
    exchange_txn = turnover * 0.00053  # 0.053%
    sebi_charges = turnover * 0.000001  # ₹10 per crore
    gst_chargeable = brokerage + exchange_txn + sebi_charges
    gst = gst_chargeable * 0.18  # 18% GST
    stamp_duty = turnover * 0.000003  # 0.0003%
    
    total_charges = brokerage + stt + exchange_txn + gst + sebi_charges + stamp_duty
    net_pnl = gross_pnl - total_charges
    
    return {
        'gross_pnl': gross_pnl,
        'turnover': turnover,
        'brokerage': brokerage,
        'stt': stt,
        'exchange_txn': exchange_txn,
        'gst': gst,
        'sebi_charges': sebi_charges,
        'stamp_duty': stamp_duty,
        'total_charges': total_charges,
        'net_pnl': net_pnl
    }

def recalculate_trade(row):
    """Recalculate from meta column"""
    try:
        if isinstance(row['meta'], str):
            meta = json.loads(row['meta'])
        else:
            meta = row['meta']
        
        entry_price = meta.get('option_entry_price', 0)
        exit_price = meta.get('option_exit_price', 0)
        quantity = row['quantity']
        
        costs = calculate_tax_and_charges(entry_price, exit_price, quantity)
        
        row['option_entry_price'] = entry_price
        row['option_exit_price'] = exit_price
        row['gross_pnl'] = costs['gross_pnl']
        row['turnover'] = costs['turnover']
        row['brokerage'] = costs['brokerage']
        row['stt'] = costs['stt']
        row['exchange_txn'] = costs['exchange_txn']
        row['gst'] = costs['gst']
        row['sebi_charges'] = costs['sebi_charges']
        row['stamp_duty'] = costs['stamp_duty']
        row['total_charges'] = costs['total_charges']
        row['net_pnl'] = costs['net_pnl']
        
        return row
    except:
        row['net_pnl'] = 0
        return row

def max_consecutive_losses(returns):
    is_loss = returns < 0
    max_streak = 0
    current_streak = 0
    for loss in is_loss:
        if loss:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0
    return max_streak

def calculate_sharpe_ratio(returns, rf_rate=0.05):
    if len(returns) < 2 or returns.std() == 0:
        return 0
    excess_returns = returns - rf_rate/252
    return np.sqrt(252) * excess_returns.mean() / returns.std()

def calculate_sortino_ratio(returns, rf_rate=0.05):
    if len(returns) < 2:
        return 0
    excess_returns = returns - rf_rate/252
    downside_returns = returns[returns < 0]
    if len(downside_returns) == 0 or downside_returns.std() == 0:
        return 0
    return np.sqrt(252) * excess_returns.mean() / downside_returns.std()

def calculate_calmar_ratio(returns, max_dd):
    if max_dd == 0:
        return 0
    annual_return = returns.mean() * 252
    return annual_return / abs(max_dd)

def calculate_win_loss_metrics(wins, losses):
    avg_win = wins.mean() if len(wins) > 0 else 0
    avg_loss = abs(losses.mean()) if len(losses) > 0 else 0
    win_loss_ratio = avg_win / avg_loss if avg_loss != 0 else np.inf
    return avg_win, avg_loss, win_loss_ratio

def calculate_ulcer_index(equity_curve):
    running_max = equity_curve.expanding().max()
    drawdown_pct = (equity_curve - running_max) / running_max * 100
    return np.sqrt((drawdown_pct ** 2).mean())

def calculate_var(returns, confidence=0.95):
    return np.percentile(returns, (1-confidence)*100)

def calculate_cvar(returns, confidence=0.95):
    var = calculate_var(returns, confidence)
    return returns[returns <= var].mean()

def monte_carlo_projection(trades, n_simulations=10000, n_future_trades=100):
    np.random.seed(42)
    simulations = []
    for i in range(n_simulations):
        sampled_trades = np.random.choice(trades, size=n_future_trades, replace=True)
        cumulative = np.cumsum(sampled_trades)
        simulations.append(cumulative)
    sim_array = np.array(simulations)
    median = np.median(sim_array[:, -1])
    p95 = np.percentile(sim_array[:, -1], 95)
    p05 = np.percentile(sim_array[:, -1], 5)
    return {'median': median, 'optimistic': p95, 'pessimistic': p05, 'all_sims': sim_array}

# Load data
def read_db_directly():
    config = {'host': '80.225.228.224','username': 'ubuntu','private_key': r'D:\NIFTY_Options_21ema_strategy\21ema_strategy_v2_Current_Working_Dec2025\oracle_key\ssh-key-2025-12-20.key',}
    remote_db_path = '/home/ubuntu/final_trading_logs.db'    
    try:
        private_key = paramiko.RSAKey.from_private_key_file(config['private_key'])
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname=config['host'],username=config['username'],pkey=private_key)
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as temp_file:
            temp_path = temp_file.name
        sftp = ssh.open_sftp()
        sftp.get(remote_db_path, temp_path)
        sftp.close()
        conn = sqlite3.connect(temp_path)
        signal_df = pd.read_sql_query("SELECT * FROM trading_logs", conn)
        conn.close()
        os.unlink(temp_path)
        ssh.close()
        return signal_df
    except Exception as e:
        print(f"Error: {e}")
        return None

# Load and process data
with st.spinner("Loading and recalculating trades..."):
    oracle_df = read_db_directly()
    oracle_df['source'] = "cloud"

    # local_df = pd.read_sql_query("SELECT * FROM trading_logs", sqlite3.connect('final_trading_logs.db'))
    # local_df['source'] = "local"

    # final_df = pd.concat([oracle_df, local_df], ignore_index=True)
    final_df = oracle_df.copy()
    
    # RECALCULATE ALL TRADES FROM META
    final_df = final_df.apply(recalculate_trade, axis=1)
    
    # Basic preprocessing
    final_df['entry_time'] = pd.to_datetime(final_df['entry_time'])
    final_df['exit_time'] = pd.to_datetime(final_df['exit_time'])
    final_df['entry_date'] = final_df['entry_time'].dt.date
    final_df['entry_hour'] = final_df['entry_time'].dt.hour
    final_df['duration_min'] = (final_df['exit_time'] - final_df['entry_time']).dt.total_seconds() / 60
    final_df['weekday'] = final_df['entry_time'].dt.day_name()
    final_df['instrument'] = final_df['symbol'].apply(lambda x: 'BANKNIFTY' if 'BANKNIFTY' in str(x).upper() else 'NIFTY' if 'NIFTY' in str(x).upper() else str(x))
    final_df['option_type'] = final_df['symbol'].apply(lambda x: 'CE' if 'CE' in str(x).upper() else 'PE')
    final_df['is_win'] = final_df['net_pnl'] > 0
    
    # Extract exit_reason from meta
    def get_exit_reason(row):
        try:
            if isinstance(row['meta'], str):
                meta = json.loads(row['meta'])
            else:
                meta = row['meta']
            return meta.get('exit_reason', 'Unknown')
        except:
            return 'Unknown'
    
    final_df['exit_reason'] = final_df.apply(get_exit_reason, axis=1)
    
    # Remove rows after 2 stoploss hit
    final_df = pd.concat([group if len(group[group['exit_reason'].str.contains('Stoploss Hit', case=False, na=False)]) < 2 else group.loc[group[group['exit_reason'].str.contains('Stoploss Hit', case=False, na=False)].head(2).index] for date, group in final_df.groupby('entry_date')]).sort_index()
    
    final_df = final_df.sort_values(['entry_date', 'entry_time']).reset_index(drop=True)
    final_df = final_df[final_df['net_pnl'].notna()]
    final_df = final_df[final_df['instrument'] == 'NIFTY']
    final_df['cumulative_pnl'] = final_df['net_pnl'].cumsum()
    final_df['running_max'] = final_df['cumulative_pnl'].cummax()
    final_df['drawdown'] = final_df['cumulative_pnl'] - final_df['running_max']
    final_df['drawdown_pct'] = (final_df['drawdown'] / INITIAL_CAPITAL) * 100

if final_df.empty:
    st.error("No trade data available.")
    st.stop()

# Sidebar filters
with st.sidebar:
    st.markdown("### Filters")
    min_date = final_df['entry_date'].min()
    max_date = final_df['entry_date'].max()
    date_range = st.date_input("Date Range", value=(min_date, max_date), min_value=min_date, max_value=max_date)
    instruments = st.multiselect("Instrument", options=final_df['instrument'].unique(), default=final_df['instrument'].unique())
    option_types = st.multiselect("Option Type", options=['CE', 'PE'], default=['CE', 'PE'])
    st.markdown("---")
    st.caption(f"Total trades: {len(final_df)}")
    st.caption(f"Date range: {min_date} to {max_date}")

if len(date_range) == 2:
    start_date, end_date = date_range
else:
    start_date, end_date = min_date, max_date

# Apply filters
mask = (final_df['entry_date'] >= start_date) & (final_df['entry_date'] <= end_date) & (final_df['instrument'].isin(instruments)) & (final_df['option_type'].isin(option_types))
fd = final_df[mask].copy()

if fd.empty:
    st.warning("No trades in selected filter.")
    st.stop()

# Calculate daily aggregates
daily = fd.groupby('entry_date')['net_pnl'].sum().sort_index()
equity_curve = INITIAL_CAPITAL + daily.cumsum()
running_max = equity_curve.expanding().max()
drawdown = equity_curve - running_max
max_dd = min(fd.groupby('entry_date')['net_pnl'].sum()) if len(daily) > 0 else 0
max_dd_pct = (max_dd / INITIAL_CAPITAL) * 100

# Calculate metrics
daily_returns = daily / INITIAL_CAPITAL
sharpe = calculate_sharpe_ratio(daily_returns)
sortino = calculate_sortino_ratio(daily_returns)
calmar = calculate_calmar_ratio(daily_returns, max_dd_pct/100)
ulcer = calculate_ulcer_index(equity_curve)

wins = fd[fd['net_pnl'] > 0]['net_pnl']
losses = fd[fd['net_pnl'] < 0]['net_pnl']
avg_win, avg_loss, win_loss_ratio = calculate_win_loss_metrics(wins, losses)
var_95 = calculate_var(daily_returns) * INITIAL_CAPITAL
cvar_95 = calculate_cvar(daily_returns) * INITIAL_CAPITAL

# DASHBOARD
st.title("Trading Dashboard")
st.markdown(f"*Analysis Period: {start_date} to {end_date}*")

# TAX BREAKDOWN SECTION
st.markdown("---")
st.markdown("## 📊 P&L Breakdown After All Taxes & Charges")

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Gross P&L (Before Tax)", f"₹{fd['gross_pnl'].sum():,.0f}")
with col2:
    st.metric("Total Charges", f"₹{fd['total_charges'].sum():,.0f}", delta=f"{(fd['total_charges'].sum()/fd['gross_pnl'].sum()*100 if fd['gross_pnl'].sum() != 0 else 0):.1f}%")
with col3:
    st.metric("Net P&L (After Tax)", f"₹{fd['net_pnl'].sum():,.0f}")
with col4:
    st.metric("Effective Tax Rate", f"{(fd['total_charges'].sum()/fd['gross_pnl'].sum()*100 if fd['gross_pnl'].sum() != 0 else 0):.1f}%")

# Detailed tax breakdown table
tax_breakdown = pd.DataFrame({
    'Component': ['Gross P&L', 'Brokerage', 'STT', 'Exchange Transaction', 'GST', 'SEBI Charges', 'Stamp Duty', 'TOTAL CHARGES', 'NET P&L'],
    'Amount (₹)': [
        fd['gross_pnl'].sum(),
        fd['brokerage'].sum(),
        fd['stt'].sum(),
        fd['exchange_txn'].sum(),
        fd['gst'].sum(),
        fd['sebi_charges'].sum(),
        fd['stamp_duty'].sum(),
        fd['total_charges'].sum(),
        fd['net_pnl'].sum()
    ]
})
st.dataframe(tax_breakdown, hide_index=True, use_container_width=True)

# Monte Carlo Simulation
st.markdown("---")
st.markdown("## 🎲 Profit Prediction (Monte Carlo Simulation)")
trades = fd['net_pnl'].values
if len(trades) >= 5:
    projection = monte_carlo_projection(trades)
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Median Case", f"₹{projection['median']:,.0f}")
    with col2:
        st.metric("Optimistic (95%)", f"₹{projection['optimistic']:,.0f}")
    with col3:
        st.metric("Pessimistic (5%)", f"₹{projection['pessimistic']:,.0f}")
    with col4:
        prob_profit = (projection['all_sims'][:, -1] > 0).mean() * 100
        st.metric("Profit Probability", f"{prob_profit:.1f}%")
    
    fig = go.Figure()
    fig.add_trace(go.Histogram(x=projection['all_sims'][:, -1], nbinsx=50, marker_color='#2E86AB'))
    fig.add_vline(x=0, line_dash="dash", line_color="gray")
    fig.add_vline(x=projection['median'], line_dash="dash", line_color="green")
    fig.update_layout(title=f"Distribution of Next 100 Trades P&L", xaxis_title="Total P&L (₹)", yaxis_title="Frequency", template="plotly_white", height=400)
    st.plotly_chart(fig, use_container_width=True)

# Key Metrics Row
st.markdown("---")
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.markdown("### Performance")
    total_pnl = fd['net_pnl'].sum()
    return_pct = (total_pnl / INITIAL_CAPITAL) * 100
    st.metric("Net P&L", f"₹{total_pnl:,.0f}", f"{return_pct:.2f}%")
    trade_count = len(fd)
    st.metric("Total Trades", f"{trade_count}")
    win_rate = (len(wins) / trade_count * 100) if trade_count > 0 else 0
    st.metric("Win Rate", f"{win_rate:.1f}%")

with col2:
    st.markdown("### Risk Metrics")
    st.metric("Sharpe Ratio", f"{sharpe:.2f}")
    st.metric("Sortino Ratio", f"{sortino:.2f}")
    st.metric("Calmar Ratio", f"{calmar:.2f}")

with col3:
    st.markdown("### Profitability")
    profit_factor = wins.sum() / abs(losses.sum()) if len(losses) > 0 else np.inf
    st.metric("Profit Factor", f"{profit_factor:.2f}")
    expectancy = fd['net_pnl'].mean()
    st.metric("Expectancy/Trade", f"₹{expectancy:,.0f}")
    st.metric("Avg Win / Avg Loss", f"{win_loss_ratio:.2f}")

with col4:
    st.markdown("### Drawdown")
    st.metric("Max Drawdown (₹)", f"₹{max_dd:,.0f}")
    st.metric("Max Drawdown (%)", f"{max_dd_pct:.2f}%")
    st.metric("Ulcer Index", f"{ulcer:.2f}")

# Cumulative Profit Chart
st.markdown("---")
st.markdown("## 📈 Cumulative Profit Growth")
cumulative_profit = fd['net_pnl'].cumsum()
fig = go.Figure()
fig.add_trace(go.Scatter(x=fd['entry_time'], y=cumulative_profit, mode='lines+markers', name='Cumulative Profit', line=dict(color='#2ECC71', width=3), marker=dict(size=8, color=['#2ECC71' if x > 0 else '#E74C3C' for x in fd['net_pnl']])))
fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
fig.update_layout(title="Cumulative Profit Growth (₹)", xaxis_title="Trade Timeline", yaxis_title="Cumulative Profit (₹)", template="plotly_white", height=500)
st.plotly_chart(fig, use_container_width=True)

# Trade Distribution
st.markdown("---")
st.markdown("## 📊 Trade Distribution Analysis")
col1, col2 = st.columns(2)
with col1:
    fig_dist = go.Figure()
    fig_dist.add_trace(go.Histogram(x=fd['net_pnl'], nbinsx=50, name='P&L Distribution', marker_color='#2E86AB', opacity=0.7))
    mu, std = fd['net_pnl'].mean(), fd['net_pnl'].std()
    x_range = np.linspace(fd['net_pnl'].min(), fd['net_pnl'].max(), 100)
    y_range = stats.norm.pdf(x_range, mu, std) * len(fd) * (fd['net_pnl'].max() - fd['net_pnl'].min()) / 50
    fig_dist.add_trace(go.Scatter(x=x_range, y=y_range, mode='lines', name='Normal Distribution', line=dict(color='red', dash='dash')))
    fig_dist.update_layout(title=f"P&L Distribution (Skewness: {fd['net_pnl'].skew():.2f})", xaxis_title="P&L (₹)", yaxis_title="Frequency", template="plotly_white", height=400)
    st.plotly_chart(fig_dist, use_container_width=True)

with col2:
    win_loss_stats = pd.DataFrame({
        'Metric': ['Count', 'Total', 'Average', 'Max', 'Min'],
        'Wins': [len(wins), f"₹{wins.sum():,.0f}", f"₹{wins.mean():,.0f}", f"₹{wins.max():,.0f}", f"₹{wins.min():,.0f}"] if len(wins) > 0 else ['-']*5,
        'Losses': [len(losses), f"₹{losses.sum():,.0f}", f"₹{losses.mean():,.0f}", f"₹{losses.max():,.0f}", f"₹{losses.min():,.0f}"] if len(losses) > 0 else ['-']*5
    })
    st.markdown("### Win/Loss Statistics")
    st.dataframe(win_loss_stats, hide_index=True, use_container_width=True)
    
    fd['win_streak'] = (fd['is_win'] != fd['is_win'].shift()).cumsum()
    streak_stats = fd.groupby(['is_win', 'win_streak']).size().reset_index(name='streak_length')
    max_win_streak = streak_stats[streak_stats['is_win'] == True]['streak_length'].max() if True in streak_stats['is_win'].values else 0
    max_loss_streak = streak_stats[streak_stats['is_win'] == False]['streak_length'].max() if False in streak_stats['is_win'].values else 0
    col3, col4 = st.columns(2)
    with col3:
        st.metric("Max Win Streak", f"{max_win_streak}")
    with col4:
        st.metric("Max Loss Streak", f"{max_loss_streak}")

# Time Analysis
st.markdown("---")
st.markdown("## ⏰ Time Analysis")
col1, col2 = st.columns(2)
with col1:
    hourly = fd.groupby('entry_hour').agg({'net_pnl': 'sum', 'is_win': 'mean'}).round(2)
    hourly['Win Rate'] = hourly['is_win'] * 100
    
    fig_hourly = go.Figure()
    fig_hourly.add_trace(go.Bar(
        x=hourly.index, 
        y=hourly['net_pnl'], 
        name='Total P&L',
        marker_color=['#2ECC71' if x > 0 else '#E74C3C' for x in hourly['net_pnl']]
    ))
    fig_hourly.add_trace(go.Scatter(
        x=hourly.index, 
        y=hourly['Win Rate'], 
        name='Win Rate %',
        yaxis='y2',
        line=dict(color='#F39C12', width=3)
    ))
    
    fig_hourly.update_layout(
        title="Hourly Performance",
        xaxis_title="Hour of Day",
        yaxis=dict(title="Total P&L (₹)", tickformat=",.0f"),
        yaxis2=dict(title="Win Rate %", overlaying='y', side='right', range=[0, 100]),
        template="plotly_white", 
        height=400
    )
    st.plotly_chart(fig_hourly, use_container_width=True)

with col2:
    weekly_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    weekly = fd.groupby('weekday').agg({'net_pnl': ['sum', 'mean', 'count'], 'is_win': 'mean'}).round(2)
    weekly.columns = ['Total P&L', 'Avg P&L', 'Trades', 'Win Rate']
    weekly['Win Rate'] *= 100
    weekly = weekly.reindex(weekly_order)
    st.dataframe(weekly, use_container_width=True)

# Exit Reason Analysis
if 'exit_reason' in fd.columns:
    st.markdown("---")
    st.markdown("## 🚪 Exit Reason Analysis")
    col1, col2 = st.columns(2)
    with col1:
        exit_analysis = fd.groupby('exit_reason').agg({'net_pnl': ['count', 'sum', 'mean'], 'is_win': 'mean'}).round(2)
        exit_analysis.columns = ['Trades', 'Total P&L', 'Avg P&L', 'Win Rate']
        exit_analysis['Win Rate'] *= 100
        st.dataframe(exit_analysis, use_container_width=True)
    with col2:
        exit_counts = fd['exit_reason'].value_counts()
        fig_exit = go.Figure(data=[go.Pie(labels=exit_counts.index, values=exit_counts.values, hole=0.4)])
        fig_exit.update_layout(title="Exit Reasons Distribution", height=400)
        st.plotly_chart(fig_exit, use_container_width=True)

# Detailed Trade Log
with st.expander("📋 Detailed Trade Log"):
    display_cols = ['entry_time', 'symbol', 'option_type', 'quantity', 'option_entry_price', 'option_exit_price', 'gross_pnl', 'brokerage', 'stt', 'exchange_txn', 'gst', 'total_charges', 'net_pnl', 'duration_min', 'exit_reason']
    display_cols = [col for col in display_cols if col in fd.columns]
    log_df = fd[display_cols].copy()
    log_df['entry_time'] = log_df['entry_time'].dt.strftime('%Y-%m-%d %H:%M')
    for col in ['gross_pnl', 'net_pnl', 'brokerage', 'stt', 'exchange_txn', 'gst', 'total_charges']:
        if col in log_df.columns:
            log_df[col] = log_df[col].apply(lambda x: f"₹{x:,.0f}")
    st.dataframe(log_df, use_container_width=True, height=400)
    
    if st.button("Export to CSV"):
        csv = log_df.to_csv(index=False)
        st.download_button(label="Download CSV", data=csv, file_name=f"trades_{start_date}_{end_date}.csv", mime="text/csv")

# Footer
st.markdown("---")
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.markdown(f"**Total Trades:** {len(fd)}")
    st.markdown(f"**Winning Trades:** {len(wins)} ({win_rate:.1f}%)")
with col2:
    st.markdown(f"**Gross Profit:** ₹{wins.sum():,.0f}")
    st.markdown(f"**Gross Loss:** ₹{losses.sum():,.0f}")
with col3:
    st.markdown(f"**Profit Factor:** {profit_factor:.2f}")
    st.markdown(f"**Expectancy:** ₹{expectancy:,.0f}")
with col4:
    if 'duration_min' in fd.columns:
        avg_hold = fd['duration_min'].mean()
        st.markdown(f"**Avg Hold Time:** {avg_hold:.1f} min")





# ==========================================
# add market context for existing rows
# add live market context in new rows from code




# import streamlit as st
# import pandas as pd
# import numpy as np
# import plotly.graph_objects as go
# import plotly.express as px
# from datetime import datetime
# import json
# import scipy.stats as stats
# import warnings
# import paramiko
# import sqlite3
# import tempfile
# import os
# warnings.filterwarnings('ignore')

# # Set page config
# st.set_page_config(
#     page_title="Trading Dashboard",
#     layout="wide",
#     initial_sidebar_state="expanded"
# )

# INITIAL_CAPITAL = 1000000

# # Custom CSS
# st.markdown("""
#     <style>
#     .big-font { font-size: 20px !important; font-weight: bold; }
#     .metric-card { background-color: #f0f2f6; border-radius: 10px; padding: 15px; margin: 10px 0; }
#     </style>
# """, unsafe_allow_html=True)

# def calculate_tax_and_charges(option_entry_price, option_exit_price, quantity):
#     """Calculate all charges for a single trade"""
#     gross_pnl = (option_exit_price - option_entry_price) * quantity
#     turnover = (option_entry_price + option_exit_price) * quantity
    
#     brokerage = 40  # ₹20 per trade x 2
#     stt = (option_exit_price * quantity) * 0.0005  # 0.05% on sell side
#     exchange_txn = turnover * 0.00053  # 0.053%
#     sebi_charges = turnover * 0.000001  # ₹10 per crore
#     gst_chargeable = brokerage + exchange_txn + sebi_charges
#     gst = gst_chargeable * 0.18  # 18% GST
#     stamp_duty = turnover * 0.000003  # 0.0003%
    
#     total_charges = brokerage + stt + exchange_txn + gst + sebi_charges + stamp_duty
#     net_pnl = gross_pnl - total_charges
    
#     return {
#         'gross_pnl': gross_pnl,
#         'turnover': turnover,
#         'brokerage': brokerage,
#         'stt': stt,
#         'exchange_txn': exchange_txn,
#         'gst': gst,
#         'sebi_charges': sebi_charges,
#         'stamp_duty': stamp_duty,
#         'total_charges': total_charges,
#         'net_pnl': net_pnl
#     }

# def recalculate_trade(row):
#     """Recalculate from meta column"""
#     try:
#         if isinstance(row['meta'], str):
#             meta = json.loads(row['meta'])
#         else:
#             meta = row['meta']
        
#         entry_price = meta.get('option_entry_price', 0)
#         exit_price = meta.get('option_exit_price', 0)
#         quantity = row['quantity']
        
#         costs = calculate_tax_and_charges(entry_price, exit_price, quantity)
        
#         row['option_entry_price'] = entry_price
#         row['option_exit_price'] = exit_price
#         row['gross_pnl'] = costs['gross_pnl']
#         row['turnover'] = costs['turnover']
#         row['brokerage'] = costs['brokerage']
#         row['stt'] = costs['stt']
#         row['exchange_txn'] = costs['exchange_txn']
#         row['gst'] = costs['gst']
#         row['sebi_charges'] = costs['sebi_charges']
#         row['stamp_duty'] = costs['stamp_duty']
#         row['total_charges'] = costs['total_charges']
#         row['net_pnl'] = costs['net_pnl']
        
#         return row
#     except:
#         row['net_pnl'] = 0
#         return row

# def max_consecutive_losses(returns):
#     is_loss = returns < 0
#     max_streak = 0
#     current_streak = 0
#     for loss in is_loss:
#         if loss:
#             current_streak += 1
#             max_streak = max(max_streak, current_streak)
#         else:
#             current_streak = 0
#     return max_streak

# def calculate_sharpe_ratio(returns, rf_rate=0.05):
#     if len(returns) < 2 or returns.std() == 0:
#         return 0
#     excess_returns = returns - rf_rate/252
#     return np.sqrt(252) * excess_returns.mean() / returns.std()

# def calculate_sortino_ratio(returns, rf_rate=0.05):
#     if len(returns) < 2:
#         return 0
#     excess_returns = returns - rf_rate/252
#     downside_returns = returns[returns < 0]
#     if len(downside_returns) == 0 or downside_returns.std() == 0:
#         return 0
#     return np.sqrt(252) * excess_returns.mean() / downside_returns.std()

# def calculate_calmar_ratio(returns, max_dd):
#     if max_dd == 0:
#         return 0
#     annual_return = returns.mean() * 252
#     return annual_return / abs(max_dd)

# def calculate_win_loss_metrics(wins, losses):
#     avg_win = wins.mean() if len(wins) > 0 else 0
#     avg_loss = abs(losses.mean()) if len(losses) > 0 else 0
#     win_loss_ratio = avg_win / avg_loss if avg_loss != 0 else np.inf
#     return avg_win, avg_loss, win_loss_ratio

# def calculate_ulcer_index(equity_curve):
#     running_max = equity_curve.expanding().max()
#     drawdown_pct = (equity_curve - running_max) / running_max * 100
#     return np.sqrt((drawdown_pct ** 2).mean())

# def calculate_var(returns, confidence=0.95):
#     return np.percentile(returns, (1-confidence)*100)

# def calculate_cvar(returns, confidence=0.95):
#     var = calculate_var(returns, confidence)
#     return returns[returns <= var].mean()

# def monte_carlo_projection(trades, n_simulations=10000, n_future_trades=100):
#     np.random.seed(42)
#     simulations = []
#     for i in range(n_simulations):
#         sampled_trades = np.random.choice(trades, size=n_future_trades, replace=True)
#         cumulative = np.cumsum(sampled_trades)
#         simulations.append(cumulative)
#     sim_array = np.array(simulations)
#     median = np.median(sim_array[:, -1])
#     p95 = np.percentile(sim_array[:, -1], 95)
#     p05 = np.percentile(sim_array[:, -1], 5)
#     return {'median': median, 'optimistic': p95, 'pessimistic': p05, 'all_sims': sim_array}

# # Load data
# def read_db_directly():
#     config = {'host': '80.225.228.224','username': 'ubuntu','private_key': r'D:\NIFTY_Options_21ema_strategy\21ema_strategy_v2_Current_Working_Dec2025\oracle_key\ssh-key-2025-12-20.key',}
#     remote_db_path = '/home/ubuntu/final_trading_logs.db'    
#     try:
#         private_key = paramiko.RSAKey.from_private_key_file(config['private_key'])
#         ssh = paramiko.SSHClient()
#         ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
#         ssh.connect(hostname=config['host'],username=config['username'],pkey=private_key)
#         with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as temp_file:
#             temp_path = temp_file.name
#         sftp = ssh.open_sftp()
#         sftp.get(remote_db_path, temp_path)
#         sftp.close()
#         conn = sqlite3.connect(temp_path)
#         signal_df = pd.read_sql_query("SELECT * FROM trading_logs", conn)
#         conn.close()
#         os.unlink(temp_path)
#         ssh.close()
#         return signal_df
#     except Exception as e:
#         print(f"Error: {e}")
#         return None

# # Load and process data
# with st.spinner("Loading and recalculating trades..."):
#     oracle_df = read_db_directly()
#     oracle_df['source'] = "cloud"

#     # local_df = pd.read_sql_query("SELECT * FROM trading_logs", sqlite3.connect('final_trading_logs.db'))
#     # local_df['source'] = "local"

#     # final_df = pd.concat([oracle_df, local_df], ignore_index=True)
#     final_df = oracle_df.copy()

#     print("===============")
#     print(final_df.columns)
#     print(final_df['entry_time'])

    
#     # RECALCULATE ALL TRADES FROM META
#     final_df = final_df.apply(recalculate_trade, axis=1)
    
#     # Basic preprocessing
#     final_df['entry_time'] = pd.to_datetime(final_df['entry_time'])
#     final_df['exit_time'] = pd.to_datetime(final_df['exit_time'])
#     final_df['entry_date'] = final_df['entry_time'].dt.date
#     final_df['entry_hour'] = final_df['entry_time'].dt.hour
#     final_df['duration_min'] = (final_df['exit_time'] - final_df['entry_time']).dt.total_seconds() / 60
#     final_df['weekday'] = final_df['entry_time'].dt.day_name()
#     final_df['instrument'] = final_df['symbol'].apply(lambda x: 'BANKNIFTY' if 'BANKNIFTY' in str(x).upper() else 'NIFTY' if 'NIFTY' in str(x).upper() else str(x))
#     final_df['option_type'] = final_df['symbol'].apply(lambda x: 'CE' if 'CE' in str(x).upper() else 'PE')
#     final_df['is_win'] = final_df['net_pnl'] > 0
    
#     # Extract exit_reason from meta
#     def get_exit_reason(row):
#         try:
#             if isinstance(row['meta'], str):
#                 meta = json.loads(row['meta'])
#             else:
#                 meta = row['meta']
#             return meta.get('exit_reason', 'Unknown')
#         except:
#             return 'Unknown'
    
#     final_df['exit_reason'] = final_df.apply(get_exit_reason, axis=1)
    
#     # Remove rows after 2 stoploss hit
#     final_df = pd.concat([group if len(group[group['exit_reason'].str.contains('Stoploss Hit', case=False, na=False)]) < 2 else group.loc[group[group['exit_reason'].str.contains('Stoploss Hit', case=False, na=False)].head(2).index] for date, group in final_df.groupby('entry_date')]).sort_index()
    
#     final_df = final_df.sort_values(['entry_date', 'entry_time']).reset_index(drop=True)
#     final_df = final_df[final_df['net_pnl'].notna()]
#     final_df = final_df[final_df['instrument'] == 'NIFTY']
#     final_df['cumulative_pnl'] = final_df['net_pnl'].cumsum()
#     final_df['running_max'] = final_df['cumulative_pnl'].cummax()
#     final_df['drawdown'] = final_df['cumulative_pnl'] - final_df['running_max']
#     final_df['drawdown_pct'] = (final_df['drawdown'] / INITIAL_CAPITAL) * 100

# if final_df.empty:
#     st.error("No trade data available.")
#     st.stop()

# # Sidebar filters
# with st.sidebar:
#     st.markdown("### Filters")
#     min_date = final_df['entry_date'].min()
#     max_date = final_df['entry_date'].max()
#     date_range = st.date_input("Date Range", value=(min_date, max_date), min_value=min_date, max_value=max_date)
#     instruments = st.multiselect("Instrument", options=final_df['instrument'].unique(), default=final_df['instrument'].unique())
#     option_types = st.multiselect("Option Type", options=['CE', 'PE'], default=['CE', 'PE'])
#     st.markdown("---")
#     st.caption(f"Total trades: {len(final_df)}")
#     st.caption(f"Date range: {min_date} to {max_date}")

# if len(date_range) == 2:
#     start_date, end_date = date_range
# else:
#     start_date, end_date = min_date, max_date

# # Apply filters
# mask = (final_df['entry_date'] >= start_date) & (final_df['entry_date'] <= end_date) & (final_df['instrument'].isin(instruments)) & (final_df['option_type'].isin(option_types))
# fd = final_df[mask].copy()

# if fd.empty:
#     st.warning("No trades in selected filter.")
#     st.stop()

# # Calculate daily aggregates
# daily = fd.groupby('entry_date')['net_pnl'].sum().sort_index()
# equity_curve = INITIAL_CAPITAL + daily.cumsum()
# running_max = equity_curve.expanding().max()
# drawdown = equity_curve - running_max
# max_dd = min(fd.groupby('entry_date')['net_pnl'].sum()) if len(daily) > 0 else 0
# max_dd_pct = (max_dd / INITIAL_CAPITAL) * 100

# # Calculate metrics
# daily_returns = daily / INITIAL_CAPITAL
# sharpe = calculate_sharpe_ratio(daily_returns)
# sortino = calculate_sortino_ratio(daily_returns)
# calmar = calculate_calmar_ratio(daily_returns, max_dd_pct/100)
# ulcer = calculate_ulcer_index(equity_curve)

# wins = fd[fd['net_pnl'] > 0]['net_pnl']
# losses = fd[fd['net_pnl'] < 0]['net_pnl']
# avg_win, avg_loss, win_loss_ratio = calculate_win_loss_metrics(wins, losses)
# var_95 = calculate_var(daily_returns) * INITIAL_CAPITAL
# cvar_95 = calculate_cvar(daily_returns) * INITIAL_CAPITAL

# # DASHBOARD
# st.title("Trading Dashboard")
# st.markdown(f"*Analysis Period: {start_date} to {end_date}*")

# # TAX BREAKDOWN SECTION
# st.markdown("---")
# st.markdown("## 📊 P&L Breakdown After All Taxes & Charges")

# col1, col2, col3, col4 = st.columns(4)
# with col1:
#     st.metric("Gross P&L (Before Tax)", f"₹{fd['gross_pnl'].sum():,.0f}")
# with col2:
#     st.metric("Total Charges", f"₹{fd['total_charges'].sum():,.0f}", delta=f"{(fd['total_charges'].sum()/fd['gross_pnl'].sum()*100 if fd['gross_pnl'].sum() != 0 else 0):.1f}%")
# with col3:
#     st.metric("Net P&L (After Tax)", f"₹{fd['net_pnl'].sum():,.0f}")
# with col4:
#     st.metric("Effective Tax Rate", f"{(fd['total_charges'].sum()/fd['gross_pnl'].sum()*100 if fd['gross_pnl'].sum() != 0 else 0):.1f}%")

# # Detailed tax breakdown table
# tax_breakdown = pd.DataFrame({
#     'Component': ['Gross P&L', 'Brokerage', 'STT', 'Exchange Transaction', 'GST', 'SEBI Charges', 'Stamp Duty', 'TOTAL CHARGES', 'NET P&L'],
#     'Amount (₹)': [
#         fd['gross_pnl'].sum(),
#         fd['brokerage'].sum(),
#         fd['stt'].sum(),
#         fd['exchange_txn'].sum(),
#         fd['gst'].sum(),
#         fd['sebi_charges'].sum(),
#         fd['stamp_duty'].sum(),
#         fd['total_charges'].sum(),
#         fd['net_pnl'].sum()
#     ]
# })
# st.dataframe(tax_breakdown, hide_index=True, use_container_width=True)

# # Monte Carlo Simulation
# st.markdown("---")
# st.markdown("## 🎲 Profit Prediction (Monte Carlo Simulation)")
# trades = fd['net_pnl'].values
# if len(trades) >= 5:
#     projection = monte_carlo_projection(trades)
#     col1, col2, col3, col4 = st.columns(4)
#     with col1:
#         st.metric("Median Case", f"₹{projection['median']:,.0f}")
#     with col2:
#         st.metric("Optimistic (95%)", f"₹{projection['optimistic']:,.0f}")
#     with col3:
#         st.metric("Pessimistic (5%)", f"₹{projection['pessimistic']:,.0f}")
#     with col4:
#         prob_profit = (projection['all_sims'][:, -1] > 0).mean() * 100
#         st.metric("Profit Probability", f"{prob_profit:.1f}%")
    
#     fig = go.Figure()
#     fig.add_trace(go.Histogram(x=projection['all_sims'][:, -1], nbinsx=50, marker_color='#2E86AB'))
#     fig.add_vline(x=0, line_dash="dash", line_color="gray")
#     fig.add_vline(x=projection['median'], line_dash="dash", line_color="green")
#     fig.update_layout(title=f"Distribution of Next 100 Trades P&L", xaxis_title="Total P&L (₹)", yaxis_title="Frequency", template="plotly_white", height=400)
#     st.plotly_chart(fig, use_container_width=True)

# # Key Metrics Row
# st.markdown("---")
# col1, col2, col3, col4 = st.columns(4)
# with col1:
#     st.markdown("### Performance")
#     total_pnl = fd['net_pnl'].sum()
#     return_pct = (total_pnl / INITIAL_CAPITAL) * 100
#     st.metric("Net P&L", f"₹{total_pnl:,.0f}", f"{return_pct:.2f}%")
#     trade_count = len(fd)
#     st.metric("Total Trades", f"{trade_count}")
#     win_rate = (len(wins) / trade_count * 100) if trade_count > 0 else 0
#     st.metric("Win Rate", f"{win_rate:.1f}%")

# with col2:
#     st.markdown("### Risk Metrics")
#     st.metric("Sharpe Ratio", f"{sharpe:.2f}")
#     st.metric("Sortino Ratio", f"{sortino:.2f}")
#     st.metric("Calmar Ratio", f"{calmar:.2f}")

# with col3:
#     st.markdown("### Profitability")
#     profit_factor = wins.sum() / abs(losses.sum()) if len(losses) > 0 else np.inf
#     st.metric("Profit Factor", f"{profit_factor:.2f}")
#     expectancy = fd['net_pnl'].mean()
#     st.metric("Expectancy/Trade", f"₹{expectancy:,.0f}")
#     st.metric("Avg Win / Avg Loss", f"{win_loss_ratio:.2f}")

# with col4:
#     st.markdown("### Drawdown")
#     st.metric("Max Drawdown (₹)", f"₹{max_dd:,.0f}")
#     st.metric("Max Drawdown (%)", f"{max_dd_pct:.2f}%")
#     st.metric("Ulcer Index", f"{ulcer:.2f}")

# # Cumulative Profit Chart
# st.markdown("---")
# st.markdown("## 📈 Cumulative Profit Growth")
# cumulative_profit = fd['net_pnl'].cumsum()
# fig = go.Figure()
# fig.add_trace(go.Scatter(x=fd['entry_time'], y=cumulative_profit, mode='lines+markers', name='Cumulative Profit', line=dict(color='#2ECC71', width=3), marker=dict(size=8, color=['#2ECC71' if x > 0 else '#E74C3C' for x in fd['net_pnl']])))
# fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
# fig.update_layout(title="Cumulative Profit Growth (₹)", xaxis_title="Trade Timeline", yaxis_title="Cumulative Profit (₹)", template="plotly_white", height=500)
# st.plotly_chart(fig, use_container_width=True)

# # Trade Distribution
# st.markdown("---")
# st.markdown("## 📊 Trade Distribution Analysis")
# col1, col2 = st.columns(2)
# with col1:
#     fig_dist = go.Figure()
#     fig_dist.add_trace(go.Histogram(x=fd['net_pnl'], nbinsx=50, name='P&L Distribution', marker_color='#2E86AB', opacity=0.7))
#     mu, std = fd['net_pnl'].mean(), fd['net_pnl'].std()
#     x_range = np.linspace(fd['net_pnl'].min(), fd['net_pnl'].max(), 100)
#     y_range = stats.norm.pdf(x_range, mu, std) * len(fd) * (fd['net_pnl'].max() - fd['net_pnl'].min()) / 50
#     fig_dist.add_trace(go.Scatter(x=x_range, y=y_range, mode='lines', name='Normal Distribution', line=dict(color='red', dash='dash')))
#     fig_dist.update_layout(title=f"P&L Distribution (Skewness: {fd['net_pnl'].skew():.2f})", xaxis_title="P&L (₹)", yaxis_title="Frequency", template="plotly_white", height=400)
#     st.plotly_chart(fig_dist, use_container_width=True)

# with col2:
#     win_loss_stats = pd.DataFrame({
#         'Metric': ['Count', 'Total', 'Average', 'Max', 'Min'],
#         'Wins': [len(wins), f"₹{wins.sum():,.0f}", f"₹{wins.mean():,.0f}", f"₹{wins.max():,.0f}", f"₹{wins.min():,.0f}"] if len(wins) > 0 else ['-']*5,
#         'Losses': [len(losses), f"₹{losses.sum():,.0f}", f"₹{losses.mean():,.0f}", f"₹{losses.max():,.0f}", f"₹{losses.min():,.0f}"] if len(losses) > 0 else ['-']*5
#     })
#     st.markdown("### Win/Loss Statistics")
#     st.dataframe(win_loss_stats, hide_index=True, use_container_width=True)
    
#     fd['win_streak'] = (fd['is_win'] != fd['is_win'].shift()).cumsum()
#     streak_stats = fd.groupby(['is_win', 'win_streak']).size().reset_index(name='streak_length')
#     max_win_streak = streak_stats[streak_stats['is_win'] == True]['streak_length'].max() if True in streak_stats['is_win'].values else 0
#     max_loss_streak = streak_stats[streak_stats['is_win'] == False]['streak_length'].max() if False in streak_stats['is_win'].values else 0
#     col3, col4 = st.columns(2)
#     with col3:
#         st.metric("Max Win Streak", f"{max_win_streak}")
#     with col4:
#         st.metric("Max Loss Streak", f"{max_loss_streak}")

# # Time Analysis
# st.markdown("---")
# st.markdown("## ⏰ Time Analysis")
# col1, col2 = st.columns(2)
# with col1:
#     hourly = fd.groupby('entry_hour').agg({'net_pnl': 'sum', 'is_win': 'mean'}).round(2)
#     hourly['Win Rate'] = hourly['is_win'] * 100
    
#     fig_hourly = go.Figure()
#     fig_hourly.add_trace(go.Bar(
#         x=hourly.index, 
#         y=hourly['net_pnl'], 
#         name='Total P&L',
#         marker_color=['#2ECC71' if x > 0 else '#E74C3C' for x in hourly['net_pnl']]
#     ))
#     fig_hourly.add_trace(go.Scatter(
#         x=hourly.index, 
#         y=hourly['Win Rate'], 
#         name='Win Rate %',
#         yaxis='y2',
#         line=dict(color='#F39C12', width=3)
#     ))
    
#     fig_hourly.update_layout(
#         title="Hourly Performance",
#         xaxis_title="Hour of Day",
#         yaxis=dict(title="Total P&L (₹)", tickformat=",.0f"),
#         yaxis2=dict(title="Win Rate %", overlaying='y', side='right', range=[0, 100]),
#         template="plotly_white", 
#         height=400
#     )
#     st.plotly_chart(fig_hourly, use_container_width=True)

# with col2:
#     weekly_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
#     weekly = fd.groupby('weekday').agg({'net_pnl': ['sum', 'mean', 'count'], 'is_win': 'mean'}).round(2)
#     weekly.columns = ['Total P&L', 'Avg P&L', 'Trades', 'Win Rate']
#     weekly['Win Rate'] *= 100
#     weekly = weekly.reindex(weekly_order)
#     st.dataframe(weekly, use_container_width=True)

# # Exit Reason Analysis
# if 'exit_reason' in fd.columns:
#     st.markdown("---")
#     st.markdown("## 🚪 Exit Reason Analysis")
#     col1, col2 = st.columns(2)
#     with col1:
#         exit_analysis = fd.groupby('exit_reason').agg({'net_pnl': ['count', 'sum', 'mean'], 'is_win': 'mean'}).round(2)
#         exit_analysis.columns = ['Trades', 'Total P&L', 'Avg P&L', 'Win Rate']
#         exit_analysis['Win Rate'] *= 100
#         st.dataframe(exit_analysis, use_container_width=True)
#     with col2:
#         exit_counts = fd['exit_reason'].value_counts()
#         fig_exit = go.Figure(data=[go.Pie(labels=exit_counts.index, values=exit_counts.values, hole=0.4)])
#         fig_exit.update_layout(title="Exit Reasons Distribution", height=400)
#         st.plotly_chart(fig_exit, use_container_width=True)

# # Detailed Trade Log
# with st.expander("📋 Detailed Trade Log"):
#     display_cols = ['entry_time', 'symbol', 'option_type', 'quantity', 'option_entry_price', 'option_exit_price', 'gross_pnl', 'brokerage', 'stt', 'exchange_txn', 'gst', 'total_charges', 'net_pnl', 'duration_min', 'exit_reason']
#     display_cols = [col for col in display_cols if col in fd.columns]
#     log_df = fd[display_cols].copy()
#     log_df['entry_time'] = log_df['entry_time'].dt.strftime('%Y-%m-%d %H:%M')
#     for col in ['gross_pnl', 'net_pnl', 'brokerage', 'stt', 'exchange_txn', 'gst', 'total_charges']:
#         if col in log_df.columns:
#             log_df[col] = log_df[col].apply(lambda x: f"₹{x:,.0f}")
#     st.dataframe(log_df, use_container_width=True, height=400)
    
#     if st.button("Export to CSV"):
#         csv = log_df.to_csv(index=False)
#         st.download_button(label="Download CSV", data=csv, file_name=f"trades_{start_date}_{end_date}.csv", mime="text/csv")

# # Footer
# st.markdown("---")
# col1, col2, col3, col4 = st.columns(4)
# with col1:
#     st.markdown(f"**Total Trades:** {len(fd)}")
#     st.markdown(f"**Winning Trades:** {len(wins)} ({win_rate:.1f}%)")
# with col2:
#     st.markdown(f"**Gross Profit:** ₹{wins.sum():,.0f}")
#     st.markdown(f"**Gross Loss:** ₹{losses.sum():,.0f}")
# with col3:
#     st.markdown(f"**Profit Factor:** {profit_factor:.2f}")
#     st.markdown(f"**Expectancy:** ₹{expectancy:,.0f}")
# with col4:
#     if 'duration_min' in fd.columns:
#         avg_hold = fd['duration_min'].mean()
#         st.markdown(f"**Avg Hold Time:** {avg_hold:.1f} min")