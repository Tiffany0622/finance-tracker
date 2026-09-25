import { useEffect, useState, type FormEvent } from 'react';
import { Search, ShoppingBag } from 'lucide-react';
import { api } from './api/client';
import type { components } from './api/schema';
import { DraftEditor } from './CaptureWorkspace';
import { Button } from './components/ui/button';

type Result = components['schemas']['SearchOutput'];
type Draft = components['schemas']['DraftOutput'];
type Account = components['schemas']['AccountOutput'];
type Category = components['schemas']['CategoryOutput'];
const message=(e:unknown)=>e instanceof Error?e.message:'商品紀錄載入失敗，請稍後再試。';

export function ProductHistory({currency,onCapture}:{currency:string;onCapture:()=>void}) {
  const [text,setText]=useState(''),[query,setQuery]=useState(''),[page,setPage]=useState(0),[refresh,setRefresh]=useState(0);
  const [result,setResult]=useState<Result|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true),[opening,setOpening]=useState(false);
  const [selected,setSelected]=useState<{draft:Draft;accounts:Account[];categories:Category[]}|null>(null);
  useEffect(()=>{let active=true;setLoading(true);setError('');
    void api<Result>(`/products/history?q=${encodeURIComponent(query)}&offset=${page*25}`).then(r=>{if(active)setResult(r);}).catch(e=>{if(active){setError(message(e));setResult(null);}}).finally(()=>{if(active)setLoading(false);});
    return()=>{active=false;};
  },[query,page,refresh]);
  function search(e:FormEvent){e.preventDefault();setPage(0);setQuery(text.trim());setRefresh(n=>n+1);}
  async function open(id:string){setOpening(true);setError('');try{
    const [draft,accounts,categories]=await Promise.all([api<Draft>('/capture/drafts/'+id),api<Account[]>('/accounts'),api<Category[]>('/categories')]);
    setSelected({draft,accounts,categories});
  }catch(e){setError(message(e));}finally{setOpening(false);}}
  return <>
    <div className="page-heading"><div><p className="eyebrow">REMEMBER THE DETAILS</p><h1>商品紀錄</h1><p>找回買過的商品，以及當時的價格。</p></div><Button variant="secondary" onClick={onCapture}>核對收據品項</Button></div>
    <section className="panel"><form className="product-search" onSubmit={search}><label>搜尋商品<input type="search" maxLength={200} placeholder="輸入品名或收據原文，例如：蘋果、Apples" value={text} onChange={e=>setText(e.target.value)}/></label><Button type="submit" disabled={loading}><Search size={16}/>搜尋</Button></form>
      <p className="footnote">搜尋全部日期與商店的已核對、已入帳支出，留白可瀏覽全部商品。支援品名與辨識原文的關鍵字；同義詞、照片搜尋尚未開放。</p>
      <p className="footnote">價格依收據品項記錄，稅與小費未另行分攤、退款未抵扣。不明欄位保留空缺，不同幣別與規格不合併計算。</p>
    </section>
    {!!result?.pending_receipts&&<div className="form-hint">有 {result.pending_receipts} 份已入帳收據尚待品項核對。請到「收據草稿」勾選「包含已入帳與已取消」，開啟舊收據補核對。</div>}
    {error&&<p role="alert" className="error">{error}<button className="text-button" onClick={()=>setRefresh(n=>n+1)}>重試</button></p>}
    {loading?<p role="status">正在搜尋商品紀錄…</p>:result&&<>
      <div className="section-heading"><h2>{query?`「${query}」的購買紀錄`:'全部購買紀錄'}</h2><span>第 {page+1} 頁 · 本頁 {result.items.length} 項</span></div>
      {!result.items.length?<section className="empty-state panel"><ShoppingBag size={30}/><h2>沒有符合的已核對商品</h2><p>可換用收據原文搜尋，或先核對收據品項。</p></section>:<div className="purchase-list">{result.items.map((item,n)=><article className="panel purchase-card" key={`${item.draft_id}-${n}`}>
        <div className="section-heading"><div><p className="eyebrow">{item.occurred_on} · {item.merchant||'未填商家'}</p><h3>{item.name}</h3></div><span className="phase-tag">{item.currency}</span></div>
        {item.raw_name&&item.raw_name!==item.name&&<p className="footnote">收據原文：{item.raw_name}</p>}
        <dl className="purchase-values"><div><dt>數量</dt><dd>{item.quantity??'不明'} {item.unit}</dd></div><div><dt>單價</dt><dd>{item.unit_price??'不明'}</dd></div><div><dt>列金額</dt><dd>{item.line_total??'不明'}</dd></div></dl>
        {item.note&&<p className="footnote">{item.note}</p>}
        <Button variant="secondary" disabled={opening} onClick={()=>void open(item.draft_id)}>查看收據／修正品項</Button>
      </article>)}</div>}
      <div className="form-actions"><Button variant="secondary" disabled={!page} onClick={()=>setPage(n=>n-1)}>上一頁</Button><span>第 {page+1} 頁</span><Button variant="secondary" disabled={!result.has_more} onClick={()=>setPage(n=>n+1)}>下一頁</Button></div>
    </>}
    {selected&&<DraftEditor key={selected.draft.id} initial={selected.draft} accounts={selected.accounts} categories={selected.categories} currency={currency} onSaved={()=>setRefresh(n=>n+1)} onClose={()=>{setSelected(null);setRefresh(n=>n+1);}}/>}
  </>;
}
