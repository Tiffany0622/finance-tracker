"""Conservative draft defaults from explicit text, then address clues (not language)."""

import re
import unicodedata
from dataclasses import dataclass

from .schemas import ParsedReceipt

SUPPORTED = {"USD", "TWD"}
CODE = re.compile(
    r"\b(?:USD|TWD|CAD|AUD|NZD|HKD|SGD|EUR|GBP|JPY|CNY|RMB|KRW|CHF|THB|VND|INR|MYR|PHP|IDR|AED)\b",
    re.I,
)
SYMBOLS = {
    "USD": r"\bUS\s*\$|\bU\.?S\.?\s+DOLLARS?\b|美元|美金",
    "TWD": r"\bNT\s*\$|\bNEW TAIWAN DOLLARS?\b|新[台臺]幣",
    "CAD": r"\b(?:CA|C)\$|\bCANADIAN DOLLARS?\b|加[元幣]",
    "AUD": r"\b(?:AU|A)\$|\bAUSTRALIAN DOLLARS?\b|澳[元幣]",
    "NZD": r"\bNZ\$|紐[元幣]",
    "HKD": r"\bHK\$|港[元幣]",
    "SGD": r"\bS\$|新加坡[元幣]",
    "EUR": r"€|歐元",
    "GBP": r"£|英鎊",
    "CNY": r"人民幣",
    "JPY": r"日[元圓]",
}
US_STATES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT "
    "NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC"
)
US_CITY_ZIP = re.compile(
    r"\b[A-Z][A-Z .'-]{1,50},?\s+(?:" + "|".join(US_STATES.split()) + r")\s+\d{5}(?:-\d{4})?\b",
    re.I,
)
TAIWAN_CITIES = (
    "臺北市|台北市|新北市|桃園市|臺中市|台中市|臺南市|台南市|高雄市|基隆市|"
    "新竹市|新竹縣|嘉義市|嘉義縣|宜蘭縣|苗栗縣|彰化縣|南投縣|雲林縣|屏東縣|"
    "花蓮縣|臺東縣|台東縣|澎湖縣|金門縣|連江縣"
)
TW_ADDRESS = re.compile(r"(?:" + TAIWAN_CITIES + r").{0,80}(?:路|街|巷|大道).{0,40}\d+(?:之\d+)?號")
US_COUNTRY = re.compile(r"\b(?:USA|U\.S\.A\.?|United States(?: of America)?)\b|美國", re.I)
TW_COUNTRY = re.compile(r"\bTaiwan\b|[台臺]灣", re.I)
FOREIGN = re.compile(
    r"\b(?:Canada|Australia|New Zealand|United Kingdom|UK|Japan|China|Hong Kong|Singapore|"
    r"Malaysia|France|Germany)\b|加拿大|澳洲|澳大利亞|紐西蘭|英國|日本|中國|香港|新加坡|馬來西亞",
    re.I,
)
NUMBERED_STREET = re.compile(
    r"\b\d+[A-Z-]*\b.{0,70}\b(?:St(?:reet)?|Ave(?:nue)?|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Way)\b",
    re.I,
)


def normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def codes(text: str) -> set[str]:
    value = normalized(text)
    found = {m.upper().replace("RMB", "CNY") for m in CODE.findall(value)}
    for code, pattern in SYMBOLS.items():
        if re.search(pattern, value, re.I):
            found.add(code)
    # Yen/yuan and generic dollars cannot identify a supported currency.
    if "¥" in value and not found.intersection({"JPY", "CNY"}):
        found.add("JPY/CNY")
    return found


def address_currency(address: str) -> str | None:
    value = normalized(address)
    us_country, tw_country = bool(US_COUNTRY.search(value)), bool(TW_COUNTRY.search(value))
    if FOREIGN.search(value) or us_country and tw_country:
        return None
    us = bool(US_CITY_ZIP.search(value)) or us_country and bool(NUMBERED_STREET.search(value))
    tw = bool(TW_ADDRESS.search(value)) or tw_country and bool(NUMBERED_STREET.search(value))
    if us and not tw and not tw_country:
        return "USD"
    if tw and not us and not us_country:
        return "TWD"
    return None


@dataclass(frozen=True)
class CurrencyDecision:
    currency: str | None
    basis: str
    explanation: str

    @property
    def warning(self) -> str:
        return "幣別判斷：" + self.explanation


def decide(result: ParsedReceipt, source_text: str, *, has_image: bool) -> CurrencyDecision:
    # For text-only inputs the original text is authoritative. Model-generated
    # quotes/addresses are never evidence unless present in that original text.
    original = normalized(source_text)
    quote = normalized(result.currency_text or "")
    address = normalized(result.merchant_address or "")
    if not has_image:
        quote = quote if quote.casefold() in original.casefold() else ""
        address = address if address.casefold() in original.casefold() else ""
    explicit = codes(original) | codes(quote)
    if len(explicit) > 1:
        return CurrencyDecision(None, "conflict", "發現不同幣別文字，請核對實際付款幣別後選擇。")
    if explicit:
        code = next(iter(explicit))
        if code not in SUPPORTED:
            return CurrencyDecision(
                None,
                "unsupported",
                f"文字顯示 {code}，目前只支援 USD／TWD；請核對實際扣款幣別與金額，不會自動換算。",
            )
        origin = "你提供的文字" if code in codes(original) else "模型讀取的收據文字"
        evidence = original if code in codes(original) else quote
        return CurrencyDecision(
            code,
            "explicit",
            f"初次辨識依{origin}「{evidence[:160]}」預填 {code}；請核對，之後以你選擇的幣別為準。",
        )
    # A raw model currency with no supporting text cannot override geography,
    # and an unsupported currency must never be converted into USD/TWD by location.
    raw = normalized(result.currency or "").upper()
    if raw and raw not in SUPPORTED:
        return CurrencyDecision(
            None,
            "unsupported",
            f"模型提出 {raw[:12]} 但缺少可確認的幣別文字，請核對實際付款幣別與金額。",
        )
    region = address_currency(address)
    if region:
        return CurrencyDecision(
            region,
            "address",
            f"初次辨識依商家地址「{address[:160]}」預填 {region}（地區推測）；地址由模型讀取，請核對付款幣別，之後以你選擇的幣別為準。",
        )
    return CurrencyDecision(
        None,
        "unknown",
        "沒有足夠的貨幣文字或商家地址線索；英文、中文及單獨 $ 都不能確定幣別，請手動選擇。",
    )
