from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
import numpy as np
import yfinance as yf
import requests
from datetime import datetime, timezone
from typing import Optional

app = FastAPI(title="SwingEdge 15 API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict to your deployed frontend domain before production.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

UNIVERSE_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
_universe_cache = {"at": None, "symbols": []}

def get_universe():
    now = datetime.now(timezone.utc)
    if _universe_cache["at"] and (now - _universe_cache["at"]).total_seconds() < 86400 and _universe_cache["symbols"]:
        return _universe_cache["symbols"]
    try:
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"}
        r = requests.get(UNIVERSE_URL, headers=headers, timeout=12)
        r.raise_for_status()
        df = pd.read_csv(pd.io.common.StringIO(r.text))
        col = next((c for c in df.columns if c.strip().lower() in ("symbol", "index symbol")), None)
        if col:
            symbols = sorted(set(df[col].dropna().astype(str).str.strip().str.upper()))
            _universe_cache.update(at=now, symbols=symbols)
            return symbols
    except Exception:
        pass
    # Small fallback list so the app remains usable if the constituents endpoint is unavailable.
    return ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "LT", "SBIN", "BHARTIARTL", "ITC", "AXISBANK", "TATAMOTORS", "SUNPHARMA", "MARUTI", "TRENT", "BEL"]

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def analyze_symbol(symbol: str, include_fundamentals: bool = False):
    symbol = symbol.strip().upper().replace(".NS", "")
    if not symbol or not symbol.replace("-", "").replace("&", "").isalnum():
        raise HTTPException(400, "Enter a valid NSE symbol.")
    ticker = yf.Ticker(symbol + ".NS")
    try:
        hist = ticker.history(period="1y", interval="1d", auto_adjust=True)
    except Exception as e:
        raise HTTPException(502, f"Price provider error for {symbol}: {e}")
    if hist is None or hist.empty or len(hist) < 60:
        raise HTTPException(404, f"Insufficient daily history for {symbol}. Verify the NSE symbol.")
    close = hist["Close"].dropna()
    high, low, volume = hist["High"], hist["Low"], hist["Volume"]
    sma20, sma50, sma200 = close.rolling(20).mean(), close.rolling(50).mean(), close.rolling(200).mean()
    delta = close.diff()
    ema12, ema26 = close.ewm(span=12, adjust=False).mean(), close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    tr = pd.concat([(high-low), (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    up, down = high.diff(), -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr_smooth = tr.ewm(alpha=1/14, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1/14, adjust=False).mean() / atr_smooth.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1/14, adjust=False).mean() / atr_smooth.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1/14, adjust=False).mean()
    avgvol = volume.rolling(20).mean()
    latest = float(close.iloc[-1])
    previous = float(close.iloc[-2])
    def val(series, idx=-1):
        x = series.iloc[idx]
        return None if pd.isna(x) else round(float(x), 2)
    rsi_v = val(rsi(close))
    macd_v, macd_signal = val(macd), val(signal)
    atr_v = val(atr)
    relvol = round(float(volume.iloc[-1] / avgvol.iloc[-1]), 2) if pd.notna(avgvol.iloc[-1]) and avgvol.iloc[-1] else None
    ret5 = (latest / float(close.iloc[-6]) - 1) * 100 if len(close) >= 6 else None
    ret20 = (latest / float(close.iloc[-21]) - 1) * 100 if len(close) >= 21 else None
    prior20 = float(high.iloc[-21:-1].max()) if len(high) >= 21 else None
    prior60 = float(high.iloc[-61:-1].max()) if len(high) >= 61 else None

    score = 0
    reasons, risks = [], []
    for condition, pts, positive, negative in [
        (latest > sma20.iloc[-1], 10, "Price above 20-day average", "Price below 20-day average"),
        (sma20.iloc[-1] > sma50.iloc[-1], 10, "20-day average above 50-day", "20-day average below 50-day"),
        (latest > sma200.iloc[-1] if pd.notna(sma200.iloc[-1]) else False, 8, "Price above 200-day average", "Price below/near 200-day average"),
        (macd.iloc[-1] > signal.iloc[-1], 10, "MACD bullish", "MACD not bullish"),
        (rsi_v is not None and 50 <= rsi_v <= 70, 10, "RSI in constructive 50–70 zone", "RSI outside preferred 50–70 zone"),
        (val(adx) is not None and val(adx) >= 20, 8, "ADX indicates a developing trend", "ADX trend strength is weak"),
        (relvol is not None and relvol >= 1.2, 10, "Volume above 20-day average", "Volume confirmation is limited"),
        (ret20 is not None and ret20 > 0, 8, "Positive 20-day momentum", "20-day momentum is negative"),
        (prior20 is not None and latest >= prior20 * 0.97, 8, "Near/above 20-day breakout level", "Not near a 20-day breakout"),
        (prior60 is not None and latest >= prior60 * 0.95, 8, "Near 60-day high", "Meaningful overhead resistance may remain"),
    ]:
        if condition:
            score += pts
            reasons.append(positive)
        else:
            risks.append(negative)

    target = latest * 1.10
    stop = max(latest - 2 * atr_v, 0.01) if atr_v else latest * 0.95
    upside_to_20_high = ((prior20/latest)-1)*100 if prior20 else None
    if score >= 68 and rsi_v is not None and rsi_v < 75:
        label = "WATCH FOR ENTRY"
    elif score >= 48:
        label = "WATCHLIST — WAIT"
    else:
        label = "NO TRADE SETUP"

    result = {
        "symbol": symbol, "price": round(latest, 2), "previous_close": round(previous, 2),
        "change_pct": round((latest/previous-1)*100, 2) if previous else 0,
        "as_of": hist.index[-1].strftime("%Y-%m-%d"),
        "score": score, "signal": label, "target_10pct": round(target, 2),
        "indicative_stop": round(stop, 2),
        "reward_risk": round((target-latest)/(latest-stop), 2) if latest > stop else None,
        "rsi14": rsi_v, "macd": macd_v, "macd_signal": macd_signal,
        "sma20": val(sma20), "sma50": val(sma50), "sma200": val(sma200),
        "adx14": val(adx), "atr14": atr_v, "relative_volume": relvol,
        "return_5d_pct": round(ret5, 2) if ret5 is not None else None,
        "return_20d_pct": round(ret20, 2) if ret20 is not None else None,
        "prior_20d_high": round(prior20, 2) if prior20 else None,
        "prior_60d_high": round(prior60, 2) if prior60 else None,
        "reasons": reasons, "risks": risks,
        "chart": [{"date": idx.strftime("%Y-%m-%d"), "close": round(float(row["Close"]), 2)} for idx, row in hist.tail(90).iterrows()],
    }
    if include_fundamentals:
        try:
            info = ticker.get_info()
            result["fundamentals"] = {
                "market_cap": info.get("marketCap"), "trailing_pe": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"), "roe": info.get("returnOnEquity"),
                "profit_margin": info.get("profitMargins"), "revenue_growth": info.get("revenueGrowth"),
                "earnings_growth": info.get("earningsGrowth"), "debt_to_equity": info.get("debtToEquity"),
                "current_ratio": info.get("currentRatio"), "free_cash_flow": info.get("freeCashflow"),
                "sector": info.get("sector"), "industry": info.get("industry"),
            }
        except Exception:
            result["fundamentals"] = {}
    return result

class AnalyzeRequest(BaseModel):
    symbol: str
    fundamentals: bool = False

@app.get("/health")
def health():
    return {"status": "ok", "provider": "Yahoo Finance via yfinance", "data_interval": "daily candles", "refresh_note": "UI refresh does not guarantee fresh underlying market data."}

@app.get("/universe")
def universe(q: Optional[str] = None):
    symbols = get_universe()
    if q:
        q = q.strip().upper()
        symbols = [s for s in symbols if q in s]
    return {"count": len(symbols), "symbols": symbols[:500]}

@app.get("/analyze/{symbol}")
def analyze(symbol: str, fundamentals: bool = False):
    return analyze_symbol(symbol, fundamentals)

@app.post("/scan")
def scan(payload: dict):
    symbols = payload.get("symbols", [])
    fundamentals = bool(payload.get("fundamentals", False))
    if not isinstance(symbols, list) or not symbols:
        raise HTTPException(400, "Pass at least one symbol.")
    if len(symbols) > 50:
        raise HTTPException(400, "Scan up to 50 symbols per request to reduce free-provider rate limits.")
    results, errors = [], []
    for sym in symbols:
        try:
            results.append(analyze_symbol(str(sym), fundamentals))
        except Exception as e:
            errors.append({"symbol": str(sym), "error": str(e)})
    results.sort(key=lambda x: x["score"], reverse=True)
    return {"results": results, "errors": errors, "scanned": len(results), "as_of": datetime.now(timezone.utc).isoformat()}

# Backtest and Top 5 research endpoints. Backtest profiles are process-memory only;
# rerun after backend restart and never treat small samples as calibrated probabilities.
from backtesting import run_symbol_backtest, summarize_portfolio
_backtest_profiles = {}

@app.post('/backtest')
def backtest(payload: dict):
    symbols = payload.get('symbols', [])
    if not isinstance(symbols, list) or not symbols:
        raise HTTPException(400, 'Pass symbols to backtest, e.g. ["RELIANCE","INFY"].')
    if len(symbols) > 30:
        raise HTTPException(400, 'Backtest up to 30 symbols per request to limit free-provider load.')
    period = str(payload.get('period', '5y'))
    if period not in ('2y','5y','10y','max'):
        raise HTTPException(400, 'period must be 2y, 5y, 10y, or max.')
    target_pct = float(payload.get('target_pct', 10))
    horizon = int(payload.get('horizon_sessions', 15))
    min_score = int(payload.get('min_score', 68))
    cost_bps = float(payload.get('round_trip_cost_bps', 30))
    if not (1 <= target_pct <= 30 and 1 <= horizon <= 30 and 0 <= cost_bps <= 500 and 0 <= min_score <= 100):
        raise HTTPException(400, 'Invalid parameters. Check target, horizon, score, and costs.')
    results, errors = [], []
    for raw in dict.fromkeys(str(s).strip().upper().replace('.NS','') for s in symbols):
        try:
            r = run_symbol_backtest(raw, period, target_pct, horizon, min_score, cost_bps)
            if r.get('error'):
                errors.append({'symbol':raw,'error':r['error']})
            else:
                results.append(r)
                oos = r.get('out_of_sample', {})
                # Do not publish an estimated rate for fewer than 30 OOS trades.
                _backtest_profiles[raw] = {'trades':oos.get('trades',0), 'target_hit_rate_pct':oos.get('target_hit_rate_pct') if oos.get('trades',0)>=30 else None,
                    'avg_net_return_pct':oos.get('avg_net_return_pct'), 'tested_at':datetime.now(timezone.utc).isoformat(),
                    'parameters':{'period':period,'target_pct':target_pct,'horizon_sessions':horizon,'min_score':min_score,'round_trip_cost_bps':cost_bps}}
        except Exception as e:
            errors.append({'symbol':raw,'error':str(e)[:240]})
    return {'results':results,'portfolio_summary':summarize_portfolio(results),'errors':errors,
            'validation':'Chronological 70/30 split; entry next session open; no overlapping look-ahead in signal features; ambiguous target/stop bars treated as stop-first. This is research-grade baseline, not proof of future profitability.',
            'generated_at':datetime.now(timezone.utc).isoformat()}

@app.get('/top5')
def top5(symbols: Optional[str] = None, limit: int = 50):
    if limit < 5 or limit > 50:
        raise HTTPException(400, 'limit must be between 5 and 50.')
    candidates = [s.strip().upper().replace('.NS','') for s in symbols.split(',') if s.strip()] if symbols else (lambda u: (u[(datetime.now(timezone.utc).timetuple().tm_yday * limit) % max(1, len(u)):] + u[:(datetime.now(timezone.utc).timetuple().tm_yday * limit) % max(1, len(u))])[:limit])(get_universe())
    candidates = list(dict.fromkeys(candidates))[:50]
    rows, errors = [], []
    for sym in candidates:
        try:
            r = analyze_symbol(sym, False)
            # Reject weak/overextended signals and reward/risk below 2x where available.
            if r.get('score',0) < 68 or (r.get('rsi14') is not None and r['rsi14'] >= 75):
                continue
            if r.get('reward_risk') is not None and r['reward_risk'] < 2:
                continue
            profile = _backtest_profiles.get(sym, {})
            r['historical_validation'] = profile or None
            r['historical_probability_available'] = bool(profile and profile.get('trades',0)>=30 and profile.get('target_hit_rate_pct') is not None)
            r['recommendation_note'] = 'Historically tested on at least 30 out-of-sample trades' if r['historical_probability_available'] else 'Technical candidate only; no adequately sized out-of-sample validation cached'
            rows.append(r)
        except Exception as e:
            errors.append({'symbol':sym,'error':str(e)[:180]})
    rows.sort(key=lambda r: (1 if r.get('historical_probability_available') else 0,
                             r.get('historical_validation',{}).get('target_hit_rate_pct') or -1,
                             r.get('score',0),r.get('return_20d_pct') or -999), reverse=True)
    return {'recommendations':rows[:5],'scanned_symbols':len(candidates),'qualified_candidates':len(rows),
            'errors':errors,'as_of':datetime.now(timezone.utc).isoformat(),
            'universe_note':'This request scans only the specified symbols or the first N symbols from the current constituent list, up to 50. It is not a full 500-stock ranking.',
            'refresh_note':'Refresh reruns analysis; the free source may not update intraday. Backtest profiles are in-memory and clear when the backend restarts.'}
