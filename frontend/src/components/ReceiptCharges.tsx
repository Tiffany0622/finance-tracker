import type { components } from '../api/schema';

type Draft = components['schemas']['DraftOutput'];
const taxModes = { added: '另加稅', included: '含稅，不重複加', unclear: '含稅方式不明' };
const tipStates = { paid: '實付欄', blank: '空白／不清楚', suggested_only: '僅建議小費', not_printed: '未列出', unclear: '不明' };

export function ReceiptCharges({ draft }: { draft: Draft }) {
  const review = draft.charge_review;
  if (!review) return null;
  const evidence = draft.parsed?.charge_evidence;
  const lines = evidence ? [...evidence.tax_lines, evidence.tip, evidence.service_charge, evidence.total].filter(line => line !== null) : [];
  return <section aria-label="稅與小費核對" className="notice">
    <strong>{draft.status === 'processing' ? '前次' : '初次'}辨識：稅與小費核對</strong>
    <p>稅額 {review.tax ?? '不明'}（{taxModes[review.tax_mode]}）<br />
      實付小費 {review.tip ?? '不明'}（{tipStates[review.tip_status]}）<br />
      服務費 {review.service_charge ?? '不明'}<br />
      最終付款 {review.amount ?? '待補確認'}</p>
    <p className="footnote">請對照原圖，下方金額應是最終付款，不需再加稅或小費。這裡保留初次辨識候選，不會隨手動修改變動。</p>
    {lines.length > 0 && <details><summary>查看模型讀到的金額原文</summary>{lines.map((line, i) => <p key={i} className="footnote">{line?.text}</p>)}</details>}
  </section>;
}
