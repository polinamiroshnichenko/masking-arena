"""Склонение ФИО для генератора (без внешних зависимостей).

Покрываются только регулярные парадигмы. Пулы имён и фамилий подбираются так, чтобы в них не было
исключений, которые эти правила склоняют неверно (беглые гласные, -ц, -ь у фамилий и т. п.).
Корректность проверяется тестами `tests/test_morph.py` на вручную выписанных формах.
"""
from __future__ import annotations

CASES = ("nom", "gen", "dat", "acc", "ins", "prep")

# окончания: (gen, dat, acc, ins, prep), основа = nom без `cut` последних символов
_FIRST = {
    "m_cons": (0, ("а", "у", "а", "ом", "е")),        # Иван
    "m_soft": (1, ("я", "ю", "я", "ем", "е")),        # Игорь
    "m_y": (1, ("я", "ю", "я", "ем", "е")),           # Сергей
    "m_iy": (1, ("я", "ю", "я", "ем", "и")),          # Дмитрий
    "m_a": (1, ("ы", "е", "у", "ой", "е")),           # Никита
    "f_a": (1, ("ы", "е", "у", "ой", "е")),           # Анна
    "f_a_velar": (1, ("и", "е", "у", "ой", "е")),     # Ольга
    "f_iya": (1, ("и", "и", "ю", "ей", "и")),         # Мария
    "f_ya": (1, ("и", "е", "ю", "ей", "е")),          # Наталья
}

_FIRST_EXPLICIT = {
    "Павел": ("Павел", "Павла", "Павлу", "Павла", "Павлом", "Павле"),
    "Лев": ("Лев", "Льва", "Льву", "Льва", "Львом", "Льве"),
    "Пётр": ("Пётр", "Петра", "Петру", "Петра", "Петром", "Петре"),
    "Илья": ("Илья", "Ильи", "Илье", "Илью", "Ильёй", "Илье"),
}


def _apply(word: str, cut: int, endings: tuple[str, ...]) -> dict[str, str]:
    stem = word[:-cut] if cut else word
    return dict(zip(CASES, (word, *(stem + e for e in endings)), strict=True))


def decline_first(name: str, paradigm: str) -> dict[str, str]:
    if name in _FIRST_EXPLICIT:
        return dict(zip(CASES, _FIRST_EXPLICIT[name], strict=True))
    cut, endings = _FIRST[paradigm]
    return _apply(name, cut, endings)


def decline_patronymic(patr: str, gender: str) -> dict[str, str]:
    if gender == "m":  # Иванович
        return _apply(patr, 0, ("а", "у", "а", "ем", "е"))
    return _apply(patr, 1, ("ы", "е", "у", "ой", "е"))  # Ивановна


def surname_nom(male_nom: str, paradigm: str, gender: str) -> str:
    if gender == "m" or paradigm in ("indecl", "cons"):
        return male_nom
    if paradigm == "ov":
        return male_nom + "а"
    if paradigm == "sky":
        return male_nom[:-2] + "ая"
    raise ValueError(paradigm)


def decline_surname(male_nom: str, paradigm: str, gender: str) -> dict[str, str]:
    if paradigm == "indecl":
        return dict.fromkeys(CASES, male_nom)
    if paradigm == "cons":
        if gender == "f":
            return dict.fromkeys(CASES, male_nom)
        return _apply(male_nom, 0, ("а", "у", "а", "ом", "е"))
    if paradigm == "ov":
        if gender == "m":
            return _apply(male_nom, 0, ("а", "у", "а", "ым", "е"))
        return _apply(male_nom + "а", 1, ("ой", "ой", "у", "ой", "ой"))
    if paradigm == "sky":
        stem = male_nom[:-2]
        if gender == "m":
            return dict(zip(CASES, (male_nom, stem + "ого", stem + "ому", stem + "ого", stem + "им",
                                    stem + "ом"), strict=True))
        return dict(zip(CASES, (stem + "ая", stem + "ой", stem + "ой", stem + "ую", stem + "ой",
                                stem + "ой"), strict=True))
    raise ValueError(paradigm)


class Person:
    """ФИО одного человека и все его формы."""

    def __init__(self, surname: str, s_par: str, first: str, f_par: str, patr_m: str,
                 patr_f: str, gender: str, initials_style: str = "И.И."):
        self.gender = gender
        self.surname_paradigm = s_par
        self.sur = decline_surname(surname, s_par, gender)
        self.first = decline_first(first, f_par)
        self.patr = decline_patronymic(patr_m if gender == "m" else patr_f, gender)
        fi, pi = self.first["nom"][0], self.patr["nom"][0]
        self.initials = {"И.И.": f"{fi}.{pi}.", "И. И.": f"{fi}. {pi}."}[initials_style]

    def form(self, fmt: str, case: str = "nom") -> str:
        s, f, p, ini = self.sur[case], self.first[case], self.patr[case], self.initials
        return {
            "full": f"{s} {f} {p}",
            "full_io": f"{f} {p} {s}",
            "short": f"{s} {ini}",
            "io_short": f"{ini} {s}",
            "name_patr": f"{f} {p}",
            "name_surn": f"{f} {s}",
            "surname": s,
            "name": f,
        }[fmt]

    def restorable_from(self, fmt: str) -> bool:
        """Однозначно ли склоняется каноническая форма fmt без знания пола (нужно для unmask).

        Неоднозначен только случай «фамилия на согласный + инициалы»: «Мельник И.И.» склоняется
        у мужчины и не склоняется у женщины.
        """
        if fmt in ("full", "full_io", "name_patr", "name_surn", "name"):
            return True
        return self.surname_paradigm in ("ov", "sky", "indecl")
