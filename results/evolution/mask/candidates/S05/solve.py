"""robust-x1 (потомок names-n1 = S03): обобщающие исправления остаточных ошибок и режимов отказа на сдвинутых данных.

rules-a1: структурные классы ПДн с контекстом и контрольными суммами (потомок baseline).

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
    ("ТЕЛЕФОН", re.compile(D + r"(?:\+7|8)[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2,3}" + ND)),
    ("ТЕЛЕФОН", re.compile(D + r"(?:\+7|8)[\s.-]?\(\d{3,5}\)[\s-]?\d{2,3}[\s-]?\d{2}[\s-]?\d{2}" + ND)),
    ("ТЕЛЕФОН", re.compile(r"(?<![\d)])\(\d{3,5}\)\s?\d{2,3}[\s-]?\d{2}[\s-]?\d{2}" + ND)),
    ("КАРТА", re.compile(D + r"\d{4}([\s-]?)\d{4}\1\d{4}\1\d{4}" + ND)),
    ("ФИО", re.compile(rf"[{UP}][{LO}]+(?:-[{UP}][{LO}]+)?[ \u00a0][{UP}]\.[ \u00a0]?[{UP}]\.")),
    ("ФИО", re.compile(rf"[{UP}]\.[ \u00a0]?[{UP}]\.[ \u00a0]?[{UP}][{LO}]+")),
    ("ФИО", re.compile(rf"[{UP}][{LO}]+[ \u00a0][{UP}][{LO}]+[ \u00a0][{UP}][{LO}]+(?:вич|вна|ична|ич)[{LO}]{{0,3}}\b")),
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
    r"(?:(?<![а-яё])рожд(?:ени|ён|ен|\.)[а-яё]*|(?<![а-яё])родил[а-яё]*|\bд\.\s?р\.|\bдр\b)[^\d\n]{0,60}$", re.I)
KW_BIRTH_NL = re.compile(
    r"(?:(?<![а-яё])рожд(?:ени|ён|ен)[а-яё]*|\bд\.\s?р\.|\bдр)[^\d\n]{0,30}[:—–-]\s*\n\s*$", re.I)
AFTER_GR = re.compile(r"\s?(?:г\.\s?р\.|г\.р\.|года\s+рождения|г\.\s+рожд\.)", re.I)
AFTER_NAME = re.compile(
    rf"[{CYR}]+(?:вич|вна|ична|ьич|ыч|ич|мич|оглы|кызы)[ ]?(?:,\s*|\(\s*|\s+\(\s*)$")
AFTER_NAMEINIT = re.compile(rf"[{UP}][{LO}]+\s[{UP}]\.\s?[{UP}]\.\s*(?:,\s*|\(\s*)$")
FOLLOW_DOC = re.compile(r"\s*,\s*(?:паспорт|снилс|инн)", re.I)
PREV_CAPWORD = re.compile(rf"[{UP}][{LO}]+,\s+$")

# ---------------------------------------------------------------- адреса

STREET_T = (
    r"(?:(?i:(?:ул|пер|пр-т|пр-д|б-р|наб)\.?|(?:ш|пл|бул|мкр|туп|просп|пр-кт)\.|улица|переулок|проспект|проезд|"
    r"бульвар|набережная|шоссе|аллея|тупик|микрорайон)|площадь(?=\s+[А-ЯЁ\d]))"
)
STREET_NAME = (rf"(?:(?:\d{{1,2}}-[{LO}]{{1,2}}\s+|\d{{1,3}}\s+(?:лет|летия)\s+)?[{CYR}][{CYR}-]*(?:\s+[{CYR}][{CYR}-]*){{0,2}}?"
               rf"|\d{{1,3}}-[{LO}]{{1,2}}(?=,))")
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
    rf"(?<![{CYR}-])(?i:г\.|город|гор\.|пос\.|п\.|с\.|ст\.|пгт\.?|дер\.|д\.|деревня|село|посёлок|поселок|станица|ст-ца)\s?{W}(?:\s+{W}){{0,2}}\s*,\s*$"
)
P_BARE_CAP = re.compile(rf"(?<![{CYR}-])[{UP}][{LO}]+(?:-[{UP}]?[{LO}]+)*(?:\s+[{UP}][{LO}]+(?:-[{UP}]?[{LO}]+)*){{0,2}}\s*,\s*$")
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


# тип улицы после названия: «Невский пр., д. 28», «Красный проспект, 25», «Тверская ул., 7»
STREET_T_AFTER = (
    r"(?i:пр-т|просп(?:ект)?|пр-кт|пр|ул(?:ица)?|ш(?:оссе)?|пер(?:еулок)?|б-р|бульвар|наб(?:ережная)?|пл(?:ощадь)?|"
    r"проезд|пр-д|аллея|тупик)"
)
STREET_ADJ = rf"[{UP}][{LO}]+(?:[ыи]й|ой|ая|яя|ое|ее|ья|[ьи]я|ьи)"
CORE_AFTER = re.compile(
    rf"(?<![{CYR}-]){STREET_ADJ}\s+{STREET_T_AFTER}(?![{CYR}])\.?(?:,\s*|\s+){HOUSE}(?:{EXTRA}){{0,3}}(?![{CYR}\d])"
)


def address_spans(text: str) -> list[tuple[int, int, str]]:
    out = []
    for rx in (CORE, CORE_AFTER):
        for m in rx.finditer(text):
            out.append((extend_address_left(text, m.start()), m.end(), "АДРЕС"))
    return out



# ---------------------------------------------------------------- ФИО: словари, морфология, NER natasha
# Словари составлены по общим знаниям о русских именах (не из значений train). Формы по падежам порождаются
# правилами окончаний; ё→е при сравнении. «Неоднозначные» имена совпадают с обычными словами (вера, роман, лев...):
# они принимаются только с заглавной буквы не в начале предложения либо в цепочке с другими признаками ФИО.

MALE_NAMES = """александр алексей анатолий андрей антон аркадий арсений артем артемий артур борис вадим валентин валерий
василий виктор виталий владимир владислав всеволод вячеслав геннадий георгий герман глеб григорий данил даниил денис
дмитрий евгений егор захар иван игорь илья кирилл константин леонид максим матвей михаил никита николай олег павел
петр руслан святослав семен сергей станислав степан тимофей тимур федор эдуард юрий ярослав яков андриан аристарх
афанасий богдан вениамин гавриил давид демьян дамир ефим емельян елисей закир ильдар ильяс ираклий казимир карл клим
корней кузьма лаврентий лазарь леон любомир макар марат мирон мстислав назар наум никифор нестор платон прохор радик
рашид ринат родион ростислав рустам савва савелий самуил тарас тихон трофим филипп харитон эльдар эмиль юлиан юлий
роберт ренат рудольф фома эрик альберт арсен арслан азат булат тагир мансур расул салават ахмед али магомед
мухаммад исмаил камиль нурлан ерлан айдар айрат аскар бронислав велимир вилен гарри генрих гордей евдоким еремей
зиновий игнат иннокентий иосиф кондрат максимилиан мефодий мирослав модест михей остап панкрат потап рамиль радмир
сава спартак эдгар марсель альфред адам аким амир анвар арман артак ашот баграт бахтияр вальтер вазген
валерьян венедикт виль гелий гусейн  динар дилшод ермолай жан зураб  измаил ильнур имран камран
карен  лукьян людвиг мартин матвий махмуд миляуш мурат назим наиль нариман октавиан орест осип оскар отар
рамазан рафаэль рахим ревмир рифат рубен сабир самвел саид салим сеймур сергий созон сулейман тельман тигран
тимерлан   фарид фаррух фёдор фидель флорентин фридрих хасан шамиль  эльмир эмин юнус юрген якуб
ян лев марк роман макс саша миша паша коля дима вася петя ваня сережа костя женя леша гена игорек толя жора
гриша боря витя володя вова сеня  стёпа данила лёва лёха санек валера кеша никитка""".split()

FEMALE_NAMES = """александра алевтина алена алина алиса алла альбина анастасия ангелина анжела анжелика анна антонина
валентина валерия варвара василиса вероника виктория виолетта галина дарья диана ева евгения екатерина елена
елизавета жанна зинаида злата зоя инга инесса инна ирина карина каролина кира клавдия кристина ксения лариса
лидия лилия лиана людмила маргарита марина мария марта милана наталия наталья нелли нина оксана олеся ольга полина
раиса регина римма рената светлана серафима снежана софья софия софа стелла таисия тамара татьяна а устинья
фаина эвелина элина эльвира эльмира эмма юлия яна ярослава зарина лейла айгуль альфия гульнара динара земфира
ляйсан равиля рузанна лиза лена оля таня света настя катя юля маша даша наташа ира аня вика ксюша мирослава
станислава владислава вячеслава ираида изабелла клара мелания матрена павла пелагея прасковья евдокия агафья агата
агния аделина адель аида акулина анфиса арина белла богдана берта бронислава венера влада гелена глафира гликерия
дарина джульетта ефросинья зарема ильмира камила капитолина кристиана лаура лолита любава майя маргарета марфа
милена мирра нонна октябрина руфина сабина сара сания тереза тоня фатима флора эльза эльнара юлиана аглая аделаида
алсу амина ася асия бэлла василина виталия вилена владлена галия гюзель дина диляра эльвина жасмин зульфия
ирма камилла кармен лада ламара лейсан леся лика лилиана луиза люба люда люсьен лия мадина малика марианна
марьяна мая муза надя назира нелля ника нурия оливия рада радмила розалия ромила руслана сания сати сима снежана
таира тася тина уля фекла хадижа цветана чулпан шакира эльмира эмилия эрика юния яся оксана а алёнка""".split()

AMBIG_NAMES = """вера надежда любовь роза лев марк роман ян мила мира лада тома дина лина ева лия ника сава адам
слава май кир мартин герман арсен диас сима рада люба света юра марта март рим тема""".split()

MALE_NAMES_X = """абдулла абрам аввакум агафон адриан азамат айрат акбар акмаль алишер амадей анзор аполлон арам арнольд
арсентий архип асхат бекзат бенедикт бруно валериан васим вахтанг вильям виссарион витольд владилен вольдемар габдулла
гаврила гамлет гарик гедеминас гоша давлат даниэль дастан демид джамал дильшод дорофей евграф евсей ермак ерофей жорж
заур зафар захарий зигмунд зуфар ибрагим игнатий изяслав ильгиз ильгам илларион ильдус ильфат иммануил инал искандер
казбек кайрат касьян клементий ксенофонт куприян ларион леонтий лукиан любим мансур маркел мартын матвей мелентий
митрофан михайло муслим мустафа назарий натан никодим никон нуриддин нурлан нургали олесь онуфрий пантелей парфен пахом
порфирий прокофий протас радислав разиль ратмир рафик рафаил рашид родислав ромуальд русик руслан рустэм сабит садык
самир сардар севастьян сидор созонт соломон спиридон тагир тамерлан тахир теймураз тимерхан трифон ульян устин фазиль
фаддей фархад федот феликс феодосий ферапонт филат фирс фрол хабиб чингиз шахзод шерзод эдик эльбрус эльнур эльшад эрнест
юсуф яким ярополк ясон леха лёша лёня толик шура юра ваня ванёк димка женька жека игорёк кирюха кирюша костик максимка
никитос олежка пашка петька ромка рома санька саня серёга серёжа славик стас стасик тёма тимка федька шурик яша
гена колян вован вадик андрюха андрюша антоша артёмка""".split()
FEMALE_NAMES_X = """авдотья аврора агриппина аделина азалия азиза алевтина александрина алёна алия амалия анфиса аполлинария ариадна
аэлита ася аят беатриса бэлла веселина виолетта галина гаяне гелена георгина гражина гузель гульмира гульшат
гюзель дарина джамиля джульетта дильназ долорес доминика евдокия евлампия евфросиния елизавета жасмина жозефина
зарема зарина зульфия зухра иветта изольда илона иоанна ираида капитолина катерина кристина лаура лилиана
любовь мадина малика мальвина марианна марфа матильда меланья мелисса милана милена мирослава мирра назира наргиз
нелли неонила нинель нонна октябрина олеся офелия павлина пелагея перизат прасковья регина рената римма розалия роксана
ромила руфина рузалия румия сабина саида салима самира севиль селена серафима сильвия снежана стефания сусанна таисия
тамила тансылу тереза ульяна урсула устинья фаина фарида фатима феодора фёкла франциска хадиджа хельга христина цветана
чулпан шакира шахноза эвелина эльвина эдита элеонора элина эльза эмилия эрика этери ядвига ямиля янина алла аля анюта
варя вита галя гуля дуня женя зося изабелла катюша катя лиля леночка люся маруся наташа настя настёна неля нюра
оля сонечка тамарка таня танюша тася тоня уля фаня юляша юленька яночка ульяночка""".split()

NAME_OVERRIDES = {  # форма → основа для имён с беглой гласной или нестандартным склонением
    "павел": "павл", "петр": "петр", "лев": "льв", "любовь": "любов", "игорь": "игор", "федор": "федор",
}
NAME_EXTRA_FORMS = {"петр": ["петра", "петру", "петром", "петре"], "федор": ["федора", "федору", "федором", "федоре"]}


def _norm(w: str) -> str:
    return w.lower().replace("ё", "е")


def _forms(name: str) -> set[str]:
    n = _norm(name)
    out = {n}
    if n in NAME_EXTRA_FORMS:
        out.update(NAME_EXTRA_FORMS[n])
        return out
    if n in NAME_OVERRIDES:
        st = NAME_OVERRIDES[n]
        if n == "любовь":
            out.update(["любови", "любовью"])
        else:
            out.update(st + e for e in ("а", "у", "ом", "е", "ем"))
        return out
    if n.endswith(("ия", "ья")):
        out.update(n[:-1] + e for e in ("и", "ю", "й", "ей", "ею", "я", "е"))
    elif n.endswith("я"):
        out.update(n[:-1] + e for e in ("и", "ю", "е", "ей", "ею", "я"))
    elif n.endswith("а"):
        out.update(n[:-1] + e for e in ("ы", "и", "у", "е", "ой", "ою", "ей"))
    elif n.endswith(("й", "ь")):
        out.update(n[:-1] + e for e in ("я", "ю", "ем", "е", "и", "ей", "ей"))
    elif n[-1] in "бвгджзклмнпрстфхцчшщ":
        out.update(n + e for e in ("а", "у", "ом", "е", "ем"))
    return out


NAME_FORMS: dict[str, bool] = {}  # форма → неоднозначна ли
_AMB_SET = {_norm(a) for a in AMBIG_NAMES}
_NOMINATIVE_OK: set[str] = set()  # именительные формы однозначных имён: «яна» — имя, а не родительный от «ян»
for _n in MALE_NAMES + FEMALE_NAMES + MALE_NAMES_X + FEMALE_NAMES_X:
    _amb = _norm(_n) in _AMB_SET
    if not _amb and len(_norm(_n)) >= 3:
        _NOMINATIVE_OK.add(_norm(_n))
    for _f in _forms(_n):
        if len(_f) >= 3:
            NAME_FORMS[_f] = NAME_FORMS.get(_f, False) or _amb
for _a in AMBIG_NAMES:
    for _f in _forms(_a):
        NAME_FORMS[_f] = True
for _f in _NOMINATIVE_OK:
    NAME_FORMS[_f] = False

# отчества: строгие (редко совпадают с обычными словами) и слабые (только рядом с другими признаками)
PATR_STRICT = re.compile(r"[а-яе]{2,}(?:ь?[ое]вич|ь?евич)(?:а|у|ем|е)?$|[а-яе]{2,}(?:[ое]вна|евна|ична|инична|ьевна)(?:ы|е|у|ой|ою)?$")
PATR_WEAK = re.compile(r"[а-яе]{2,}(?:ьич|ыч|ич)(?:а|у|ем|е)?$")
# фамилии по суффиксам: используются только в связке с другими признаками ФИО или NER
SURN = re.compile(
    r"[а-я]{2,}(?:(?:ов|ев|ин|ын)(?:а|у|ым|ой|е|ых|ы|ою)?|(?:ск|цк)(?:ий|ая|ого|ому|им|ой|ую|ом|ие|их|ими)"
    r"|(?:енк|ушк|чук|юк|ук|як|янц|ян|дзе|швили|ман|берг|штейн|зон|ер)(?:о|а|у|ом|е|ы)?|(?:их|ых)|[аяео]ва|ых)$"
)
SURN_STRONG = re.compile(
    r"[а-я]{2,}(?:(?:ов|ев|ин|ын)(?:а|у|ым|ой|е|ых|ою)?|(?:ск|цк)(?:ий|ая|ого|ому|им|ой|ую|ом|ие|их|ими)"
    r"|енк(?:о|а|у|ом|е)|чук(?:а|у|ом|е)?|ян|янц|дзе|швили|(?:их|ых))$"
)
STOP = set(
    "россия москва январь февраль март апрель май июнь июль август сентябрь октябрь ноябрь декабрь понедельник вторник "
    "среда четверг пятница суббота воскресенье спасибо здравствуйте добрый уважаемый уважаемая директор генеральный "
    "заказчик исполнитель подпись клиент сотрудник договор паспорт адрес телефон почта дата сумма банк ооо зао оао "
    "прошу просим сообщаю сообщаем жду надеюсь передаю принято решение постановили слушали выступили секретарь "
    "председатель доверенность заявление жалоба обращение приказ акт номер город улица область район свидетель "
    "получатель отправитель карта счет снилс инн электронная контакты контакт менеджер бухгалтер юрист".split()
)
TITLES = {"гр", "гражданин", "гражданка", "гражданина", "гражданке", "гражданину", "гражданкой", "гражданином", "г-н", "г-жа",
          "господин", "госпожа", "господина", "госпожи", "господину", "тов", "товарищ", "мр", "мисс", "миссис", "доктор",
          "проф", "профессор"}
STREET_PREV = {"ул", "улица", "улице", "улицы", "улицу", "пр", "пр-т", "проспект", "проспекте", "пер", "переулок", "переулке",
               "б-р", "бульвар", "наб", "набережная", "набережной", "пл", "площадь", "площади", "ш", "шоссе", "им", "имени",
               "мкр", "микрорайон", "проезд", "пр-д", "аллея", "тупик", "туп", "проспектом", "улицей", "площадью", "переулком",
               "бульваром", "набережной", "проспекта", "переулка", "бульвара", "шоссе", "проезда", "аллее", "проезде"}

WORD_RE = re.compile(rf"[{CYR}]+(?:-[{CYR}]+)*")
INIT_AFTER = re.compile(rf"[ \u00a0]?[{UP}]\.[ \u00a0]?[{UP}]\.|[ \u00a0][{UP}]\.(?![{CYR}\w])")
INIT_BEFORE = re.compile(rf"(?<![{CYR}\w])[{UP}]\.[ \u00a0]?(?:[{UP}]\.[ \u00a0]?)?$")

_NER = None
_NER_WARNED = False


def _ner_per_spans(text: str) -> list[tuple[int, int]]:
    """Сущности PER natasha (NewsNERTagger). При недоступности библиотеки — предупреждение в stderr и пустой список."""
    global _NER, _NER_WARNED
    try:
        if _NER is None:
            from natasha import Doc, NewsEmbedding, NewsNERTagger, Segmenter
            _NER = (Doc, Segmenter(), NewsNERTagger(NewsEmbedding()))
        Doc, seg, tagger = _NER
        doc = Doc(text)
        doc.segment(seg)
        doc.tag_ner(tagger)
        return [(s.start, s.stop) for s in doc.spans if s.type == "PER"]
    except Exception as exc:  # noqa: BLE001 — решатель обязан отвечать и без NER
        if not _NER_WARNED:
            print(f"WARN: natasha NER недоступен: {exc!r}", file=sys.stderr)
            _NER_WARNED = True
        return []


def _is_cap(w: str) -> bool:
    return all(p[:1].isupper() and p[1:].islower() for p in w.split("-"))


def _is_upper(w: str) -> bool:
    return len(w) >= 4 and all(len(p) >= 2 and p.isupper() for p in w.split("-"))


def _fn_info(low: str):
    """None — не имя; иначе флаг неоднозначности. Для двойных имён (Анна-Мария) все части должны быть именами."""
    parts = low.split("-")
    infos = [NAME_FORMS.get(p) for p in parts]
    if any(i is None for i in infos):
        return None
    return all(infos)


def _surn_any(low: str) -> bool:
    return any(SURN_STRONG.match(p) for p in low.split("-"))


# обращения и титулы: не часть ФИО, но признак того, что дальше человек
SALUT = re.compile(
    r"(?:много|глубоко)?(?:уважаем|дорог|любим)(?:ый|ая|ое|ые|ого|ой|ому|ую|ым|ыми|ых)$|"
    r"господин(?:а|у|ом|е)?$|господа$|госпож(?:а|и|е|у|ой)$|гражданин(?:а|у|ом|е)?$|гражданк(?:а|и|е|у|ой)$|"
    r"товарищ(?:а|у|ем|е)?$|коллег(?:а|и|е|у|ой)?$|мистер$|мисс$|миссис$|сэр$|мадам$|г-н$|г-жа$|"
    r"доктор(?:а|у|ом|е)?$|профессор(?:а|у|ом|е)?$"
)
# глаголы общения: после них имя в любом регистре — получатель сообщения («скажи вере», «спроси марка»)
COMM_VERBS = {
    "скажи", "скажите", "передай", "передайте", "напиши", "напишите", "позвони", "позвоните", "спроси", "спросите",
    "сообщи", "сообщите", "попроси", "попросите", "отправь", "отправьте", "отдай", "отдайте", "ответь", "звони", "пиши",
    "пусть", "пускай", "уточни", "уточните", "свяжись", "свяжитесь", "набери", "наберите", "поздравь", "обратись",
    "обратитесь", "привет", "здравствуй", "позови", "позовите", "перешли", "перешлите",
}
PO_IMENI = re.compile(r"(?i)(?<![а-яё])по\s+имени\s*$")
CITY_CUE = re.compile(
    r"(?i)(?:(?<![а-яё])(?:г|гор|пос|пгт|дер)\.\s?|(?<![а-яё])(?:город|деревня|село|посёлок|поселок|станица)\s+)$"
)
SPEAKER = re.compile(r"(?:^|\n)[ \t]*\[?\d{1,2}:\d{2}(?::\d{2})?\]?[ \t]+$")
# слова-глаголы/наречия с заглавной (начало предложения), которые NER иногда включает в имя
VERB_RE = re.compile(r"(?:л|ла|ли|ло|лся|лась|лись|ю|ет|ут|ют|ит|ят|ем|ешь|ите|ете|ть|ться|ти|чь|ся)$")


def fio_spans(text: str) -> list[tuple[int, int, str]]:
    words = [(m.start(), m.end(), m.group(0)) for m in WORD_RE.finditer(text)]
    if not words:
        return []
    per = _ner_per_spans(text)
    n = len(words)
    low = [_norm(w[2]) for w in words]
    cap_t = [_is_cap(w[2]) and len(w[2]) >= 2 for w in words]
    cap = [cap_t[i] or _is_upper(words[i][2]) for i in range(n)]  # заглавные слова целиком — подписи и шапки
    ner = [any(a < w[1] and w[0] < b for a, b in per) for w in words]
    fn = [_fn_info(low[i]) if len(low[i]) >= 3 else None for i in range(n)]  # None / False / True(неоднозначно)
    patr_any = [bool(PATR_STRICT.match(low[i])) and len(low[i]) >= 6 for i in range(n)]
    patr_s = [cap[i] and patr_any[i] for i in range(n)]
    patr_low = [(not cap[i]) and patr_any[i] for i in range(n)]
    patr_w = [cap[i] and bool(PATR_WEAK.match(low[i])) for i in range(n)]
    stop = [low[i] in STOP or bool(SALUT.match(low[i])) for i in range(n)]
    surn = [_surn_any(low[i]) and not stop[i] for i in range(n)]
    verbish = [cap[i] and fn[i] is None and not surn[i] and not patr_any[i] and not patr_w[i]
               and len(low[i]) >= 5 and bool(VERB_RE.search(low[i])) for i in range(n)]

    def adjacent(i: int) -> bool:  # слова i и i+1 разделены одним пробелом
        return words[i + 1][0] - words[i][1] == 1 and text[words[i][1]] in "  "

    def sentence_initial(i: int) -> bool:
        before = text[max(0, words[i][0] - 6):words[i][0]].rstrip(" \t «\"(—–-")
        return before == "" or before.endswith((".", "!", "?", ":", "\n", ";"))

    def prev_word(i: int) -> str:
        m = re.search(rf"([{CYR}][{CYR}.-]*)\s*$", text[max(0, words[i][0] - 20):words[i][0]])
        return _norm(m.group(1)).rstrip(".") if m else ""

    def street_ctx(i: int) -> bool:
        pre = text[max(0, words[i][0] - 14):words[i][0]]
        if PO_IMENI.search(pre):
            return False  # «по имени Руслан» — человек, а не «школа имени Гагарина»
        return prev_word(i) in STREET_PREV or bool(CITY_CUE.search(pre))

    street = [street_ctx(i) for i in range(n)]
    ner_only = [cap[i] and ner[i] and not (surn[i] or patr_any[i] or patr_w[i]) for i in range(n)]
    surname_like = [cap[i] and not stop[i] and not verbish[i] and not street[i]
                    and (surn[i] or patr_w[i] or patr_s[i] or ner[i]) for i in range(n)]
    init_ner_only = [ner_only[i] and fn[i] is None and sentence_initial(i) for i in range(n)]

    inc = [False] * n
    for i in range(n):
        if stop[i] or street[i]:
            continue
        if patr_s[i] or fn[i] is False:
            inc[i] = True  # отчество; однозначное имя в любом регистре
        elif fn[i] is True and cap_t[i] and not sentence_initial(i):
            inc[i] = True  # неоднозначное имя: только с заглавной буквы не в начале предложения
        elif fn[i] is True and prev_word(i) in COMM_VERBS:
            inc[i] = True  # «скажи вере», «спроси марка»
        elif fn[i] is True and SPEAKER.search(text[max(0, words[i][0] - 14):words[i][0]]) and text[words[i][1]:words[i][1] + 1] == ":":
            inc[i] = True  # «10:20 вера: привет» — автор реплики
        elif cap[i] and ner[i] and surn[i]:
            inc[i] = True
        elif cap[i] and len(low[i]) >= 3 and prev_word(i) in TITLES and not verbish[i]:
            inc[i] = True
    # неоднозначное имя в начале предложения принимается рядом с однозначным признаком ФИО;
    # «вера павловна» в любом регистре: имя перед строгим отчеством
    for i in range(n):
        if fn[i] is True and not inc[i] and not stop[i] and not street[i]:
            if cap_t[i]:
                for j in (i - 1, i + 1):
                    if 0 <= j < n and adjacent(min(i, j)) and (surname_like[j] or (cap[j] and fn[j] is False)):
                        inc[i] = True
            if i + 1 < n and adjacent(i) and patr_any[i + 1] and not street[i + 1]:
                inc[i] = True
    changed = True
    while changed:
        changed = False
        for i in range(n):
            if inc[i] or stop[i] or street[i]:
                continue
            ok = False
            for j in (i - 1, i + 1):
                if not (0 <= j < n) or not inc[j] or not adjacent(min(i, j)):
                    continue
                if init_ner_only[i] and not (fn[j] is False or patr_any[j]):
                    continue  # «Пришёл Эльнур Гусейнов»: слово в начале предложения без признаков, только от NER
                if surname_like[i]:
                    ok = True  # заглавное слово с суффиксом фамилии/отчества или PER рядом с ФИО
                elif not cap[i] and (surn[i] or (patr_low[i] and j == i - 1)) and (fn[j] is not None or patr_any[j]):
                    ok = True  # нижний регистр (чат): «имя фамилия», «имя отчество»
                elif j == i - 1 and cap[i] and len(low[i]) >= 3 and (fn[j] is False or patr_s[j]) and not fn[i] \
                        and not verbish[i]:
                    ok = True  # «Имя Фамилия» без суффикса фамилии
                if ok:
                    break
            if ok:
                inc[i] = True
                changed = True
    # PER без других признаков: два и более заглавных слова либо слово с суффиксом фамилии
    for a, b in per:
        idx = [i for i in range(n) if a < words[i][1] and words[i][0] < b and cap[i] and not stop[i] and not street[i]
               and not verbish[i] and len(low[i]) >= 3]
        if idx and not any(inc[i] for i in idx) and (len(idx) >= 2 or surn[idx[0]]):
            for i in idx:
                inc[i] = True

    spans: list[tuple[int, int, str]] = []
    i = 0
    while i < n:
        if not inc[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and inc[j + 1] and adjacent(j):
            j += 1
        a, b = words[i][0], words[j][1]
        m = INIT_AFTER.match(text, b)
        if m:
            b = m.end()
        pre = text[max(0, a - 8):a]
        m2 = INIT_BEFORE.search(pre)
        if m2:
            a = a - len(pre) + m2.start()
        spans.append((a, b, "ФИО"))
        i = j + 1
    # фамилия + инициалы / инициалы + фамилия (фамилия с суффиксом либо по NER)
    for i in range(n):
        if inc[i] or not surname_like[i] or street[i]:
            continue
        a, b = words[i][0], words[i][1]
        m = INIT_AFTER.match(text, b)
        pre = text[max(0, a - 8):a]
        m2 = INIT_BEFORE.search(pre)
        if m or m2:
            if m:
                b = m.end()
            if m2:
                a = a - len(pre) + m2.start()
            spans.append((a, b, "ФИО"))
    return spans


def trim_salutation(text: str, span: tuple[int, int, str]) -> tuple[int, int, str] | None:
    """Убирает ведущие обращения/титулы («Уважаемая», «Господин») из ФИО-диапазона; пустой диапазон отбрасывается."""
    a, b, label = span
    while True:
        m = re.match(rf"([{CYR}-]+\.?)[  ]+", text[a:b])
        if not m:
            break
        w = _norm(m.group(1)).rstrip(".")
        if SALUT.match(w) or w in TITLES:
            a += m.end()
        else:
            break
    if b <= a or not re.search(rf"[{CYR}]", text[a:b]):
        return None
    return (a, b, label)


# ---------------------------------------------------------------- поиск сущностей


def birth_spans(text: str) -> list[tuple[int, int, str]]:
    out = []
    for m in DATE_RE.finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        prefix = text[line_start:m.start()]
        kw = KW_BIRTH.search(prefix[-100:])
        if kw and re.search(r"(?i)выдан|составлен|подписан", kw.group(0)):
            kw = None  # «свидетельство о рождении выдано …» — дата выдачи, а не рождения
        ok = bool(kw) or bool(KW_BIRTH_NL.search(text[max(0, m.start() - 60):m.start()]))
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
        if label == "ФИО":
            continue  # ФИО-шаблоны обрабатываются ниже вместе с fio_spans (обрезка обращений)
        for m in rx.finditer(text):
            spans.append((m.start(), m.end(), label))
    spans += address_spans(text)
    fio = fio_spans(text) + [(m.start(), m.end(), lb) for lb, rx in PATTERNS if lb == "ФИО" for m in rx.finditer(text)]
    for sp in fio:
        t = trim_salutation(text, sp)
        if t:
            spans.append(t)
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
    numbering: dict[tuple[str, str], int] = {}
    counters: dict[str, int] = {}
    out, reps, pos = [], [], 0
    for a, b, label in find_spans(text):
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
