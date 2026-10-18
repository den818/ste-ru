#!/usr/bin/env python3
"""Эвристическая проверка русского текста по правилам ste-ru (не проверка соответствия ASD-STE100).

Использование:
    python3 ste_check.py FILE [--mode procedural|descriptive|auto] [--fail-on error|warning] [--address vy|ty|auto]
                             [--terms "сервис,служба,приложение" ...] [--glossary FILE] [--json]
    python3 ste_check.py - --mode procedural <<'EOF'
    ...текст...
    EOF

Тип текста:
    --mode procedural  — тип «инструкция»: лимит 20 слов на предложение.
    --mode descriptive — тип «описание»: лимит 25 слов.
    --mode auto        — 20 для пунктов списка, команд и предупреждений, 25 для остального (по умолчанию).

Форма обращения:
    --address vy   — команды во 2-м лице мн. ч.: «нажмите» (по умолчанию).
    --address ty   — команды на «ты»: «нажми» (эвристика по окончаниям, шумнее).
    --address auto — форма определяется по большинству команд в тексте; смешение форм — отдельное замечание.

Коды выхода:
    0 — замечаний нет или они ниже порога --fail-on;
    1 — есть замечания на уровне порога или выше (--fail-on error — только ошибки; --fail-on warning — любые;
        --strict — то же, что --fail-on warning);
    2 — ошибка входа: файл не читается, неверные аргументы.

Единицы подсчёта слов — по references/counting.md: код в бэктиках, цитата в «ёлочках», **элемент интерфейса**,
скобочная вставка, число с единицей, диапазон, путь или URL, ключ тикета, версия, дефисное слово, имя собственное
из нескольких слов с заглавной буквы и термин из --glossary считаются одним словом. Содержимое скобок проверяется
как отдельное короткое предложение.

Скрипт не понимает морфологию — он ищет поверхностные признаки. Каждое замечание перепроверьте.
Чего он не видит: деепричастия вне встроенного списка основ, условие через «при + существительное»,
выдуманные факты и смену силы утверждения. «Когда» он считает маркером условия или времени, как и «если».
YAML-шапка, блоки кода (``` и ~~~), строки таблиц и заголовки из анализа исключаются, номера строк сохраняются.
Незакрытая YAML-шапка или незакрытый блок кода — предупреждение в stderr, текст анализируется как есть.
Каждое замечание помечено правилом RU-xx из references/rules-ru.md и номером строки исходного файла.
"""
import argparse
import json
import re
import sys

LIMIT_PROCEDURAL = 20
LIMIT_DESCRIPTIVE = 25
MAX_SENTENCES_PER_PARAGRAPH = 6

DEEPRI_IMPERF = (
    r"использу|нажима|удержива|выбира|открыва|закрыва|проверя|получа|отправля|запуска|создава|добавля|удаля|изменя|"
    r"применя|сохраня|учитыва|исключа|чита|дела|зна|име|ожида|дожида|выполня|обновля|устанавлива|подключа|отключа|"
    r"перезапуска|ввод|указыва|передава|вызыва|возвраща|обраща|следу|пыта|стара|наход|явля|остава|перезагружа|"
    r"настраива|редактиру|копиру|переход|заход|уход|приход|подход|перейд|зайд|найд"
)
DEEPRI_PERF = (
    r"сдела|нажа|получи|провери|запусти|откры|закры|заверши|выполни|удали|добави|измени|примени|сохрани|подключи|"
    r"отключи|перезапусти|указа|переда|вызва|верну|прочита|записа|скопирова|настрои|установи|обнови|созда|выбра|"
    r"отправи|останови|включи|выключи|собра|разверну|перезагрузи|исправи|заполни|дожда|убеди|подтверди|"
    r"обнаружи|зада|реши|сравни|очисти|перенес|выгрузи|загрузи"
)

# (регулярка, название замечания, правило RU, подсказка). Применяются к предложению без учёта регистра.
PATTERNS = [
    (r"\b(необходимо|следует|надлежит|требуется|рекомендуется|целесообразно|желательно)\b|\b(нужно|надо)\s+\w+(?:ть|ться|чь)\b",
     "модальность", "RU-15",
     "безличная модальность: команда читателю («нажмите»); о системе — «должен + глагол»; возможность — «можно»; совет в описании — «Рекомендуем»"),
    (r"\b(?:должн(?:а|о|ы)|должен)\s+быть\s+\w+(?:н|т)(?:а|о|ы)?\b",
     "страдательный залог", "RU-12",
     "скрытый пассив: если исполнитель — читатель, пишите команду; если это предусловие — «убедитесь, что …»; исполнителя не выдумывайте"),
    (r"\b(может|могут)\s+быть\s+\w+(н|т)(а|о|ы)?\b",
     "страдательный залог", "RU-12", "«может быть выполнен» — пассив; напишите, кто может это сделать, или «можно + глагол»"),
    (r"\bбыл[аио]?\s+\w+(?:ан|ен|ён|ят|ыт|ит)[аоы]?\b",
     "страдательный залог", "RU-12",
     "«был … -н/-т»: если исполнитель известен из контекста — сделайте его подлежащим; иначе оставьте состояние без «был» («Заказ отменён»)"),
    (r"\b(создаётся|создается|создаются|указывается|указываются|производится|производятся|осуществляется|осуществляются|"
     r"выполняется|выполняются|инициируется|вносятся|вносится|отправляется|отправляются|запускается|запускаются|"
     r"проверяется|проверяются|устанавливается|устанавливаются|назначается|назначаются|задаётся|задается|задаются|"
     r"используется|используются|применяется|применяются|формируется|формируются|определяется|определяются|"
     r"обрабатывается|обрабатываются|сохраняется|сохраняются|удаляется|удаляются|передаётся|передается|передаются)\b",
     "страдательный залог", "RU-12",
     "возвратный глагол в страдательном значении: если исполнитель известен из контекста — сделайте его подлежащим; если неизвестен — оставьте «-ся», исполнителя не выдумывайте (RU-00)"),
    (r"\b(осуществл\w+|производ(?:ить|ит|ят|ил[аи]?|иться)|обеспечива\w+|выполн(?:ить|ите|яет|яют)\s+(?:проверку|настройку|установку|запуск|анализ|обработку|отправку|сохранение|удаление|обновление|создание))\b",
     "пустой глагол", "RU-11", "«осуществлять / производить / выполнять + отглагольное существительное» → один глагол действия («проверьте», «настройте»)"),
    (r"\b(является|являются|являлся|являлась|являлись)\b",
     "связка «является»", "RU-11", "замените на тире или прямой глагол: «X — это Y», «X нужен для…»"),
    (r"\b(в целях|с целью|в случае(?:,)?\s+если|в случае\s+(?:наличия|отсутствия|обнаружения|возникновения)|при помощи|в рамках|в процессе|по завершении|"
     r"на предмет|в соответствии с|для того,?\s+чтобы|в связи с|в настоящее время|имеет место|в кратчайшие сроки|"
     r"с учётом того|при этом|а также|в том числе|в частности|таким образом|в результате чего|за исключением случаев|при условии,?\s+что|"
     r"в течение|в ходе|со стороны|по причине|ввиду|вследствие)\b",
     "канцелярская связка", "RU-01", "уберите или замените коротким словом: «чтобы», «если», «когда», «во время», «после», «за»"),
    (r"\b(данн(?:ый|ая|ое|ого|ой|ую|ым|ому|ом|ыми))\b",  # «данные/данных» (data) не ловим
     "«данный»", "RU-01", "«данный» → «этот» или имя объекта"),
    (r"\b(указанн\w+|вышеуказанн\w+|вышеупомянут\w+|нижеследующ\w+|соответствующ\w+|последн(?:ий|яя|ее|ие)\b(?=\s+(?:из|в|при)))",
     "ссылочное слово", "RU-21", "неявная ссылка; назовите объект прямо"),
    (r"\b(отсутств(?:ие|ия|ует|уют)|налич(?:ие|ия))\b",
     "отглагольное существительное", "RU-11", "«наличие / отсутствие» → «есть / нет»"),
    (r"\b\w+(?:ение|ание|яние|ация|ирование)"
     r"(?:\s+(?!(?:был[аио]?|буд\w*)\b)(?!\w*(?:ена|ана|ены|аны|ено|ано|ёна|ёны)\b)\w{3,}(?:ения|ания|ации|ирования|ов|ей|ии|и|ы|а|я)){3}\b",
     "цепочка родительных падежей", "RU-19", "четыре и больше существительных подряд; разбейте предлогом, глаголом или «который»"),
    (r"\b\w{3,}(?:вши|вшись|ясь|учи|ючи)\b(?=[,\s])",
     "деепричастный оборот", "RU-13", "деепричастие → отдельное предложение или «после того как / когда»"),
    (r"\b(?:(?:" + DEEPRI_IMPERF + r")(?:я|ясь)|(?:" + DEEPRI_PERF + r")(?:в|вшись))\b",
     "деепричастный оборот", "RU-13", "деепричастие → отдельное предложение; одновременное действие («Удерживая Shift, нажмите…») — допустимо"),
    (r"\b\w{3,}(?:ующ|ющ|ащ|ящ|вш|емы|имы|енн|ённ|анн|янн)(?:ий|ый|ая|ее|ие|его|ей|ему|им|их|ими|ыми|ую|ых|ым|ой|ое|ые|ого|ому|ом)\b",
     "причастный оборот", "RU-13", "причастный оборот → «который …» или отдельное предложение; одиночное причастие-термин («входящий запрос») — норма"),
    (r";", "точка с запятой", "RU-23", "точка с запятой запрещена; разбейте на два предложения или сделайте список"),
    (r"\bне\s+(?:реже|более|менее|позднее|ранее)\b",
     "отрицательное сравнение", "RU-32", "«не реже раза в месяц» → «минимум раз в месяц»; «не более» → «максимум»"),
    (r"\b(?:и/или|и\s*\(или\))",
     "и/или", "RU-10", "«и/или» двусмысленно; напишите «или», «или оба» или два предложения"),
    (r"\b(достаточно|довольно|весьма|крайне|очень|практически|в целом|как правило|некоторые|определённые|определенные|ряд)\b",
     "неопределённое слово", "RU-09", "уточните: сколько, когда, какие именно; в режиме переписывания без данных — «<уточните>"),
]

RULE_TO_RU = {"длина предложения": "RU-16", "пункт не с команды": "RU-24", "условие после команды": "RU-26",
              "длина абзаца": "RU-31", "синонимы для одного понятия": "RU-03", "смешение форм обращения": "RU-24",
              "длинная скобочная вставка": "RU-18"}

PARTICIPLE_WHITELIST = re.compile(
    r"^(следующ\w+|текущ\w+|будущ\w+|настоящ\w+|предыдущ\w+|общ\w+|блестящ\w+|подлежащ\w+|недостающ\w+|входящ\w+|исходящ\w+|ответственн\w+|собственн\w+|современн\w+|"
    r"временн\w+|постоянн\w+|длинн\w+|обыкновенн\w+|многочисленн\w+|мгновенн\w+|единственн\w+|искусственн\w+|естественн\w+|"
    r"внутренн\w+|утренн\w+|деревянн\w+|стеклянн\w+|оловянн\w+|подлинн\w+|истинн\w+|ценн\w+|военн\w+|законн\w+|"
    r"обязательн\w+|производственн\w+|государственн\w+|письменн\w+|медленн\w+|туманн\w+|странн\w+|нужн\w+)$", re.IGNORECASE)

PREP_AFTER_RE = re.compile(r"^(в|во|на|за|с|со|к|ко|по|от|из|для|при|о|об|у|без|через|под|над|между|до|после|около|вместо|против)\b")

ADVERB_PREFIX_RE = re.compile(
    r"^(?:[«\"(]?)(?:ещё раз|снова|затем|потом|сначала|теперь|после этого|обязательно|заново|повторно|аккуратно|внимательно|"
    r"при необходимости|только потом|только после этого|немедленно|сразу|всегда|далее|дополнительно|отдельно|параллельно|вручную)\s+",
    re.IGNORECASE)
NEG_PREFIX_RE = re.compile(r"^(?:[«\"(]?)(?:не|никогда не)\s+", re.IGNORECASE)
PP_PREFIX = r"(?:(?:в|во|на|после|перед|до|из|для|с|со|по|через|при|у|к|от|без)\s+(?:\S+\s+){1,5})?"
IMPERATIVE_VY_RE = re.compile(r"^(?:[«\"(]?)" + PP_PREFIX + r"([А-ЯЁа-яё]+(?:ите|йте|ьте|итесь|йтесь|ьтесь))\b", re.IGNORECASE)
# Формы на «ты» — по окончаниям; шумно, поэтому включаются только флагом --address ty|auto.
IMPERATIVE_TY_RE = re.compile(r"^(?:[«\"(]?)" + PP_PREFIX + r"([А-ЯЁа-яё]{3,}(?:и|й|ь|ись|йся|ься|айся|уйся))\b", re.IGNORECASE)
TY_STOPWORDS = {"или", "если", "ведь", "лишь", "только", "чуть", "очень", "здесь", "теперь", "потом", "затем", "иначе",
                "логи", "сервисы", "пользователи", "ошибки", "параметры", "настройки", "тесты", "запросы", "ключи", "файлы",
                "данные", "цели", "части", "детали", "модели", "версии", "сети", "роли", "секреты", "воркеры", "ретраи",
                "чтобы", "пока", "хотя", "почти", "вроде", "весь", "всей", "путь", "связь", "цель", "часть", "модель"}
CONDITION_TAIL_RE = re.compile(r",\s*(если|когда|при условии|в случае)\b", re.IGNORECASE)
CONDITION_START = ("если", "когда", "после", "перед", "до ", "чтобы", "пока")
CLAUSE_VERBS = ("дождитесь", "подождите", "проверьте", "убедитесь", "посмотрите", "узнайте", "уточните", "выясните",
                "спросите", "определите", "запишите", "укажите", "отметьте", "решите", "сообщите", "напишите",
                "дождись", "подожди", "проверь", "убедись", "посмотри", "узнай", "уточни", "выясни", "спроси", "запиши", "укажи", "сообщи", "напиши")

WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_]+(?:[-–][A-Za-zА-Яа-яЁё0-9_]+)*")
LIST_RE = re.compile(r"^(\d+[.)]|[-*•]|[а-яa-z][.)])\s+")
NUMBERED_RE = re.compile(r"^\d+[.)]\s+")
HEADING_RE = re.compile(r"^#{1,6}\s+")
LABEL_RE = re.compile(r"^\*{0,2}(ПРЕДУПРЕЖДЕНИЕ|ВНИМАНИЕ|ПРИМЕЧАНИЕ|ОСТОРОЖНО|ЗАПРЕЩАЕТСЯ)\*{0,2}\s*[:.!]?", re.IGNORECASE)
UNITS = r"(?:мс|с|сек|мин|ч|час|часа|часов|сут|дн|дней|нед|мес|%|МБ|ГБ|КБ|ТБ|Мб|Гб|Кб|байт|б|RPS|rps|шт|руб|₽|\$|px|пкс|Гц|МГц|ГГц|кг|г|м|км|мм|см|°C|°)"

TOKEN_UNIT = "⁠"   # заглушка-«одно слово» (word joiner: не буква и не пробел)


# ---------- лексер Markdown ----------

def mask_code(text):
    """Inline-код в бэктиках → заглушка. Нужен для подсчёта и поиска, но не для текста замечания."""
    return re.sub(r"`[^`\n]*`", " " + TOKEN_UNIT + " ", text)


def mask_protected(text):
    """Маскирует всё, что скилл не правит: код, цитаты, элементы интерфейса, URL, пути, идентификаторы.

    Нужна для проверки терминов: слово внутри команды, цитаты лога или чужого сообщения — не выбор
    автора, поэтому синонимом его считать нельзя (граница скилла в SKILL.md).
    """
    t = mask_code(text)
    t = re.sub(r"«[^»]*»", " " + TOKEN_UNIT + " ", t)
    t = re.sub(r"\*\*[^*]+\*\*", " " + TOKEN_UNIT + " ", t)
    t = re.sub(r"https?://\S+|\S*/\S+|[A-Za-z]:\\\S+", " " + TOKEN_UNIT + " ", t)
    t = re.sub(r"\b[A-ZА-Я]{2,}-\d+\b", " " + TOKEN_UNIT + " ", t)
    t = re.sub(r"\b\w+(?:\.\w+){1,}\b", " " + TOKEN_UNIT + " ", t)
    return t


def strip_code(text, diagnostics=None):
    """Убирает YAML-шапку, блоки кода (``` и ~~~), строки таблиц и заголовки, сохраняя количество строк.

    Незакрытая шапка или незакрытый блок кода — неверный вход: текст проверяется как обычная проза,
    в diagnostics попадает запись уровня fatal, main() возвращает код 2. Inline-код в строке остаётся:
    его маскируют подсчёт и поиск шаблонов, чтобы цитата в тексте замечания была дословной.
    """
    lines = text.split("\n")
    out = []
    fence_char, fence_len = None, 0
    in_yaml = False
    if lines and lines[0].strip() == "---":
        closing = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if closing is None:
            if diagnostics is not None:
                diagnostics.append(("fatal", "YAML-шапка не закрыта: строка 1 начинается с «---», закрывающего «---» нет. Текст проверен как обычный."))
        else:
            in_yaml = True
    for i, line in enumerate(lines):
        st = line.strip()
        if in_yaml:
            out.append("")
            if i > 0 and st == "---":
                in_yaml = False
            continue
        m = re.match(r"^(`{3,}|~{3,})(.*)$", st)
        if m:
            char, length, tail = m.group(1)[0], len(m.group(1)), m.group(2)
            if fence_char is None:
                fence_char, fence_len = char, length          # у открывающего маркера может быть язык
            elif char == fence_char and length >= fence_len and not tail.strip():
                fence_char, fence_len = None, 0               # закрывает тот же символ, не короче, без хвоста
            out.append("")
            continue
        if fence_char is not None or st.startswith("|") or HEADING_RE.match(st):
            out.append("")
            continue
        out.append(line)
    if fence_char is not None and diagnostics is not None:
        diagnostics.append(("fatal", "блок кода не закрыт: текст после открывающего маркера проверен как обычный."))
        out = _restore_unclosed(lines, out)
    return "\n".join(out)


def _restore_unclosed(lines, out):
    """Возвращает в анализ строки после последнего незакрытого открывающего маркера."""
    last, fence_char, fence_len = None, None, 0
    for i, line in enumerate(lines):
        m = re.match(r"^(`{3,}|~{3,})(.*)$", line.strip())
        if not m:
            continue
        char, length, tail = m.group(1)[0], len(m.group(1)), m.group(2)
        if fence_char is None:
            fence_char, fence_len, last = char, length, i
        elif char == fence_char and length >= fence_len and not tail.strip():
            fence_char, fence_len, last = None, 0, None
    if last is None:
        return out
    for i in range(last + 1, len(lines)):
        st = lines[i].strip()
        if st.startswith("|") or HEADING_RE.match(st) or re.match(r"^(`{3,}|~{3,})", st):
            continue
        out[i] = lines[i]
    return out


def split_paragraphs(text):
    """Список абзацев; абзац — список единиц (номер строки, текст). Пункты списка — отдельные единицы."""
    paras, current = [], []
    for idx, line in enumerate(text.split("\n"), start=1):
        stripped = line.strip()
        if not stripped:
            if current:
                paras.append(current)
                current = []
            continue
        stripped = re.sub(r"^>\s?", "", stripped)   # цитата блоком — обычный текст
        indent = len(line) - len(line.lstrip(" "))
        if indent >= 2 and current and LIST_RE.match(current[-1][1]) and not LIST_RE.match(stripped):
            current[-1] = (current[-1][0], current[-1][1] + " " + stripped, current[-1][2])   # продолжение пункта списка
            continue
        current.append((idx, stripped, indent))
    if current:
        paras.append(current)
    joined = []
    for para in paras:
        units = []
        for ln, l, ind in para:
            if units and not LIST_RE.match(l) and not LIST_RE.match(units[-1][1]):
                units[-1] = (units[-1][0], units[-1][1] + " " + l, units[-1][2])   # перенос строки внутри абзаца — не граница предложения
            else:
                units.append((ln, l, ind))
        joined.append(units)
    return joined


def split_sentences(line):
    parts = re.split(r"(?<=[.!?][»\"])\s+(?=[А-ЯЁA-Z0-9«\"(`*⁠])|(?<=[.!?])\s+(?=[А-ЯЁA-Z0-9«\"(`*⁠])", line)
    return [p.strip() for p in parts if p.strip()]


# ---------- подсчёт слов по counting.md ----------

def glossary_regexes(terms):
    regs = []
    for t in terms:
        words = t.split()
        if len(words) < 2:
            continue
        parts = []
        for w in words:
            stem = w[:-2] if len(w) >= 5 else w
            parts.append(re.escape(stem) + r"\w*")
        regs.append(re.compile(r"\b" + r"\s+".join(parts) + r"\b", re.IGNORECASE))
    return regs


def tokenize_units(sentence, glossary=()):
    """Заменяет единицы из counting.md одной заглушкой и возвращает (текст, список скобочных вставок)."""
    s = mask_code(sentence)
    parens = re.findall(r"\(([^()]{2,})\)", s)
    s = re.sub(r"\([^()]*\)", " " + TOKEN_UNIT + " ", s)                     # скобочная вставка целиком
    s = re.sub(r"«[^»]*»", " " + TOKEN_UNIT + " ", s)                        # цитата, название, элемент интерфейса
    s = re.sub(r"\*\*[^*]+\*\*", " " + TOKEN_UNIT + " ", s)                  # **элемент интерфейса**
    s = re.sub(r"https?://\S+|\S*/\S+|[A-Za-z]:\\\S+", " " + TOKEN_UNIT + " ", s)   # URL, путь
    for rg in glossary:
        s = rg.sub(" " + TOKEN_UNIT + " ", s)
    s = re.sub(r"\b(?:PR|MR)\s+\d+\b", " " + TOKEN_UNIT + " ", s)
    s = re.sub(r"\b[A-ZА-Я]{2,}-\d+\b", " " + TOKEN_UNIT + " ", s)          # ключ тикета
    num = r"\d+(?:[   ]\d{3})*(?:[.,]\d+)?"
    s = re.sub(r"(?<![\w.])" + num + r"(?:\s?[–-]\s?" + num + r")?(?:[   ]?" + UNITS + r")?(?!\w|\.\w)",
               " " + TOKEN_UNIT + " ", s)                                     # число, диапазон, число с единицей
    s = re.sub(r"\b\w+(?:\.\w+){1,}\b", " " + TOKEN_UNIT + " ", s)          # версия 2.1.0, host.example.com, file.yaml
    # имя собственное из нескольких слов с заглавной буквы (не с начала предложения, кроме латиницы)
    def merge_names(m):
        return " " + TOKEN_UNIT + " "
    s = re.sub(r"(?<=[^\s⁠][\s])(?:[A-ZА-ЯЁ][\wё-]+\s+){1,}[A-ZА-ЯЁ][\wё-]+", merge_names, s)
    s = re.sub(r"^\s*(?:[A-Z][A-Za-z\d-]+\s+){1,}[A-Z][A-Za-z\d-]+", merge_names, s)          # Google Cloud в начале
    s = re.sub(r"^\s*(?:[А-ЯЁ][а-яё\d-]+\s+){1,}[А-ЯЁ][а-яё\d-]+", merge_names, s)          # Иван Петров в начале
    return s, parens


def count_words(sentence, glossary=()):
    s, _ = tokenize_units(sentence, glossary)
    return len(WORD_RE.findall(s)) + s.count(TOKEN_UNIT)


# ---------- команды и формы обращения ----------

def command_start(sentence, address="vy"):
    """Возвращает (похоже на команду, слово-команда, форма 'vy'|'ty'|'')."""
    s = ADVERB_PREFIX_RE.sub("", sentence)
    s = NEG_PREFIX_RE.sub("", s)
    m = IMPERATIVE_VY_RE.match(s)
    if m:
        return True, m.group(1).lower(), "vy"
    if address in ("ty", "auto"):
        m = IMPERATIVE_TY_RE.match(s)
        if m and m.group(1).lower() not in TY_STOPWORDS and not m.group(1).lower().endswith(("ости", "ости")):
            return True, m.group(1).lower(), "ty"
    return False, "", ""


def count_forms(units_text):
    vy = ty = 0
    for sent in units_text:
        ok, _, form = command_start(sent, "auto")
        if ok and form == "vy":
            vy += 1
        elif ok and form == "ty":
            ty += 1
    return vy, ty


def detect_address(units_text, address):
    """Форму задаёт большинство команд. При равенстве — «вы» (документированный запасной вариант)."""
    if address != "auto":
        return address
    vy, ty = count_forms(units_text)
    return "ty" if ty > vy else "vy"


# ---------- анализ ----------

def analyze(text, mode, terms_groups, address="vy", glossary_terms=(), diagnostics=None):
    text = strip_code(text, diagnostics)
    glossary = glossary_regexes(glossary_terms)
    issues = []
    stats = {"sentences": 0, "words": 0, "paragraphs": 0, "max_sentence_words": 0, "long_sentences": 0}
    seen = set()

    def add(rule, severity, line, sent, detail, ru=None):
        key = (rule, line, sent, detail.split(":")[0])
        if key in seen:
            return
        seen.add(key)
        issues.append({"rule": rule, "ru": ru or RULE_TO_RU.get(rule, ""), "severity": severity,
                       "line": line, "text": sent, "detail": detail})

    paragraphs = split_paragraphs(text)
    all_sentences = []
    for para in paragraphs:
        for _, l, _ in para:
            all_sentences += split_sentences(LABEL_RE.sub("", LIST_RE.sub("", l)))
    effective_address = detect_address(all_sentences, address)
    if address == "auto":
        vy, ty = count_forms(all_sentences)
        if vy and ty:
            add("смешение форм обращения", "warn", 0, "весь текст", "команды и на «вы» (%d), и на «ты» (%d); выберите одну форму" % (vy, ty))

    for para in paragraphs:
        stats["paragraphs"] += 1
        para_sentences = 0
        para_is_list = all(LIST_RE.match(l) for _, l, _ in para)
        base_indent = min((ind for _, _, ind in para), default=0)
        top = [l for _, l, ind in para if ind == base_indent]
        para_is_numbered = bool(top) and all(NUMBERED_RE.match(l) for l in top)
        # Проверять «пункт не с команды» только там, где список — действительно шаги:
        # нумерованный список в режиме procedural — всегда; иначе — если не меньше половины пунктов начинаются с команды.
        check_items = False
        if para_is_list and mode != "descriptive":
            firsts = []
            for _, l, ind in para:
                if ind > base_indent:
                    continue   # вложенный список — перечисление объектов, не шаги
                body = LIST_RE.sub("", l).strip()
                is_cmd, _, _ = command_start(body, effective_address)
                firsts.append(is_cmd or body.lower().startswith(CONDITION_START))
            half = sum(1 for f in firsts if f) * 2 >= len(firsts)
            check_items = (para_is_numbered and mode == "procedural") or half
        prev_ends_colon = False
        for unit_idx, (ln, raw_line, indent) in enumerate(para):
            is_list = bool(LIST_RE.match(raw_line))
            # Перечисление объектов не проверяем как шаги: вложенный пункт или пункт после вводной фразы с двоеточием.
            is_enumeration = indent > base_indent or prev_ends_colon
            prev_ends_colon = raw_line.rstrip().endswith(":")
            line = LIST_RE.sub("", raw_line)
            is_label = bool(LABEL_RE.match(line))
            line = LABEL_RE.sub("", line).strip()
            if not line:
                continue
            for sent_idx, sent in enumerate(split_sentences(line)):
                stats["sentences"] += 1
                if not is_list:
                    para_sentences += 1
                tokenized, parens = tokenize_units(sent, glossary)
                n = len(WORD_RE.findall(tokenized)) + tokenized.count(TOKEN_UNIT)
                stats["words"] += n
                stats["max_sentence_words"] = max(stats["max_sentence_words"], n)
                looks_imperative, imp_word, _ = command_start(sent, effective_address)
                if mode == "procedural":
                    limit = LIMIT_PROCEDURAL
                elif mode == "descriptive":
                    limit = LIMIT_DESCRIPTIVE
                else:
                    limit = LIMIT_PROCEDURAL if (is_list or looks_imperative or is_label) else LIMIT_DESCRIPTIVE
                if n > limit:
                    stats["long_sentences"] += 1
                    add("длина предложения", "error", ln, sent, "%d слов, лимит %d" % (n, limit))
                for p in parens:
                    pn = count_words(p, glossary)
                    if pn > limit:
                        add("длинная скобочная вставка", "warn", ln, sent, "вставка «(%s…)»: %d слов, лимит %d; вынесите её отдельным предложением" % (p[:30], pn, limit))
                if (is_list and sent_idx == 0 and check_items and not is_enumeration and not looks_imperative
                        and not sent.lower().startswith(CONDITION_START)):
                    add("пункт не с команды", "warn", ln, sent, "пункт процедуры начинается с глагола-команды или с условия «Если …»")
                if CONDITION_TAIL_RE.search(sent) and looks_imperative and imp_word not in CLAUSE_VERBS:
                    add("условие после команды", "warn", ln, sent, "условие и время ставьте перед командой: «Если …, сделайте …»")
                low = mask_code(sent).lower()
                low_scan = re.sub(r"«[^»]*»", lambda m: " " * len(m.group(0)), low)  # цитаты не проверяем, позиции сохраняем
                for pat, rule, ru, hint in PATTERNS:
                    for m in re.finditer(pat, low_scan, flags=re.IGNORECASE):
                        token = m.group(0)
                        if rule == "причастный оборот":
                            if PARTICIPLE_WHITELIST.match(token):
                                continue
                            before = low[:m.start()].rstrip()
                            after = low[m.end():].lstrip()
                            if not (before.endswith(",") or PREP_AFTER_RE.match(after)):
                                continue   # одиночное причастие перед существительным — не оборот
                        add(rule, "error" if rule == "точка с запятой" else "warn", ln, sent, "«%s»: %s" % (token, hint), ru)
        if para_sentences > MAX_SENTENCES_PER_PARAGRAPH:
            add("длина абзаца", "warn", para[0][0], para[0][1][:80] + "…", "%d предложений, максимум %d" % (para_sentences, MAX_SENTENCES_PER_PARAGRAPH))
    # Терминологическая дисциплина: в одной группе синонимов используется один термин.
    protected = mask_protected(text)   # термины ищем только в тексте автора, не в коде и цитатах
    low_all = protected.lower()
    for group in terms_groups:
        used = {}
        for t in group:
            if t.isupper():
                used[t] = len(re.findall(r"(?<![A-Za-zА-Яа-яЁё])" + re.escape(t) + r"(?![A-Za-zА-Яа-яЁё])", protected))
            else:
                used[t] = len(re.findall(r"(?<![a-zа-яё])" + re.escape(t.lower()) + r"[а-яё]{0,3}(?![a-zа-яё])", low_all))
        used = {t: c for t, c in used.items() if c >= 2}   # одно упоминание — расшифровка «термин (АББР)», не синоним
        if len(used) > 1:
            add("синонимы для одного понятия", "warn", 0, ", ".join("%s×%d" % (t, c) for t, c in used.items()), "выберите один термин и используйте только его")
    stats["address"] = effective_address
    return stats, issues


MODE_LABEL = {"procedural": "инструкция (лимит %d слов)" % LIMIT_PROCEDURAL,
              "descriptive": "описание (лимит %d слов)" % LIMIT_DESCRIPTIVE,
              "auto": "auto (%d для пунктов списка, команд и предупреждений, %d для остального)" % (LIMIT_PROCEDURAL, LIMIT_DESCRIPTIVE)}


def load_glossary(path):
    terms = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("|"):
            cell = line.strip("|").split("|")[0].strip().strip("`")
            if cell and not set(cell) <= set("-: "):
                terms.append(cell)
        else:
            terms.append(line.split("|")[0].split(" — ")[0].strip().strip("`"))
    return [t for t in terms if t]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="файл с текстом или - для stdin")
    ap.add_argument("--mode", choices=["auto", "procedural", "descriptive"], default="auto")
    ap.add_argument("--address", choices=["vy", "ty", "auto"], default="vy", help="форма обращения в командах")
    ap.add_argument("--fail-on", choices=["error", "warning"], default="error", help="порог для кода выхода 1")
    ap.add_argument("--strict", action="store_true", help="то же, что --fail-on warning")
    ap.add_argument("--json", action="store_true", help="вывести JSON")
    ap.add_argument("--terms", action="append", default=[], help="группа синонимов через запятую, можно повторять")
    ap.add_argument("--glossary", default=None, help="файл терминов проекта: строка или первая колонка таблицы — один термин")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code not in (0, None) else 0
    try:
        text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        glossary_terms = load_glossary(args.glossary) if args.glossary else []
    except (OSError, UnicodeDecodeError) as e:
        print("ошибка входа: %s" % e, file=sys.stderr)
        return 2
    groups = [[t.strip() for t in g.split(",") if t.strip()] for g in args.terms]
    diagnostics = []
    stats, issues = analyze(text, args.mode, groups, args.address, glossary_terms, diagnostics)
    fail_on = "warning" if args.strict else args.fail_on
    errors = sum(1 for i in issues if i["severity"] == "error")
    failing = len(issues) if fail_on == "warning" else errors
    exit_code = 1 if failing else 0
    by_rule, by_ru = {}, {}
    for i in issues:
        by_rule[i["rule"]] = by_rule.get(i["rule"], 0) + 1
        by_ru[i["ru"]] = by_ru.get(i["ru"], 0) + 1
    fatal = [d for lvl, d in diagnostics if lvl == "fatal"]
    for _, d in diagnostics:
        print("диагностика: " + d, file=sys.stderr)
    if fatal:
        exit_code = 2   # незакрытая разметка — неверный вход: gate не должен проходить
    if args.json:
        print(json.dumps({"mode": args.mode, "address": stats["address"], "fail_on": fail_on, "exit_code": exit_code,
                          "diagnostics": [d for _, d in diagnostics], "stats": stats, "by_rule": by_rule, "by_ru": by_ru, "issues": issues},
                         ensure_ascii=False, indent=2))
    else:
        avg = stats["words"] / stats["sentences"] if stats["sentences"] else 0
        print("Тип: %s. Обращение: %s." % (MODE_LABEL[args.mode], "вы" if stats["address"] == "vy" else "ты"))
        print("Предложений: %d, слов: %d, среднее: %.1f слов/предл., максимум: %d, длинных: %d, абзацев: %d"
              % (stats["sentences"], stats["words"], avg, stats["max_sentence_words"], stats["long_sentences"], stats["paragraphs"]))
        print("Замечаний: %d (ошибок: %d). Порог: %s, код выхода: %d" % (len(issues), errors, fail_on, exit_code))
        for rule, cnt in sorted(by_rule.items(), key=lambda kv: -kv[1]):
            ru = RULE_TO_RU.get(rule) or next((i["ru"] for i in issues if i["rule"] == rule), "")
            print("  %s %s: %d" % (ru, rule, cnt))
        print()
        for i in issues:
            mark = "ERR " if i["severity"] == "error" else "warn"
            where = "строка %d" % i["line"] if i["line"] else "весь текст"
            print("[%s] %s %s (%s) — %s\n       %s" % (mark, i["ru"], i["rule"], where, i["detail"], i["text"][:160]))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
