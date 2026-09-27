import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api } from '../api/client';
import type { components } from '../api/schema';
import { Button } from './ui/button';
import { ConfirmDialog } from './ConfirmDialog';

export type Product = components['schemas']['ProductOutput'];
const message = (e: unknown) => e instanceof Error ? e.message : '商品尚未儲存，請重試。';

export function ProductCatalog({onSaved}:{onSaved:()=>void}) {
  const [products,setProducts]=useState<Product[]>([]),[selected,setSelected]=useState<Product|null>(null);
  const [name,setName]=useState(''),[aliases,setAliases]=useState(''),[note,setNote]=useState('');
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[refresh,setRefresh]=useState(0),[dirty,setDirty]=useState(false);
  const submission=useRef<{body:string;key:string}|null>(null);
  const [discard,setDiscard]=useState<{product:Product|null;reload:boolean}|null>(null);
  useEffect(()=>{let active=true;setLoading(true);
    void api<Product[]>('/products/catalog').then(p=>{if(active){setProducts(p);setError('');}}).catch(e=>{if(active)setError(message(e));}).finally(()=>{if(active)setLoading(false);});
    return()=>{active=false;};
  },[refresh]);
  function apply(p:Product|null){
    setSelected(p);setName(p?.name??'');setAliases(p?.aliases.join('\n')??'');setNote(p?.note??'');setError('');setNotice('');setDirty(false);submission.current=null;
  }
  function edit(p:Product|null){if(dirty)setDiscard({product:p,reload:false});else apply(p);}
  function reload(){if(dirty)setDiscard({product:null,reload:true});else{apply(null);setRefresh(n=>n+1);}}
  async function save(e:FormEvent){e.preventDefault();setBusy(true);setError('');setNotice('');
    const body=JSON.stringify({name:name.trim(),aliases:aliases.split('\n').map(s=>s.trim()).filter(Boolean),note,...(selected?{expected_revision:selected.revision}:{})});
    if(submission.current?.body!==body)submission.current={body,key:crypto.randomUUID()};
    try{const p=await api<Product>('/products/catalog'+(selected?'/'+selected.id:''),{method:selected?'PUT':'POST',headers:{'Idempotency-Key':submission.current.key},body});
      setSelected(p);setName(p.name);setAliases(p.aliases.join('\n'));setNote(p.note);setDirty(false);submission.current=null;setNotice('商品與別名已儲存。請在收據品項中選擇對應商品，既有品項不會自動歸類。');setRefresh(n=>n+1);onSaved();
    }catch(e){setError(message(e));}finally{setBusy(false);}
  }
  return <section className="panel product-catalog" aria-labelledby="catalog-title">
    <h2 id="catalog-title">商品名稱與別名</h2>
    <p className="footnote">例如：商品「牛奶」，別名「鮮乳」「MILK」。搜尋完整別名可找到手動連結的品項，忽略英文字母大小寫、全半形及多餘空白。品項的數量、單價與規格各自保留。</p>
    <label>選擇要編輯的商品<select disabled={busy||loading} value={selected?.id??''} onChange={e=>edit(products.find(p=>p.id===e.target.value)??null)}><option value="">建立新商品</option>{products.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
    <form onSubmit={save}><fieldset disabled={busy||loading}>
      <label>商品名稱<input required maxLength={200} value={name} onChange={e=>{setName(e.target.value);setDirty(true);}}/></label>
      <label>商品別名（每行一個，最多 30 個）<textarea rows={3} value={aliases} onChange={e=>{setAliases(e.target.value);setDirty(true);}} placeholder={'鮮乳\nMILK'}/></label>
      <label>商品備註<input maxLength={500} value={note} onChange={e=>{setNote(e.target.value);setDirty(true);}} placeholder="可說明如何區分品牌或規格；不會帶入收據價格"/></label>
      <div className="form-actions"><Button type="submit">{busy?'儲存中…':selected?'儲存商品修改':'建立商品'}</Button><Button type="button" variant="secondary" onClick={()=>edit(null)}>清空／新增另一商品</Button></div>
    </fieldset></form>
    {error&&<p role="alert" className="error">{error}</p>}{notice&&<p role="status" className="success">{notice}</p>}
    <button type="button" className="text-button" disabled={busy} onClick={reload}>重新載入商品清單</button>
    {discard&&<ConfirmDialog title="捨棄商品修改？" confirmLabel="捨棄修改並繼續" onCancel={()=>setDiscard(null)} onConfirm={()=>{apply(discard.product);if(discard.reload)setRefresh(n=>n+1);setDiscard(null);}}>切換或重新載入商品會捨棄尚未儲存的修改。已儲存的商品與收據不受影響。</ConfirmDialog>}
  </section>;
}
