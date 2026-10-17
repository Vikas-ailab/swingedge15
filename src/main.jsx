import React, { useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Search, RefreshCw, Star, StarOff, TrendingUp, ShieldAlert, Clock3, Download, Settings2, Activity, Wifi } from 'lucide-react'
import './style.css'

const API = 'https://swingedge15-backend.onrender.com'
const fmt = n => n == null || Number.isNaN(Number(n)) ? '—' : Number(n).toLocaleString('en-IN', {maximumFractionDigits: 2})
const pct = n => n == null ? '—' : `${Number(n)>0?'+':''}${fmt(n)}%`
function App() {
  const [query, setQuery] = useState('')
  const [symbol, setSymbol] = useState('RELIANCE')
  const [data, setData] = useState(null)
  const [hourlyData, setHourlyData] = useState(null)
  const [universe, setUniverse] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [auto, setAuto] = useState(true)
  const [intervalSec, setIntervalSec] = useState(60)
  const [fundamentals, setFundamentals] = useState(false)
  const [watchlist, setWatchlist] = useState(() => JSON.parse(localStorage.getItem('se15-watchlist') || '["RELIANCE","HDFCBANK","INFY"]'))
  const [lastUpdated, setLastUpdated] = useState(null)
  const [tab, setTab] = useState('analyze')
  const [scanSymbols, setScanSymbols] = useState('RELIANCE,HDFCBANK,ICICIBANK,INFY,TCS,SBIN,LT')
  const [scanResults, setScanResults] = useState([])
  const [scanErrors, setScanErrors] = useState([])
  const [scanBusy, setScanBusy] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [top5, setTop5] = useState([])
  const [top5Busy, setTop5Busy] = useState(false)
  const [top5Meta, setTop5Meta] = useState('')
  const [btSymbols, setBtSymbols] = useState('RELIANCE,HDFCBANK,ICICIBANK,INFY,TCS,SBIN,LT,BEL')
  const [btBusy, setBtBusy] = useState(false)
  const [btResult, setBtResult] = useState(null)
  const [btError, setBtError] = useState('')

  useEffect(() => {
    fetch(`${API}/universe`).then(r=>r.json()).then(d=>setUniverse(d.symbols||[])).catch(()=>{})
    if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(()=>{})
  }, [])
  
async function load(sym=symbol) {
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${API}/analyze/${encodeURIComponent(sym)}?fundamentals=${fundamentals}`);
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || 'Unable to load this symbol.');

      setData(body);
      setSymbol(body.symbol);
      setLastUpdated(new Date());

      try {
        const hourlyRes = await fetch(`${API}/hourly/${encodeURIComponent(body.symbol)}`);
        const hourlyBody = await hourlyRes.json();
        setHourlyData(hourlyRes.ok ? hourlyBody : null);
      } catch (hourlyError) {
        setHourlyData(null);
      }
    } catch(e) {
      setError(`${e.message}. Check that the backend is running and the NSE symbol is correct.`);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load('RELIANCE') }, [])
  useEffect(() => {
    if (!auto || tab !== 'analyze') return
    const id = setInterval(() => { if (!loading) load(symbol) }, Math.max(30, intervalSec) * 1000)
    return () => clearInterval(id)
  }, [auto, intervalSec, symbol, fundamentals, tab])
  useEffect(() => { localStorage.setItem('se15-watchlist', JSON.stringify(watchlist)) }, [watchlist])
  const suggestions = useMemo(() => query ? universe.filter(s=>s.includes(query.toUpperCase())).slice(0,8) : [], [query, universe])
  const saved = watchlist.includes(symbol)
  function toggleWatch(s=symbol) { setWatchlist(old => old.includes(s) ? old.filter(x=>x!==s) : [...old,s]) }
  async function scan() {
    const symbols = [...new Set(scanSymbols.split(/[,\s]+/).map(s=>s.trim().toUpperCase().replace('.NS','')).filter(Boolean))].slice(0,50)
    if (!symbols.length) return
    setScanBusy(true); setScanErrors([])
    try {
      const res = await fetch(`${API}/scan`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbols,fundamentals})})
      const body = await res.json()
      if (!res.ok) throw new Error(body.detail || 'Scan failed')
      setScanResults(body.results || []); setScanErrors(body.errors || [])
    } catch(e) { setScanErrors([{symbol:'API',error:e.message}]) }
    finally { setScanBusy(false) }
  }
  async function loadTop5() {
    setTop5Busy(true); setTop5Meta('');
    try {
      const res = await fetch(`${API}/top5?limit=50`); const body = await res.json();
      if (!res.ok) throw new Error(body.detail || 'Top 5 scan failed');
      setTop5(body.recommendations || []); setTop5Meta(`${body.scanned_symbols} symbols scanned · ${body.qualified_candidates} qualified · ${new Date(body.as_of).toLocaleString()}`);
      if (body.universe_note) setTop5Meta(`${body.universe_note} ${body.refresh_note || ''}`);
    } catch(e) { setTop5Meta(e.message); } finally { setTop5Busy(false); }
  }
  async function runBacktest() {
    const symbols = [...new Set(btSymbols.split(/[ ,\n]+/).map(s=>s.trim().toUpperCase().replace('.NS','')).filter(Boolean))].slice(0,30);
    if (!symbols.length) return;
    setBtBusy(true); setBtError(''); setBtResult(null);
    try {
      const res = await fetch(`${API}/backtest`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbols,period:'5y',target_pct:10,horizon_sessions:15,min_score:68,round_trip_cost_bps:30})});
      const body = await res.json(); if (!res.ok) throw new Error(body.detail || 'Backtest failed'); setBtResult(body);
    } catch(e) { setBtError(e.message); } finally { setBtBusy(false); }
  }
  useEffect(() => { if (tab === 'top5') loadTop5(); }, [tab]);
  useEffect(() => { if (tab !== 'top5' || !auto) return; const id = setInterval(loadTop5, Math.max(30, intervalSec)*1000); return () => clearInterval(id); }, [tab, auto, intervalSec]);
  function exportCSV() {
    const rows = scanResults.map(r=>[r.symbol,r.price,r.score,r.signal,r.target_10pct,r.indicative_stop,r.rsi14,r.return_20d_pct].join(','))
    const blob = new Blob([['Symbol,Price,Score,Signal,10% Target,Indicative Stop,RSI14,20D Return %',...rows].join('\\n')], {type:'text/csv'})
    const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href=url; a.download='swingedge15-scan.csv'; a.click(); URL.revokeObjectURL(url)
  }
  return <div className="app-shell">
    <header className="topbar">
      <div className="brand-mark"><TrendingUp size={23}/></div>
      <div className="brand"><b>SwingEdge <span>15</span></b><small>NIFTY 500 · SWING RESEARCH</small></div>
      <button className="icon-btn" onClick={()=>setShowSettings(v=>!v)} aria-label="Settings"><Settings2 size={20}/></button>
    </header>
    {showSettings && <section className="settings panel">
      <div className="section-title">Refresh & data</div>
      <label className="toggle-row"><span>Auto-refresh screen</span><input type="checkbox" checked={auto} onChange={e=>setAuto(e.target.checked)}/></label>
      <label className="field-label">Refresh interval <select value={intervalSec} onChange={e=>setIntervalSec(Number(e.target.value))}><option value="30">30 seconds</option><option value="60">60 seconds</option><option value="120">2 minutes</option><option value="300">5 minutes</option></select></label>
      <label className="toggle-row"><span>Include fundamentals (slower)</span><input type="checkbox" checked={fundamentals} onChange={e=>setFundamentals(e.target.checked)}/></label>
      <p className="muted tiny">Refresh frequency is not the same as data freshness. This free source uses daily candles and does not provide guaranteed millisecond/live exchange data.</p>
    </section>}
    <main>
      <section className="hero">
        <div className="eyebrow"><span className="live-dot"></span> RULE-BASED MARKET SCANNER</div>
        <h1>Find your next<br/><em>15-day setup.</em></h1>
        <p>Research Nifty 500 stocks for momentum, trend strength and a potential 10% move.</p>
        <div className="hero-stat-row"><div><strong>10%</strong><span>Target reference</span></div><div><strong>15D</strong><span>Trading horizon</span></div><div><strong>500</strong><span>Universe focus</span></div></div>
      </section>
      <nav className="tabs"><button className={tab==='top5'?'active':''} onClick={()=>setTab('top5')}>Top 5</button><button className={tab==='analyze'?'active':''} onClick={()=>setTab('analyze')}>Analyze</button><button className={tab==='scan'?'active':''} onClick={()=>setTab('scan')}>Scan</button><button className={tab==='backtest'?'active':''} onClick={()=>setTab('backtest')}>Backtest</button><button className={tab==='watchlist'?'active':''} onClick={()=>setTab('watchlist')}>Saved <span className="count">{watchlist.length}</span></button></nav>
      {tab==='top5' && <section className="panel">
        <div className="section-title">Daily Top 5 <span className="pill">Research shortlist</span></div>
        <p className="muted">Candidates are ranked using the current technical rules. A historical hit rate appears only after you run a backtest with at least 30 out-of-sample trades for that symbol.</p>
        <button className="primary-btn full" onClick={loadTop5} disabled={top5Busy}>{top5Busy?<><RefreshCw className="spin" size={16}/> Scanning…</>:<><Activity size={16}/> Refresh Top 5</>}</button>
        {top5Meta && <p className="muted tiny">{top5Meta}</p>}
        {top5.length===0 && !top5Busy && <p className="muted">No qualifying candidates returned. This is preferable to forcing weak trades.</p>}
        <div className="result-list">{top5.map((r,i)=><button className="result-row" key={r.symbol} onClick={()=>{setTab('analyze');setQuery(r.symbol);load(r.symbol)}}><span className="result-symbol">{i+1}. {r.symbol}<small>{r.historical_probability_available?`OOS target hit rate ${r.historical_validation.target_hit_rate_pct}% · n=${r.historical_validation.trades}`:'Historical probability not validated'}</small></span><span className="result-metrics"><b>₹{fmt(r.price)}</b><small>Target ₹{fmt(r.target_10pct)}</small></span><span className={`score ${r.score>=68?'good':'mid'}`}>{r.score}</span></button>)}</div>
        <p className="muted tiny">This endpoint scans up to 50 symbols per request, not all 500. Free-source refresh is not a live exchange feed.</p>
      </section>}
      {tab==='backtest' && <section className="panel">
        <div className="section-title">Strategy backtest <span className="pill">5 years · 15 sessions</span></div>
        <p className="muted">Runs a historical simulation using a 10% target, 2× ATR stop, minimum score 68, next-session-open entries, 30 bps round-trip cost assumption, and chronological 70/30 development/out-of-sample split. Up to 30 symbols per run.</p>
        <label className="field-label">NSE symbols (comma-separated)</label><textarea value={btSymbols} onChange={e=>setBtSymbols(e.target.value)} rows="3" placeholder="RELIANCE, INFY, BEL…"/>
        <button className="primary-btn full" onClick={runBacktest} disabled={btBusy}>{btBusy?<><RefreshCw className="spin" size={16}/> Running backtest…</>:<><Activity size={16}/> Run backtest</>}</button>
        {btError && <p className="error-inline">{btError}</p>}
        {btResult && <><div className="section-title">Out-of-sample pooled summary</div><div className="metrics-grid">{[['Trades',btResult.portfolio_summary?.trades],['Target hit rate',btResult.portfolio_summary?.target_hit_rate_pct==null?'—':`${btResult.portfolio_summary.target_hit_rate_pct}%`],['95% hit-rate interval',btResult.portfolio_summary?.target_hit_rate_95ci_pct?.map(x=>`${x}%`).join('–')||'—'],['Avg net return / trade',btResult.portfolio_summary?.avg_net_return_pct==null?'—':`${btResult.portfolio_summary.avg_net_return_pct}%`],['Profit factor',btResult.portfolio_summary?.profit_factor??'—']].map(([k,v])=><div className="metric" key={k}><span>{k}</span><b>{v??'—'}</b></div>)}</div>
          <div className="section-title">Per-symbol results</div>{btResult.results.map(r=><div className="watch-row" key={r.symbol}><div><b>{r.symbol}</b><small>OOS: {r.out_of_sample?.trades||0} trades · hit rate {r.out_of_sample?.target_hit_rate_pct==null?'—':`${r.out_of_sample.target_hit_rate_pct}%`} · avg net {r.out_of_sample?.avg_net_return_pct==null?'—':`${r.out_of_sample.avg_net_return_pct}%`}</small></div></div>)}{btResult.errors.map(e=><p className="error-inline" key={e.symbol}>{e.symbol}: {e.error}</p>)}<p className="muted tiny">{btResult.validation}</p></>}
      </section>}
      {tab==='analyze' && <>
        <section className="search-wrap">
          <div className="search-box"><Search size={19}/><input value={query} onChange={e=>setQuery(e.target.value)} onKeyDown={e=>e.key==='Enter'&&load(query||symbol)} placeholder="Search NSE symbol (e.g. BEL, TCS)"/><button onClick={()=>load(query||symbol)} disabled={loading}>{loading?'…':'Go'}</button></div>
          {suggestions.length>0 && <div className="suggestions">{suggestions.map(s=><button key={s} onClick={()=>{setQuery(s);load(s)}}>{s}<span>NSE</span></button>)}</div>}
        </section>
        {error && <div className="error-box">{error}</div>}
        {data && <StockCard data={data} saved={watchlist.includes(data.symbol)} toggleWatch={()=>toggleWatch(data.symbol)} loading={loading} refresh={()=>load(data.symbol)} lastUpdated={lastUpdated}/>}

{hourlyData && <section className="panel">
  <h3>Hourly Trend · {hourlyData.symbol}</h3>
  <div className="metrics-grid">
    <div className="metric">
      <span>Hourly Price</span>
      <strong>₹{hourlyData.last_price}</strong>
    </div>
    <div className="metric">
      <span>Hourly Trend</span>
      <strong>{hourlyData.hourly_trend}</strong>
    </div>
  </div>
  <p className="muted">
    Latest hourly data: {new Date(hourlyData.as_of).toLocaleString('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: true,
      timeZone: 'Asia/Kolkata'
    })} IST
  </p>
  <p className="muted">
    Market status: {['Saturday', 'Sunday'].includes(
      new Date().toLocaleDateString('en-IN', {
        timeZone: 'Asia/Kolkata',
        weekday: 'long'
      })
    ) ? 'Market closed — showing latest available session data.' : 'Check timestamp; data may be delayed.'}
  </p>
  <p className="muted">Hourly data may be delayed.</p>
</section>}


      </>}
      {tab==='scan' && <section className="panel scan-panel">
        <div className="section-title">Batch scan <span className="pill">Up to 50</span></div>
        <p className="muted">Enter comma-separated Nifty 500 symbols. Free data providers may rate-limit larger scans.</p>
        <textarea value={scanSymbols} onChange={e=>setScanSymbols(e.target.value)} rows="3" placeholder="RELIANCE, INFY, BEL..."/>
        <button className="primary-btn full" onClick={scan} disabled={scanBusy}>{scanBusy?<><RefreshCw className="spin" size={16}/> Scanning…</>:<><Activity size={16}/> Scan symbols</>}</button>
        {scanResults.length>0 && <><div className="scan-head"><b>{scanResults.length} results</b><button className="text-btn" onClick={exportCSV}><Download size={15}/> CSV</button></div><div className="result-list">{scanResults.map(r=><button className="result-row" key={r.symbol} onClick={()=>{setTab('analyze');setQuery(r.symbol);load(r.symbol)}}><span className="result-symbol">{r.symbol}<small>{r.signal}</small></span><span className="result-metrics"><b>{fmt(r.price)}</b><small>Score {r.score}/100</small></span><span className={`score ${r.score>=68?'good':r.score>=48?'mid':'bad'}`}>{r.score}</span></button>)}</div></>}
        {scanErrors.map((e,i)=><p className="error-inline" key={i}>{e.symbol}: {e.error}</p>)}
      </section>}
      {tab==='watchlist' && <section className="panel"><div className="section-title">Your watchlist</div><p className="muted">Saved on this device.</p>{watchlist.length===0?<p className="muted">No saved stocks yet. Add one from Analyze.</p>:watchlist.map(s=><div className="watch-row" key={s}><button onClick={()=>{setTab('analyze');setQuery(s);load(s)}}><b>{s}</b><small>Open analysis</small></button><button className="icon-btn" onClick={()=>toggleWatch(s)} aria-label={`Remove ${s}`}><StarOff size={17}/></button></div>)}</section>}
      <section className="disclaimer"><ShieldAlert size={17}/><p><b>Research tool, not a promise.</b> A 10% return within 15 days cannot be guaranteed. Validate signals, liquidity, news and price with your broker before acting.</p></section>
    </main>
    <footer><span><Wifi size={13}/> Free-source mode</span><span>{lastUpdated ? `Checked ${lastUpdated.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}` : 'Waiting for data'}</span></footer>
  </div>
}
function StockCard({data:d,saved,toggleWatch,loading,refresh,lastUpdated}) {
  const chart = d.chart || []
  const min = Math.min(...chart.map(x=>x.close)), max=Math.max(...chart.map(x=>x.close))
  const points = chart.map((p,i)=>`${i/(chart.length-1||1)*100},${96-(p.close-min)/(max-min||1)*84}`).join(' ')
  return <section className="stock-card">
    <div className="stock-heading"><div><div className="symbol-line"><h2>{d.symbol}</h2><button className={`star-btn ${saved?'saved':''}`} onClick={toggleWatch}>{saved?<Star size={18} fill="currentColor"/>:<Star size={18}/>}</button></div><span className="muted tiny">NSE · Daily candles · As of {d.as_of}</span></div><button className="refresh-btn" onClick={refresh} disabled={loading}><RefreshCw size={16} className={loading?'spin':''}/></button></div>
    <div className="price-line"><strong>₹{fmt(d.price)}</strong><span className={d.change_pct>=0?'positive':'negative'}>{pct(d.change_pct)} today</span></div>
    <div className="chart-wrap"><svg viewBox="0 0 100 100" preserveAspectRatio="none"><defs><linearGradient id="fade" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#55e4a5" stopOpacity=".25"/><stop offset="100%" stopColor="#55e4a5" stopOpacity="0"/></linearGradient></defs><polygon points={`0,100 ${points} 100,100`} fill="url(#fade)"/><polyline points={points} fill="none" stroke="#55e4a5" strokeWidth="1.6" vectorEffect="non-scaling-stroke"/></svg><div className="chart-labels"><span>90 trading days</span><span>Latest close</span></div></div>
    <div className="signal-box"><div><span className="muted tiny">RULE-BASED SCORE</span><div className="signal-title">{d.signal}</div></div><div className={`score-ring ${d.score>=68?'good':d.score>=48?'mid':'bad'}`}><b>{d.score}</b><small>/100</small></div></div>
    <div className="target-grid"><div><span>10% target</span><b>₹{fmt(d.target_10pct)}</b><small>Not a forecast</small></div><div><span>Indicative stop</span><b>₹{fmt(d.indicative_stop)}</b><small>ATR-based reference</small></div><div><span>Reward / risk</span><b>{d.reward_risk==null?'—':`${fmt(d.reward_risk)}x`}</b><small>Before costs/slippage</small></div></div>
    <div className="section-title">Momentum & trend</div>
    <div className="metrics-grid">{[['RSI (14)',fmt(d.rsi14)],['ADX (14)',fmt(d.adx14)],['MACD',fmt(d.macd)],['Rel. volume',d.relative_volume==null?'—':`${fmt(d.relative_volume)}x`],['5-day return',pct(d.return_5d_pct)],['20-day return',pct(d.return_20d_pct)],['SMA 20',fmt(d.sma20)],['SMA 50',fmt(d.sma50)],['SMA 200',fmt(d.sma200)],['ATR (14)',fmt(d.atr14)]].map(([k,v])=><div className="metric" key={k}><span>{k}</span><b>{v}</b></div>)}</div>
    {d.fundamentals && <><div className="section-title">Fundamentals <span className="pill">Yahoo data</span></div><div className="metrics-grid">{[['Market cap',d.fundamentals.market_cap?`₹${fmt(d.fundamentals.market_cap/1e7)} Cr`:'—'],['Trailing P/E',fmt(d.fundamentals.trailing_pe)],['Forward P/E',fmt(d.fundamentals.forward_pe)],['ROE',d.fundamentals.roe==null?'—':pct(d.fundamentals.roe*100)],['Profit margin',d.fundamentals.profit_margin==null?'—':pct(d.fundamentals.profit_margin*100)],['Revenue growth',d.fundamentals.revenue_growth==null?'—':pct(d.fundamentals.revenue_growth*100)],['Debt / equity',fmt(d.fundamentals.debt_to_equity)],['Sector',d.fundamentals.sector||'—']].map(([k,v])=><div className="metric" key={k}><span>{k}</span><b>{v}</b></div>)}</div></>}
    <div className="section-title">What supports the setup</div><ul className="reason-list">{(d.reasons||[]).map((x,i)=><li className="positive-item" key={i}>{x}</li>)}{!(d.reasons||[]).length&&<li>No strong confirmation detected.</li>}</ul>
    <div className="section-title">Risks / missing confirmation</div><ul className="reason-list risks">{(d.risks||[]).slice(0,5).map((x,i)=><li key={i}>{x}</li>)}</ul>
  </section>
}
createRoot(document.getElementById('root')).render(<App />)
