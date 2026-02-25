# BE Planning Pipeline — Руководство пользователя

Руководство по использованию системы планирования исследований биоэквивалентности (BE).

---

## 1. Формат и структура входных данных

### 1.1. Основные входные параметры (RunPipelineRequest)

| Параметр | Тип | Обязательный | Описание |
|----------|-----|--------------|----------|
| `inn` | string | **Да** | Международное непатентованное название (English) для PubMed/DrugBank |
| `inn_ru` | string | Нет | МНН на русском для синопсиса и LLM-перевода |
| `dosage_form` | string | **Да** | Лекарственная форма (таблетки, капсулы, раствор и т.д.) |
| `dose` | string | **Да** | Дозировка (напр. «500 mg», «10 mcg») |
| `retmax` | int | Нет | Макс. число источников при поиске PubMed (1–50, по умолчанию 10) |
| `selected_sources` | list[string] | Нет | Список PMCID/PMID/URL — при задании поиск PubMed **не выполняется** |
| `manual_cv` | float | Нет | Ручное значение CVintra (%) |
| `cv_confirmed` | bool | Нет | Подтверждение CV пользователем (рекомендуется для финализации) |
| `protocol_condition` | literal | **Да** | Режим приёма: `fasted` (натощак), `fed` (после еды), `both` (оба варианта) |
| `power` | float | Нет | Мощность (0.5–0.99, по умолчанию 0.8) |
| `alpha` | float | Нет | Уровень значимости (0.01–0.1, по умолчанию 0.05) |
| `dropout` | float | Нет | Доля выбываний (0–0.5) |
| `screen_fail` | float | Нет | Доля screen-fail (0–0.8) |
| `output_mode` | literal | Нет | `draft` — только предупреждения; `final` — 422 при блокерах |
| `use_fallback` | bool | Нет | Использовать fallback-значения при отсутствии данных |

**Обязательные параметры для Run pipeline:** INN, лекарственная форма (`dosage_form`), дозировка (`dose`), режим приёма (`protocol_condition`: натощак / после еды / оба варианта). Без них кнопка «Запустить полный расчёт» заблокирована.

### 1.2. Идентификаторы источников

- **PMID:** `PMID:12345` или `12345`
- **PMCID:** `PMCID:PMC67890` или `PMC67890`
- **URL:** полный URL (DailyMed, FDA и т.п. — reference-only, PK не извлекается)

### 1.3. Структура PKValue (выход извлечения)

```json
{
  "name": "Cmax",
  "value": 40.0,
  "unit": "ng/mL",
  "normalized_value": 40.0,
  "normalized_unit": "ng/mL",
  "evidence": [
    {
      "pmid_or_url": "PMID:12345",
      "excerpt": "...текст из источника...",
      "location": "abstract",
      "context_tags": {"fasted": true, "log_transformed": true}
    }
  ],
  "warnings": []
}
```

Поддерживаемые `name`: `AUC`, `AUC0-t`, `AUC0-inf`, `Cmax`, `CVintra`, `t1/2`.

### 1.4. Структура CIValue (90% ДИ)

```json
{
  "param": "AUC",
  "ci_low": 0.90,
  "ci_high": 1.10,
  "ci_type": "ratio",
  "confidence_level": 0.90,
  "n": 24,
  "design_hint": "2x2_crossover",
  "evidence": []
}
```

`param`: `AUC` или `Cmax`. `ci_type`: `ratio` (0.8–1.25) или `percent` (80–125).

### 1.5. Режимы поиска PubMed (mode)

- `be` (по умолчанию): исключаются DDI-исследования
- `ddi`: допускаются drug-drug interaction исследования

---

## 2. Архитектура решения

### 2.1. Общая схема

```
┌─────────────────────────────────────────────────────────────────┐
│                     Streamlit Frontend                          │
│  Ввод INN, метаданных, выбор источников, Run pipeline, экспорт  │
└──────────────────────────────┬──────────────────────────────────┘
                               │ HTTP (BACKEND_URL)
┌──────────────────────────────▼──────────────────────────────────┐
│                     FastAPI Backend                             │
│  /search_sources | /extract_pk | /run_pipeline | /build_docx    │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                     Pipeline (10 этапов)                        │
│  Sources→Abstracts→PK→Validation→CV→DQI→Design→N_det→N_risk→Reg │
└──────────────────────────────┬──────────────────────────────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        ▼                      ▼                      ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│ NCBI E-utils  │    │ PowerTOST     │    │ Yandex LLM    │
│ ESearch,      │    │ sampleN.TOST  │    │ PK extraction │
│ ESummary,     │    │               │    │ INN translate │
│ EFetch        │    │               │    │               │
└───────────────┘    └───────────────┘    └───────────────┘
```

### 2.2. Ключевые модули

| Модуль | Путь | Назначение |
|--------|------|------------|
| `pubmed_client` | `backend/services/pubmed_client.py` | Поиск PubMed/PMC, ESearch/ESummary/EFetch, скоринг, resolve_sources |
| `pk_extractor` | `backend/services/pk_extractor.py` | Regex + LLM извлечение PK, контекст (fed/fasted, design) |
| `pmc_fetcher` | `backend/services/pmc_fetcher.py` | Полный текст PMC, секции, таблицы, сниппеты для LLM |
| `validator` | `backend/services/validator.py` | Проверка единиц, CI, CV, t½, primary endpoints |
| `cv_gate` | `backend/services/cv_gate.py` | Выбор CV (reported/derived_from_ci/manual/range/fallback) |
| `data_quality` | `backend/services/data_quality.py` | DQI, компоненты, hard gates, allow_n_det |
| `design_engine` | `backend/services/design_engine.py` | Выбор дизайна 2×2/replicate/RSABE/parallel |
| `sample_size` | `backend/services/sample_size.py` | PowerTOST + приближённая формула |
| `sample_size_risk` | `backend/services/sample_size_risk.py` | Monte Carlo, triangular/lognormal CV |
| `reg_checker` | `backend/services/reg_checker.py` | Регуляторные правила (EAEU 85 и др.) |
| `docx_builder` | `backend/services/docx_builder.py` | Генерация synopsis.docx по REQUIRED_HEADINGS |

### 2.3. Поток данных в пайплайне

1. **Sources** — если `selected_sources` заданы: `resolve_sources`; иначе `search_sources` (PubMed).
2. **Abstracts** — EFetch аннотаций по выбранным PMCID/PMID.
3. **PK extraction** — regex → LLM по абстрактам → (при эскалации) PMC full-text → LLM.
4. **Validation** — единицы, CI, CV, t½, primary endpoints.
5. **CV gate** — приоритет: manual → reported → CVfromCI → range → fallback.
6. **Data Quality** — DQI 0–100, green/yellow/red, allow_n_det.
7. **Design** — по правилам (CV, t½, NTI, RSABE), с учётом preferred_design.
8. **Sample size** — N_det (PowerTOST) при allow_n_det; N_risk (Monte Carlo) при range CV.
9. **Reg checks** — EAEU 85 и др.
10. **FullReport** — агрегированный отчёт; при `output_mode=final` и блокерах — 422.

---

## 3. Инструкция по загрузке и вводу данных

### 3.1. Запуск системы

**Вариант A: Docker (рекомендуется, одной командой)**

```bash
# 1. Настройка окружения
cp .env.example .env
# Отредактируйте .env: задайте YANDEX_API_KEY и YANDEX_FOLDER_ID

# 2. Сборка и запуск бекенда + фронтенда
docker compose up --build
```

После запуска:
- **API:** http://localhost:8000
- **UI:** http://localhost:8501

R и PowerTOST уже включены в образ, дополнительные переменные не нужны.

**Вариант B: Локальный запуск**

```bash
# 1. Установка зависимостей
pip install -r requirements.txt

# 2. Настройка окружения (копировать .env.example → .env)
# Обязательно: YANDEX_API_KEY, YANDEX_FOLDER_ID

# 3. Запуск бекенда
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# 4. Запуск фронтенда (в другом терминале)
streamlit run frontend/app.py
```

### 3.2. Порядок работы (Streamlit UI)

1. **INN** — введите МНН (кириллица или латиница), нажмите «🔄 Определить INN EN».
2. **Метаданные** — обязательно укажите лекарственную форму, дозировку и режим приёма (натощак / после еды / оба варианта). Опционально: NTI, RSABE.
3. **Источники** — «Найти источники (PubMed/PMC)» → отметьте релевантные статьи. Либо введите `PMID:...` / `PMCID:...` вручную и передайте в API как `selected_sources`.
4. **CVintra** — подтвердите значение (галочка); при отсутствии — введите вручную или дождитесь расчёта диапазона.
5. **Параметры расчёта** — power, alpha, dropout, screen-fail (по умолчанию можно оставить).
6. **Регуляторный ввод** — washout, длительности, объём крови (опционально).
7. **Run pipeline** — единая кнопка для полного расчёта.

### 3.3. API: пошаговый режим

- `POST /translate_inn` — перевод МНН (ru → en)
- `POST /search_sources` — поиск PubMed/PMC
- `POST /extract_pk` — извлечение PK из выбранных источников
- `POST /select_design` — подбор дизайна
- `POST /calc_sample_size` — расчёт N_det
- `POST /variability_estimate` — оценка диапазона CV
- `POST /risk_estimate` — Monte Carlo риск
- `POST /reg_check` — регуляторные проверки
- `POST /build_docx` — сборка synopsis.docx

### 3.4. Полный пайплайн через API

```bash
curl -X POST "http://localhost:8000/run_pipeline" \
  -H "Content-Type: application/json" \
  -d '{
    "inn": "metformin",
    "inn_ru": "метформин",
    "dosage_form": "таблетки",
    "dose": "500 mg",
    "retmax": 10,
    "manual_cv": 25,
    "cv_confirmed": true,
    "protocol_condition": "fasted",
    "output_mode": "draft"
  }'
```

---

## 4. Формат выдачи результата и интерпретация рекомендаций

### 4.1. FullReport (выход run_pipeline)

Основные поля:

| Поле | Тип | Описание |
|------|-----|----------|
| `inn`, `inn_ru` | string | Идентификаторы препарата |
| `sources` | list[SourceCandidate] | Использованные источники |
| `pk_values`, `ci_values` | list | PK и 90% ДИ |
| `cv_info` | CVInfo | Источник CV, значение, диапазон, подтверждение |
| `data_quality` | DataQuality | DQI score, level, allow_n_det, reasons |
| `design` | DesignDecision | Рекомендованный дизайн, reasoning |
| `sample_size_det` | SampleSizeDet | N_total, N_rand, N_screen (при allow_n_det) |
| `sample_size_risk` | SampleSizeRisk | n_targets, p_success_at_n (при range CV) |
| `reg_check` | list[RegCheckItem] | OK / RISK / CLARIFY |
| `open_questions` | list[OpenQuestion] | Вопросы для уточнения |

### 4.2. Интерпретация DQI

| Уровень | DQI | allow_n_det | Рекомендация |
|---------|-----|-------------|--------------|
| green | ≥ 80 | да (при CV eligible) | N_det можно экспортировать |
| yellow | 55–79 | зависит от правил | Проверить причины, рассмотреть доп. источники |
| red | < 55 | нет | Исправить данные, добавить источники |

### 4.3. Регуляторные проверки (RegCheckItem)

- **OK** — требование выполнено
- **RISK** — потенциальный риск, требует внимания
- **CLARIFY** — необходимо уточнение

### 4.4. Экспорт

- **FullReport.json** — полный отчёт для аудита и повторной загрузки
- **synopsis.md** — текст синопсиса в Markdown
- **synopsis.docx** — документ с разделами по REQUIRED_HEADINGS (см. `synopsis_requirements.py`)

---

## 5. Ограничения, допущения и область применимости

### 5.1. Область применимости

- Планирование in vivo BE на здоровых добровольцах
- Референс: FDA, EMA, EAEU (Decision 85)
- Поддерживаемые дизайны: 2×2 crossover, 3-way/4-way replicate, RSABE, parallel

### 5.2. Ограничения

- **Поиск:** только NCBI E-utilities (PubMed/PMC); скрапинг не используется
- **LLM:** опционально (Yandex); без API ключа — только regex-парсинг
- **PowerTOST:** расчёт N_det выполняется через PowerTOST (в Docker-образе уже включён)
- **Официальные URL:** DailyMed, FDA, EMA — reference-only (PK не извлекается)
- **Многоязычность:** перевод INN ru→en через LLM; остальные языки — ограниченно

### 5.3. Допущения

- 90% ДИ для CVfromCI — в лог-шкале, 2×2 crossover
- θ₂ = 1.25 для TOST
- Вариабельность CV — triangular или lognormal при Monte Carlo
- Один препарат (INN) на запрос

### 5.4. output_mode: draft vs final

- **draft:** отчёт всегда возвращается; блокеры не дают 422
- **final:** при блокерах (N не вычислен, CV отсутствует, нет primary endpoints) — HTTP 422

---

## 6. Методология валидации и метрики качества

### 6.1. Data Quality Index (DQI)

$$\text{DQI} = \text{round}(0.25 C + 0.25 T + 0.20 P + 0.20 K + 0.10 S)$$

| Компонент | Вес | Описание |
|-----------|-----|----------|
| **C** (Completeness) | 25% | AUC, Cmax, t½, CV, CI, n, условия |
| **T** (Traceability) | 25% | evidence (PMID/PMCID/URL) для значений |
| **P** (Plausibility) | 20% | единицы, CI, CV, t½ без штрафов |
| **K** (Consistency) | 20% | отсутствие конфликтов между источниками |
| **S** (Source quality) | 10% | релевантность (human BE/PK vs animal/review) |

Пороги: green ≥ 80, yellow ≥ 55, red < 55.

### 6.2. Жёсткие красные флаги (DQI = 0)

Наличие любого кода блокирует allow_n_det и опускает DQI:

- `missing_primary_endpoints` — AUC и Cmax отсутствуют
- `traceability_zero` — нет трассируемости
- `unit_suspect_critical` — подозрительные единицы
- `unresolved_source_conflict` — конфликт без выбора
- `cv_from_ci_invalid` — CVfromCI некорректен
- `math_contradiction` — CV<0, CI некорректен
- `fallback_pk` — PK/CV из fallback
- `protocol_condition_conflicts_with_evidence`
- `selected_sources_mismatch`

### 6.3. Валидация PK (validator)

- Единицы измерения (допустимые: ng/mL, ng·h/mL и т.п.)
- Порядок CI (ci_low < ci_high)
- CI в допустимых границах (0.5–2.0 для ratio)
- CV ≤ 100%, t½ в разумных пределах

---

## 7. Сценарии тестирования

### 7.1. Юнит-тесты (pytest)

```bash
# Базовые тесты DQI и дизайна
pytest backend/tests/test_data_quality_weighting.py backend/tests/test_data_quality_hard_red.py -v

# Валидация PK
pytest backend/tests/test_pk_math_validation.py -v

# Движок дизайна
pytest backend/tests/test_design_engine.py backend/tests/test_design_testcases.py -v

# PMC и LLM fallback
pytest backend/tests/test_pmc_fetcher.py backend/tests/test_pk_extractor_pmc_fallback.py -v

# Интеграция пайплайна
pytest backend/tests/test_api_run_pipeline_final.py -v

# DOCX и синопсис
pytest tests/test_docx_has_required_headings.py tests/test_docx_null_safety.py -v

# Политики (fallback, protocol_condition)
pytest backend/tests/test_use_fallback_false.py backend/tests/test_protocol_condition.py -v
```

### 7.2. QA Smoke (_qa_smoke.py)

5 чекпоинтов (запуск из корня проекта):

```bash
python _qa_smoke.py
```

| № | Сценарий | Проверки |
|---|----------|----------|
| QA-1 | Happy Path (AUC, Cmax, CV=20%, t½=10h) | FullReport, DOCX, DQI green/yellow, дизайн |
| QA-2 | DQI Hard-Block (пустые данные) | level=red, allow_n_det=False, текст в DOCX |
| QA-3 | EAEU HVD (CV=40%, 55%) | replicate/4-way_replicate, RSABE rule |
| QA-4 | CRO Synopsis | REQUIRED_HEADINGS, null-safety DOCX |

### 7.3. Пакетный прогон валидационных кейсов

Из корня репозитория:

```bash
python eval_metrics.py --cases docs/organizers_validation.json --out-dir output
```

Результаты по каждому кейсу: `output/cases/<case_id>/` — `request.json`, `report.json`, `metrics.json`, `sources.json`, `warnings.json`, при возможности `synopsis.docx`. Сводка: `output/cases_summary.json`.

*Требуется запущенный бекенд (локально или Docker).*

### 7.4. Проверка PMC и LLM

- Реальный PMC (парсинг без LLM):  
  `python -m backend.scripts.test_pmc_and_llm PMC6386472`
- С LLM (нужны YANDEX_API_KEY, YANDEX_FOLDER_ID):  
  `python -m backend.scripts.test_pmc_and_llm PMC6386472 --llm`

### 7.5. Рекомендуемая последовательность проверки

1. `python _qa_smoke.py` — smoke-тест
2. `pytest backend/tests/ -v` — полный набор тестов
3. Ручной запуск Run pipeline через UI с типовым INN (напр. metformin)

---

## Приложения

### A. Переменные окружения (.env)

**Основные (нужны для полной работы):**

| Переменная | Описание |
|-----------|----------|
| YANDEX_API_KEY | Ключ Yandex Cloud — для LLM (перевод INN, извлечение PK) |
| YANDEX_FOLDER_ID | Folder ID Yandex Cloud |

**Опционально:** `NCBI_EMAIL`, `NCBI_API_KEY`, `NCBI_TOOL` (PubMed); `BACKEND_URL` (для фронтенда, по умолчанию `http://localhost:8000`).

### B. Ссылки на правила (YAML)

- `backend/rules/validation_rules.yaml` — валидация PK
- `backend/rules/design_rules.yaml` — выбор дизайна
- `backend/rules/reg_rules.yaml` — регуляторные проверки
- `backend/rules/variability_rules.yaml` — модель вариабельности
- `docs/DATA_QUALITY_CRITERIA.yaml` — критерии DQI

### C. Структура репозитория

| Путь | Назначение |
|------|------------|
| `backend/` | FastAPI: pubmed_client, pk_extractor, pmc_fetcher, pipeline, cv_gate, data_quality, sample_size, reg_checker, docx_builder |
| `backend/rules/` | design_rules.yaml, validation_rules.yaml, reg_rules.yaml, variability_rules.yaml |
| `backend/schemas/` | Модели и API |
| `docs/` | DATA_QUALITY_CRITERIA.yaml, тест-кейсы |
| `frontend/` | Streamlit UI |
| `r/` | R-скрипт PowerTOST для расчёта N |
