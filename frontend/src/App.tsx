import { useEffect, useState, type FormEvent } from 'react';
import { ArrowRight, Check, ChevronRight, Database, FileClock, LayoutDashboard, Leaf, LockKeyhole, LogOut, RefreshCw, Settings2, ShieldCheck, Wallet } from 'lucide-react';
import { api, ApiError, prepareCsrf } from './api/client';
import type { components } from './api/schema';
import { FinanceWorkspace } from './FinanceWorkspace';
import { Button } from './components/ui/button';
type User = components['schemas']['UserOutput'];
type Status = components['schemas']['StatusOutput'];
type Job = components['schemas']['JobOutput'];
const message = (error: unknown) => error instanceof Error ? error.message : '操作未完成，請稍後再試。';

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState('');
  const [tab, setTab] = useState('overview');
  const [updated, setUpdated] = useState('');
  async function load() {
    try {
      const current = await api<User>('/auth/me'); setUser(current);
      const next = await api<Status>('/status'); setStatus(next);
      setUpdated(new Date().toLocaleTimeString('zh-TW')); setError('');
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {setUser(null); setStatus(null);}
      else setError(message(e));
    } finally {setChecking(false);}
  }
  useEffect(() => { void prepareCsrf().then(load).catch(e => {setError(message(e)); setChecking(false);}); }, []);
  useEffect(() => {if (!user) return; const timer = setInterval(() => {void load();}, 30000); return () => clearInterval(timer);}, [user?.username]);
  async function logout() {
    try {await api('/auth/logout', {method: 'POST'}); setUser(null); setStatus(null); setError('');}
    catch(e) {setError(message(e));}
  }
  if (checking) return <main className="loading" aria-live="polite"><Leaf size={32}/><p>正在連接您的財務空間…</p></main>;
  if (!user) return <Login onLogin={load} connectionError={error}/>;
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark"><Leaf size={22}/></span><div>Finance Tracker<small>我的財務空間</small></div></div>
      <p className="nav-label">工作空間</p>
      <nav aria-label="主要導覽">
        <button aria-current={tab === 'overview' ? 'page' : undefined} onClick={() => setTab('overview')}><LayoutDashboard size={19}/>總覽</button>
        <button aria-current={tab === 'accounts' ? 'page' : undefined} onClick={() => setTab('accounts')}><Wallet size={19}/>帳戶</button>
        <button aria-current={tab === 'transactions' ? 'page' : undefined} onClick={() => setTab('transactions')}><FileClock size={19}/>記帳</button>
        <button aria-current={tab === 'system' ? 'page' : undefined} onClick={() => setTab('system')}><Database size={19}/>系統狀態</button>
        <button aria-current={tab === 'settings' ? 'page' : undefined} onClick={() => setTab('settings')}><Settings2 size={19}/>偏好設定</button>
        <button aria-current={tab === 'security' ? 'page' : undefined} onClick={() => setTab('security')}><ShieldCheck size={19}/>帳號安全</button>
      </nav>
      <div className="sidebar-bottom"><span className="local-dot"/>帳本保存在這台電腦<p>自己的資料，自己掌握。</p></div>
    </aside>
    <div className="main-shell">
      <header className="topbar"><span><span className="muted">我的空間</span><ChevronRight size={14}/>{{overview:'總覽',accounts:'帳戶',transactions:'記帳',system:'系統狀態',settings:'偏好設定',security:'帳號安全'}[tab]}</span><button onClick={logout} className="logout"><LogOut size={16}/>登出</button></header>
      <main className="content">
        {error && <div className="alert" role="alert">{error}<button onClick={load}>重新連線</button></div>}
        {['overview','accounts','transactions'].includes(tab) && user.settings.setup_completed && <FinanceWorkspace tab={tab} currency={user.settings.book_currency!} timezone={user.settings.timezone!}/>}
        {(['overview','accounts','transactions'].includes(tab) && !user.settings.setup_completed || tab === 'system') && <>
          <div className="page-heading"><div><p className="eyebrow">A CLEARER PICTURE</p><h1>從容整理，每一筆生活。</h1><p>歡迎回來，{user.username}。先把您的財務空間準備好。</p></div><span className="phase-tag">基礎環境已建立</span></div>
          <section className="welcome-card"><div><span className="small-label">開始之前</span><h2>{user.settings.setup_completed ? '您的帳本偏好已就緒' : '讓這裡，成為您的帳本'}</h2><p>{user.settings.setup_completed ? '幣別與時區已儲存。可從帳戶頁建立銀行、現金與信用卡帳戶，再開始記帳。' : '選擇常用幣別與帳務時區，讓之後的記帳與報表使用相同標準。'}</p><Button onClick={() => setTab('settings')}>{user.settings.setup_completed ? '查看偏好設定' : '完成首次設定'}<ArrowRight size={16}/></Button></div><div className="welcome-art" aria-hidden="true"><div className="art-disc"><Wallet size={66} strokeWidth={1}/></div><span className="art-chip"><Check size={14}/>本機保存</span></div></section>
          <div className="section-heading"><h2>空間狀態</h2><span>{updated && `上次確認 ${updated}`}{error && ' · 資料可能已過期'}</span></div>
          <div className="status-grid">
            <StatusCard icon={<Database/>} label="資料庫" value={status?.database === 'ready' ? '連線正常' : '尚未確認'} detail="正式資料保存在本機 PostgreSQL"/>
            <StatusCard icon={<FileClock/>} label="最近備份" value={status?.backup_last_success ? new Date(status.backup_last_success).toLocaleString('zh-TW') : '尚無成功備份'} detail={status?.backup_last_error ? '最近備份失敗，請檢查目的地' : status?.backup_overdue ? '備份尚未完成或已超過 26 小時' : '已寫入設定的目的地；外接磁碟須另行確認'}/>
            <StatusCard icon={<ShieldCheck/>} label="登入保護" value={user.totp_enabled ? '雙重驗證已開啟' : '密碼保護'} detail={user.totp_enabled ? '登入時需要密碼與驗證碼' : '可在帳號安全加上第二道保護'}/>
          </div>
          <div className="lower-grid"><section className="panel"><div className="section-heading"><h2>接下來的功能</h2><span>尚未啟用</span></div>{[['01','週期記帳與帳戶對帳','後續 Phase 1 工作'],['02','Numbers XLSX 與 PDF','Phase 1.5 報表檔案'],['03','Telegram 快速記帳','Phase 2 收據與文字記錄']].map(([number,title,desc]) => <div className="roadmap" key={number}><span>{number}</span><div><h3>{title}</h3><p>{desc}</p></div><LockKeyhole size={15}/></div>)}</section>
          <section className="panel"><h2>系統檢查</h2><p className="muted">確認背景工作能排入並完成。</p><Probe/><div className="system-detail"><span>待處理工作</span><strong>{status?.jobs_pending ?? '—'}</strong></div><div className="system-detail"><span>失敗工作</span><strong>{status?.jobs_failed ?? '—'}</strong></div><div className="system-detail"><span>可用空間</span><strong>{status ? `${(status.disk_free_bytes / 1024 ** 3).toFixed(1)} GB` : '—'}</strong></div>{status?.disk_low && <p className="error">磁碟剩餘空間不足 1 GB。</p>}<p className="footnote">AI、Telegram 與 Notion 尚未啟用。</p></section></div>
        </>}
        {tab === 'settings' && <Preferences user={user} onSave={load}/>}
        {tab === 'security' && <Security enabled={user.totp_enabled} onChange={load}/>}
        <footer>Finance Tracker <span>保存在本機 · 以您的步調開始</span></footer>
      </main>
    </div>
  </div>;
}
function StatusCard({icon,label,value,detail}: {icon: React.ReactNode; label: string; value: string; detail: string}) {
  return <section className="status-card"><div className="status-label">{label}{icon}</div><h3>{value}</h3><p>{detail}</p></section>;
}
function Login({onLogin,connectionError}: {onLogin: () => Promise<void>; connectionError: string}) {
  const [error,setError]=useState(''), [busy,setBusy]=useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();const form=new FormData(e.currentTarget);setBusy(true);setError('');
    try {await api('/auth/login',{method:'POST',body:JSON.stringify({username:form.get('username'),password:form.get('password'),code:form.get('code') || ''})});await onLogin();}
    catch(e) {setError(message(e));} finally {setBusy(false);}
  }
  return <main className="login-page"><section className="login-story"><div className="brand"><span className="brand-mark"><Leaf/></span>Finance Tracker</div><div><p className="eyebrow">YOUR MONEY. YOUR SPACE.</p><h1>把生活理清，<br/>從每一筆開始。</h1><p>一個留給自己的財務空間。<br/>帳本留在本機，生活走得更從容。</p><div className="story-lines" aria-hidden="true"><span/><span/><span/><span/><span/><span/></div></div><p className="small-label"><LockKeyhole size={14}/>自己的資料，自己掌握</p></section><section className="login-form"><div className="login-inner"><span className="phase-tag">歡迎回來</span><h2>登入您的財務空間</h2><p className="muted">使用在這台電腦建立的帳號。</p>{(error || connectionError) && <div role="alert" className="alert">{error || connectionError}</div>}<form onSubmit={submit}><label>帳號<input name="username" autoComplete="username" required maxLength={100}/></label><label>密碼<input name="password" type="password" autoComplete="current-password" required maxLength={1024}/></label><label>驗證碼或備援碼 <span className="optional">（有啟用時填寫）</span><input name="code" autoComplete="one-time-code" maxLength={64}/></label><Button disabled={busy} type="submit">{busy ? '登入中…' : '登入'}<ArrowRight size={16}/></Button></form><p className="footnote">第一次使用？請依 README 的初始化步驟，<br/>在本機建立帳號與密碼。</p></div></section></main>;
}
function Preferences({user,onSave}: {user: User; onSave: () => Promise<void>}) {
  const [currency,setCurrency]=useState(user.settings.book_currency ?? ''),[zone,setZone]=useState(user.settings.timezone ?? '');
  const [error,setError]=useState(''),[saved,setSaved]=useState(false),[busy,setBusy]=useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();setBusy(true);setError('');setSaved(false);
    try {await api('/settings',{method:'PUT',body:JSON.stringify({book_currency:currency,timezone:zone,expected_revision:user.settings.settings_revision})});await onSave();setSaved(true);}
    catch(e){setError(message(e));}finally{setBusy(false);}
  }
  return <><div className="page-heading"><div><p className="eyebrow">MAKE IT YOURS</p><h1>偏好設定</h1><p>讓帳本、日期與未來的報表保持一致。</p></div></div><section className="panel narrow"><h2>帳本基本設定</h2><p className="muted">正式入帳後，基準幣別會固定；報表顯示幣別可另行切換。</p><form onSubmit={submit}><label>帳本基準幣別<select required value={currency} onChange={e=>setCurrency(e.target.value)}><option value="" disabled>請選擇幣別</option><option value="USD">USD · 美元</option><option value="TWD">TWD · 新台幣</option></select></label><label>帳務時區<input required list="timezones" placeholder="例如 Asia/Taipei" value={zone} onChange={e=>setZone(e.target.value)}/><datalist id="timezones"><option value="Asia/Taipei"/><option value="America/Los_Angeles"/><option value="America/New_York"/><option value="UTC"/></datalist></label>{error && <p className="error" role="alert">{error}</p>}{saved && <p className="success" role="status">設定已儲存。</p>}<Button disabled={busy} type="submit">{busy?'儲存中…':'儲存設定'}<Check size={16}/></Button></form></section></>;
}
function Security({enabled,onChange}: {enabled: boolean; onChange: () => Promise<void>}) {
  const [password,setPassword]=useState(''),[code,setCode]=useState(''),[secret,setSecret]=useState('');
  const [codes,setCodes]=useState<string[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  async function action(path: string) {
    setBusy(true);setError('');
    try {
      const result=await api<Partial<components['schemas']['TotpSetupOutput'] & components['schemas']['RecoveryOutput']>>('/auth/totp/'+path,{method:'POST',body:JSON.stringify({password,code})});
      if(result.secret)setSecret(result.secret);
      if(result.recovery_codes){setCodes(result.recovery_codes);setSecret('');setPassword('');setCode('');}
      if(path==='disable'){setCodes([]);setSecret('');setPassword('');setCode('');}
      await onChange();
    }catch(e){setError(message(e));}finally{setBusy(false);}
  }
  return <><div className="page-heading"><div><p className="eyebrow">PEACE OF MIND</p><h1>帳號安全</h1><p>為個人的財務資料，多留一層保護。</p></div></div><section className="panel narrow"><h2>雙重驗證 <span className="phase-tag">{enabled?'已開啟':'尚未開啟'}</span></h2><p className="muted">使用驗證器 App 的一次性驗證碼。設定變更需要目前密碼。</p><label>目前密碼<input type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)}/></label>{secret && <div className="secret-box"><p>在驗證器新增帳號，手動輸入此金鑰，再填入驗證碼：</p><code>{secret}</code></div>}{(secret || enabled) && <label>驗證碼或備援碼<input autoComplete="one-time-code" value={code} onChange={e=>setCode(e.target.value)}/></label>}{error && <p role="alert" className="error">{error}</p>}<Button disabled={busy || !password || ((enabled || !!secret) && !code)} variant={enabled?'secondary':'default'} onClick={()=>action(enabled?'disable':secret?'enable':'setup')}>{busy?'處理中…':enabled?'停用雙重驗證':secret?'確認並啟用':'設定雙重驗證'}</Button>{codes.length>0 && <div className="secret-box" role="status"><h3>請保存備援碼</h3><p>每組只能使用一次，只在這次設定完成後顯示。離開此頁前，請保存到安全的位置。</p><pre>{codes.join('\n')}</pre><Button variant="secondary" onClick={()=>setCodes([])}>我已保存</Button></div>}</section></>;
}
function Probe() {
  const [job,setJob]=useState<Job|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  useEffect(()=>{if(!job || ['succeeded','failed','cancelled'].includes(job.status))return;const timer=setInterval(()=>{void api<Job>('/jobs/'+job.id).then(setJob).catch(e=>setError(message(e)));},2000);return()=>clearInterval(timer);},[job?.id,job?.status]);
  async function run(){setBusy(true);setError('');try{setJob(await api<Job>('/jobs/probe',{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()}}));}catch(e){setError(message(e));}finally{setBusy(false);}}
  const states:Record<string,string>={queued:'已排入，等待背景處理',running:'檢查中',retry_wait:'稍後重試',succeeded:'背景工作正常',failed:'檢查失敗，請查看服務狀態',cancelled:'已取消'};
  return <><Button variant="secondary" onClick={run} disabled={busy || !!job && !['succeeded','failed','cancelled'].includes(job.status)}><RefreshCw size={15}/>執行檢查</Button>{job&&<p role="status" className="footnote">{states[job.status]}</p>}{error&&<p role="alert" className="error">{error}</p>}</>;
}
