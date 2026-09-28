"""Review printed charges without changing the raw parse or adding to the paid total."""

import re
from decimal import Decimal, localcontext

from .currency import normalized
from .schemas import ChargeLine, ChargeReview, ParsedReceipt

TAX = r"\b(?:tax|vat|gst|pst|hst)\b|稅"
TIP = r"\b(?:tip|gratuity)\b|小費"
SERVICE = r"\bservice\s+(?:charge|fee)\b|服務費"
SUGGESTED = r"\bsuggest|\brecommend|\btip guide\b|建議|推薦|參考小費"
TOTAL_TAX = r"\btotal\s+tax\b|\btax\s+total\b|稅額合計|合計稅額|稅金合計"
FINAL_PAID = r"\b(?:amount paid|grand total|final total|credit card sale|charged)\b|實付|應付總額"
NOT_PAYMENT = r"\b(?:cash tendered|change|pre.?authori[sz]\w*|authori[sz]\w*|sub[ -]?total|pre[ -]?tip|before tip)\b|找零|預授權|小計|小費前"
NUMBERS = re.compile(r"(?<![\d.,])-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d.,]|\s*%)")


def money(line: ChargeLine | None, label: str, original: str, has_image: bool) -> str | None:
    if line is None or line.amount is None or Decimal(line.amount) < 0:
        return None
    text = normalized(line.text)
    if not has_image and text.casefold() not in original.casefold():
        return None
    if not re.search(label, text, re.I) or re.search(SUGGESTED, text, re.I):
        return None
    values = {Decimal(m.group().replace(",", "")) for m in NUMBERS.finditer(text)}
    return line.amount if Decimal(line.amount) in values else None


def sum_money(values: list[str]) -> Decimal:
    # Money permits 14 integer + 18 fraction digits; ten tax lines still fit exactly.
    with localcontext() as ctx:
        ctx.prec = 40
        return sum((Decimal(value) for value in values), Decimal(0))


def review(result: ParsedReceipt, source_text: str, *, has_image: bool) -> ChargeReview | None:
    evidence = result.charge_evidence
    if evidence is None:
        return None  # Old parses remain readable without inventing new evidence.
    original = normalized(source_text)
    warnings: list[str] = []
    tax: str | None = None
    unique: dict[str, ChargeLine] = {}
    conflict = False
    for line in evidence.tax_lines:
        key = normalized(line.text).casefold()
        if key in unique:
            previous_amount = unique[key].amount
            if (Decimal(previous_amount) if previous_amount is not None else None) != (
                Decimal(line.amount) if line.amount is not None else None
            ):
                conflict = True
            warnings.append("稅額原文有重複，未重複計入；請核對。")
        unique[key] = line
    lines = list(unique.values())
    totals = [line for line in lines if re.search(TOTAL_TAX, line.text, re.I)]
    amounts = [money(line, TAX, original, has_image) for line in lines]
    if lines and not conflict and all(value is not None for value in amounts):
        if not totals:
            total_amount = sum_money([value for value in amounts if value is not None])
            if total_amount < Decimal("100000000000000"):
                tax = format(total_amount, "f")
        elif len(totals) == 1:
            total = totals[0]
            components = [line for line in lines if line is not total]
            if not components or sum_money([line.amount or "0" for line in components]) == Decimal(
                total.amount or "0"
            ):
                tax = total.amount
    if tax is None:
        warnings.append("稅額缺少清楚的金額原文或各列不一致；稅率不當成稅額，也不自行反推。")
    elif result.tax is not None and Decimal(result.tax) != Decimal(tax):
        warnings.append("模型稅額與逐列原文不一致，核對候選依逐列金額合計，請對照收據。")

    tip = money(evidence.tip, TIP, original, has_image) if evidence.tip_status == "paid" else None
    fee = money(evidence.service_charge, SERVICE, original, has_image)
    if (
        evidence.tip
        and evidence.service_charge
        and normalized(evidence.tip.text) == normalized(evidence.service_charge.text)
    ):
        tip = None
        warnings.append("同一列同時被辨識為小費與服務費，僅保留服務費候選，請核對用途。")
    if tip is None:
        warnings.append(
            {
                "blank": "小費欄空白或手寫不清楚，實付小費仍不明。",
                "suggested_only": "只有建議小費選項，沒有當成已支付小費。",
                "not_printed": "未列出實付小費，保留不明，不代填 0。",
            }.get(evidence.tip_status, "沒有可確認的實付小費原文，請核對。")
        )
    elif result.tip is not None and Decimal(result.tip) != Decimal(tip):
        warnings.append("模型小費與實付原文不一致，請核對小費候選。")

    amount = None
    if (
        evidence.total_status == "final"
        and evidence.total
        and not re.search(NOT_PAYMENT, evidence.total.text, re.I)
    ):
        amount = money(
            evidence.total,
            r"\b(?:total|paid|sale|charged)\b|實付|合計|總額|總計",
            original,
            has_image,
        )
        if evidence.tip_status == "blank" and not re.search(FINAL_PAID, evidence.total.text, re.I):
            amount = None
    if amount is None:
        warnings.append(
            "最終付款金額尚未確認；未採用小費前總額、建議金額、付款現金或找零，請手動核對。"
        )
    elif result.amount is not None and Decimal(result.amount) != Decimal(amount):
        warnings.append("模型總額與最終付款原文不一致，請核對付款金額候選。")
    if evidence.tax_mode == "included":
        warnings.append("收據標示含稅，稅額已包含於小計／價格，不會再加一次。")
    elif evidence.tax_mode == "unclear":
        warnings.append("含稅方式不明，請核對小計與最終付款金額。")
    if evidence.service_charge and fee is None:
        warnings.append("服務費原文不清楚或只有費率，未推算金額。")
    return ChargeReview(
        tax=tax,
        tip=tip,
        service_charge=fee,
        amount=amount,
        tax_mode=evidence.tax_mode,
        tip_status=evidence.tip_status,
        warnings=warnings,
    )
