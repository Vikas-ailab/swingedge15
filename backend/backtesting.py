"""Historical walk-forward backtest for SwingEdge 15.
Signals are computed at close t; entry is next session open to avoid look-ahead.
If target and stop are both touched in one daily bar, stop is conservatively assumed first.
"""
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import yfinance as yf


def _rsi(close, period=14):
    d = close.diff()
    gain = d.clip(lower=0).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _signals(df):
    close, high, low, volume = df.Close, df.High, df.Low, df.Volume
    sma20, sma50 = close.rolling(20).mean(), close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    ema12, ema26 = close.ewm(span=12, adjust=False).mean(), close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    tr = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    rv = volume / volume.rolling(20).mean().replace(0, np.nan)
    prior20 = high.shift(1).rolling(20).max()
    rsi = _rsi(close)
    # Weighted score aligned with the app's intended strategy; only past/current data used.
    score = (close.gt(sma20)*10 + sma20.gt(sma50)*10 + close.gt(sma200)*8 +
             macd.gt(macd_signal)*10 + rsi.between(50,70)*10 + rv.ge(1.2)*10 +
             close.pct_change(20).gt(0)*8 + close.ge(prior20*.97)*8 +
             (close.pct_change(60) > 0)*8 + (close.pct_change(20) > close.pct_change(60))*8)
    # require adequate history, avoid NaN-induced false signals
    valid = sma200.notna() & atr.notna() & rsi.notna() & rv.notna() & prior20.notna()
    return score.where(valid), atr


def run_symbol_backtest(symbol, period='5y', target_pct=10.0, horizon=15, min_score=68, cost_bps=30):
    symbol = str(symbol).upper().replace('.NS','').strip()
    hist = yf.Ticker(symbol + '.NS').history(period=period, interval='1d', auto_adjust=True, actions=False)
    if hist is None or hist.empty or len(hist) < 260:
        return {'symbol': symbol, 'error': 'Insufficient historical daily data (need about 1 year or more).'}
    hist = hist[['Open','High','Low','Close','Volume']].dropna().copy()
    scores, atr = _signals(hist)
    # split chronologically: final 30% of signal dates are out-of-sample; earlier dates are development.
    split_pos = int(len(hist) * .70)
    trades = []
    for i in range(210, len(hist)-horizon-1):
        score = scores.iloc[i]
        if pd.isna(score) or float(score) < min_score:
            continue
        entry_i = i + 1
        entry = float(hist.Open.iloc[entry_i])
        if not np.isfinite(entry) or entry <= 0 or pd.isna(atr.iloc[i]):
            continue
        stop = max(entry - 2.0*float(atr.iloc[i]), 0.01)
        target = entry * (1 + target_pct/100)
        outcome, exit_price, exit_i = 'time_exit', float(hist.Close.iloc[entry_i+horizon]), entry_i+horizon
        for j in range(entry_i, entry_i+horizon+1):
            hit_stop = float(hist.Low.iloc[j]) <= stop
            hit_target = float(hist.High.iloc[j]) >= target
            if hit_stop and hit_target:
                outcome, exit_price, exit_i = 'stop_ambiguous_conservative', stop, j
                break
            if hit_stop:
                outcome, exit_price, exit_i = 'stop', stop, j
                break
            if hit_target:
                outcome, exit_price, exit_i = 'target', target, j
                break
        gross = (exit_price/entry - 1)*100
        net = gross - (2*cost_bps/100) # round-trip cost in bps -> percentage points
        trades.append({'symbol':symbol,'signal_date':str(hist.index[i].date()),'entry_date':str(hist.index[entry_i].date()),
                       'entry':round(entry,2),'target':round(target,2),'stop':round(stop,2),'exit_date':str(hist.index[exit_i].date()),
                       'outcome':outcome,'gross_return_pct':round(gross,2),'net_return_pct':round(net,2),'holding_sessions':int(exit_i-entry_i+1),
                       'score':int(score),'sample':'development' if i < split_pos else 'out_of_sample'})
    def metrics(rows):
        if not rows: return {'trades':0,'target_hit_rate_pct':None,'avg_net_return_pct':None,'median_net_return_pct':None,'profit_factor':None,'max_drawdown_pct':None,'avg_holding_sessions':None}
        rets = np.array([x['net_return_pct'] for x in rows], dtype=float)
        wins = rets[rets > 0]; losses = rets[rets < 0]
        equity = np.cumprod(1 + rets/100)
        peak = np.maximum.accumulate(equity)
        dd = (equity/peak-1)*100
        return {'trades':len(rows),'target_hit_rate_pct':round(100*sum(x['outcome']=='target' for x in rows)/len(rows),2),
                'avg_net_return_pct':round(float(np.mean(rets)),3),'median_net_return_pct':round(float(np.median(rets)),3),
                'profit_factor':round(float(wins.sum()/abs(losses.sum())),3) if len(losses) and len(wins) else (None if not len(wins) else 'infinite_no_losses'),
                'max_drawdown_pct':round(float(dd.min()),2),
                'avg_holding_sessions':round(float(np.mean([x.get('holding_sessions', 0) for x in rows])),1)}
    dev = [t for t in trades if t['sample']=='development']
    oos = [t for t in trades if t['sample']=='out_of_sample']
    return {'symbol':symbol,'data_start':str(hist.index[0].date()),'data_end':str(hist.index[-1].date()),'target_pct':target_pct,'horizon_sessions':horizon,
            'minimum_score':min_score,'round_trip_cost_bps':cost_bps,'development':metrics(dev),'out_of_sample':metrics(oos),
            'out_of_sample_trades':oos,'disclaimer':'Historical results are not predictive guarantees. Daily bars cannot reveal intraday target/stop order; ambiguous bars are counted as stop-first.'}


def summarize_portfolio(results):
    trades=[]
    for r in results:
        if not r.get('error'): trades.extend(r.get('out_of_sample_trades',[]))
    if not trades: return {'trades':0,'note':'No out-of-sample trades generated; no probability estimate available.'}
    rets=np.array([t['net_return_pct'] for t in trades],dtype=float)
    targets=sum(t['outcome']=='target' for t in trades)
    wins=rets[rets>0]; losses=rets[rets<0]
    return {'trades':len(trades),'target_hit_rate_pct':round(targets/len(trades)*100,2),
            'target_hit_rate_95ci_pct':[round(max(0,(targets/len(trades))-1.96*np.sqrt((targets/len(trades))*(1-targets/len(trades))/len(trades)))*100,2),
                                        round(min(1,(targets/len(trades))+1.96*np.sqrt((targets/len(trades))*(1-targets/len(trades))/len(trades)))*100,2)],
            'avg_net_return_pct':round(float(rets.mean()),3),'profit_factor':round(float(wins.sum()/abs(losses.sum())),3) if len(losses) and len(wins) else None,
            'warning':'Pooled trades across symbols may overlap and are not independent. Use a portfolio-level position-sizing simulation before deployment.'}
