"""SwingEdge 15 API.
Research tool only; daily Yahoo Finance candles are not a real-time market-data feed.
"""
from datetime import datetime, timezone, date
from threading import Lock, Thread
from typing import Optional
import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import numpy as np
import pandas as pd
import requests
import yfinance as yf

app = FastAPI(title="SwingEdge 15 API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

UNIVERSE_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
_universe_cache = {"at": None, "symbols": []}
_analysis_cache = {}       # symbol -> {"at": epoch, "data": analysis}
_analysis_lock = Lock()
_TOP5_CACHE_SECONDS = 15 * 60
_ANALYSIS_CACHE_SECONDS = 15 * 60
_top5_state = {
    "running": False, "started_at": None, "completed_at": None,
    "scanned_symbols": 0, "total_symbols": 0, "recommendations": [],
    "qualified_candidates": 0, "errors": [], "message": "Full-universe scan has not run yet."
}
_top5_lock = Lock()
_tracking = {}             # process-memory only; cleared on backend restart
_backtest_profiles = {}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def get_universe():
    now = datetime.now(timezone.utc)
    if (_universe_cache["at"] and
            (now - _universe_cache["at"]).total_seconds() < 86400 and
            _universe_cache["symbols"]):
        return _universe_cache["symbols"]
    try:
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"}
        response = requests.get(UNIVERSE_URL, headers=headers, timeout=12)
        response.raise_for_status()
        frame = pd.read_csv(pd.io.common.StringIO(response.text))
        col = next((c for c in frame.columns
                    if c.strip().lower() in ("symbol", "index symbol")), None)
        if col:
            symbols = sorted(set(frame[col].dropna().astype(str).str.strip().str.upper()))
            if symbols:
                _universe_cache.update(at=now, symbols=symbols)
                return symbols
    except Exception:
        pass
    fallback = ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "LT",
                "SBIN", "BHARTIARTL", "ITC", "AXISBANK", "TATAMOTORS",
                "SUNPHARMA", "MARUTI", "TRENT", "BEL"]
    return fallback


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def market_sentiment():
    """A transparent technical proxy; not news sentiment or a live sentiment feed."""
    output = {
        "label": "Unavailable", "score": None, "source": "Yahoo Finance daily candles",
        "as_of": None,
        "note": "Technical market proxy only. News, events and live global sentiment are not included."
    }
    try:
        hist = yf.Ticker("^NSEI").history(period="6mo", interval="1d", auto_adjust=True)
        if hist is None or len(hist) < 30:
            return output
        close = hist["Close"].dropna()
        latest = float(close.iloc[-1])
        sma20 = float(close.rolling(20).mean().iloc[-1])
        sma50 = float(close.rolling(50).mean().iloc[-1])
        ret20 = (latest / float(close.iloc[-21]) - 1) * 100
        score = 50
        score += 20 if latest > sma20 else -20
        score += 20 if sma20 > sma50 else -20
        score += 10 if ret20 > 0 else -10
        score = max(0, min(100, score))
        label = "Positive" if score >= 70 else "Neutral" if score >= 40 else "Cautious"
        output.update(label=label, score=score, as_of=hist.index[-1].strftime("%Y-%m-%d"),
                      nifty_return_20d_pct=round(ret20, 2),
                      nifty_above_sma20=latest > sma20, sma20_above_sma50=sma20 > sma50)
    except Exception:
        pass
    return output


def _target_sets(entry, stop):
    horizons = {}
    for sessions in (7, 15, 30):
        targets = {}
        for pct in (5, 10):
            target = round(entry * (1 + pct / 100), 2)
            risk = entry - stop
            reward_risk = round((target - entry) / risk, 2) if risk > 0 else None
            targets[f"target_{pct}pct"] = {
                "target_pct": pct, "target_price": target,
                "estimated_return_pct": pct, "reward_risk": reward_risk
            }
        horizons[str(sessions)] = {
            "horizon_sessions": sessions,
            "expiry_note": f"Expires after {sessions} trading sessions; this is a planning horizon, not a forecast.",
            "targets": targets
        }
    return horizons


def analyze_symbol(symbol: str, include_fundamentals: bool = False, use_cache: bool = True):
    symbol = str(symbol).strip().upper().replace(".NS", "")
    if not symbol or not symbol.replace("-", "").replace("&", "").isalnum():
        raise HTTPException(400, "Enter a valid NSE symbol.")

    now_epoch = time.time()
    with _analysis_lock:
        cached = _analysis_cache.get(symbol)
        if use_cache and cached and now_epoch - cached["at"] < _ANALYSIS_CACHE_SECONDS:
            cached_result = dict(cached["data"])
            if not include_fundamentals or "fundamentals" in cached_result:
                return cached_result

    ticker = yf.Ticker(symbol + ".NS")
    try:
        hist = ticker.history(period="1y", interval="1d", auto_adjust=True, actions=False)
    except Exception as exc:
        raise HTTPException(502, f"Price provider error for {symbol}: {str(exc)[:160]}")
    if hist is None or hist.empty or len(hist) < 60:
        raise HTTPException(404, f"Insufficient daily history for {symbol}. Verify the NSE symbol.")

    hist = hist.dropna(subset=["Close"])
    close = hist["Close"].dropna()
    high, low, volume = hist["High"], hist["Low"], hist["Volume"]
    sma20, sma50, sma200 = close.rolling(20).mean(), close.rolling(50).mean(), close.rolling(200).mean()
    ema12, ema26 = close.ewm(span=12, adjust=False).mean(), close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    tr = pd.concat([(high-low), (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    up, down = high.diff(), -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr_smooth = tr.ewm(alpha=1/14, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1/14, adjust=False).mean() / atr_smooth.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1/14, adjust=False).mean() / atr_smooth.replace(0, np.nan)
    dx = 100 * (plus_di-minus_di).abs() / (plus_di+minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1/14, adjust=False).mean()
    avgvol = volume.rolling(20).mean()
    latest, previous = float(close.iloc[-1]), float(close.iloc[-2])

    def val(series, idx=-1):
        x = series.iloc[idx]
        return None if pd.isna(x) else round(float(x), 2)

    rsi_v, macd_v, macd_sig_v, atr_v = val(rsi(close)), val(macd), val(macd_signal), val(atr)
    relvol = round(float(volume.iloc[-1] / avgvol.iloc[-1]), 2) if pd.notna(avgvol.iloc[-1]) and avgvol.iloc[-1] else None
    ret5 = (latest / float(close.iloc[-6]) - 1) * 100 if len(close) >= 6 else None
    ret20 = (latest / float(close.iloc[-21]) - 1) * 100 if len(close) >= 21 else None
    prior20 = float(high.iloc[-21:-1].max()) if len(high) >= 21 else None
    prior60 = float(high.iloc[-61:-1].max()) if len(high) >= 61 else None

    score, reasons, risks = 0, [], []
    checks = [
        (latest > sma20.iloc[-1], 10, "Price above 20-day average", "Price below 20-day average"),
        (sma20.iloc[-1] > sma50.iloc[-1], 10, "20-day average above 50-day", "20-day average below 50-day"),
        (latest > sma200.iloc[-1] if pd.notna(sma200.iloc[-1]) else False, 8, "Price above 200-day average", "Price below/near 200-day average"),
        (macd.iloc[-1] > macd_signal.iloc[-1], 10, "MACD bullish", "MACD not bullish"),
        (rsi_v is not None and 50 <= rsi_v <= 70, 10, "RSI in constructive 50–70 zone", "RSI outside preferred 50–70 zone"),
        (val(adx) is not None and val(adx) >= 20, 8, "ADX indicates a developing trend", "ADX trend strength is weak"),
        (relvol is not None and relvol >= 1.2, 10, "Volume above 20-day average", "Volume confirmation is limited"),
        (ret20 is not None and ret20 > 0, 8, "Positive 20-day momentum", "20-day momentum is negative"),
        (prior20 is not None and latest >= prior20 * .97, 8, "Near/above 20-day breakout level", "Not near a 20-day breakout"),
        (prior60 is not None and latest >= prior60 * .95, 8, "Near 60-day high", "Meaningful overhead resistance may remain"),
    ]
    for condition, points, positive, negative in checks:
        if condition:
            score += points
            reasons.append(positive)
        else:
            risks.append(negative)

    stop = round(max(latest - 2 * atr_v, .01), 2) if atr_v else round(latest * .95, 2)
    target_sets = _target_sets(latest, stop)
    label = "WATCH FOR ENTRY" if score >= 68 and rsi_v is not None and rsi_v < 75 else (
        "ENTER" if score > 80 else "KEEP ON RADAR" if score >= 50 else "NO TRADE")
    sentiment = market_sentiment()
    # Sentiment is a modest overlay, never represented as news analysis.
    sentiment_adjustment = 5 if sentiment["label"] == "Positive" else -5 if sentiment["label"] == "Cautious" else 0
    adjusted_score = max(0, min(100, score + sentiment_adjustment))
    result = {
        "symbol": symbol, "price": round(latest, 2), "previous_close": round(previous, 2),
        "change_pct": round((latest/previous-1)*100, 2) if previous else 0,
        "as_of": hist.index[-1].strftime("%Y-%m-%d"),
        "data_interval": "daily candles", "data_source": "Yahoo Finance via yfinance",
        "score": score, "adjusted_score": adjusted_score, "sentiment_adjustment": sentiment_adjustment,
        "signal": label, "target_10pct": round(latest*1.10, 2),
        "target_5pct": round(latest*1.05, 2), "indicative_stop": stop,
        "reward_risk": round((latest*1.10-latest)/(latest-stop), 2) if latest > stop else None,
        "targets_by_horizon": target_sets,
        "rsi14": rsi_v, "macd": macd_v, "macd_signal": macd_sig_v,
        "sma20": val(sma20), "sma50": val(sma50), "sma200": val(sma200),
        "adx14": val(adx), "atr14": atr_v, "relative_volume": relvol,
        "return_5d_pct": round(ret5, 2) if ret5 is not None else None,
        "return_20d_pct": round(ret20, 2) if ret20 is not None else None,
        "prior_20d_high": round(prior20, 2) if prior20 else None,
        "prior_60d_high": round(prior60, 2) if prior60 else None,
        "reasons": reasons, "risks": risks, "market_sentiment": sentiment,
        "sentiment_note": "Nifty technical proxy only; no news, corporate-event or global-market sentiment feed is included.",
        "chart": [{"date": idx.strftime("%Y-%m-%d"), "close": round(float(row["Close"]), 2)}
                  for idx, row in hist.tail(90).iterrows()],
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
    with _analysis_lock:
        _analysis_cache[symbol] = {"at": time.time(), "data": dict(result)}
    return result


def _track_recommendation(row):
    symbol = row["symbol"]
    key = f'{symbol}:{row["as_of"]}'
    if key not in _tracking:
        _tracking[key] = {
            "id": key, "symbol": symbol, "signal_date": row["as_of"],
            "entry_price": row["price"], "stop_loss": row["indicative_stop"],
            "targets_by_horizon": row["targets_by_horizon"], "status": "Pending",
            "status_detail": "Awaiting subsequent daily price data",
            "last_checked": utc_now(), "last_price": row["price"],
        }
    return _tracking[key]


def _update_tracking_item(item):
    try:
        hist = yf.Ticker(item["symbol"] + ".NS").history(period="3mo", interval="1d", auto_adjust=True)
        if hist is None or hist.empty:
            item["status_detail"] = "Price data unavailable; status not verified"
            return item
        entry_date = pd.to_datetime(item["signal_date"]).date()
        after = hist[hist.index.date > entry_date]
        if after.empty:
            item["status_detail"] = "No later daily candle yet"
            item["last_checked"] = utc_now()
            return item
        stop = float(item["stop_loss"])
        target5 = float(item["entry_price"]) * 1.05
        target10 = float(item["entry_price"]) * 1.10
        # Conservative daily-bar rule: if stop and target occur on the same candle, stop is first.
        for idx, candle in after.iterrows():
            low, high = float(candle["Low"]), float(candle["High"])
            if low <= stop:
                item.update(status="Stop-Loss Hit", status_detail=f"Daily low reached stop on {idx.strftime('%Y-%m-%d')}",
                            resolved_date=idx.strftime("%Y-%m-%d"))
                break
            if high >= target10:
                item.update(status="Target 10% Met", status_detail=f"Daily high reached +10% target on {idx.strftime('%Y-%m-%d')}",
                            resolved_date=idx.strftime("%Y-%m-%d"))
                break
            if high >= target5:
                item.update(status="Target 5% Met", status_detail=f"Daily high reached +5% target on {idx.strftime('%Y-%m-%d')}",
                            resolved_date=idx.strftime("%Y-%m-%d"))
                break
        last = hist.iloc[-1]
        item["last_price"] = round(float(last["Close"]), 2)
        item["last_price_date"] = hist.index[-1].strftime("%Y-%m-%d")
        item["last_checked"] = utc_now()
        if item["status"] == "Pending":
            item["status_detail"] = "No target or stop confirmed by available daily candles"
        return item
    except Exception:
        item["status_detail"] = "Price data unavailable; status not verified"
        item["last_checked"] = utc_now()
        return item


def _full_scan_worker(symbols):
    rows, errors = [], []
    with _top5_lock:
        _top5_state.update(running=True, started_at=utc_now(), completed_at=None,
                           scanned_symbols=0, total_symbols=len(symbols), recommendations=[],
                           qualified_candidates=0, errors=[], message="Full-universe scan in progress.")
    for i, sym in enumerate(symbols, start=1):
        try:
            row = analyze_symbol(sym, False, use_cache=True)
            if row.get("score", 0) >= 68 and (row.get("rsi14") is None or row["rsi14"] < 75):
                if row.get("reward_risk") is None or row["reward_risk"] >= 2:
                    row["historical_validation"] = _backtest_profiles.get(sym)
                    row["recommendation_note"] = "Technical candidate; historical probability is not established."
                    rows.append(row)
                    _track_recommendation(row)
        except Exception as exc:
            errors.append({"symbol": sym, "error": str(exc)[:180]})
        with _top5_lock:
            _top5_state.update(scanned_symbols=i, qualified_candidates=len(rows),
                               recommendations=sorted(rows, key=lambda x: (x.get("adjusted_score", x["score"]),
                                                                           x.get("return_20d_pct") or -999), reverse=True)[:5],
                               errors=errors[-100:],
                               message=f"Scanned {i} of {len(symbols)} symbols.")
    with _top5_lock:
        _top5_state.update(running=False, completed_at=utc_now(),
                           recommendations=sorted(rows, key=lambda x: (x.get("adjusted_score", x["score"]),
                                                                       x.get("return_20d_pct") or -999), reverse=True)[:5],
                           qualified_candidates=len(rows), errors=errors[-100:],
                           message=f"Scan complete: {len(rows)} candidates qualified from {len(symbols)} symbols.")


@app.get("/health")
def health():
    return {
        "status": "ok", "version": "2.0.0",
        "provider": "Yahoo Finance via yfinance",
        "data_interval": "daily candles",
        "refresh_note": "UI refresh does not guarantee fresh underlying market data.",
        "full_universe_scan": "background scan; progress available at /top5/status",
        "tracking_storage": "process memory only; clears on backend restart",
    }


@app.get("/universe")
def universe(q: Optional[str] = None):
    symbols = get_universe()
    if q:
        q = q.strip().upper()
        symbols = [s for s in symbols if q in s]
    return {"count": len(symbols), "symbols": symbols[:500], "source": "Nifty Indices constituent CSV or fallback list"}


@app.get("/hourly/{symbol}")
def hourly_analysis(symbol: str):
    symbol = symbol.strip().upper().replace(".NS", "")
    if not symbol or not symbol.replace("-", "").replace("&", "").isalnum():
        raise HTTPException(400, "Enter a valid NSE symbol.")

    try:
        hist = yf.Ticker(symbol + ".NS").history(
            period="60d",
            interval="1h",
            auto_adjust=True,
            actions=False
        )

        if hist is None or hist.empty:
            raise HTTPException(
                404, f"No hourly candle data available for {symbol}."
            )

        hist = hist.dropna(subset=["Close"])
        close = hist["Close"]
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()

        return {
            "symbol": symbol,
            "data_interval": "1h candles",
            "data_source": "Yahoo Finance via yfinance",
            "as_of": hist.index[-1].isoformat(),
            "last_price": round(float(close.iloc[-1]), 2),
            "hourly_trend": (
                "Bullish" if macd.iloc[-1] > signal.iloc[-1]
                else "Bearish"
            ),
            "candles": [
                {
                    "time": idx.isoformat(),
                    "open": round(float(row["Open"]), 2),
                    "high": round(float(row["High"]), 2),
                    "low": round(float(row["Low"]), 2),
                    "close": round(float(row["Close"]), 2),
                    "volume": int(row["Volume"])
                }
                for idx, row in hist.tail(120).iterrows()
            ],
            "note": (
                "Hourly data may be delayed or unavailable. "
                "It is not a guaranteed real-time feed."
            )
        }

    except HTTPException:
        raise
    
    except Exception as exc:
        raise HTTPException(
            502, f"Hourly data error for {symbol}: {str(exc)[:150]}"
        )


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
        except Exception as exc:
            errors.append({"symbol": str(sym), "error": str(exc)[:180]})
    results.sort(key=lambda x: x.get("adjusted_score", x["score"]), reverse=True)
    return {"results": results, "errors": errors, "scanned": len(results), "as_of": utc_now()}


from backtesting import run_symbol_backtest, summarize_portfolio


@app.post("/backtest")
def backtest(payload: dict):
    symbols = payload.get("symbols", [])
    if not isinstance(symbols, list) or not symbols:
        raise HTTPException(400, 'Pass symbols to backtest, e.g. ["RELIANCE","INFY"].')
    if len(symbols) > 30:
        raise HTTPException(400, "Backtest up to 30 symbols per request to limit free-provider load.")
    period = str(payload.get("period", "5y"))
    if period not in ("2y", "5y", "10y", "max"):
        raise HTTPException(400, "period must be 2y, 5y, 10y, or max.")
    target_pct = float(payload.get("target_pct", 10))
    horizon = int(payload.get("horizon_sessions", 15))
    min_score = int(payload.get("min_score", 68))
    cost_bps = float(payload.get("round_trip_cost_bps", 30))
    if not (1 <= target_pct <= 30 and 1 <= horizon <= 30 and 0 <= cost_bps <= 500 and 0 <= min_score <= 100):
        raise HTTPException(400, "Invalid parameters. Check target, horizon, score, and costs.")
    results, errors = [], []
    for raw in dict.fromkeys(str(s).strip().upper().replace(".NS", "") for s in symbols):
        try:
            result = run_symbol_backtest(raw, period, target_pct, horizon, min_score, cost_bps)
            if result.get("error"):
                errors.append({"symbol": raw, "error": result["error"]})
            else:
                results.append(result)
                oos = result.get("out_of_sample", {})
                _backtest_profiles[raw] = {
                    "trades": oos.get("trades", 0),
                    "target_hit_rate_pct": oos.get("target_hit_rate_pct") if oos.get("trades", 0) >= 30 else None,
                    "avg_net_return_pct": oos.get("avg_net_return_pct"),
                    "tested_at": utc_now(),
                    "parameters": {"period": period, "target_pct": target_pct, "horizon_sessions": horizon,
                                   "min_score": min_score, "round_trip_cost_bps": cost_bps},
                }
        except Exception as exc:
            errors.append({"symbol": raw, "error": str(exc)[:240]})
    return {
        "results": results, "portfolio_summary": summarize_portfolio(results), "errors": errors,
        "validation": "Chronological 70/30 split; entry next session open; ambiguous target/stop bars treated as stop-first. Research baseline, not proof of future profitability.",
        "generated_at": utc_now(),
    }


@app.get("/top5")
def top5(symbols: Optional[str] = None, limit: int = 50, refresh: bool = False):
    if limit < 5 or limit > 50:
        raise HTTPException(400, "limit must be between 5 and 50.")
    if symbols:
        candidates = list(dict.fromkeys(s.strip().upper().replace(".NS", "") for s in symbols.split(",") if s.strip()))[:50]
        rows, errors = [], []
        for sym in candidates:
            try:
                row = analyze_symbol(sym)
                if row["score"] >= 68 and (row["rsi14"] is None or row["rsi14"] < 75) and (row["reward_risk"] is None or row["reward_risk"] >= 2):
                    row["historical_validation"] = _backtest_profiles.get(sym)
                    rows.append(row)
                    _track_recommendation(row)
            except Exception as exc:
                errors.append({"symbol": sym, "error": str(exc)[:180]})
        rows.sort(key=lambda x: (x.get("adjusted_score", x["score"]), x.get("return_20d_pct") or -999), reverse=True)
        return {"recommendations": rows[:5], "scanned_symbols": len(candidates), "qualified_candidates": len(rows),
                "errors": errors, "as_of": utc_now(), "scan_scope": "specified symbols, max 50",
                "universe_note": "Only supplied symbols were scanned; this is not a full Nifty 500 ranking.",
                "refresh_note": "Yahoo Finance daily candles; not guaranteed live data."}

    with _top5_lock:
        should_start = (not _top5_state["running"] and
                        (refresh or not _top5_state["completed_at"] or
                         (datetime.now(timezone.utc) - datetime.fromisoformat(_top5_state["completed_at"])).total_seconds() > _TOP5_CACHE_SECONDS))
        if should_start:
            candidates = get_universe()[:500]
            Thread(target=_full_scan_worker, args=(candidates,), daemon=True).start()
        state = dict(_top5_state)
    return {
        "recommendations": state["recommendations"],
        "scanned_symbols": state["scanned_symbols"],
        "total_symbols": state["total_symbols"],
        "qualified_candidates": state["qualified_candidates"],
        "errors": state["errors"],
        "as_of": state["completed_at"] or state["started_at"] or utc_now(),
        "running": state["running"] or should_start,
        "message": state["message"],
        "scan_scope": "full current Nifty 500 constituent list when available",
        "universe_note": "A full scan runs in the background; poll /top5/status for progress. Until completion, results may be empty or from the previous scan.",
        "refresh_note": "Yahoo Finance daily candles; not guaranteed live data. Provider limits or Render restarts can interrupt scans.",
    }


@app.get("/top5/status")
def top5_status():
    with _top5_lock:
        state = dict(_top5_state)
    return {**state, "as_of": state["completed_at"] or state["started_at"], "data_interval": "daily candles"}


@app.get("/tracking")
def tracking(status: Optional[str] = None, refresh: bool = False):
    items = list(_tracking.values())
    if refresh:
        for item in items:
            _update_tracking_item(item)
    if status:
        items = [item for item in items if item.get("status", "").lower() == status.lower()]
    items.sort(key=lambda x: (x.get("signal_date", ""), x.get("symbol", "")), reverse=True)
    return {
        "count": len(items), "items": items, "as_of": utc_now(),
        "note": "Tracking is process-memory only and clears on backend restart. Daily candles cannot establish intraday event order; stop is checked first when both levels are touched.",
    }


@app.get("/tracking/{tracking_id:path}")
def tracking_detail(tracking_id: str, refresh: bool = False):
    item = _tracking.get(tracking_id)
    if not item:
        raise HTTPException(404, "Tracking item not found. Tracking history is in-memory and may have cleared after a restart.")
    if refresh:
        item = _update_tracking_item(item)
    return item


@app.get("/sentiment")
def sentiment():
    return market_sentiment()


@app.get("/top5/status/healthcheck")
def top5_status_healthcheck():
    return {"status": "ok", "top5_scan_running": _top5_state["running"], "as_of": utc_now()}
