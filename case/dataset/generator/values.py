"""Значения ПДн и «соседей» (реквизиты организаций, номера документов) с разными форматами записи.

Рендер значения возвращает список кусков `(text, tag)`:
- tag = None — обычный текст (не ПДн, маскирование считается ложным срабатыванием);
- tag = метка из PII_LABELS — ПДн, обязательно к маскированию;
- tag, начинающийся с "~", — нейтральная зона (реквизиты организаций, детали выдачи паспорта):
  её маскирование не штрафуется и не требуется (см. ADR-0003).
"""
from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field

from .morph import Person

PII_LABELS = ("ФИО", "АДРЕС", "ТЕЛЕФОН", "EMAIL", "ПАСПОРТ", "СНИЛС", "ИНН", "КАРТА", "СЧЕТ",
              "ДАТА_РОЖДЕНИЯ")
Piece = tuple[str, str | None]

MONTHS_GEN = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
              "сентября", "октября", "ноября", "декабря")

_TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r",
     "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"], strict=True))


def translit(s: str) -> str:
    return "".join(_TRANSLIT.get(c, c) for c in s.lower() if c.isalnum())


def digits(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789") for _ in range(n))


def pick(rng: random.Random, weighted: dict[str, float]) -> str:
    keys = list(weighted)
    return rng.choices(keys, weights=[weighted[k] for k in keys])[0]


# ---------- контрольные суммы ----------

def snils_checksum(nine: str) -> str:
    s = sum(int(d) * w for d, w in zip(nine, range(9, 0, -1), strict=True))
    if s < 100:
        c = s
    elif s in (100, 101):
        c = 0
    else:
        c = s % 101
        c = 0 if c == 100 else c
    return f"{c:02d}"


def inn12(rng: random.Random) -> str:
    d = [int(x) for x in str(rng.choice(range(1, 90))).zfill(2) + digits(rng, 8)]
    w11 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
    d.append(sum(a * b for a, b in zip(d, w11, strict=True)) % 11 % 10)
    w12 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
    d.append(sum(a * b for a, b in zip(d, w12, strict=True)) % 11 % 10)
    return "".join(map(str, d))


def inn10(rng: random.Random) -> str:
    d = [int(x) for x in str(rng.choice(range(1, 90))).zfill(2) + digits(rng, 7)]
    w = (2, 4, 10, 3, 5, 9, 4, 6, 8)
    d.append(sum(a * b for a, b in zip(d, w, strict=True)) % 11 % 10)
    return "".join(map(str, d))


def ogrn(rng: random.Random) -> str:
    body = rng.choice("15") + digits(rng, 11)
    return body + str(int(body) % 11 % 10)


def luhn_complete(prefix: str, length: int, rng: random.Random) -> str:
    body = prefix + digits(rng, length - len(prefix) - 1)
    total = 0
    for i, ch in enumerate(reversed(body)):
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            n = n - 9 if n > 9 else n
        total += n
    return body + str((10 - total % 10) % 10)


def luhn_valid(number: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(number)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            n = n - 9 if n > 9 else n
        total += n
    return total % 10 == 0


# ---------- даты ----------

def random_date(rng: random.Random, y0: int, y1: int) -> dt.date:
    start = dt.date(y0, 1, 1).toordinal()
    end = dt.date(y1, 12, 31).toordinal()
    return dt.date.fromordinal(rng.randint(start, end))


def fmt_date(rng: random.Random, d: dt.date, style: str | None = None) -> str:
    style = style or pick(rng, {"dots": 5, "words": 2, "words_g": 1, "iso": 0.6, "slash": 0.4})
    return {
        "dots": f"{d.day:02d}.{d.month:02d}.{d.year}",
        "words": f"{d.day} {MONTHS_GEN[d.month - 1]} {d.year}",
        "words_g": f"«{d.day:02d}» {MONTHS_GEN[d.month - 1]} {d.year}",
        "iso": d.isoformat(),
        "slash": f"{d.day:02d}/{d.month:02d}/{d.year}",
        "year": str(d.year),
    }[style]


# ---------- адрес ----------

@dataclass
class Address:
    index: str
    region: str | None
    city: str
    city_kind: str  # "г." | "с." | "пос."
    street: str
    street_type: str
    house: str
    apt: str | None

    def render(self, rng: random.Random, style: str | None = None, office: bool = False) -> str:
        st_full = {"ул.": "улица", "пр-т": "проспект", "пер.": "переулок", "б-р": "бульвар",
                   "ш.": "шоссе", "наб.": "набережная", "пр-д": "проезд"}[self.street_type]
        street = f"{self.street_type} {self.street}"
        if rng.random() < 0.25:
            street = f"{st_full} {self.street}"
        house = rng.choice([f"д. {self.house}", f"д.{self.house}", f"дом {self.house}"])
        apt = ""
        if self.apt:
            unit = "офис" if office else rng.choice(["кв.", "кв.", "квартира"])
            apt = f", {unit} {self.apt}"
        city = f"{self.city_kind} {self.city}" if rng.random() < 0.8 else self.city
        style = style or pick(rng, {"full_index": 3, "city": 4, "region": 1.5, "short_dash": 1.2,
                                    "street_only": 1.0})
        if style == "full_index":
            reg = f"{self.region}, " if self.region else ""
            return f"{self.index}, {reg}{city}, {street}, {house}{apt}"
        if style == "region" and self.region:
            return f"{self.region}, {city}, {street}, {house}{apt}"
        if style == "short_dash" and self.apt and not office:
            return f"{city}, {street}, {self.house}-{self.apt}"
        if style == "street_only":
            return f"{street}, {house}{apt}"
        if style == "chat":
            a = f" кв {self.apt}" if self.apt else ""
            return f"{self.street_type.rstrip('.')} {self.street} {self.house}{a}"
        return f"{city}, {street}, {house}{apt}"


def make_address(rng: random.Random, pools, with_apt: bool = True) -> Address:
    loc = rng.choice(pools.localities)  # (region|None, city, kind)
    stype = pick(rng, {"ул.": 10, "пр-т": 2, "пер.": 1.5, "б-р": 0.7, "ш.": 0.6, "наб.": 0.5,
                       "пр-д": 0.7})
    house = str(rng.randint(1, 180))
    r = rng.random()
    if r < 0.12:
        house += rng.choice("АБВ")
    elif r < 0.22:
        house += f", корп. {rng.randint(1, 5)}"
    elif r < 0.27:
        house += f"/{rng.randint(1, 12)}"
    apt = str(rng.randint(1, 420)) if with_apt and rng.random() < 0.85 else None
    return Address(index=f"{rng.randint(101, 692)}{rng.randint(0, 999):03d}", region=loc[0],
                   city=loc[1], city_kind=loc[2], street=rng.choice(pools.streets),
                   street_type=stype, house=house, apt=apt)


# ---------- человек ----------

@dataclass
class PersonData:
    person: Person
    phone10: str
    email: str
    pass_series: str
    pass_number: str
    pass_issuer: str
    pass_date: dt.date
    pass_code: str
    snils: str
    inn: str
    card: str
    account: str
    birth: dt.date
    address: Address
    position: str
    extra: dict = field(default_factory=dict)

    @property
    def gender(self) -> str:
        return self.person.gender


def make_person(rng: random.Random, pools, gender: str | None = None) -> PersonData:
    gender = gender or rng.choice("mf")
    sur, s_par = rng.choice(pools.surnames)
    first, f_par = rng.choice(pools.first_m if gender == "m" else pools.first_f)
    patr_m, patr_f = rng.choice(pools.patronymics)
    p = Person(sur, s_par, first, f_par, patr_m, patr_f, gender,
               initials_style="И.И." if rng.random() < 0.8 else "И. И.")
    birth = random_date(rng, 1950, 2005)
    pass_date = random_date(rng, max(birth.year + 20, 2001), 2025)
    mobile = rng.random() < 0.85
    phone10 = ("9" + digits(rng, 9)) if mobile else (rng.choice(pools.city_codes) + digits(rng, 7))
    fl = translit(p.first["nom"])
    sl = translit(p.sur["nom"])
    yy = str(birth.year)[2:]
    user = pick(rng, {f"{fl[0]}.{sl}": 3, f"{fl}.{sl}": 3, f"{sl}.{fl[0]}": 1, f"{sl}{yy}": 1.5,
                      f"{fl}_{sl}{rng.randint(1, 99)}": 1, f"{sl}-{fl[0]}{fl[-1]}": 0.5})
    email = f"{user}@{rng.choice(pools.email_domains)}"
    card_prefix = pick(rng, {"220": 5, "4": 3, "5": 2})
    if card_prefix == "220":
        card_prefix = "220" + str(rng.randint(0, 4))
    elif card_prefix == "5":
        card_prefix = "5" + str(rng.randint(1, 5))
    return PersonData(
        person=p, phone10=phone10, email=email,
        pass_series=f"{rng.randint(1, 99):02d}{rng.randint(0, 25):02d}",
        pass_number=f"{rng.randint(100000, 999999)}",
        pass_issuer=rng.choice(pools.pass_issuers), pass_date=pass_date,
        pass_code=f"{rng.randint(100, 990)}-{rng.randint(1, 99):03d}",
        snils=(nine := f"{rng.randint(1, 999):03d}" + digits(rng, 6)) + snils_checksum(nine),
        inn=inn12(rng), card=luhn_complete(card_prefix, 16, rng),
        account="40817810" + str(rng.randint(0, 9)) + digits(rng, 11),
        birth=birth, address=make_address(rng, pools), position=rng.choice(pools.positions))


# ---------- рендер ПДн человека ----------

def r_phone(rng: random.Random, d10: str, style: str | None = None) -> list[Piece]:
    a, b, c, e = d10[:3], d10[3:6], d10[6:8], d10[8:]
    style = style or pick(rng, {"plus_paren": 4, "eight_paren": 2, "plus_compact": 1.5,
                                "eight_compact": 1.5, "eight_dash": 1.5, "plus_spaces": 1.5,
                                "eight_space7": 0.7})
    text = {
        "plus_paren": f"+7 ({a}) {b}-{c}-{e}",
        "eight_paren": f"8 ({a}) {b}-{c}-{e}",
        "plus_compact": f"+7{d10}",
        "eight_compact": f"8{d10}",
        "eight_dash": f"8-{a}-{b}-{c}-{e}",
        "plus_spaces": f"+7 {a} {b} {c} {e}",
        "eight_space7": f"8 {a} {b}{c}{e}",
    }[style]
    return [(text, "ТЕЛЕФОН")]


def r_passport(rng: random.Random, pd: PersonData) -> list[Piece]:
    s, n = pd.pass_series, pd.pass_number
    s_txt = s if rng.random() < 0.6 else f"{s[:2]} {s[2:]}"
    style = pick(rng, {"plain": 3, "seria_no": 3, "serii_nomer": 2, "no_only": 1.5, "glued": 0.5})
    if style == "plain":
        return [(s_txt, "ПАСПОРТ"), (" ", None), (n, "ПАСПОРТ")]
    if style == "seria_no":
        return [("серия ", None), (s_txt, "ПАСПОРТ"), (" № ", None), (n, "ПАСПОРТ")]
    if style == "serii_nomer":
        return [("серии ", None), (s_txt, "ПАСПОРТ"), (" номер ", None), (n, "ПАСПОРТ")]
    if style == "no_only":
        return [(s_txt, "ПАСПОРТ"), (" № ", None), (n, "ПАСПОРТ")]
    return [(s_txt, "ПАСПОРТ"), ("№", None), (n, "ПАСПОРТ")]


def r_passport_issue(rng: random.Random, pd: PersonData) -> list[Piece]:
    date = fmt_date(rng, pd.pass_date, pick(rng, {"dots": 4, "words": 1}))
    text = rng.choice([f"выдан {pd.pass_issuer} {date}, код подразделения {pd.pass_code}",
                       f"выдан {date} {pd.pass_issuer}, к/п {pd.pass_code}",
                       f"выдан {pd.pass_issuer}, дата выдачи {date}"])
    return [(text, "~PASSPORT_ISSUE")]


def r_snils(rng: random.Random, s: str) -> list[Piece]:
    style = pick(rng, {"std": 5, "dash4": 1.5, "compact": 2, "spaces": 1})
    return [({"std": f"{s[:3]}-{s[3:6]}-{s[6:9]} {s[9:]}",
              "dash4": f"{s[:3]}-{s[3:6]}-{s[6:9]}-{s[9:]}",
              "compact": s,
              "spaces": f"{s[:3]} {s[3:6]} {s[6:9]} {s[9:]}"}[style], "СНИЛС")]


def r_inn(rng: random.Random, s: str) -> list[Piece]:
    return [(s if rng.random() < 0.9 else f"{s[:4]} {s[4:8]} {s[8:]}", "ИНН")]


def r_card(rng: random.Random, s: str) -> list[Piece]:
    style = pick(rng, {"groups": 5, "compact": 2, "dashes": 1})
    g = [s[i:i + 4] for i in range(0, 16, 4)]
    return [({"groups": " ".join(g), "compact": s, "dashes": "-".join(g)}[style], "КАРТА")]


def r_account(rng: random.Random, s: str) -> list[Piece]:
    if rng.random() < 0.75:
        return [(s, "СЧЕТ")]
    return [(f"{s[:5]} {s[5:8]} {s[8]} {s[9:13]} {s[13:]}", "СЧЕТ")]


def r_birth(rng: random.Random, d: dt.date, style: str | None = None) -> list[Piece]:
    return [(fmt_date(rng, d, style), "ДАТА_РОЖДЕНИЯ")]


# ---------- организации (нейтральная зона) ----------

@dataclass
class OrgData:
    name: str
    inn: str
    ogrn: str
    kpp: str
    rs: str
    ks: str
    bik: str
    bank: str
    address: Address
    phone: str
    email: str


def make_org(rng: random.Random, pools) -> OrgData:
    form = pick(rng, {"ООО": 6, "АО": 2, "ПАО": 0.7, "ООО ТД": 0.5, "АНО": 0.4})
    core = rng.choice(pools.org_names)
    name = f"{form} «{core}»"
    dom = translit(core)[:14] or "company"
    inn = inn10(rng)
    phone = rng.choice([f"8-800-{rng.randint(100, 999)}-{rng.randint(10, 99)}-{rng.randint(10, 99)}",
                        f"+7 ({rng.choice(pools.city_codes)}) {rng.randint(100, 999)}-"
                        f"{rng.randint(10, 99)}-{rng.randint(10, 99)}"])
    return OrgData(name=name, inn=inn, ogrn=ogrn(rng), kpp=inn[:4] + rng.choice(["01", "43"]) + "001",
                   rs="40702810" + digits(rng, 12), ks="30101810" + digits(rng, 12),
                   bik="04" + digits(rng, 7), bank=rng.choice(pools.banks),
                   address=make_address(rng, pools), phone=phone,
                   email=f"{rng.choice(['info', 'office', 'support', 'mail', 'hr', 'zakaz'])}@{dom}.ru")


# ---------- «дистракторы»: похожие на ПДн, но не ПДн ----------

def distractor(rng: random.Random, kind: str, pools) -> str:
    if kind == "DATE":
        return fmt_date(rng, random_date(rng, 2019, 2026))
    if kind == "DOCNO":
        return rng.choice([f"{rng.randint(1, 250)}/{rng.randint(2019, 2026)}",
                           f"{rng.randint(1, 99)}-{rng.choice(['ДП', 'АР', 'К', 'ОУ'])}/{rng.randint(19, 26)}",
                           f"{rng.randint(2019, 2026)}-{rng.randint(1, 9999):04d}",
                           str(rng.randint(100, 99999))])
    if kind == "SUM":
        v = rng.choice([rng.randint(5, 999) * 1000, rng.randint(1, 99) * 50000, rng.randint(1000, 99999)])
        s = f"{v:,}".replace(",", " ")
        return rng.choice([f"{s} руб.", f"{s},00 рублей", f"{s} ₽", f"{s} (рублей)"])
    if kind == "ORDERNO":
        return rng.choice([digits(rng, 10), digits(rng, 8), f"{digits(rng, 4)}-{digits(rng, 6)}",
                           f"{rng.choice('ABCDEZ')}{digits(rng, 7)}"])
    if kind == "TRACK":
        return rng.choice([f"RA{digits(rng, 9)}RU", digits(rng, 14), f"CDEK-{digits(rng, 10)}"])
    if kind == "TIME":
        return f"{rng.randint(8, 19)}:{rng.choice(['00', '15', '30', '45'])}"
    if kind == "AREA":
        return f"{rng.randint(18, 140)},{rng.randint(0, 9)} кв. м"
    if kind == "CITY":
        loc = rng.choice(pools.localities)
        return f"г. {loc[1]}" if loc[2] == "г." else f"{loc[2]} {loc[1]}"
    if kind == "LAW":
        return rng.choice([f"ст. {rng.randint(150, 1100)} ГК РФ", "Федеральным законом № 152-ФЗ",
                           f"ст. {rng.randint(15, 420)} ТК РФ", f"п. {rng.randint(1, 9)}.{rng.randint(1, 9)} договора"])
    if kind == "EAN":
        return "460" + digits(rng, 10)
    if kind == "PERCENT":
        return f"{rng.randint(1, 99)}%"
    if kind == "MONEYACC":  # номер лицевого счёта ЖКХ / договора — не ПДн по нашей схеме
        return digits(rng, rng.choice([7, 9, 11]))
    raise KeyError(kind)
