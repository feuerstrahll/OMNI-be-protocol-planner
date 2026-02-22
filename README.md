# BE Planning MVP

Минимально рабочий MVP для планирования биоэквивалентности (BE): поиск источников через **PubMed/PMC (NCBI E-utilities)**, извлечение и валидация PK/CV, расчёт размера выборки, регуляторные проверки, единый отчёт и синопсис протокола.

---

## Работа с PubMed и PMC (ключевое)

Весь ввод литературы идёт **только через официальные NCBI E-utilities** (без парсинга веб-страниц).

1. **Поиск статей**  
   По INN выполняется один и тот же поисковый запрос в двух базах:
   - **PubMed** (`db=pubmed`) — ESearch → список PMID.
   - **PMC** (`db=pmc`) — ESearch → список PMC ID (без префикса "PMC" в ответе).

   Запрос: `{INN}[Title/Abstract] AND (bioequivalence OR "healthy volunteers" OR "healthy subjects" OR "crossover") AND (pharmacokinetics OR Cmax OR AUC OR pharmacokinetics[MeSH Terms])`.  
   Метаданные (заголовок, год, тип) подтягиваются через **ESummary** по полученным ID.

2. **Получение текстов**  
   - **EFetch** для выбранных ID:
     - PubMed: `rettype=abstract` — возвращается XML с абстрактами (теги `<AbstractText>`).
     - PMC: `rettype=full` — полный JATS XML статьи; из него берётся только `<abstract>`, абстракты отдаются под ключом `PMCID:{id}`.
   - Идентификаторы в пайплайне: **PMID** как строка числа, **PMC** как `PMCID:{id}` (например `PMCID:6386472`).

3. **Дополнительный шаг при нехватке CV**  
   Если по абстрактам **CVintra не найден** и настроен Yandex LLM, для каждого источника с ключом `PMCID:...` вызывается **отдельный EFetch** полного текста статьи (PMC XML). Из XML извлекаются все секции (кроме References/Appendix) и таблицы, строятся сниппеты вокруг триггеров (CV, CI, Cmax, AUC и т.д.), пересекающиеся окна объединяются. LLM вызывается по очереди по: сниппетам → целевому тексту (Results/Pharmacokinetics/Statistical + таблицы) → полному тексту, до первого успешного извлечения CV/CI.

**Ограничения и нюансы:**

- Без API-ключа NCBI лимит ~3 запроса/с; с ключом — выше (~10/с). В коде стоят паузы (`_throttle`) между запросами.
- Используется кэш запросов (настраивается через конфиг/каталог кэша).
- PMC в начале 2026 может обновлять E-utilities; eFetch для `db=pmc` ожидаемо сохранится.

---

## Строгая последовательность шагов пайплайна

При вызове **POST /run_pipeline** выполняется ровно следующее.

| № | Шаг | Описание |
|---|-----|----------|
| **1** | **Sources** | По INN вызывается `pubmed_client.search_sources(inn, retmax)`: ESearch по PubMed и PMC, затем ESummary по всем ID. Возвращаются список кандидатов (PMID / `PMCID:...`) и предупреждения. Если пользователь передал `selected_sources`, берётся он; иначе — все ID из поиска. |
| **2** | **Abstracts** | `pubmed_client.fetch_abstracts(selected_sources)`: для PMID — EFetch PubMed (abstract), для PMC — EFetch PMC (full XML), из него извлекаются только абстракты. Результат: словарь `{ source_id: abstract_text }`. |
| **3** | **PK extraction** | `pk_extractor.extract(abstracts, inn)` по этому словарю. Внутри: (3a) regex по абстрактам (Cmax, AUC, CV, CI и т.д.); (3b) при наличии LLM-экстрактора — извлечение из абстрактов по каждому источнику; (3c) если **CVintra так и не найден** и задан Yandex LLM — для каждого `PMCID:...` вызывается `fetch_pmc_sections(pmcid)` (ещё один EFetch полного текста), парсинг секций/таблиц и сниппетов, затем LLM по `snippets_text` → `target_text` → `full_text` до первого успешного CV/CI. На выходе: `pk_values`, `ci_values`, `missing`, контекст (fed/fasted, design hints) и список предупреждений. |
| **4** | **Validation** | Валидация извлечённых PK/CI по правилам из YAML (`validator.validate_with_warnings`). При необходимости добавляются предупреждения (например, уточнение условий приёма пищи). |
| **5** | **CV gate** | Выбор источника CV и значения для расчётов: ручной ввод, подтверждённый CV, вывод из 90% CI через PowerTOST (CVfromCI) или fallback. Учитываются Data Quality и правила вариабельности. |
| **6** | **Data Quality** | Расчёт DQI (0–100), уровень (green/amber/red), причины. Влияет на допустимость расчёта N_det и на Open Questions. |
| **7** | **Design** | Выбор дизайна по правилам (design_engine): 2×2 crossover, replicate, RSABE и т.д. Учитываются предпочтение пользователя (`preferred_design`) и явный запрос RSABE. |
| **8** | **Sample size (N_det)** | При наличии CV и разрешении DQI — расчёт детерминированного размера выборки (PowerTOST/R или приближённая формула). |
| **9** | **Sample size (N_risk)** | При CV в виде диапазона — расчёт риска (Monte Carlo) по целевым N и P(success). |
| **10** | **Reg checks** | Регуляторные проверки по YAML: дизайн, условия, объёмы крови, длительности и т.д. Формируются список проверок и Open Questions. |
| **11** | **FullReport** | Сбор всех результатов в единую модель `FullReport` (источники, PK/CI, CV, DQI, дизайн, N_det/N_risk, reg check, open questions, synopsis completeness и т.д.). |
| **12** | **Docx (отдельный вызов)** | Генерация синопсиса не входит в `run_pipeline`. Фронтенд передаёт полученный FullReport (или его JSON) в **POST /build_docx**; там вызывается `build_docx(all_json)` — формирование `synopsis.docx` по обязательным заголовкам, таблицам и блоку DQI. |

Итого: **поиск и тексты — только PubMed/PMC через E-utilities**; при отсутствии CV в абстрактах — подтягивание полного текста PMC и извлечение через LLM по сниппетам/целевому/полному тексту.

---

## Быстрый старт

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

## Настройки

Скопируйте `.env.example` в `.env` и заполните:

- **NCBI:** `NCBI_API_KEY` (опционально, снимает жёсткий лимит запросов), `NCBI_EMAIL`, `NCBI_TOOL`.
- **LLM (Yandex):** `YANDEX_API_KEY`, `YANDEX_FOLDER_ID` — для перевода МНН, извлечения PK из абстрактов и из полного текста PMC при эскалации.
- **PowerTOST (R):** для точного N нужен R и пакеты; путь к `Rscript` — `RSCRIPT_PATH`, при необходимости `R_LIBS_USER`.

Без Yandex LLM пайплайн работает только по regex по абстрактам; эскалация на полный текст PMC не выполняется.

---

## Пример INN

Попробуйте `metformin` или `atorvastatin`.

---

## PowerTOST (R) — для точного N

Для точного расчёта N используется PowerTOST через `Rscript`. Если R недоступен, применяется приближённая формула с предупреждением.

1. Установите R (Windows).
2. Задайте `RSCRIPT_PATH` (или добавьте Rscript в PATH). При необходимости укажите `R_LIBS_USER`.
3. Установите пакеты:  
   `Rscript -e "install.packages(c('PowerTOST','jsonlite'), repos='https://cran.rstudio.com')"`

---

## Структура

- `backend/` — FastAPI, сервисы (в т.ч. `pubmed_client`, `pmc_fetcher`, `pk_extractor`, `pipeline`).
- `backend/rules/` — YAML: дизайн, регуляторика, валидация, вариабельность.
- `backend/services/pubmed_client.py` — ESearch/ESummary/EFetch для PubMed и PMC.
- `backend/services/pmc_fetcher.py` — EFetch полного текста PMC, парсинг секций/таблиц, сниппеты для LLM.
- `frontend/` — Streamlit UI.
- `r/` — R-скрипт PowerTOST.
- `templates/` — шаблон синопсиса.
- `docs/` — спецификации, тест-кейсы, схемы.

---

## Ограничения MVP

- Извлечение PK в первую очередь из абстрактов (regex; при наличии LLM — гибрид с абстрактами и при необходимости с полным текстом PMC).
- Все числовые значения должны иметь источник и evidence (PMID/URL + контекст).
- Нет hard-fail при отсутствии данных — только предупреждения и Open Questions.
- CVintra всегда требует ручного подтверждения для использования в N_det, даже если получен из CI или LLM.

---

## Выходные файлы

- **FullReport** (JSON) — полный отчёт по расчётам и проверкам (возвращается из `/run_pipeline`).
- **synopsis.docx** — генерируется по запросу через `/build_docx` по данным отчёта; обязательные заголовки задаются в `backend/services/synopsis_requirements.py` (`REQUIRED_HEADINGS`). Отсутствующие разделы дополняются плейсхолдерами. В документ вносятся блок Data Quality (DQI) и раздел Open Questions.

---

## Проверка скрапинга, парсинга и LLM

- **Юнит-тесты (мок, без сети):**  
  `pytest backend/tests/test_pmc_fetcher.py -v`

- **Реальный PMC (парсинг без LLM):**  
  `python -m backend.scripts.test_pmc_and_llm PMC6386472`

- **Скрапинг + парсинг + LLM (нужны YANDEX_API_KEY, YANDEX_FOLDER_ID):**  
  `python -m backend.scripts.test_pmc_and_llm PMC6386472 --llm`
