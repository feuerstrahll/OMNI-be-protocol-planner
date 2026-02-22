# BE Planning Pipeline — документация решения

Система планирования биоэквивалентности (BE): поиск и ранжирование источников (PubMed/PMC + официальные), извлечение и валидация PK/CV, расчёт размера выборки, регуляторные проверки, единый отчёт и синопсис протокола. Документ описывает полную логику и правила для предзащиты и презентации.

---

## 1. Архитектура и поток данных

- **Ввод:** INN (МНН), опционально — выбранные источники, ручной CV, предпочтения по дизайну и режиму (fed/fasted).
- **Источники:** только NCBI E-utilities (ESearch, ESummary, EFetch); при нехватке CV — полный текст PMC и LLM (Yandex).
- **Выход:** FullReport (JSON), синопсис DOCX по запросу (`/build_docx`).

Последовательность: **Sources → Abstracts → PK extraction → Validation → CV gate → Data Quality → Design → Sample size (N_det / N_risk) → Reg checks → FullReport.** Генерация DOCX выполняется отдельным вызовом после пайплайна.

---

## 2. Поиск и ранжирование источников (PubMed/PMC)

### 2.1. Только E-utilities

Весь ввод литературы — через официальные NCBI E-utilities (без парсинга веб-страниц). Используются ESearch, ESummary, EFetch; кэш запросов и паузы (`_throttle`) между вызовами (без API-ключа ~3 req/s, с ключом ~10 req/s).

### 2.2. Двухшаговый поиск (INN — главный объект)

**Шаг A (высокая точность):**

- Запрос: INN в **заголовке** или как **MeSH Major Topic**: `(inn[ti] OR inn[majr])`.
- Обязательно: тематические маркеры (PK/BE/формы/качество), фильтр «только люди», исключение анти-тем (см. ниже).
- Если PubMed возвращает **меньше 3** статей — выполняется шаг B.

**Шаг B (расширение):**

- Запрос: INN в **заголовке или аннотации**: `inn[tiab]`.
- Те же тематические маркеры, фильтр по людям и анти-темы сохраняются.
- Результаты шагов A и B объединяются без дубликатов по PMID.

**Тематические маркеры (входят в запрос):**

- BE/PK: bioequivalence, bioavailability, pharmacokinetics (в т.ч. MeSH), Cmax, AUC, healthy volunteers, healthy subjects, crossover.
- Формы/качество: delayed release, enteric, enteric-coated, formulation, capsule, tablet, dissolution, generic.

**Анти-темы (исключаются через NOT в запросе):**

- Probe/DDI/фенотипирование: phenotyping, phenotype, probe, cocktail, microdose.
- Ветеринария/животные: veterinary, horse, equine, cat, feline, dog, canine, rat, mice, mouse, pigs, swine.

**Фильтр по виду (обязательно для PubMed):**

- `NOT (animals[mh] NOT humans[mh])` — только исследования с участием людей.

Для **PMC** используется один запрос (title/abstract + тематические маркеры + анти-темы); фильтр MeSH по людям для PMC не применяется (нет такого тега), но анти-темы и пост-фильтр по заголовку отсекают ветеринарию и шум.

### 2.3. Ранжирование (scoring) и отсечение

После получения списка статей выполняется **загрузка аннотаций** (EFetch) и **скоринг** по заголовку и аннотации:

**Плюсы:**

- +10 — INN в заголовке как отдельное слово (точное совпадение токена).
- +5 — INN в заголовке частично/вариант.
- +3 — каждое тематическое ключевое слово в заголовке; +1 — в аннотации.
- +2 — наличие любого из «обязательных»: delayed-release, enteric, dissolution.

**Минусы:**

- −10 — в заголовке: phenotyping, phenotype, probe, cocktail, microdose.
- −5 — эти же слова только в аннотации.
- −10 — заголовок вида «… pharmacokinetics … effect(s) of &lt;INN&gt; …» (INN как модификатор, объект — другой препарат).
- −20 — исследование на животных (species=animal или маркеры в тексте: in rats, in mice, veterinary и т.д.).

**Отсечение:** статьи с итоговым баллом **&lt; 3** не возвращаются.  
**Сортировка:** по убыванию score, при равенстве — по убыванию года.

### 2.4. Официальные источники (обязательные)

После ранжированных PubMed/PMC-результатов в выдачу **всегда** добавляются **4 официальных источника** с `id_type="URL"`:

1. **FDA label** (для омепразола: Prilosec / omeprazole delayed-release).
2. **EMA SmPC** (Losec / omeprazole).
3. **DailyMed** (generic omeprazole delayed-release).
4. **BNF (NICE)** omeprazole dosing.

Для других INN подставляются заголовки и поисковые/обзорные URL (FDA/EMA/DailyMed/BNF). В UI они отображаются отдельным блоком **«Official / Regulatory»**.

### 2.5. Модель источника и дедупликация

Каждый источник описывается единой моделью:

- **id_type:** `"PMID"` | `"PMCID"` | `"URL"`.
- **id:** строка без префикса (число для PMID/PMCID, URL для официальных).
- **url**, **title**, **year**, **journal** (при наличии).
- **ref_id** (вычисляемое): `id_type:id` — единственный идентификатор для API и отображения; **никаких склеек вида PMID:PMCID:...**.

Дедупликация: по паре (нормализованный заголовок, год); при совпадении статьи в PubMed и PMC сохраняется **одна запись** (приоритет у PubMed). В DOCX и в списке источников выводится **один источник на статью** в формате `PMID:12345678`, `PMCID:PMC1234567` или `URL:<ссылка>`.

### 2.6. Получение текстов (EFetch)

- **PubMed:** EFetch с `rettype=abstract` — XML с абстрактами (`<AbstractText>`).
- **PMC:** EFetch с `rettype=full` — JATS XML; из него извлекается только `<abstract>`. Ключи в словаре: `PMID:&lt;id&gt;` или `PMCID:&lt;id&gt;`.
- Идентификаторы с префиксом **URL:** в `fetch_abstracts` не запрашиваются (для них нет абстракта в NCBI).

При **нехватке CV** и наличии Yandex LLM для источников с PMCID выполняется дополнительный EFetch полного текста PMC, парсинг секций и таблиц, построение сниппетов вокруг триггеров (CV, CI, Cmax, AUC и т.д.) и последовательный вызов LLM по сниппетам → целевому тексту → полному тексту до первого успешного извлечения CV/CI.

---

## 3. Последовательность шагов пайплайна (POST /run_pipeline)

| № | Шаг | Описание и правила |
|---|-----|---------------------|
| **1** | **Sources** | `search_sources(inn, retmax)`: двухшаговый PubMed-запрос (шаг A, при необходимости B), один запрос PMC, скоринг и отсечение по порогу, сортировка, добавление 4 официальных URL-источников. Если передан `selected_sources`, используются они; иначе — все `ref_id` из выдачи. |
| **2** | **Abstracts** | `fetch_abstracts(selected_sources)`: для PMID — EFetch PubMed (abstract), для PMCID — EFetch PMC (full), извлечение абстрактов. URL-источники пропускаются. Результат: `{ ref_id: abstract_text }`. |
| **3** | **PK extraction** | Извлечение PK/CI из абстрактов (regex + при наличии — LLM по абстрактам). При отсутствии CV и наличии LLM — эскалация на полный текст PMC по каждому PMCID (секции, таблицы, сниппеты, LLM). Выход: `pk_values`, `ci_values`, `missing`, контекст (fed/fasted, design hints), предупреждения. |
| **4** | **Validation** | Валидация PK/CI по правилам из `backend/rules/validation_rules.yaml`. Добавление предупреждений (например, уточнение условий приёма пищи при fed). |
| **5** | **CV gate** | Выбор источника CV и значения: ручной ввод, подтверждённый пользователем CV, вывод из 90% CI (PowerTOST/CVfromCI) или fallback. Учитываются правила вариабельности (`variability_rules.yaml`) и политика доверия (confidence_score, doubtful). |
| **6** | **Data Quality** | Расчёт DQI (0–100), уровень green/amber/red, причины. Влияет на `allow_n_det` и Open Questions. Формула и правила — ниже. |
| **7** | **Design** | Выбор дизайна по `design_rules.yaml`: 2×2 crossover, replicate, RSABE и т.д. Учитываются `preferred_design`, явный запрос RSABE (переопределение на 4-way replicate), NTI, CV. |
| **8** | **Sample size (N_det)** | При допустимом CV (подтверждённый или с высоким confidence_score и без doubtful) и разрешении DQI (`allow_n_det`) — расчёт детерминированного N через PowerTOST (R) или приближённую формулу. |
| **9** | **Sample size (N_risk)** | При CV в виде диапазона — расчёт риска (Monte Carlo) по целевым N и P(success). |
| **10** | **Reg checks** | Регуляторные проверки по `reg_rules.yaml`: DQI, трассируемость, CVfromCI, обязательные PK (Decision 85), объёмы крови, длительности и т.д. Формируются список проверок и Open Questions. |
| **11** | **FullReport** | Сбор всех результатов в модель FullReport (источники, PK/CI, CV, DQI, дизайн, N_det/N_risk, reg check, open questions, synopsis completeness). |
| **12** | **Blockers (режим final)** | При `output_mode=final` проверяются блокирующие условия: наличие N_det или N_risk, наличие CV (или диапазона), наличие первичных конечных точек (Cmax, AUC). При блокерах возвращается 422. |
| **13** | **Docx** | Генерация синопсиса **не входит** в `run_pipeline`. Фронтенд передаёт FullReport в **POST /build_docx**; там формируется `synopsis.docx` по обязательным заголовкам, таблицам, DQI и списку источников. |

---

## 4. Data Quality (DQI)

- **Формула:** `DQI = round(0.25*C + 0.25*T + 0.20*P + 0.20*K + 0.10*S)`  
  Компоненты: Completeness (C), Traceability (T), Plausibility (P), Consistency (K), Source quality (S). Веса заданы в `docs/data_quality_criteria.yaml`.

- **Уровни:** green ≥ 80, yellow ≥ 55, red &lt; 55.

- **Жёсткие красные флаги (DQI = 0, red):** отсутствие первичных конечных точек (AUC и Cmax), нулевая трассируемость, подозрительные/некорректные единицы для критичных параметров, неразрешённый конфликт источников, невалидный/неподтверждённый CVfromCI, математические противоречия (отрицательные AUC/Cmax, CV&lt;0, CI≤0). Коды заданы в `hard_red_codes`.

- **Штрафы:** за единицы измерения, порядок CI, выход CI за границы, расхождение AUC/Cmax между источниками, расхождение CV, конфликты по fed/fasted и дизайну, один источник (ограничение максимума K) и др. — см. `penalties` и `completeness_rules` в YAML.

- **Source quality (S):** оценка релевантности лучшего первичного источника (human BE/PK, условия совпадают → до 0.95; animal/in vitro → до 0.40).

- **allow_n_det:** при red или при срабатывании жёстких флагов расчёт N_det блокируется (в т.ч. для режима final).

---

## 5. Выбор дизайна

- Базовый дизайн: **2×2 crossover** (`design_rules.yaml`).
- **RSABE / HVD:** при CVintra ≥ 50% рекомендуется 4-way replicate (Reference-Scaled ABE, EAEU Decision 85).
- **NTI (narrow therapeutic index):** replicate crossover.
- **Длинный t½:** параллельный дизайн или длинный washout.
- Учитываются предпочтение пользователя (`preferred_design`) и явный запрос RSABE (`rsabe_requested` → переопределение на 4-way_replicate с предупреждением).

---

## 6. Регуляторные проверки

Правила в `backend/rules/reg_rules.yaml`: низкое DQI, отсутствие трассируемости, CVfromCI без явных допущений, отсутствие обязательных PK (AUC(0-t), Cmax, t1/2 по Decision 85), объёмы крови, длительности визитов и др. Решения: OK / CLARIFY / RISK; формируются сообщения и список того, что уточнить (what_to_clarify). Агрегация по наихудшему решению.

---

## 7. Синопсис (DOCX)

- **Обязательные разделы** заданы в `synopsis_requirements.py` (`REQUIRED_HEADINGS`): название и номер исследования, спонсор, фаза, действующее вещество, лекарственная форма и дозировка, цель и задачи, первичные/вторичные конечные точки, дизайн и обоснование, методология, популяция, критерии включения/невключения, фармакокинетические параметры, биоаналитика, таймпоинты, размер выборки, рандомизация, статистические методы, безопасность, этика и регуляторика, DQI, Open Questions, **библиографический список источников**.

- **Формат списка источников:** для каждой записи выводится строка вида  
  `... (year) PMID:12345678` или `... (year) PMCID:PMC1234567` или `... (year) URL:<ссылка>`.  
  **Никаких склеек PMID:PMCID:.** Для официальных источников (URL) идентификатор выводится как `URL:...`, не как PMID.

- Генерация: `synopsis_builder.py` (секции и таблицы из отчёта), `writer.py` (однотабличный DOCX и список источников под таблицей). Отсутствующие разделы заполняются плейсхолдерами.

---

## 8. Быстрый старт

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload
```

В другом терминале:

```bash
streamlit run frontend/app.py
```

По умолчанию frontend обращается к `http://localhost:8000`.

---

## 9. Настройки

Скопируйте `.env.example` в `.env` и при необходимости заполните:

- **NCBI:** `NCBI_API_KEY` (снимает жёсткий лимит запросов), `NCBI_EMAIL`, `NCBI_TOOL`.
- **LLM (Yandex):** `YANDEX_API_KEY`, `YANDEX_FOLDER_ID` — перевод МНН, извлечение PK из абстрактов и из полного текста PMC при эскалации.
- **PowerTOST (R):** для точного N — путь `RSCRIPT_PATH`, при необходимости `R_LIBS_USER`.

Без Yandex LLM пайплайн работает по regex по абстрактам; эскалация на полный текст PMC не выполняется.

---

## 10. Структура репозитория

- **backend/** — FastAPI, сервисы: `pubmed_client` (поиск, скоринг, официальные источники, EFetch), `pk_extractor`, `pmc_fetcher`, `pipeline`, `cv_gate`, `data_quality`, `sample_size`, `sample_size_risk`, `reg_checker`, `docx_builder`, `docx/synopsis_builder`, `docx/writer`.
- **backend/rules/** — YAML: `design_rules.yaml`, `validation_rules.yaml`, `reg_rules.yaml`, `variability_rules.yaml`.
- **backend/schemas/** — модели (в т.ч. SourceCandidate с id_type/id/ref_id), API-запросы/ответы.
- **docs/** — `data_quality_criteria.yaml`, спецификации, тест-кейсы.
- **frontend/** — Streamlit UI (поиск источников, блоки Literature и Official/Regulatory, выбор источников, Run pipeline, экспорт, build_docx).
- **r/** — R-скрипт PowerTOST для расчёта N.

---

## 11. Ограничения и политики

- Извлечение PK в первую очередь из абстрактов (regex; при наличии LLM — гибрид с абстрактами и при необходимости с полным текстом PMC).
- Все ключевые числовые значения должны иметь источник и evidence (PMID/PMCID/URL + контекст).
- Нет hard-fail при отсутствии данных в режиме draft — только предупреждения и Open Questions; в режиме **final** включены блокеры (N, CV, первичные конечные точки).
- Использование CV для N_det без ручного подтверждения возможно только при высоком confidence_score и отсутствии doubtful (с предупреждением в Open Questions).

---

## 12. Выходные артефакты

- **FullReport (JSON)** — полный отчёт по расчётам и проверкам (возвращается из `/run_pipeline`).
- **synopsis.docx** — по запросу через **POST /build_docx** по данным отчёта; обязательные заголовки из `REQUIRED_HEADINGS`, блок DQI, Open Questions, библиографический список в формате PMID:... / PMCID:PMC... / URL:... .

---

## 13. Проверка

- Юнит-тесты (мок, без сети):  
  `pytest backend/tests/test_data_quality_weighting.py backend/tests/test_data_quality_hard_red.py backend/tests/test_pk_math_validation.py -v`
- Реальный PMC (парсинг без LLM):  
  `python -m backend.scripts.test_pmc_and_llm PMC6386472`
- С LLM (нужны YANDEX_API_KEY, YANDEX_FOLDER_ID):  
  `python -m backend.scripts.test_pmc_and_llm PMC6386472 --llm`
