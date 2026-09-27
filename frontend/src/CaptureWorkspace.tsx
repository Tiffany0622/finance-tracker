import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Camera, FileText, RefreshCw, Plus, X, Check, Send } from 'lucide-react';
import { api } from './api/client';
import type { components } from './api/schema';
import { ReceiptItemEditor } from './components/ReceiptItemEditor';
import { ConfirmDialog } from './components/ConfirmDialog';
import { CategoryForm } from './components/CategoryForm';
import { CategorySuggestions } from './components/CategorySuggestions';
import { Button } from './components/ui/button';

type Draft = components['schemas']['DraftOutput'];
type Proposal = components['schemas']['Proposal'];
type Account = components['schemas']['AccountOutput'];
type Category = components['schemas']['CategoryOutput'];
type Status = components['schemas']['CaptureStatus'];
const message = (e: unknown) => e instanceof Error ? e.message : '操作未完成，請稍後再試。';
const labels: Record<string,string> = {needs_review:'待確認',processing:'處理中',failed:'處理失敗',confirmed:'已入帳',cancelled:'已取消'};

export function CaptureWorkspace({currency}:{currency:string}) {
  const [rows,setRows]=useState<Draft[]>([]),[selected,setSelected]=useState<Draft|null>(null);
  const [accounts,setAccounts]=useState<Account[]>([]),[categories,setCategories]=useState<Category[]>([]),[status,setStatus]=useState<Status|null>(null);
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[page,setPage]=useState(0),[closed,setClosed]=useState(false);
  const [text,setText]=useState('');
  const [upload,setUpload]=useState<{file:File;key:string}|null>(null);
  const textKey=useRef({text:'',key:crypto.randomUUID()});
  const load=useCallback(async()=>{
    try {
      const [d,a,c,s]=await Promise.all([api<Draft[]>(`/capture/drafts?offset=${page*25}&include_closed=${closed}`),api<Account[]>('/accounts'),api<Category[]>('/categories'),api<Status>('/capture/status')]);
      setRows(d);setAccounts(a);setCategories(c);setStatus(s);setError('');
    } catch(e){setError(message(e));}
  },[page,closed]);
  useEffect(()=>{void load(); const timer=setInterval(()=>void load(),5000);return()=>clearInterval(timer);},[load]);
  async function sendFile() {
    if(!upload)return;
    setBusy(true);setError('');setNotice('');
    try {
      const draft=await api<Draft>('/capture/image?filename='+encodeURIComponent(upload.file.name.slice(0,200)),{method:'POST',headers:{'Content-Type':upload.file.type||'application/octet-stream','Idempotency-Key':upload.key},body:upload.file});
      setUpload(null);setSelected(draft);setNotice('照片已保存為草稿，尚未入帳。');await load();
    }catch(e){setError(message(e));}finally{setBusy(false);}
  }
  async function sendText(e:FormEvent) {
    e.preventDefault();if(!text.trim())return;setBusy(true);setError('');setNotice('');
    if(textKey.current.text!==text)textKey.current={text,key:crypto.randomUUID()};
    try {const draft=await api<Draft>('/capture/text',{method:'POST',headers:{'Idempotency-Key':textKey.current.key},body:JSON.stringify({text})});setSelected(draft);setText('');textKey.current={text:'',key:crypto.randomUUID()};await load();}
    catch(e){setError(message(e));}finally{setBusy(false);}
  }
  const connected=status?.bridge_last_seen && Date.now()-Date.parse(status.bridge_last_seen)<60000;
  return <>
    <div className="page-heading"><div><p className="eyebrow">CAPTURE THE LITTLE THINGS</p><h1>收據草稿</h1><p>留下一張照片，核對後再記進帳本。</p></div><Button variant="secondary" onClick={()=>void load()}><RefreshCw size={16}/>更新草稿</Button></div>
    <div className="capture-status"><span><Camera size={17}/>{status?.provider==='disabled'?'辨識尚未啟用':`${status?.provider==='ollama'?'本機辨識':'OpenAI 雲端辨識'} · ${connected?'整合服務運作中':'等待整合服務連線'}`}</span><span><Send size={17}/>Telegram {status?.telegram_configured?(status.telegram_last_received?'已收到訊息':'已設定，等待首次訊息'):'尚未設定'}</span></div>
    {status?.provider==='disabled'&&<p className="form-hint">你尚未選擇辨識方式。目前可上傳照片、手動填寫草稿；照片不會送往 AI 服務。啟用後可選本機 Ollama 或 OpenAI API。</p>}
    {status?.provider==='openai'&&<p className="form-hint">上傳後會將收據預覽或輸入文字送到 OpenAI API 辨識，並產生 API 用量費用。請核對結果再入帳。</p>}
    {error&&<p className="alert" role="alert">{error}</p>}{notice&&<p className="success" role="status">{notice}</p>}
    <div className="capture-inputs">
      <section className="panel"><h2><Camera size={20}/>從收據開始</h2><p className="muted">JPEG、PNG、HEIC，每張最多 20 MiB。每張照片建立一份草稿。</p><label className="capture-upload">選擇收據照片<input type="file" accept="image/jpeg,image/png,image/heic,image/heif,.heic,.heif" disabled={busy} onChange={e=>{const f=e.target.files?.[0];if(f){if(f.size>20*1024*1024){setError('圖片最多 20 MiB。');setUpload(null);}else{setUpload({file:f,key:crypto.randomUUID()});setError('');}}e.target.value='';}}/></label>{upload&&<p className="file-name">{upload.file.name}</p>}<Button disabled={busy||!upload} onClick={()=>void sendFile()}>{busy?'保存中…':'上傳並建立草稿'}</Button></section>
      <section className="panel"><h2><FileText size={20}/>用文字記下</h2><form onSubmit={sendText}><label>記帳內容<textarea value={text} onChange={e=>setText(e.target.value)} maxLength={2000} rows={3} required placeholder="例如：2026-09-24 在超市買菜，總共 USD 26.50"/></label><Button disabled={busy||!text.trim()} type="submit">建立文字草稿</Button></form></section>
    </div>
    <div className="section-heading"><h2>待你核對</h2><label className="checkbox-label"><input type="checkbox" checked={closed} onChange={e=>{setClosed(e.target.checked);setPage(0);}}/>包含已入帳與已取消</label></div>
    {!rows.length?<section className="empty-state panel"><Camera size={32}/><h2>把收據留在這裡</h2><p>上傳照片或傳送 Telegram 訊息後，草稿會出現在這裡。</p></section>:<div className="draft-list">{rows.map(row=><button className="draft-card" key={row.id} onClick={()=>setSelected(row)}><span className="draft-icon"><FileText/></span><span><strong>{row.proposal.merchant||'尚未填寫商家'}</strong><small>{row.proposal.occurred_on||'日期待補'} · {row.source==='telegram'?'Telegram':'網頁'}</small></span><span className="draft-amount">{row.proposal.currency||'幣別待補'} {row.proposal.amount||'金額待補'}<small>{labels[row.status]}</small></span></button>)}</div>}
    <div className="form-actions"><Button variant="secondary" disabled={!page} onClick={()=>setPage(p=>p-1)}>上一頁</Button><span>第 {page+1} 頁</span><Button variant="secondary" disabled={rows.length<25} onClick={()=>setPage(p=>p+1)}>下一頁</Button></div>
    {selected&&<DraftEditor key={selected.id} initial={selected} accounts={accounts} categories={categories} currency={currency} onClose={()=>setSelected(null)} onSaved={text=>{if(text)setNotice(text);void load();}}/>}
  </>;
}

export function DraftEditor({initial,accounts,categories,currency,onClose,onSaved}:{initial:Draft;accounts:Account[];categories:Category[];currency:string;onClose:()=>void;onSaved:(notice?:string)=>void}) {
  const [draft,setDraft]=useState(initial),[proposal,setProposal]=useState<Proposal>(initial.proposal),[dirty,setDirty]=useState(false);
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[ack,setAck]=useState(false),[preview,setPreview]=useState('');
  const [cancelPrompt,setCancelPrompt]=useState(false);
  const [itemDirty,setItemDirty]=useState(false),[itemBusy,setItemBusy]=useState(false);
  const [discard,setDiscard]=useState<'close'|'reload'|null>(null),[reloadVersion,setReloadVersion]=useState(0);
  const [categoryTarget,setCategoryTarget]=useState<'single'|number|null>(null),[createdCategories,setCreatedCategories]=useState<Category[]>([]);
  const [categoryName,setCategoryName]=useState(''),[suggestionVersion,setSuggestionVersion]=useState(0);
  const categoryList=[...new Map([...createdCategories,...categories].map(c=>[c.id,c])).values()];
  const availableCategories=categoryList.filter(c=>!c.archived&&c.kind===proposal.kind);
  const categoryLabel=(category:Category)=>{const parent=categoryList.find(c=>c.id===category.parent_id);return parent?`${parent.name} → ${category.name}`:category.name;};
  const dialogRef=useRef<HTMLDialogElement>(null);
  const closed=['confirmed','cancelled'].includes(draft.status);
  useEffect(()=>{const node=dialogRef.current,before=document.activeElement as HTMLElement|null;node?.showModal();return()=>{node?.close();if(before?.isConnected)before.focus();};},[]);
  useEffect(()=>{if(!draft.attachment_id)return;let active=true;let url='';void api<Blob>(`/attachments/${draft.attachment_id}/preview`,{},true,'blob').then(blob=>{url=URL.createObjectURL(blob);if(active)setPreview(url);else URL.revokeObjectURL(url);}).catch(e=>{if(active)setError(message(e));});return()=>{active=false;if(url)URL.revokeObjectURL(url);};},[draft.attachment_id]);
  useEffect(()=>{if(draft.status!=='processing'||dirty)return;const timer=setInterval(()=>{void api<Draft>('/capture/drafts/'+draft.id).then(row=>{setDraft(row);setProposal(row.proposal);}).catch(e=>setError(message(e)));},2000);return()=>clearInterval(timer);},[draft.id,draft.status,dirty]);
  const change=<K extends keyof Proposal>(key:K,value:Proposal[K])=>{setProposal(p=>({...p,[key]:value}));setDirty(true);setAck(false);setNotice('');};
  function removeSplit(index:number){
    const remaining=(proposal.splits??[]).filter((_,i)=>i!==index);
    if(remaining.length<=1){change('category_id',remaining[0]?.category_id||null);change('splits',[]);}else change('splits',remaining);
  }
  function categoryCreated(category:Category){
    setCreatedCategories(rows=>[...rows,category]);
    if(categoryTarget==='single')change('category_id',category.id);
    else change('splits',(proposal.splits??[]).map((s,i)=>i===categoryTarget?{...s,category_id:category.id}:s));
    setCategoryTarget(null);setSuggestionVersion(v=>v+1);setNotice(`分類「${category.name}」已新增並選用，請儲存草稿修改。`);onSaved();
  }
  function openCategory(target:'single'|number,name=''){setCategoryName(name);setCategoryTarget(target);}
  function chooseSuggestion(suggestion:components['schemas']['CategorySuggestion'],target:'single'|number){
    if(!suggestion.category_id){openCategory(target,suggestion.name);return;}
    if(!availableCategories.some(c=>c.id===suggestion.category_id)){setError('分類清單已變更，請更新草稿與建議後再選用。');onSaved();return;}
    if(target==='single')change('category_id',suggestion.category_id);
    else change('splits',(proposal.splits??[]).map((s,i)=>i===target?{...s,category_id:suggestion.category_id!}:s));
    setNotice('已選用建議分類，請儲存草稿修改。');
  }
  async function reload(){setBusy(true);setError('');try{const row=await api<Draft>('/capture/drafts/'+draft.id);setDraft(row);setProposal(row.proposal);setDirty(false);setItemDirty(false);setReloadVersion(v=>v+1);setAck(false);setNotice('已重新載入已儲存的草稿與品項。');}catch(e){setError(message(e));}finally{setBusy(false);}}
  async function save(e:FormEvent){e.preventDefault();setBusy(true);setError('');try{const row=await api<Draft>('/capture/drafts/'+draft.id,{method:'PUT',body:JSON.stringify({expected_revision:draft.revision,proposal})});setDraft(row);setProposal(row.proposal);setDirty(false);setNotice('草稿已儲存，請核對後確認入帳。');onSaved();}catch(e){setError(message(e));}finally{setBusy(false);}}
  async function action(kind:'confirm'|'cancel'|'retry'){
    setBusy(true);setError('');setNotice('');
    try{const row=await api<Draft>(`/capture/drafts/${draft.id}/${kind}`,{method:'POST',body:JSON.stringify({expected_revision:draft.revision,acknowledge_warnings:ack})});setDraft(row);setProposal(row.proposal);setDirty(false);setAck(false);setCancelPrompt(false);const text=kind==='confirm'?'已確認入帳，收據已附在交易上。':kind==='cancel'?'草稿已取消，沒有新增交易。':'已安排重新辨識。';setNotice(text);onSaved(text);if(kind!=='retry')onClose();}catch(e){setError(message(e));}finally{setBusy(false);}
  }
  const source=accounts.find(a=>a.id===proposal.account_id);
  function close(){if(busy||itemBusy)return;if(dirty||itemDirty)setDiscard('close');else onClose();}
  return <><dialog ref={dialogRef} className="finance-dialog capture-editor" aria-labelledby="draft-title" onCancel={e=>{e.preventDefault();close();}}><div className="dialog-heading"><h2 id="draft-title">核對收據草稿</h2><button type="button" aria-label="關閉草稿" disabled={busy||itemBusy} onClick={close}><X/></button></div>
    {(busy||itemBusy)&&<p role="status" className="form-hint">正在處理，請稍候。若連線逾時，會保留輸入並恢復關閉按鈕。</p>}
    <p className="muted">{labels[draft.status]} · {draft.id.slice(0,8)}{draft.job_status==='retry_wait'?' · 連線失敗，稍後重試':''}</p>
    <div className="capture-editor-grid"><div className="capture-evidence">{preview?<img className="capture-preview" src={preview} alt="收據預覽"/>:<p className="form-hint">{draft.attachment_id?'圖片載入中':'這份草稿沒有照片'}</p>}
    {draft.parsed&&<details><summary>原始辨識品項（{draft.parsed.items.length}）</summary><p className="footnote">保留辨識原文供核對，不會自動建立商品或分攤。</p>{draft.parsed.items.map((item,i)=><div className="capture-item" key={i}><span>{item.raw_name}<small>{item.quantity??'數量不明'} × {item.unit_price??'單價不明'}</small></span><strong>{item.line_total??'金額不明'}</strong></div>)}<p>小計 {draft.parsed.subtotal??'不明'} · 稅 {draft.parsed.tax??'不明'} · 小費 {draft.parsed.tip??'不明'} · 折扣 {draft.parsed.discount??'不明'}</p></details>}</div>
    <div><form onSubmit={save}><fieldset disabled={busy||closed||itemBusy||itemDirty}><div className="form-grid"><label>收支類型<select value={proposal.kind} onChange={e=>{change('kind',e.target.value as Proposal['kind']);change('category_id',null);change('splits',[]);}}><option value="expense">支出</option><option value="income">收入</option></select></label><label>收據幣別<select value={proposal.currency??''} onChange={e=>change('currency',(e.target.value||null) as Proposal['currency'])}><option value="">請核對幣別</option><option>USD</option><option>TWD</option></select></label></div>
    {!proposal.currency&&draft.parsed?.currency&&<p className="form-hint">AI 建議幣別：{draft.parsed.currency}，請核對原圖後選擇。</p>}
    <div className="form-grid"><label>金額<input inputMode="decimal" value={proposal.amount??''} onChange={e=>change('amount',e.target.value||null)} pattern="[0-9]+(\.[0-9]{1,2})?" placeholder="尚未辨識"/></label><label>帳務日期<input type="date" value={proposal.occurred_on??''} onChange={e=>change('occurred_on',e.target.value||null)}/></label></div>
    <label>記帳帳戶<select value={proposal.account_id??''} onChange={e=>change('account_id',e.target.value||null)}><option value="">請選擇帳戶</option>{accounts.filter(a=>!a.archived).map(a=><option key={a.id} value={a.id}>{a.name} · {a.currency}</option>)}</select></label>
    {source&&source.currency!==currency&&<label>入帳匯率（1 {source.currency} = 多少 {currency}）<input inputMode="decimal" value={proposal.fx_rate??''} onChange={e=>change('fx_rate',e.target.value||null)}/></label>}
    <div className="section-heading compact"><h3>分類{proposal.splits?.length?'分攤':''}</h3>{!proposal.splits?.length&&<button type="button" className="text-button" onClick={()=>openCategory('single')}>新增分類</button>}</div>
    {!closed&&<CategorySuggestions draftId={draft.id} input={{expected_revision:draft.revision,kind:proposal.kind,merchant:proposal.merchant,note:proposal.note}} reviewVersion={suggestionVersion+reloadVersion} splitCount={proposal.splits?.length??0} disabled={busy||itemBusy||itemDirty} onChoose={chooseSuggestion}/>}
    {!availableCategories.length&&<p className="form-hint">尚未建立{proposal.kind==='income'?'收入':'支出'}分類。請按「新增分類」建立後再選用。</p>}
    {!proposal.splits?.length?<label>分類<select value={proposal.category_id??''} onChange={e=>change('category_id',e.target.value||null)}><option value="">請選擇分類</option>{availableCategories.map(c=><option key={c.id} value={c.id}>{categoryLabel(c)}</option>)}</select></label>:proposal.splits.map((s,i)=><div className="receipt-split" key={i}><div className="split-row"><label>分攤分類 {i+1}<select required value={s.category_id} onChange={e=>change('splits',proposal.splits!.map((v,j)=>j===i?{...v,category_id:e.target.value}:v))}><option value="">請選擇</option>{availableCategories.map(c=><option key={c.id} value={c.id}>{categoryLabel(c)}</option>)}</select></label><label>分攤金額 {i+1}<input required inputMode="decimal" pattern="[0-9]+(\.[0-9]{1,2})?" placeholder="請填寫金額" value={s.amount} onChange={e=>change('splits',proposal.splits!.map((v,j)=>j===i?{...v,amount:e.target.value}:v))}/></label><button type="button" aria-label={`移除分攤${i+1}`} onClick={()=>removeSplit(i)}><X size={16}/></button></div><button type="button" className="text-button" aria-label={`新增分類至分攤 ${i+1}`} onClick={()=>openCategory(i)}>新增分類</button></div>)}
    <p className="footnote">找不到適合的選項？按「新增分類」建立名稱。只有一筆收據要分到多個分類時，才使用下方的分類分攤。</p>
    <button type="button" className="text-button receipt-split-add" disabled={(proposal.splits?.length??0)>=30} onClick={()=>{const splits=proposal.splits?.length?proposal.splits:[{category_id:proposal.category_id||'',amount:proposal.amount||''}];change('splits',[...splits,{category_id:'',amount:''}]);change('category_id',null);}}><Plus size={14}/>加入分類分攤</button>
    {!!proposal.splits?.length&&<p className="form-hint">每列都需選擇分類、填寫大於 0 的金額，合計必須等於收據金額 {proposal.currency} {proposal.amount??'（待填）'}。移除至一列時會改回單一分類，使用整筆收據金額。</p>}
    <label>商家<input maxLength={200} value={proposal.merchant} onChange={e=>change('merchant',e.target.value)}/></label><label>標籤（逗號分隔）<input value={(proposal.tags??[]).join(',')} onChange={e=>change('tags',e.target.value.split(',').filter(Boolean))}/></label><label>備註<textarea rows={2} maxLength={2000} value={proposal.note} onChange={e=>change('note',e.target.value)}/></label>
    {!closed&&<Button type="submit" disabled={busy||itemBusy||itemDirty||!dirty}>儲存草稿修改</Button>}</fieldset></form>
    <ReceiptItemEditor key={`${draft.id}:${reloadVersion}`} draft={draft} disabled={busy||dirty} onDirty={setItemDirty} onBusy={setItemBusy} onSaved={()=>setSuggestionVersion(v=>v+1)}/>
    {draft.warnings.length>0&&<div className="capture-warnings"><strong>請核對以下提醒</strong><ul>{draft.warnings.map((w,i)=><li key={i}>{w}</li>)}</ul>{!closed&&<label className="checkbox-label"><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)}/>我已核對提醒與收據，確認填寫金額正確</label>}</div>}
    {error&&<p role="alert" className="error">{error}<button type="button" disabled={busy||itemBusy} className="text-button" onClick={()=>{if(dirty||itemDirty)setDiscard('reload');else void reload();}}>重新載入草稿</button></p>}{notice&&<p role="status" className="success">{notice}</p>}
    {!closed&&<><p className="footnote">儲存修改後，再按「確認入帳」。取消草稿會保留照片與歷程。</p><div className="form-actions"><Button variant="secondary" disabled={busy||itemBusy||itemDirty||dirty||draft.status==='processing'} onClick={()=>void action('retry')}>重新辨識</Button><Button disabled={busy||itemBusy||itemDirty||dirty||draft.status==='processing'||draft.warnings.length>0&&!ack} onClick={()=>void action('confirm')}><Check size={16}/>確認入帳</Button></div>{cancelPrompt?<div className="form-hint">確定取消這份草稿？<Button variant="danger" disabled={busy||itemBusy||itemDirty} onClick={()=>void action('cancel')}>確定取消草稿</Button><button onClick={()=>setCancelPrompt(false)}>返回</button></div>:<button className="text-button" disabled={busy||itemBusy||itemDirty} onClick={()=>setCancelPrompt(true)}>取消這份草稿</button>}</>}
    <div className="form-actions"><Button type="button" variant="secondary" disabled={busy||itemBusy} onClick={close}>完成／關閉</Button></div>
    </div></div></dialog>{categoryTarget!==null&&<CategoryForm categories={categoryList} fixedKind={proposal.kind} initialName={categoryName} onClose={()=>setCategoryTarget(null)} onSave={categoryCreated}/>} {discard&&<ConfirmDialog title={discard==='close'?'離開收據核對？':'重新載入收據？'} confirmLabel={discard==='close'?'捨棄修改並關閉':'捨棄修改並重新載入'} onCancel={()=>setDiscard(null)} onConfirm={()=>{const action=discard;setDiscard(null);if(action==='close')onClose();else void reload();}}>有尚未儲存的草稿或品項修改。捨棄後只保留先前已儲存的內容，原始收據與已入帳資料不會刪除。</ConfirmDialog>}</>;
}
