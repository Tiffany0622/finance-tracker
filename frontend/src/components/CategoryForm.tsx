import { useState, type FormEvent } from 'react';
import { api } from '../api/client';
import { useSubmitKey } from '../api/useSubmitKey';
import type { components } from '../api/schema';
import { Modal } from './Modal';
import { Button } from './ui/button';

type Category = components['schemas']['CategoryOutput'];
export function CategoryForm({categories,fixedKind,initialName='',onClose,onSave}:{categories:Category[];fixedKind?:Category['kind'];initialName?:string;onClose:()=>void;onSave:(category:Category)=>void|Promise<void>}) {
  const [kind,setKind]=useState(fixedKind??'expense'),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const key=useSubmitKey();
  async function submit(e:FormEvent<HTMLFormElement>){
    e.preventDefault();const f=new FormData(e.currentTarget);
    const body={name:String(f.get('name')??'').trim(),kind,parent_id:f.get('parent')||null};
    if(!body.name){setError('請填寫分類名稱。');return;}
    setBusy(true);setError('');
    try{const category=await api<Category>('/categories',{method:'POST',headers:{'Idempotency-Key':key(body)},body:JSON.stringify(body)});await onSave(category);}
    catch(e){setError(e instanceof Error?e.message:'分類尚未儲存，請重試。');}finally{setBusy(false);}
  }
  return <Modal title={fixedKind?'新增分類':'分類管理'} onClose={onClose} busy={busy}>
    {fixedKind?<p className="form-hint">新增後會選用這個分類，並回到收據核對。已填寫的收據內容會保留。</p>:<div className="category-chips">{categories.map(c=><span key={c.id}>{c.parent_id?'↳ ':''}{c.name} · {c.kind==='income'?'收入':'支出'}</span>)}</div>}
    <form onSubmit={submit}><fieldset disabled={busy}><label>分類名稱<input name="name" required maxLength={80} defaultValue={initialName} autoFocus/></label>
      <div className="form-grid"><label>收支類型<select value={kind} disabled={!!fixedKind} onChange={e=>setKind(e.target.value as Category['kind'])}><option value="expense">支出</option><option value="income">收入</option></select></label>
        <label>上層分類<select key={kind} name="parent"><option value="">無（建立主分類）</option>{categories.filter(c=>!c.archived&&c.kind===kind&&!c.parent_id).map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label></div>
      {error&&<p className="error" role="alert">{error}</p>}<div className="form-actions"><Button type="button" variant="secondary" onClick={onClose}>取消</Button><Button type="submit">{busy?'儲存中…':'儲存分類'}</Button></div>
    </fieldset></form>
  </Modal>;
}
