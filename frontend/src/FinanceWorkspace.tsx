import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { ArrowDownUp, Plus, Download, CreditCard, Landmark, Wallet, RefreshCw, X, Pencil, CornerUpLeft } from 'lucide-react';
import type { components } from './api/schema';
import { api } from './api/client';
import { Button } from './components/ui/button';
import { ReportChart } from './components/ReportChart';
import { ReceiptManager } from './components/ReceiptManager';

type Account = components['schemas']['AccountOutput'];
type Category = components['schemas']['CategoryOutput'];
type Txn = components['schemas']['TransactionOutput'];
type TxnInput = components['schemas']['TransactionInput'];
type Report = {id:string; document: ReportDocument};
type ReportDocument = {
  metadata: {as_of:string;report_currency:string;period_start:string;period_end_exclusive:string;timezone:string;snapshot_id:string};
  metrics: Record<string,{value:string|null;status:string}>;
  categories: {id:string;name:string;kind:string;value:string}[];
  monthly: {month:string;income:string;expense:string;balance:string}[];
  net_worth_trend: {date:string;value:string|null}[];
  rows: (Txn & {income:string;expense:string})[];
  warnings: string[];
};
const message = (e:unknown) => e instanceof Error ? e.message : '操作未完成，請稍後再試。';
const kinds:Record<string,string> = {bank:'銀行',cash:'現金',credit_card:'信用卡',income:'收入',expense:'支出',transfer:'轉帳',refund:'退款',opening:'期初',adjustment:'調整'};
const fmt = (value:string|null, currency='') => value === null ? '—' : `${currency} ${new Intl.NumberFormat('zh-TW',{minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(value))}`.trim();
function todayIn(zone:string) {
  const parts = new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());
  return ['year','month','day'].map(type=>parts.find(p=>p.type===type)!.value).join('-');
}
const dayAfter = (day:string) => {const d=new Date(day+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+1);return d.toISOString().slice(0,10);};
function useSubmitKey() {
  const last = useRef({body:'',key:''});
  return (body:unknown) => {const encoded=JSON.stringify(body);if(last.current.body!==encoded)last.current={body:encoded,key:crypto.randomUUID()};return last.current.key;};
}
function Modal({title,children,onClose,busy=false}:{title:string;children:ReactNode;onClose:()=>void;busy?:boolean}) {
  const ref=useRef<HTMLDialogElement>(null);
  useEffect(()=>{ref.current?.showModal();return()=>ref.current?.close();},[]);
  return <dialog ref={ref} className="finance-dialog" aria-label={title} onCancel={e=>{e.preventDefault();if(!busy)onClose();}}><div className="dialog-heading"><h2>{title}</h2><button aria-label="關閉" onClick={onClose} disabled={busy}><X size={21}/></button></div>{children}</dialog>;
}
export function FinanceWorkspace({tab,currency,timezone}:{tab:string;currency:string;timezone:string}) {
  const today=todayIn(timezone);
  const [start,setStart]=useState(today.slice(0,8)+'01'),[end,setEnd]=useState(today);
  const [filterAccount,setFilterAccount]=useState(''),[filterCategory,setFilterCategory]=useState(''),[query,setQuery]=useState('');
  const [accounts,setAccounts]=useState<Account[]>([]),[categories,setCategories]=useState<Category[]>([]);
  const [txns,setTxns]=useState<Txn[]>([]),[total,setTotal]=useState(0),[page,setPage]=useState(0);
  const [report,setReport]=useState<Report|null>(null),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [busy,setBusy]=useState(false),[downloading,setDownloading]=useState(false),[revision,setRevision]=useState(0);
  const [accountEditor,setAccountEditor]=useState<Account|'new'|null>(null);
  const [editor,setEditor]=useState<{txn?:Txn;refund?:Txn}|null>(null),[voiding,setVoiding]=useState<Txn|null>(null);
  const [categoryEditor,setCategoryEditor]=useState(false),[quoteEditor,setQuoteEditor]=useState(false);
  const [receipts,setReceipts]=useState<Txn|null>(null);
  const sequence=useRef(0);
  const filters={start:start||today,end_exclusive:dayAfter(end || today),account_id:filterAccount||null,category_id:filterCategory||null,query};
  const load=useCallback(async()=>{
    const seq=++sequence.current;setBusy(true);setError('');
    try {
      const [a,c]=await Promise.all([api<Account[]>('/accounts'),api<Category[]>('/categories')]);
      if(seq!==sequence.current)return;
      setAccounts(a);setCategories(c);
      if(tab==='overview') {
        const next=await api<Report>('/reports',{method:'POST',body:JSON.stringify(filters)});
        if(seq===sequence.current)setReport(next);
      } else if(tab==='transactions') {
        const params=new URLSearchParams({start:filters.start,end_exclusive:filters.end_exclusive,query,offset:String(page*25),limit:'25'});
        if(filterAccount)params.set('account_id',filterAccount);if(filterCategory)params.set('category_id',filterCategory);
        const next=await api<components['schemas']['TransactionPage']>('/transactions?'+params);
        if(seq===sequence.current){setTxns(next.items);setTotal(next.total);}
      }
    } catch(e){if(seq===sequence.current)setError(message(e));}
    finally {if(seq===sequence.current)setBusy(false);}
  },[tab,start,end,filterAccount,filterCategory,query,page,revision]);
  useEffect(()=>{const timer=setTimeout(()=>void load(),200);return()=>{clearTimeout(timer);sequence.current++;};},[load]);
  function saved(text:string) {setNotice(text);setAccountEditor(null);setEditor(null);setVoiding(null);setCategoryEditor(false);setQuoteEditor(false);setRevision(r=>r+1);}
  async function download() {
    if(!report)return;setDownloading(true);setError('');
    try {const blob=await api<Blob>(`/reports/${report.id}/csv`,{},true,'blob');const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`finance-report-${report.id}.zip`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
    catch(e){setError(message(e));}finally{setDownloading(false);}
  }
  const doc=report?.document;
  const chartOptions=doc ? {
    monthly: {legend:{top:0},xAxis:{type:'category',data:doc.monthly.map(m=>m.month)},yAxis:{type:'value'},series:[{name:'收入',type:'bar',data:doc.monthly.map(m=>Number(m.income))},{name:'支出淨額',type:'bar',data:doc.monthly.map(m=>Number(m.expense))}]},
    categories: {grid:{left:95,right:20,top:15,bottom:30},xAxis:{type:'value'},yAxis:{type:'category',data:doc.categories.filter(c=>c.kind==='expense').map(c=>c.name)},series:[{type:'bar',data:doc.categories.filter(c=>c.kind==='expense').map(c=>Number(c.value)),barMaxWidth:22}]},
    worth: {xAxis:{type:'category',data:doc.net_worth_trend.map(m=>m.date)},yAxis:{type:'value',scale:true},series:[{name:'淨資產',type:'line',connectNulls:false,data:doc.net_worth_trend.map(m=>m.value===null?null:Number(m.value)),symbolSize:7}]}
  } : null;
  return <>
    <div className="page-heading"><div><p className="eyebrow">{tab==='accounts'?'YOUR ACCOUNTS':tab==='transactions'?'EVERY LITTLE DETAIL':'YOUR MONEY, AT A GLANCE'}</p><h1>{tab==='accounts'?'給每一筆錢，一個位置。':tab==='transactions'?'記下生活，慢慢累積。':'從容整理，每一筆生活。'}</h1><p>{tab==='accounts'?'銀行、現金與信用卡，一起掌握。':tab==='transactions'?'收支、轉帳與退款，都有清楚的紀錄。':`以 ${currency} 看懂收支，依 ${timezone} 記錄日期。`}</p></div><Button onClick={()=>tab==='accounts'?setAccountEditor('new'):setEditor({})} disabled={tab!=='accounts'&&!accounts.some(a=>!a.archived)}><Plus size={17}/>{tab==='accounts'?'新增帳戶':'新增交易'}</Button></div>
    {error&&<div className="alert" role="alert">{error} {report&&'目前保留上次快照，資料可能已過期。'}<button onClick={()=>void load()}>重試</button></div>}
    {notice&&<p className="success notice" role="status">{notice}</p>}
    {tab!=='accounts'&&<section className="filter-panel" aria-label="報表與明細篩選"><label>開始日期<input type="date" required value={start} onChange={e=>{setStart(e.target.value);setPage(0);}}/></label><label>結束日期（含）<input type="date" required min={start} value={end} onChange={e=>{setEnd(e.target.value);setPage(0);}}/></label><label>帳戶<select value={filterAccount} onChange={e=>{setFilterAccount(e.target.value);setPage(0);}}><option value="">全部帳戶</option>{accounts.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label><label>分類<select value={filterCategory} onChange={e=>{setFilterCategory(e.target.value);setPage(0);}}><option value="">全部分類</option>{categories.map(c=><option key={c.id} value={c.id}>{c.parent_id?'↳ ':''}{c.name}</option>)}</select></label><label className="filter-search">搜尋商家、備註、標籤<input value={query} maxLength={100} onChange={e=>{setQuery(e.target.value);setPage(0);}} placeholder="搜尋生活中的一筆…"/></label><Button variant="secondary" onClick={()=>void load()} disabled={busy} aria-label="更新資料"><RefreshCw size={16}/></Button></section>}
    {busy&&<p className="muted" role="status">正在更新資料…</p>}
    {tab==='accounts'&&<>
      <div className="account-grid">{accounts.map(a=><section className="account-card" key={a.id}><div className="account-top"><span className="account-icon">{a.kind==='credit_card'?<CreditCard/>:a.kind==='cash'?<Wallet/>:<Landmark/>}</span><span>{kinds[a.kind]} · {a.currency}{a.archived?' · 已封存':''}</span><button aria-label={`編輯${a.name}`} onClick={()=>setAccountEditor(a)}><Pencil size={16}/></button></div><h2>{a.name}</h2><p className="balance-label">{a.kind==='credit_card'?(Number(a.balance)<0?'溢繳（負欠款）':'目前欠款'):'目前餘額'}</p><strong className="account-balance">{fmt(a.balance,a.currency)}</strong><p className="footnote">{a.include_in_net_worth?'納入淨資產':'未納入淨資產'}{a.archived?' · 歷史餘額保留':''}</p></section>)}</div>
      {!accounts.length&&!busy&&<div className="empty-state"><Wallet size={38}/><h2>從常用的帳戶開始</h2><p>建立銀行、現金或信用卡帳戶，填入開始記帳時的餘額。</p><Button onClick={()=>setAccountEditor('new')}>建立第一個帳戶</Button></div>}
      <section className="panel finance-help"><h2>讓餘額保持清楚</h2><p>期初餘額不計收入。繳信用卡費請使用「轉帳」，刷卡消費才記為支出。信用卡欠款填正數，溢繳填負數。</p><Button variant="secondary" onClick={()=>setQuoteEditor(true)}>設定外幣估值匯率</Button><p className="footnote">目前支援 USD / TWD 與人工匯率；沒有估值匯率時，淨資產會標示資料不完整。</p></section>
    </>}
    {tab==='transactions'&&<><div className="section-heading"><h2>交易紀錄 <span>{total} 筆</span></h2><Button variant="secondary" onClick={()=>setCategoryEditor(true)}>管理分類</Button></div><TransactionTable rows={txns} onEdit={txn=>setEditor({txn})} onRefund={refund=>setEditor({refund})} onVoid={setVoiding} onReceipts={setReceipts}/><div className="pagination"><Button variant="secondary" disabled={page===0||busy} onClick={()=>setPage(p=>p-1)}>上一頁</Button><span>第 {page+1} 頁，共 {Math.max(1,Math.ceil(total/25))} 頁</span><Button variant="secondary" disabled={(page+1)*25>=total||busy} onClick={()=>setPage(p=>p+1)}>下一頁</Button></div></>}
    {tab==='overview'&&doc&&chartOptions&&<>
      <div className="metric-grid">{[['income','期間收入'],['expense','支出淨額'],['balance','期間結餘'],['net_worth','期末淨資產']].map(([key,label])=><section className="metric-card" key={key}><p>{label}</p><strong>{fmt(doc.metrics[key].value,currency)}</strong><small>{doc.metrics[key].status==='partial'?'缺少估值匯率':doc.metrics[key].status==='not_applicable'?'此篩選不適用':key==='expense'?'已扣除本期退款':key==='net_worth'?'包含所選帳戶的期末餘額':'依所選期間與條件'}</small></section>)}</div>
      <div className="report-toolbar"><span>儲蓄率 <strong>{doc.metrics.savings_rate.value===null?'不適用（收入 ≤ 0）':doc.metrics.savings_rate.value+'%'}</strong></span><Button variant="secondary" onClick={download} disabled={downloading||busy||!!error}><Download size={16}/>{downloading?'準備下載…':'匯出 CSV（ZIP）'}</Button></div>
      <div className="charts-grid"><section className="panel"><h2>收支走勢</h2><ReportChart label="月度收入與支出淨額" option={chartOptions.monthly}/><div className="preset-buttons"><button onClick={()=>{const d=new Date(today+'T12:00:00Z');d.setUTCMonth(d.getUTCMonth()-5,1);setStart(d.toISOString().slice(0,10));}}>最近六個月</button><button onClick={()=>setStart(today.slice(0,8)+'01')}>本月</button><button onClick={()=>setStart(today.slice(0,4)+'-01-01')}>今年</button></div></section><section className="panel"><h2>支出分類</h2>{doc.categories.some(c=>c.kind==='expense')?<ReportChart label="支出分類，退款以負數扣除" option={chartOptions.categories}/>:<p className="chart-empty">這個期間尚無支出紀錄。</p>}<div className="category-chips">{doc.categories.filter(c=>c.kind==='expense').map(c=><button key={c.id} onClick={()=>setFilterCategory(c.id)}>{c.name} · {fmt(c.value)}</button>)}</div></section><section className="panel full-width"><h2>淨資產走勢</h2>{doc.net_worth_trend.length?<ReportChart label="期末淨資產歷史重建曲線，缺值不連線" option={chartOptions.worth}/>:<p className="chart-empty">請清除分類與搜尋篩選以檢視淨資產。</p>}</section></div>
      <details className="report-details"><summary>同一快照的明細與報表資訊（{doc.rows.length} 筆）</summary><div className="table-scroll"><table><thead><tr><th>日期</th><th>帳戶</th><th>說明</th><th>收入</th><th>支出淨額</th></tr></thead><tbody>{doc.rows.map(r=><tr key={r.id}><td>{r.occurred_on}</td><td>{r.account}</td><td>{r.merchant||r.note||kinds[r.kind]}</td><td>{fmt(r.income)}</td><td>{fmt(r.expense)}</td></tr>)}</tbody></table></div><p>快照：{report!.id}<br/>截至：{new Date(doc.metadata.as_of).toLocaleString('zh-TW')} · {doc.metadata.timezone}<br/>期間：{doc.metadata.period_start} 至 {doc.metadata.period_end_exclusive}（結束日不含）</p></details>
      <div className="report-notes">{doc.warnings.map((w,i)=><p key={i}>{w}</p>)}</div>
      {!accounts.length&&<div className="empty-state"><h2>準備好開始記帳了</h2><p>先建立常用帳戶，這裡就會隨著每筆紀錄呈現變化。</p><Button onClick={()=>setAccountEditor('new')}>建立第一個帳戶</Button></div>}
    </>}
    {receipts&&<ReceiptManager transactionId={receipts.id} description={`${receipts.occurred_on} · ${receipts.merchant||receipts.note||kinds[receipts.kind]} · ${fmt(receipts.amount,receipts.currency)}`} onClose={()=>setReceipts(null)}/>}
    {accountEditor&&<AccountForm account={accountEditor==='new'?undefined:accountEditor} currency={currency} today={today} onClose={()=>setAccountEditor(null)} onSave={saved}/>}
    {editor&&<TransactionForm {...editor} accounts={accounts} categories={categories} currency={currency} today={today} onClose={()=>setEditor(null)} onSave={saved} onCategory={()=>setCategoryEditor(true)}/>}
    {voiding&&<VoidForm txn={voiding} onClose={()=>setVoiding(null)} onSave={saved}/>}
    {categoryEditor&&<CategoryForm categories={categories} onClose={()=>setCategoryEditor(false)} onSave={async()=>{setCategories(await api('/categories'));setCategoryEditor(false);setNotice('分類已新增。');}}/>}
    {quoteEditor&&<QuoteForm currency={currency} today={today} onClose={()=>setQuoteEditor(false)} onSave={saved}/>}
  </>;
}

function AccountForm({account,currency,today,onClose,onSave}:{account?:Account;currency:string;today:string;onClose:()=>void;onSave:(text:string)=>void}) {
  const [kind,setKind]=useState(account?.kind??'bank'),[ccy,setCcy]=useState(account?.currency??currency),[busy,setBusy]=useState(false),[error,setError]=useState('');const key=useSubmitKey();
  async function submit(e:FormEvent<HTMLFormElement>) {e.preventDefault();const f=new FormData(e.currentTarget);setBusy(true);setError('');
    const body=account?{name:f.get('name'),include_in_net_worth:f.has('include'),archived:f.has('archived'),expected_revision:account.revision}:{name:f.get('name'),kind,currency:ccy,opening_balance:f.get('opening'),opening_on:f.get('date'),fx_rate:ccy!==currency?(f.get('fx')||null):null,include_in_net_worth:f.has('include')};
    try {await api('/accounts'+(account?'/'+account.id:''),{method:account?'PUT':'POST',headers:{'Idempotency-Key':key(body)},body:JSON.stringify(body)});onSave(account?'帳戶已更新。':'帳戶已建立。');}catch(e){setError(message(e));}finally{setBusy(false);}
  }
  return <Modal title={account?'編輯帳戶':'新增帳戶'} onClose={onClose} busy={busy}><form onSubmit={submit}><label>帳戶名稱<input name="name" required maxLength={100} defaultValue={account?.name} placeholder="例如：日常銀行、錢包"/></label><div className="form-grid"><label>帳戶類型<select disabled={!!account} value={kind} onChange={e=>setKind(e.target.value)}><option value="bank">銀行</option><option value="cash">現金</option><option value="credit_card">信用卡</option></select></label><label>帳戶幣別<select disabled={!!account} value={ccy} onChange={e=>setCcy(e.target.value)}><option>USD</option><option>TWD</option></select></label></div>{!account&&<><div className="form-grid"><label>{kind==='credit_card'?'期初欠款（溢繳填負數）':'期初餘額'}<input name="opening" inputMode="decimal" required defaultValue="0" pattern="-?[0-9]+(\.[0-9]{1,2})?"/></label><label>開始記帳日期<input type="date" name="date" required defaultValue={today}/></label></div>{ccy!==currency&&<label>期初匯率（1 {ccy} = 多少 {currency}）<input name="fx" inputMode="decimal" placeholder="非零期初餘額時必填"/></label>}<p className="footnote">期初餘額不列入收支；入帳後幣別固定。</p></>}
    <label className="checkbox-label"><input type="checkbox" name="include" defaultChecked={account?.include_in_net_worth??true}/>納入淨資產</label>{account&&<><label className="checkbox-label"><input name="archived" type="checkbox" defaultChecked={account.archived}/>封存帳戶（停止新增記帳）</label><p className="footnote">封存會保留餘額 {fmt(account.balance,account.currency)}；勾選「納入淨資產」時仍會計入。</p></>}{error&&<p role="alert" className="error">{error}</p>}<div className="form-actions"><Button variant="secondary" type="button" onClick={onClose} disabled={busy}>取消</Button><Button type="submit" disabled={busy}>{busy?'儲存中…':'儲存帳戶'}</Button></div></form></Modal>;
}
function TransactionTable({rows,onEdit,onRefund,onVoid,onReceipts}:{rows:Txn[];onEdit:(txn:Txn)=>void;onRefund:(txn:Txn)=>void;onVoid:(txn:Txn)=>void;onReceipts:(txn:Txn)=>void}) {
  if(!rows.length)return <div className="empty-state"><ArrowDownUp size={35}/><h2>這個期間還沒有紀錄</h2><p>新增一筆交易，或調整上方的日期與篩選。</p></div>;
  return <div className="table-scroll"><table className="transactions-table"><thead><tr><th>日期 / 類型</th><th>說明 / 分類</th><th>帳戶</th><th>金額</th><th>操作</th></tr></thead><tbody>{rows.map(t=><tr key={t.id}><td>{t.occurred_on}<small>{kinds[t.kind]}</small></td><td><strong>{t.merchant||t.note||kinds[t.kind]}</strong><small>{t.categories.join(' / ')} {t.tags.map(tag=>'#'+tag).join(' ')}</small></td><td>{t.account}{t.to_account&&<small>→ {t.to_account}</small>}</td><td className="money-cell">{fmt(t.amount,t.currency)}{t.fee!=='0'&&<small>手續費 {fmt(t.fee)}</small>}</td><td><button className="text-button receipt-link" onClick={()=>onReceipts(t)}>收據</button>{!['opening','adjustment'].includes(t.kind)&&<div className="row-actions"><button onClick={()=>onEdit(t)} aria-label={`更正 ${t.merchant||t.note||kinds[t.kind]}`}><Pencil size={15}/>更正</button>{t.kind==='expense'&&<button onClick={()=>onRefund(t)}><CornerUpLeft size={15}/>退款</button>}<button onClick={()=>onVoid(t)}>作廢</button></div>}</td></tr>)}</tbody></table></div>;
}
function TransactionForm({txn,refund,accounts,categories,currency,today,onClose,onSave,onCategory}:{txn?:Txn;refund?:Txn;accounts:Account[];categories:Category[];currency:string;today:string;onClose:()=>void;onSave:(text:string)=>void;onCategory:()=>void}) {
  const [kind,setKind]=useState<TxnInput['kind']>((refund?'refund':txn?.kind??'expense') as TxnInput['kind']);
  const [account,setAccount]=useState(txn?.account_id??refund?.account_id??accounts.find(a=>!a.archived)?.id??'');
  const [to,setTo]=useState(txn?.to_account_id??'');const [split,setSplit]=useState<{category_id:string;amount:string}[]>(txn?.splits.length?txn.splits:[{category_id:'',amount:''}]);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');const key=useSubmitKey();
  const source=accounts.find(a=>a.id===account),destination=accounts.find(a=>a.id===to);const cross=source&&destination&&source.currency!==destination.currency;
  const available=categories.filter(c=>!c.archived&&c.kind===kind);
  async function submit(e:FormEvent<HTMLFormElement>) {e.preventDefault();const f=new FormData(e.currentTarget);setBusy(true);setError('');
    const body={kind,occurred_on:f.get('date'),account_id:account,amount:f.get('amount'),fx_rate:source?.currency!==currency?f.get('fx'):null,
      category_id:['income','expense'].includes(kind)&&split.length===1?split[0].category_id:null,
      splits:['income','expense'].includes(kind)&&split.length>1?split:[],to_account_id:kind==='transfer'?to:null,
      received_amount:kind==='transfer'&&cross?f.get('received'):null,received_fx_rate:kind==='transfer'&&cross&&destination?.currency!==currency?f.get('receivedfx'):null,
      fee:kind==='transfer'?(f.get('fee')||'0'):'0',fee_category_id:kind==='transfer'?(f.get('feecategory')||null):null,
      refund_of_id:kind==='refund'?(refund?.id??txn?.refund_of_id):null,merchant:f.get('merchant')||'',note:f.get('note')||'',tags:String(f.get('tags')||'').split(',').map(s=>s.trim()).filter(Boolean),
      ...(txn?{expected_revision:txn.revision,reason:f.get('reason')}:{})};
    try {await api('/transactions'+(txn?'/'+txn.id:''),{method:txn?'PUT':'POST',headers:{'Idempotency-Key':key(body)},body:JSON.stringify(body)});onSave(txn?'交易已更正，原始分錄已保留。':'交易已入帳。');}catch(e){setError(message(e));}finally{setBusy(false);}
  }
  return <Modal title={refund?'登記退款':txn?'更正交易':'新增交易'} onClose={onClose} busy={busy}><form onSubmit={submit}><div className="segmented">{(refund||kind==='refund'?['refund']:['expense','income','transfer']).map(k=><button key={k} type="button" aria-pressed={kind===k} disabled={!!txn&&kind!==k} onClick={()=>{setKind(k as TxnInput['kind']);setSplit([{category_id:'',amount:''}]);}}>{kinds[k]}</button>)}</div>{refund&&<p className="form-hint">原交易：{refund.occurred_on} · {refund.merchant||refund.categories.join(' / ')} · {fmt(refund.amount,refund.currency)}。退款將依原分類扣減支出。</p>}
    <div className="form-grid"><label>{kind==='transfer'?'轉出帳戶':'記帳帳戶'}<select required value={account} onChange={e=>setAccount(e.target.value)}><option value="">請選擇</option>{accounts.filter(a=>!a.archived||a.id===account).map(a=><option key={a.id} value={a.id}>{a.name} · {a.currency}{a.archived?'（已封存）':''}</option>)}</select></label><label>帳務日期<input name="date" type="date" required defaultValue={txn?.occurred_on??today}/></label></div>
    <label>{kind==='transfer'?'轉出本金':'金額'}（{source?.currency??currency}）<input name="amount" required inputMode="decimal" defaultValue={txn?.amount} pattern="[0-9]+(\.[0-9]{1,2})?" placeholder="0.00"/></label>
    {source?.currency!==currency&&<label>入帳匯率（1 {source?.currency} = 多少 {currency}）<input name="fx" required inputMode="decimal" defaultValue={txn?.fx_rate}/></label>}
    {kind==='transfer'&&<><label>轉入帳戶<select required value={to} onChange={e=>setTo(e.target.value)}><option value="">請選擇</option>{accounts.filter(a=>!a.archived&&a.id!==account).map(a=><option key={a.id} value={a.id}>{a.name} · {a.currency}</option>)}</select></label>{cross&&<><label>實際轉入金額（{destination?.currency}）<input name="received" required inputMode="decimal" defaultValue={txn?.received_amount??''}/></label>{destination?.currency!==currency&&<label>轉入匯率（1 {destination?.currency} = 多少 {currency}）<input name="receivedfx" required inputMode="decimal" defaultValue={txn?.received_fx_rate??''}/></label>}</>}<div className="form-grid"><label>手續費（{source?.currency}）<input name="fee" inputMode="decimal" defaultValue={txn?.fee??'0'}/></label><label>手續費分類<select name="feecategory" defaultValue={txn?.fee_category_id??''}><option value="">無費用可留空</option>{categories.filter(c=>c.kind==='expense').map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label></div><p className="footnote">轉帳本金及信用卡還款不計支出；手續費單獨計入。</p></>}
    {['income','expense'].includes(kind)&&<><div className="section-heading compact"><h3>分類{split.length>1?'分攤':''}</h3><button type="button" className="text-button" onClick={onCategory}>新增分類</button></div>{split.map((s,i)=><div key={i} className="split-row"><label>分類 {split.length>1?i+1:''}<select required value={s.category_id} onChange={e=>setSplit(v=>v.map((item,j)=>j===i?{...item,category_id:e.target.value}:item))}><option value="">請選擇分類</option>{available.map(c=><option key={c.id} value={c.id}>{c.parent_id?'↳ ':''}{c.name}</option>)}</select></label>{split.length>1&&<><label>分攤金額<input required inputMode="decimal" value={s.amount} onChange={e=>setSplit(v=>v.map((item,j)=>j===i?{...item,amount:e.target.value}:item))}/></label><button type="button" aria-label={`移除分攤${i+1}`} onClick={()=>setSplit(v=>v.filter((_,j)=>j!==i))}><X size={16}/></button></>}</div>)}<button className="text-button" type="button" onClick={()=>setSplit(v=>[...v,{category_id:'',amount:''}])} disabled={split.length>=30}>＋ 加入分類分攤</button></>}
    <div className="form-grid"><label>商家<input name="merchant" maxLength={200} defaultValue={txn?.merchant??refund?.merchant}/></label><label>標籤（逗號分隔）<input name="tags" defaultValue={txn?.tags.join(', ')} placeholder="旅遊, 報帳"/></label></div><label>備註<textarea name="note" rows={2} maxLength={2000} defaultValue={txn?.note}/></label><p className="footnote">收據照片可在入帳後，從交易列的「收據」上傳。</p>{txn&&<label>更正原因<input name="reason" required maxLength={500}/></label>}{error&&<p role="alert" className="error">{error}</p>}<div className="form-actions"><Button variant="secondary" type="button" onClick={onClose} disabled={busy}>取消</Button><Button type="submit" disabled={busy}>{busy?'入帳中…':txn?'儲存更正':'確認入帳'}</Button></div></form></Modal>;
}
function CategoryForm({categories,onClose,onSave}:{categories:Category[];onClose:()=>void;onSave:()=>void|Promise<void>}) {
  const [kind,setKind]=useState('expense'),[busy,setBusy]=useState(false),[error,setError]=useState('');const key=useSubmitKey();
  async function submit(e:FormEvent<HTMLFormElement>){e.preventDefault();const f=new FormData(e.currentTarget);const body={name:f.get('name'),kind,parent_id:f.get('parent')||null};setBusy(true);setError('');try{await api('/categories',{method:'POST',headers:{'Idempotency-Key':key(body)},body:JSON.stringify(body)});await onSave();}catch(e){setError(message(e));}finally{setBusy(false);}}
  return <Modal title="分類管理" onClose={onClose} busy={busy}><div className="category-chips">{categories.map(c=><span key={c.id}>{c.parent_id?'↳ ':''}{c.name} · {kinds[c.kind]}</span>)}</div><form onSubmit={submit}><label>分類名稱<input name="name" required maxLength={80}/></label><div className="form-grid"><label>收支類型<select value={kind} onChange={e=>setKind(e.target.value)}><option value="expense">支出</option><option value="income">收入</option></select></label><label>上層分類<select key={kind} name="parent"><option value="">無（建立主分類）</option>{categories.filter(c=>c.kind===kind&&!c.parent_id).map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label></div>{error&&<p className="error" role="alert">{error}</p>}<div className="form-actions"><Button type="submit" disabled={busy}>儲存分類</Button></div></form></Modal>;
}
function VoidForm({txn,onClose,onSave}:{txn:Txn;onClose:()=>void;onSave:(text:string)=>void}) {
  const [busy,setBusy]=useState(false),[error,setError]=useState('');const key=useSubmitKey();
  async function submit(e:FormEvent<HTMLFormElement>){e.preventDefault();const f=new FormData(e.currentTarget);const body={reason:f.get('reason'),expected_revision:txn.revision};setBusy(true);setError('');try{await api('/transactions/'+txn.id+'/void',{method:'POST',headers:{'Idempotency-Key':key(body)},body:JSON.stringify(body)});onSave('交易已作廢，原始紀錄已保留。');}catch(e){setError(message(e));}finally{setBusy(false);}}
  return <Modal title="作廢交易" onClose={onClose} busy={busy}><p>{txn.occurred_on} · {txn.account} · {fmt(txn.amount,txn.currency)}</p><p className="form-hint">作廢後將沖回金額，並保留原始分錄與原因。</p><form onSubmit={submit}><label>作廢原因<input name="reason" required maxLength={500}/></label>{error&&<p className="error" role="alert">{error}</p>}<div className="form-actions"><Button type="button" variant="secondary" onClick={onClose} disabled={busy}>取消</Button><Button type="submit" variant="danger" disabled={busy}>確認作廢</Button></div></form></Modal>;
}
function QuoteForm({currency,today,onClose,onSave}:{currency:string;today:string;onClose:()=>void;onSave:(text:string)=>void}) {
  const foreign=currency==='USD'?'TWD':'USD';const [busy,setBusy]=useState(false),[error,setError]=useState('');const key=useSubmitKey();
  async function submit(e:FormEvent<HTMLFormElement>){e.preventDefault();const f=new FormData(e.currentTarget);const body={currency:foreign,effective_on:f.get('date'),rate:f.get('rate'),source:f.get('source')};setBusy(true);setError('');try{await api('/fx-quotes',{method:'POST',headers:{'Idempotency-Key':key(body)},body:JSON.stringify(body)});onSave('估值匯率已保存。請更新總覽查看最新快照。');}catch(e){setError(message(e));}finally{setBusy(false);}}
  return <Modal title="外幣估值匯率" onClose={onClose} busy={busy}><p className="form-hint">用於淨資產換算；不改寫已入帳的收支金額。系統會採估值日以前最新一筆人工匯率。</p><form onSubmit={submit}><label>1 {foreign} 可換多少 {currency}<input name="rate" required inputMode="decimal"/></label><label>匯率日期<input name="date" type="date" required defaultValue={today}/></label><label>來源或填寫原因<input name="source" required maxLength={200} placeholder="例如銀行帳單上的匯率"/></label>{error&&<p className="error" role="alert">{error}</p>}<div className="form-actions"><Button type="submit" disabled={busy}>儲存匯率</Button></div></form></Modal>;
}
