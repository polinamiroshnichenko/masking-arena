# Блок-схема: решение и потоки данных

## 1. Барьер в работе агента (что выращиваем)

```mermaid
flowchart LR
    A[Агент: документ с ПДн] --> M["mask (solve.py)<br/>детектор → метки [КЛАСС_n]"]
    M -->|текст с метками| L[Внешняя LLM]
    M -->|таблица «метка → значение»<br/>остаётся у барьера| T[(Таблица замен)]
    L -->|ответ с метками,<br/>в т.ч. искажёнными| U["unmask (solve.py)<br/>нормализация меток + склонение"]
    T --> U
    U -->|ответ с данными<br/>в нужном падеже| A
```

Арена `mask` выращивает левую часть (детектор и расстановку меток), арена `unmask` — правую (восстановление). Это два отдельных этапа с общим baseline (ADR-0006).

## 2. Данные: от генератора до оценщиков

```mermaid
flowchart TB
    subgraph OP["Оператор (ноутбук, вне Git: operator/)"]
        C["Шаблоны 177+47 семейств, пулы значений<br/>operator/content"]
        S["secret.json: сид разбиения"]
        G["Генератор case/dataset/generator<br/>(код в Git)"]
        C --> G
        S --> G
        G -->|"семейства 2:1:1 + покрытие классов"| TR["train 1700"]
        G --> PU["public 600"]
        G --> PR["private 900"]
        SEL["tools/select_task.py"]
        TR --> SEL
        PU --> SEL
        PR --> SEL
        SEL -->|"mask: 1200/420/630<br/>unmask: 500/180/270"| D[(operator/arena/&lt;c&gt;/data)]
        RD["data_idd: реальные документы МФТИ"] -->|"29 док., разметка<br/>tools/annotate.py"| RC[(Проверочная выборка<br/>вне поиска)]
    end
    TR -->|"в Git: case/dataset/train.jsonl"| WS["workspace исследователя<br/>cases/pii-masking-ru/dataset/train"]
```

## 3. Одна арена во время поиска (mask; unmask устроена так же)

```mermaid
flowchart LR
    subgraph RC["Контейнер pii-researcher:1 (Claude Code, Sonnet 5.5 High)"]
        O[Оркестратор] --> R1["Исследователь A<br/>worktree .wt/rules"]
        O --> R2["Исследователь B<br/>worktree .wt/tagger"]
        WSP["/workspace: арена + кейс + train"]
    end
    J[("/search/search.sqlite3<br/>журнал: S01, S02, …")]
    R1 -->|"propose: снимок, родители, гипотеза"| J
    R2 -->|propose| J
    subgraph HOST["Хост оператора (127.0.0.1)"]
        TS["train-сервис :8111<br/>evaluator.train"]
        PS["public :8112<br/>ARENA_SPLIT=public"]
        PV["private :8113<br/>ARENA_SPLIT=private"]
        MET["Замороженная метрика pii-mask-v1<br/>score.py + aggregate.py"]
        RUN["Одноразовые контейнеры pii-mask-runner:1<br/>--network none, read-only, /candidate ro"]
        TS --> RUN
        PS --> RUN
        PV --> RUN
        RUN -->|ответ| MET
    end
    R1 -->|"train submit (снимок из журнала)"| TS
    TS -->|"все ответы, оценки, статистика"| R1
    O -->|"begin + pack + submit"| PS
    PS -->|только общий score| O
    O -->|"finalize: пакет ровно K финалистов"| J
    OPR -->|"readiness и submit пакета<br/>(private-токен только у оператора)"| PV
    PV -->|только статус| OPR
    PV -->|"оценки и N лучших"| OPR[Оператор]
    TS -. проверка снимка по журналу .-> J
    PS -. допуск по журналу .-> J
    PV -. search-readiness .-> J
```

Граница изоляции — права доступа: в контейнер исследователя смонтированы только `workspace/`, каталог журнала и submit-токены train и public. Private-токен и отправка финалистов — у оператора. Данных public/private, генератора с шаблонами и реальной выборки в нём нет (`/Users` в контейнере отсутствует). Оценщики слушают только 127.0.0.1 хоста, исследователь ходит к ним через `host.docker.internal` с токеном.

## 4. Путь одного кандидата

```mermaid
sequenceDiagram
    participant Res as Исследователь
    participant J as Журнал
    participant T as train-сервис
    participant P as public-сервис
    participant Rn as runner (контейнер на пример)
    participant M as метрика
    Res->>J: propose ways/S02 (гипотеза, родитель S01) → S02
    Res->>T: train submit S02 (snapshot из журнала)
    T->>J: begin S02-train (проверка digest)
    loop 1200 примеров, 4 параллельно
        T->>Rn: stdin {input}
        Rn-->>T: stdout {answer}
        T->>M: score.py {example, answer}
    end
    T->>M: aggregate.py {results} → 0.956
    T-->>Res: результат со статистикой по примерам
    Res->>J: (train-клиент) finish S02-train 0.956
    Res->>J: begin S02-public
    Res->>P: submit bundle(S02)
    P-->>Res: {score: 0.969}
    Res->>J: finish S02-public 0.969
```
