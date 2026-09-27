import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { components } from '../api/schema';
import { Button } from './ui/button';

type Suggestion = components['schemas']['CategorySuggestion'];
type Result = components['schemas']['CategorySuggestions'];
type Input = components['schemas']['SuggestionInput'];
const sources:Record<Suggestion['source'],string>={merchant_history:'你的歷史選擇',receipt_text:'收據文字線索',common:'常用選項',fallback:'待你判斷'};

export function CategorySuggestions({draftId,input,reviewVersion,splitCount,disabled,onChoose}:{
  draftId:string;input:Input;reviewVersion:number;splitCount:number;disabled:boolean;
  onChoose:(suggestion:Suggestion,target:'single'|number)=>void;
}) {
  const [refresh,setRefresh]=useState(0),[target,setTarget]=useState('');
  const [state,setState]=useState<{key:string;result?:Result;error?:string}|null>(null);
  const body=JSON.stringify(input),key=JSON.stringify([draftId,body,reviewVersion,refresh]);
  useEffect(()=>{setTarget('');},[splitCount]);
  useEffect(()=>{
    let active=true;const controller=new AbortController();
    const timer=setTimeout(()=>{void api<Result>(`/capture/drafts/${draftId}/category-suggestions`,{method:'POST',body,signal:controller.signal})
      .then(result=>{if(active)setState({key,result});})
      .catch(e=>{if(active)setState({key,error:e instanceof Error?e.message:'建議暫時無法載入，可手動選分類。'});});},350);
    return()=>{active=false;clearTimeout(timer);controller.abort();};
  },[draftId,body,key]);
  const current=state?.key===key?state:null;
  const needsTarget=splitCount>0&&(target===''||Number(target)>=splitCount);
  return <section className="category-suggestions" aria-label="建議分類">
    <div className="section-heading compact"><h4>建議分類</h4><button type="button" className="text-button" disabled={disabled} onClick={()=>setRefresh(v=>v+1)}>更新建議</button></div>
    {!current&&<p role="status" className="footnote">正在參考本機記帳紀錄與收據文字…</p>}
    {current?.error&&<p role="status" className="error">{current.error} 仍可手動選擇分類。</p>}
    {current?.result&&<>
      <p className="footnote">{current.result.message}</p>
      {splitCount>0&&<label>建議套用至<select value={target} onChange={e=>setTarget(e.target.value)} disabled={disabled}><option value="">請選擇分攤列</option>{Array.from({length:splitCount},(_,i)=><option value={String(i)} key={i}>分攤分類 {i+1}</option>)}</select></label>}
      <div className="category-suggestion-list">{current.result.suggestions.map(s=><article key={s.category_id??s.name} className="category-suggestion-card">
        <span className="suggestion-source">{sources[s.source]}{!s.category_id?' · 尚未建立':''}</span><strong>{s.name}</strong><p>{s.reason}</p>
        <Button type="button" variant="secondary" disabled={disabled||needsTarget} aria-label={`${s.category_id?'使用':'建立'}建議分類 ${s.name}`} onClick={()=>onChoose(s,splitCount?Number(target):'single')}>{s.category_id?'使用此分類':'建立並選用'}</Button>
      </article>)}</div>
      <p className="footnote">{splitCount?'只替換你指定列的分類，分攤金額保持不變。':'按下選用才會更改分類，之後仍需儲存草稿與確認入帳。'}</p>
    </>}
  </section>;
}
