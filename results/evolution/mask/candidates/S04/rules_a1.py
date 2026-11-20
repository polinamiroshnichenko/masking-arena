"""rules-a1: структурные классы ПДн с контекстом и контрольными суммами (потомок baseline).

Контракт runner арены (немодельный режим): stdin {"input": "<JSON-строка задачи>", "assets_dir": ...}
→ stdout {"answer": "<JSON-строка ответа>"}. Только стандартная библиотека Python 3.12.

Что добавлено к baseline (ФИО-правила baseline не менялись):
- АДРЕС: ядро «тип улицы + название + дом [+ корпус/квартира]» с необязательным «д.», дом-квартира «5-321»,
  слитная чат-форма «ул Х 5 кв 9»; слева по запятым добавляются индекс, регион, город (с маркером «г.» или без).
- ДАТА_РОЖДЕНИЯ: даты в форматах дд.мм.гггг, дд/мм/гггг, гггг-мм-дд, «д месяц гггг», «дд» месяц гггг;
  метка ставится только по контексту: слова «рождения/родился/др» слева в той же строке, «г.р.» справа,
  дата сразу после ФИО (слово с суффиксом отчества + запятая/скобка).
- ПАСПОРТ: серия + номер со словом «номер»/«№» и без пробелов, серия 2+2 и 4 цифры; слитные 10 цифр после слова «паспорт».
- СНИЛС: все четыре формата (дефисы, пробелы, слитно); слитные 11 цифр — по ключевому слову или контрольной сумме.
- ИНН: 12 цифр слитно и группами 4-4-4 — по контрольной сумме или ключевому слову «ИНН».
- СЧЁТ: 20 цифр слитно на 40… и группами 5-3-1-4-7.
- ТЕЛЕФОН: последняя группа 2–3 цифры.
Тип метки не влияет на recall метрики (покрытие токенов), поэтому конфликты между числовыми классами не критичны.
"""
import json
import re
import sys

D = r"(?<!\d)"
ND = r"(?!\d)"
UP = "А-ЯЁ"
LO = "а-яё"
CYR = "А-Яа-яЁё"
W = rf"[{CYR}][{CYR}-]*"

MONTHS = "января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря"

# ---------------------------------------------------------------- контрольные суммы


def snils_ok(digits: str) -> bool:
    if len(digits) != 11 or not digits.isdigit():
        return False
    d = [int(c) for c in digits]
    n = sum(d[i] * (9 - i) for i in range(9))
    if n < 100:
        c = n
    elif n in (100, 101):
        c = 0
    else:
        c = n % 101
        if c in (100, 101):
            c = 0
    return c == d[9] * 10 + d[10]


def inn12_ok(digits: str) -> bool:
    if len(digits) != 12 or not digits.isdigit():
        return False
    d = [int(c) for c in digits]
    w1 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
    w2 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
    c1 = sum(a * b for a, b in zip(w1, d[:10], strict=True)) % 11 % 10
    c2 = sum(a * b for a, b in zip(w2, d[:11], strict=True)) % 11 % 10
    return c1 == d[10] and c2 == d[11]


# ---------------------------------------------------------------- базовые (структурные) шаблоны

PATTERNS = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("ТЕЛЕФОН", re.compile(D + r"(?:\+7|8)[\s-]?\(?\d{3}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2,3}" + ND)),
    ("КАРТА", re.compile(D + r"\d{4}([\s-]?)\d{4}\1\d{4}\1\d{4}" + ND)),
    ("ФИО", re.compile(rf"[{UP}][{LO}]+(?:-[{UP}][{LO}]+)?\s[{UP}]\.\s?[{UP}]\.")),
    ("ФИО", re.compile(rf"[{UP}]\.\s?[{UP}]\.\s?[{UP}][{LO}]+")),
    ("ФИО", re.compile(rf"[{UP}][{LO}]+\s[{UP}][{LO}]+\s[{UP}][{LO}]+(?:вич|вна|ична|ич)[{LO}]{{0,3}}\b")),
]

# приоритет при пересечении: меньше — важнее
PRIORITY = {
    "EMAIL": 0, "АДРЕС": 1, "ДАТА_РОЖДЕНИЯ": 2, "ПАСПОРТ": 3, "СНИЛС": 4, "ИНН": 4, "СЧЕТ": 4,
    "КАРТА": 5, "ТЕЛЕФОН": 6, "ФИО": 7,
}

# ---------------------------------------------------------------- номера документов

PASSPORT_SEP = r"[,;]?\s*(?:№|N|номер|ном\.?)\s*"
PASSPORT_NUM = re.compile(D + r"(\d{2}\s?\d{2})(" + PASSPORT_SEP + r"|\s)(\d{6})" + ND, re.I)
PASSPORT_SOLID = re.compile(r"паспорт\w*[^\d\n]{0,40}?(?<!\d)(\d{10})" + ND, re.I)

SNILS_GROUPED = re.compile(D + r"\d{3}[- ]\d{3}[- ]\d{3}[- ]\d{2}" + ND)
DIGITS11 = re.compile(r"(?<![\d+-])\d{11}(?![\d-])")
DIGITS12 = re.compile(r"(?<![\d+-])\d{12}(?![\d-])")
INN_GROUPED = re.compile(r"(?<![\d])(?<!\d[ -])\d{4} \d{4} \d{4}(?!\d)(?![ -]\d)")
ACCOUNT = re.compile(D + r"40\d{3}[ -]?\d{3}[ -]?\d[ -]?\d{4}[ -]?\d{7}" + ND)
ACCOUNT_SOLID_KW = re.compile(r"(?<![\d-])\d{20}(?![\d-])")

KW_SNILS = re.compile(r"снилс[^\d\n]{0,25}$", re.I)
KW_INN = re.compile(r"инн[^\d\n]{0,25}$", re.I)
KW_ACCOUNT = re.compile(r"сч[её]т[^\d\n]{0,25}$", re.I)
KW_PHONE = re.compile(r"(?:тел|звон|мобиль|номер|на)[^\d\n]{0,25}$", re.I)

# ---------------------------------------------------------------- даты рождения

DATE_RE = re.compile(
    rf"(?<![\d.])(?:«?\d{{1,2}}»?\s+(?:{MONTHS})\s+\d{{4}}|\d{{1,2}}[./]\d{{1,2}}[./]\d{{4}}|\d{{4}}-\d{{2}}-\d{{2}})(?![\d])",
    re.I,
)
KW_BIRTH = re.compile(
    r"(?:рожд[а-яё]*|родил[а-яё]*|\bд\.\s?р\.|\bдр\b)[^\d\n]{0,60}$", re.I)
AFTER_GR = re.compile(r"\s?(?:г\.\s?р\.|г\.р\.|года\s+рождения|г\.\s+рожд\.)", re.I)
AFTER_NAME = re.compile(
    rf"[{CYR}]+(?:вич|вна|ична|ьич|ыч|ич|мич|оглы|кызы)[ ]?(?:,\s*|\(\s*|\s+\(\s*)$")
AFTER_NAMEINIT = re.compile(rf"[{UP}][{LO}]+\s[{UP}]\.\s?[{UP}]\.\s*(?:,\s*|\(\s*)$")
FOLLOW_DOC = re.compile(r"\s*,\s*(?:паспорт|снилс|инн)", re.I)
PREV_CAPWORD = re.compile(rf"[{UP}][{LO}]+,\s+$")

# ---------------------------------------------------------------- адреса

STREET_T = (
    r"(?i:(?:ул|пер|пр-т|пр-д|б-р|наб)\.?|(?:ш|пл|бул|мкр|туп|просп|пр-кт)\.|улица|переулок|проспект|проезд|"
    r"бульвар|набережная|шоссе|площадь|аллея|тупик|микрорайон)"
)
STREET_NAME = rf"(?:\d{{1,2}}-[{LO}]{{1,2}}\s+)?[{CYR}][{CYR}-]*(?:\s+[{CYR}][{CYR}-]*){{0,2}}?"
HOUSE_NUM = rf"\d{{1,4}}(?:[{CYR}](?![{CYR}]))?(?:/\d{{1,4}})?"
HOUSE = rf"(?:(?i:д\.?|дом)\s?{HOUSE_NUM}|{HOUSE_NUM}(?:-\d{{1,4}})?)"
EXTRA = (
    rf"(?:,\s*|\s+)(?i:(?:корп(?:ус)?\.?|стр(?:оение)?\.?|кв(?:артира)?\.?|оф(?:ис)?\.?|комн(?:ата)?\.?|к\.)\s?"
    rf"\d{{1,4}}(?:[{CYR}](?![{CYR}]))?(?:-\d{{1,4}})?)"
)
CORE = re.compile(
    rf"(?<![{CYR}-]){STREET_T}(?=\s)\s+{STREET_NAME}(?:,\s*|\s+){HOUSE}(?:{EXTRA}){{0,3}}(?![{CYR}\d])"
)

P_INDEX = re.compile(r"(?<!\d)\d{6}\s*,\s*$")
P_REGION = re.compile(
    rf"(?<![{CYR}-])(?:[{UP}][{CYR}-]*(?:\s+[{CYR}][{CYR}-]*)?\s+(?i:обл\.?|область|край|округ|АО)|(?i:республика|респ\.)\s+{W}(?:\s+{W})?)\s*,\s*$"
)
P_CITY = re.compile(
    rf"(?<![{CYR}-])(?i:г\.|город|гор\.|пос\.|п\.|с\.|ст\.|пгт\.?|дер\.|деревня|село|посёлок|поселок)\s?{W}(?:\s+{W}){{0,2}}\s*,\s*$"
)
P_BARE_CAP = re.compile(rf"(?<![{CYR}-])[{UP}][{LO}-]+(?:\s+[{UP}][{LO}-]+){{0,2}}\s*,\s*$")
P_BARE_LOW = re.compile(rf"(?<![{CYR}-])[{LO}-]+(?:\s+[{LO}-]+){{0,2}}\s*,\s*$")
KW_ADDR = re.compile(
    r"адрес\w*(?:\s+(?:регистрации|проживания|доставки|прописки|места\s+жительства))?\s*[:—–-]?\s*$", re.I)
BARE_OK_BEFORE = re.compile(r"(?:^|[\n:;(])\s*$")
STOP_BARE = re.compile(r"^(?:адрес\w*|регистрации|проживания|по|и|на|в|с|для|от|из|мой|мои|мое|моё)\b", re.I)


def extend_address_left(text: str, start: int) -> int:
    for _ in range(5):
        prefix = text[max(0, start - 90):start]
        base = start - len(prefix)
        step = None
        for rx in (P_INDEX, P_REGION, P_CITY):
            m = rx.search(prefix)
            if m:
                step = base + m.start()
                break
        if step is None:
            m = P_BARE_CAP.search(prefix)
            if m and not STOP_BARE.match(m.group(0)):
                before = prefix[:m.start()]
                if (BARE_OK_BEFORE.search(before) or KW_ADDR.search(before)
                        or P_INDEX.search(before) or P_REGION.search(before) or P_CITY.search(before)):
                    step = base + m.start()
            if step is None:
                m = P_BARE_LOW.search(prefix)
                if m and not STOP_BARE.match(m.group(0)):
                    before = prefix[:m.start()]
                    if KW_ADDR.search(before) or P_INDEX.search(before):
                        step = base + m.start()
        if step is None:
            break
        start = step
    return start


def address_spans(text: str) -> list[tuple[int, int, str]]:
    out = []
    for m in CORE.finditer(text):
        out.append((extend_address_left(text, m.start()), m.end(), "АДРЕС"))
    return out


# ---------------------------------------------------------------- поиск сущностей


def birth_spans(text: str) -> list[tuple[int, int, str]]:
    out = []
    for m in DATE_RE.finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        prefix = text[line_start:m.start()]
        ok = bool(KW_BIRTH.search(prefix[-100:]))
        if not ok:
            ok = bool(AFTER_GR.match(text, m.end()))
        if not ok:
            ok = bool(AFTER_NAME.search(prefix[-60:]) or AFTER_NAMEINIT.search(prefix[-40:]))
        if not ok and FOLLOW_DOC.match(text, m.end()) and PREV_CAPWORD.search(prefix[-40:]):
            ok = True
        if ok:
            out.append((m.start(), m.end(), "ДАТА_РОЖДЕНИЯ"))
    return out


def passport_spans(text: str) -> list[tuple[int, int, str]]:
    out = []
    for m in PASSPORT_NUM.finditer(text):
        sep = m.group(2)
        if any(ch.isalpha() for ch in sep):
            out.append((m.start(1), m.end(1), "ПАСПОРТ"))
            out.append((m.start(3), m.end(3), "ПАСПОРТ"))
        else:
            out.append((m.start(), m.end(), "ПАСПОРТ"))
    for m in PASSPORT_SOLID.finditer(text):
        out.append((m.start(1), m.end(1), "ПАСПОРТ"))
    return out


def number_spans(text: str) -> list[tuple[int, int, str]]:
    out = []
    for m in SNILS_GROUPED.finditer(text):
        out.append((m.start(), m.end(), "СНИЛС"))
    for m in DIGITS11.finditer(text):
        pre = text[max(0, m.start() - 40):m.start()]
        if KW_SNILS.search(pre) or snils_ok(m.group(0)):
            out.append((m.start(), m.end(), "СНИЛС"))
    for m in DIGITS12.finditer(text):
        pre = text[max(0, m.start() - 40):m.start()]
        if KW_INN.search(pre) or inn12_ok(m.group(0)):
            out.append((m.start(), m.end(), "ИНН"))
    for m in INN_GROUPED.finditer(text):
        pre = text[max(0, m.start() - 40):m.start()]
        if KW_INN.search(pre) or inn12_ok(re.sub(r"\D", "", m.group(0))):
            out.append((m.start(), m.end(), "ИНН"))
    for m in ACCOUNT.finditer(text):
        out.append((m.start(), m.end(), "СЧЕТ"))
    for m in ACCOUNT_SOLID_KW.finditer(text):
        pre = text[max(0, m.start() - 40):m.start()]
        if KW_ACCOUNT.search(pre):
            out.append((m.start(), m.end(), "СЧЕТ"))
    return out


def find_spans(text: str) -> list[tuple[int, int, str]]:
    spans = []
    for label, rx in PATTERNS:
        for m in rx.finditer(text):
            spans.append((m.start(), m.end(), label))
    spans += address_spans(text)
    spans += birth_spans(text)
    spans += passport_spans(text)
    spans += number_spans(text)
    spans.sort(key=lambda s: (PRIORITY[s[2]], -(s[1] - s[0]), s[0]))
    chosen: list[tuple[int, int, str]] = []
    for s in spans:
        if all(s[1] <= c[0] or s[0] >= c[1] for c in chosen):
            chosen.append(s)
    chosen.sort(key=lambda s: s[0])
    return chosen


def mask(text: str) -> dict:
    return render(text, find_spans(text))


def render(text: str, spans: list[tuple[int, int, str]]) -> dict:
    numbering: dict[tuple[str, str], int] = {}
    counters: dict[str, int] = {}
    out, reps, pos = [], [], 0
    for a, b, label in spans:
        orig = text[a:b]
        key = (label, orig)
        if key not in numbering:
            counters[label] = counters.get(label, 0) + 1
            numbering[key] = counters[label]
        ph = f"[{label}_{numbering[key]}]"
        out.append(text[pos:a])
        out.append(ph)
        reps.append({"placeholder": ph, "original": orig})
        pos = b
    out.append(text[pos:])
    return {"masked_text": "".join(out), "replacements": reps}


def unmask(mapping: dict, reply: str) -> dict:
    for ph, value in mapping.items():
        reply = reply.replace(ph, value)
    return {"text": reply}


def solve(task_input: str) -> str:
    task = json.loads(task_input)
    if task["task"] == "mask":
        return json.dumps(mask(task["text"]), ensure_ascii=False)
    return json.dumps(unmask(task["mapping"], task["reply"]), ensure_ascii=False)


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    print(json.dumps({"answer": solve(payload["input"])}, ensure_ascii=False))
