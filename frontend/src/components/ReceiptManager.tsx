import { useEffect, useRef, useState } from 'react';
import { Download, ImagePlus, RefreshCw, Trash2, X } from 'lucide-react';
import { api } from '../api/client';
import type { components } from '../api/schema';
import { Button } from './ui/button';

type Attachment = components['schemas']['AttachmentOutput'];
type Pending = { file: File; key: string; error: string };
const errorText = (e: unknown) => e instanceof Error ? e.message : '操作未完成，請稍後再試。';
const size = (n: number) => n < 1024 * 1024 ? `${Math.ceil(n / 1024)} KiB` : `${(n / 1024 / 1024).toFixed(1)} MiB`;

export function ReceiptManager({transactionId, description, onClose}: {
  transactionId: string; description: string; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [rows, setRows] = useState<Attachment[]>([]);
  const [queue, setQueue] = useState<Pending[]>([]);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
  const [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [removing, setRemoving] = useState<string | null>(null);
  useEffect(() => { const node = dialog.current; node?.showModal(); return () => node?.close(); }, []);
  async function load() {
    setLoading(true); setError('');
    try { setRows(await api(`/transactions/${transactionId}/attachments`)); }
    catch(e) { setError(errorText(e)); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, [transactionId]);
  function selectFiles(files: FileList | null) {
    if (!files) return;
    const selected = Array.from(files);
    setError(''); setNotice('');
    if (rows.length + queue.length + selected.length > 20) { setError('每筆交易最多 20 張收據圖片。'); return; }
    setQueue(q => [...q, ...selected.map(file => ({file, key: crypto.randomUUID(), error: ''}))]);
  }
  async function upload() {
    setBusy(true); setError(''); setNotice('');
    let completed = 0;
    for (const item of queue) {
      try {
        if (!item.file.size || item.file.size > 20 * 1024 * 1024) throw new Error('每張圖片最多 20 MiB，且不能是空檔案。');
        const row = await api<Attachment>(`/transactions/${transactionId}/attachments?filename=${encodeURIComponent(item.file.name)}`, {
          method: 'POST', headers: {'Content-Type': item.file.type || 'application/octet-stream', 'Idempotency-Key': item.key}, body: item.file,
        });
        setRows(previous => [...previous.filter(r => r.id !== row.id), row].sort((a, b) => a.page_no - b.page_no));
        setQueue(previous => previous.filter(q => q.key !== item.key));
        completed++;
      } catch(e) {
        setQueue(previous => previous.map(q => q.key === item.key ? {...q, error: errorText(e)} : q));
      }
    }
    setNotice(completed ? `已保存 ${completed} 張圖片。交易金額保持不變。` : '圖片尚未上傳成功，請查看下方原因。');
    setBusy(false);
  }
  async function remove(id: string) {
    setBusy(true); setError('');
    try {
      await api(`/transactions/${transactionId}/attachments/${id}`, {method:'DELETE'});
      setRows(previous => previous.filter(r => r.id !== id));
      setRemoving(null); setNotice('附件已移除，交易金額保持不變。');
    } catch(e) { setError(errorText(e)); }
    finally { setBusy(false); }
  }
  return <dialog ref={dialog} className="finance-dialog receipt-dialog" aria-label="收據附件" onCancel={e => {e.preventDefault(); if (!busy) onClose();}}>
    <div className="dialog-heading"><div><p className="eyebrow">KEEP THE LITTLE DETAILS</p><h2>收據附件</h2></div><button aria-label="關閉收據附件" onClick={onClose} disabled={busy}><X size={21}/></button></div>
    <p className="receipt-description">{description}</p>
    <p className="form-hint">保留原始照片，方便查帳、退貨或報帳。這裡只保存附件；金額與分類請在交易中填寫。</p>
    <label className="receipt-picker"><ImagePlus size={28}/><strong>選擇收據圖片</strong><span>JPEG、PNG、HEIC · 每張最多 20 MiB / 5,000 萬像素 · 每筆最多 20 張</span>
      <input type="file" multiple accept=".jpg,.jpeg,.png,.heic,.heif,image/jpeg,image/png,image/heic,image/heif" disabled={busy || loading} onChange={e => {selectFiles(e.target.files); e.target.value='';}}/>
    </label>
    <p className="footnote">依上傳順序排列，點選圖片可放大。PDF 與動態圖片尚未支援。</p>
    {error && <div role="alert" className="alert">{error}<button disabled={busy} onClick={() => void load()}><RefreshCw size={14}/>重新載入</button></div>}
    {notice && <p role="status" className="success">{notice}</p>}
    {queue.length > 0 && <section className="receipt-pending" aria-label="待上傳圖片"><h3>待上傳 · {queue.length} 張</h3>{queue.map(item => <div className="receipt-pending-row" key={item.key}><div><strong>{item.file.name}</strong><small>{size(item.file.size)}</small>{item.error && <p role="alert" className="error">{item.error}</p>}</div><button disabled={busy} aria-label={`取消上傳 ${item.file.name}`} onClick={() => {setQueue(previous => previous.filter(q => q.key !== item.key)); setNotice('');}}><X size={18}/></button></div>)}<Button disabled={busy || loading} onClick={() => void upload()}>{busy ? '正在保存圖片…' : '上傳／重試待處理圖片'}</Button></section>}
    <div className="section-heading"><h3>已保存 · {rows.length} 張</h3></div>
    {loading ? <p role="status" className="muted">正在載入收據…</p> : !rows.length && !error ? <p className="receipt-empty">這筆交易還沒有收據，從上方選擇照片開始。</p> : null}
    <div className="receipt-grid">{rows.map(row => <ReceiptCard key={row.id} row={row} disabled={busy} onRemove={() => setRemoving(row.id)}/>)}</div>
    {removing && <section className="receipt-confirm" role="alert"><p>確定移除此附件？交易會保留，既有備份仍可能包含這張照片。</p><div className="form-actions"><Button variant="secondary" disabled={busy} onClick={() => setRemoving(null)}>保留附件</Button><Button variant="danger" disabled={busy} onClick={() => void remove(removing)}>確認移除附件</Button></div></section>}
    <div className="form-actions"><Button variant="secondary" disabled={busy} onClick={onClose}>完成</Button></div>
  </dialog>;
}

function ReceiptCard({row, disabled, onRemove}: {row: Attachment; disabled: boolean; onRemove: () => void}) {
  const [url, setUrl] = useState(''), [error, setError] = useState(''), [expanded, setExpanded] = useState(false);
  const [retry, setRetry] = useState(0), [downloading, setDownloading] = useState(false);
  useEffect(() => {
    let active = true, objectUrl = '';
    setError(''); setUrl('');
    api<Blob>(`/attachments/${row.id}/preview`, {}, true, 'blob').then(blob => {
      if (!active) return;
      objectUrl = URL.createObjectURL(blob); setUrl(objectUrl);
    }).catch(e => { if (active) setError(errorText(e)); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [row.id, retry]);
  async function download() {
    setDownloading(true); setError('');
    try {
      const blob = await api<Blob>(`/attachments/${row.id}/original`, {}, true, 'blob');
      const href = URL.createObjectURL(blob), link = document.createElement('a');
      const ext = row.mime === 'image/jpeg' ? 'jpg' : row.mime === 'image/png' ? 'png' : 'heic';
      link.href = href; link.download = (row.original_name.replace(/\.[^.]*$/, '') || 'receipt') + '.' + ext; link.click();
      setTimeout(() => URL.revokeObjectURL(href), 1000);
    } catch(e) { setError(errorText(e)); }
    finally { setDownloading(false); }
  }
  return <article className={`receipt-card${expanded ? ' receipt-expanded' : ''}`}>
    {url && <button className="receipt-image" aria-label={`${expanded ? '縮小' : '放大'}預覽 ${row.original_name}`} onClick={() => setExpanded(v => !v)}><img src={url} alt={`收據 ${row.original_name}`}/></button>}
    {!url && !error && <p className="receipt-empty">正在準備預覽…</p>}
    <div className="receipt-info"><strong>{row.original_name}</strong><small>第 {row.page_no} 張 · {row.width} × {row.height} · {size(row.size_bytes)}</small><small>{new Date(row.created_at).toLocaleString('zh-TW')}</small>
      {error && <p role="alert" className="error">{error} <button className="text-button" onClick={() => setRetry(v => v+1)}>重試預覽</button></p>}
      <div className="row-actions"><button disabled={disabled || downloading} onClick={() => void download()}><Download size={15}/>{downloading ? '下載中…' : '下載原圖'}</button><button disabled={disabled} onClick={onRemove}><Trash2 size={15}/>移除</button></div>
    </div>
  </article>;
}
