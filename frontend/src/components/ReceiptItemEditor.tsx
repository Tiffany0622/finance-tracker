import { useEffect, useState, type FormEvent } from 'react';
import { Plus, X } from 'lucide-react';
import { api } from '../api/client';
import type { components } from '../api/schema';
import { Button } from './ui/button';

type Review = components['schemas']['ReviewOutput'];
type Item = components['schemas']['ItemOutput'];
type Draft = components['schemas']['DraftOutput'];
const message = (e: unknown) => e instanceof Error ? e.message : '品項尚未儲存，請稍後再試。';
const emptyItem = (): Item => ({source_line_no:null,raw_name:'',name:'',quantity:null,unit_price:null,line_total:null,unit:'',note:''});

export function ReceiptItemEditor({draft,disabled,onDirty,onBusy}:{draft:Draft;disabled:boolean;onDirty:(dirty:boolean)=>void;onBusy:(busy:boolean)=>void}) {
  const [review,setReview]=useState<Review|null>(null),[items,setItems]=useState<Item[]>([]);
  const [dirty,setDirty]=useState(false),[busy,setBusy]=useState(false),[ack,setAck]=useState(false);
  const [error,setError]=useState(''),[notice,setNotice]=useState('');
  const path=`/capture/drafts/${draft.id}/items`;
  useEffect(()=>{let active=true;setReview(null);setError('');setNotice('');setAck(false);
    void api<Review>(path).then(r=>{if(active){setReview(r);setItems(r.items);setDirty(false);onDirty(false);}}).catch(e=>{if(active)setError(message(e));});
    return()=>{active=false;};
  },[path,draft.revision,onDirty]);
  function change(next:Item[]){setItems(next);setDirty(true);onDirty(true);setAck(false);setNotice('');}
  function field(index:number,key:keyof Item,value:string){change(items.map((i,n)=>n===index?{...i,[key]:['quantity','unit_price','line_total'].includes(key)?value||null:value}:i));}
  async function reload(){if(dirty&&!window.confirm('重新載入會捨棄尚未儲存的品項修改，確定繼續？'))return;
    try{const r=await api<Review>(path);setReview(r);setItems(r.items);setDirty(false);onDirty(false);setAck(false);setError('');setNotice('');}catch(e){setError(message(e));}}
  async function save(event:FormEvent){event.preventDefault();if(!review)return;setBusy(true);onBusy(true);setError('');setNotice('');
    try{const r=await api<Review>(path,{method:'PUT',body:JSON.stringify({expected_revision:review.revision,expected_draft_revision:review.draft_revision,expected_transaction_revision:review.transaction_revision,acknowledged:ack,items:items.map(({raw_name:_,...i})=>i)})});
      setReview(r);setItems(r.items);setDirty(false);onDirty(false);setAck(false);setNotice(draft.status==='confirmed'?'品項已核對，可到「商品紀錄」搜尋。帳務金額未改動。':'品項已核對；確認入帳後即可在「商品紀錄」搜尋。');
    }catch(e){setError(message(e));}finally{setBusy(false);onBusy(false);}}
  return <section className="item-review" aria-labelledby="item-review-title">
    <div className="section-heading"><h3 id="item-review-title">品項核對／修正</h3><span>{review?{unreviewed:'尚未核對',reviewed:'已核對',stale:'需要重新核對'}[review.status]:'載入中'}</span></div>
    <p className="footnote">先儲存上方的幣別與帳務修改，再核對品項。這裡只保存商品明細，不會增加支出或變更帳務總額。</p>
    <p className="footnote">價格單位：{review?.currency||'請先選擇幣別'}。不明數值留白；列金額照收據填寫，可為折扣負值，稅與小費不會自動攤入。只有單價明確時才填單價。</p>
    {review?.warnings.map((w,i)=><p className="form-hint" key={i}>{w}</p>)}
    {review&&<form onSubmit={save}><fieldset disabled={disabled||busy||!review.editable||!review.currency}>
      <div className="item-edit-list">{items.map((item,index)=><div className="item-edit-card" key={index}>
        <div className="section-heading"><strong>品項 {index+1}</strong><button type="button" aria-label={`移除品項 ${index+1}`} onClick={()=>change(items.filter((_,n)=>n!==index))}><X size={16}/></button></div>
        {item.raw_name&&<p className="footnote">辨識原文：{item.raw_name}</p>}
        <label>品名 {index+1}<input required maxLength={500} value={item.name} onChange={e=>field(index,'name',e.target.value)}/></label>
        <div className="form-grid"><label>數量 {index+1}<input inputMode="decimal" placeholder="不明可留白" value={item.quantity??''} onChange={e=>field(index,'quantity',e.target.value)}/></label><label>單位／規格 {index+1}<input maxLength={40} placeholder="例如：盒／500g" value={item.unit} onChange={e=>field(index,'unit',e.target.value)}/></label></div>
        <div className="form-grid"><label>單價 {index+1}<input inputMode="decimal" placeholder="不明可留白" value={item.unit_price??''} onChange={e=>field(index,'unit_price',e.target.value)}/></label><label>列金額 {index+1}<input inputMode="decimal" placeholder="照收據填寫" value={item.line_total??''} onChange={e=>field(index,'line_total',e.target.value)}/></label></div>
        <label>品項備註 {index+1}<input maxLength={500} placeholder="例如：折扣、規格待確認" value={item.note} onChange={e=>field(index,'note',e.target.value)}/></label>
      </div>)}</div>
      {!items.length&&<p className="muted">還沒有品項，可依收據手動新增。</p>}
      <Button type="button" variant="secondary" disabled={items.length>=200} onClick={()=>change([...items,emptyItem()])}><Plus size={15}/>新增品項</Button>
      <label className="checkbox-label item-ack"><input type="checkbox" checked={ack} onChange={e=>{setAck(e.target.checked);setDirty(true);onDirty(true);}}/>我已逐項核對原收據；不確定的數值已留白</label>
      <Button type="submit" disabled={!ack||!review.currency}>{busy?'儲存中…':'儲存已核對品項'}</Button>
    </fieldset></form>}
    {error&&<p role="alert" className="error">{error}</p>}
    {notice&&<p role="status" className="success">{notice}</p>}
    <button type="button" className="text-button" disabled={busy||disabled} onClick={()=>void reload()}>重新載入品項</button>
  </section>;
}
