import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, CalendarDays, CheckCircle2, CircleDollarSign, Loader2, ShieldCheck, Sparkles, WalletCards, XCircle } from 'lucide-react';

type Request = { request_id: string; user_id: string; date: string; amount: number; type: string; text: string };
type Decision = { request_id: string; amount_safe_to_pay: number; affordability_status: string; recommended_payment_method: string; payment_plan: string; earliest_date_for_full_payment: string; spending_changes_needed: string; decision_explanation: string };
type Overview = { requests: number; users: number; events: number; messages: number; currencies: string[]; total_balance: number };

const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';
const money = (n: number, currency = 'INR') => new Intl.NumberFormat('en-IN', { style: 'currency', currency, maximumFractionDigits: 2 }).format(n);

export default function App() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [requests, setRequests] = useState<Request[]>([]);
  const [selected, setSelected] = useState('');
  const [decision, setDecision] = useState<Decision | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    Promise.all([fetch(`${API}/api/overview`).then(r => r.json()), fetch(`${API}/api/requests`).then(r => r.json())])
      .then(([o, r]) => { setOverview(o); setRequests(r.requests); if (r.requests[0]) setSelected(r.requests[0].request_id); })
      .catch(() => setError('Backend is offline. Start it with: python -m backend.main --serve'));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setLoading(true); setError('');
    fetch(`${API}/api/decision?request_id=${encodeURIComponent(selected)}`)
      .then(r => r.json()).then(d => { if (d.error) throw new Error(d.error); setDecision(d); })
      .catch(e => setError(e.message)).finally(() => setLoading(false));
  }, [selected]);

  const currentRequest = useMemo(() => requests.find(r => r.request_id === selected), [requests, selected]);
  const safeRatio = decision && currentRequest ? Math.min(100, (decision.amount_safe_to_pay / currentRequest.amount) * 100) : 0;
  const status = decision?.affordability_status || '';
  const positive = status === 'affordable_now' || status === 'affordable_with_plan';

  return <div className="app-shell">
    <header className="topbar"><div className="brand"><div className="brand-mark"><Sparkles size={18}/></div><div><strong>BUY OR WAIT</strong><span>Financial decision engine</span></div></div><div className="live"><span/> deterministic safety layer</div></header>
    <main>
      <section className="hero"><div><div className="eyebrow">PERSONAL FINANCE · 90-DAY SAFETY WINDOW</div><h1>Make the purchase.<br/><em>Only when it makes sense.</em></h1><p>The agent reconstructs your cash-flow, protects your minimum balance, and turns a purchase request into a clear financial decision.</p></div><div className="hero-card"><ShieldCheck size={22}/><span>Safety verified</span><b>Every day in the forecast matters.</b></div></section>

      <section className="stats">{[['Requests', overview?.requests ?? '—'], ['Users', overview?.users ?? '—'], ['Events', overview?.events ?? '—'], ['Messages', overview?.messages ?? '—']].map(([label,value]) => <div className="stat" key={label as string}><span>{label}</span><strong>{value}</strong></div>)}</section>

      <section className="workspace">
        <aside className="request-panel"><div className="section-label">PURCHASE REQUESTS</div>{requests.map(r => <button className={`request ${r.request_id === selected ? 'selected' : ''}`} key={r.request_id} onClick={() => setSelected(r.request_id)}><div><b>{r.request_id.replace('request_', 'Request ')}</b><span>{r.text}</span></div><ArrowRight size={16}/></button>)}</aside>
        <section className="decision-panel">
          {loading ? <div className="loading"><Loader2 className="spin"/><span>Running financial verification…</span></div> : error ? <div className="error"><XCircle/><b>{error}</b></div> : decision && currentRequest ? <>
            <div className="decision-head"><div><div className="section-label">AGENT DECISION · {decision.request_id}</div><h2>{positive ? (status === 'affordable_now' ? 'You can buy this.' : 'You can make this work.') : 'Wait before buying.'}</h2></div><div className={`status ${status}`}>{positive ? <CheckCircle2 size={17}/> : <CalendarDays size={17}/>} {status.replaceAll('_',' ')}</div></div>
            <div className="amount-grid"><div className="big-card"><span>Requested</span><strong>{money(currentRequest.amount, overview?.currencies?.[0] || 'INR')}</strong><small>{currentRequest.type}</small></div><div className="big-card accent"><span>Safe to pay today</span><strong>{money(decision.amount_safe_to_pay, overview?.currencies?.[0] || 'INR')}</strong><small>{Math.round(safeRatio)}% of requested amount</small><div className="meter"><i style={{width: `${safeRatio}%`}}/></div></div></div>
            <div className="details"><div className="detail"><WalletCards/><span>Recommended method</span><b>{decision.recommended_payment_method.replaceAll('_',' ')}</b></div><div className="detail"><CalendarDays/><span>Earliest full payment</span><b>{decision.earliest_date_for_full_payment || 'Plan required'}</b></div></div>
            <div className="explanation"><div className="section-label">WHY THE AGENT SAYS THIS</div><p>{decision.decision_explanation}</p></div>
            <div className="plan"><div><div className="section-label">PAYMENT PLAN</div><b>{decision.payment_plan === 'none' ? 'No additional plan required' : decision.payment_plan}</b></div><div className="change"><span>Spending changes</span><strong>{decision.spending_changes_needed}</strong></div></div>
          </> : <div className="empty"><CircleDollarSign size={40}/><h2>Select a purchase</h2><p>The agent will run the safety analysis here.</p></div>}
        </section>
      </section>
    </main>
  </div>;
}
