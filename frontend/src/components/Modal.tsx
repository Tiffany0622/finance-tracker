import { useEffect, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';

export function Modal({title,children,onClose,busy=false}:{title:string;children:ReactNode;onClose:()=>void;busy?:boolean}) {
  const ref=useRef<HTMLDialogElement>(null);
  useEffect(()=>{const node=ref.current,before=document.activeElement as HTMLElement|null;node?.showModal();return()=>{node?.close();if(before?.isConnected)before.focus();};},[]);
  return <dialog ref={ref} className="finance-dialog" aria-label={title} onCancel={e=>{e.preventDefault();e.stopPropagation();if(!busy)onClose();}}><div className="dialog-heading"><h2>{title}</h2><button type="button" aria-label="關閉" onClick={onClose} disabled={busy}><X size={21}/></button></div>{children}</dialog>;
}
