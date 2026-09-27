import { useEffect, useId, useRef, type ReactNode } from 'react';
import { Button } from './ui/button';

// Browser JS confirmations may be hidden or suppressed by an embedded browser.
// Keep the decision visible, keyboard-accessible and separate from the edit form.
export function ConfirmDialog({title,children,confirmLabel,onConfirm,onCancel}: {
  title:string;children:ReactNode;confirmLabel:string;onConfirm:()=>void;onCancel:()=>void;
}) {
  const ref=useRef<HTMLDialogElement>(null), titleId=useId(), descriptionId=useId();
  useEffect(()=>{
    const node=ref.current, before=document.activeElement as HTMLElement|null;
    node?.showModal();
    return()=>{node?.close();if(before?.isConnected)before.focus();};
  },[]);
  return <dialog ref={ref} className="finance-dialog confirm-dialog" aria-labelledby={titleId} aria-describedby={descriptionId} onCancel={e=>{e.preventDefault();e.stopPropagation();onCancel();}}>
    <h2 id={titleId}>{title}</h2><p id={descriptionId}>{children}</p>
    <div className="form-actions"><Button type="button" variant="secondary" autoFocus onClick={onCancel}>繼續編輯</Button><Button type="button" variant="danger" onClick={onConfirm}>{confirmLabel}</Button></div>
  </dialog>;
}
