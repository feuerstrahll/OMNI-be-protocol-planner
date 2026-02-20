# DATA_QUALITY_CRITERIA.md
Версия: 1.0 (MVP)  
Контекст: автоматизированное извлечение PK/CV/CI → валидация → расчёт N_det/N_risk → рег-чек → синопсис

---

## 0) Зачем нужен Data Quality Index (DQI)
DQI — **внутренний риск-индикатор качества входных данных** (0–100), который:
- повышает доверие к цифрам (прозрачная трассируемость + sanity-checks),
- управляет **гейтингом расчётов**: когда допустим N_det, а когда обязателен N_risk,
- автоматически формирует **Open Questions** и усиливает регуляторные предупреждения.

Важно: DQI не заменяет регуляторную оценку и клиническое обоснование; это “quality/risk lens” для планирования.

---

## 1) Принципы (data integrity → data quality)
Мы проектируем DQI так, чтобы он был совместим с ожиданиями по data integrity:
- **ALCOA/ALCOA+**: attributable, legible, contemporaneous, original, accurate, complete (+ consistent, enduring, available),
- изменения должны быть **трассируемы**, не скрывать оригинал, при необходимости объясняться (audit trail).

Практический вывод для MVP:
- каждая цифра в отчёте должна иметь **source + evidence**;
- любые “derived” значения (например CVfromCI) требуют явных допущений и подтверждения человеком;
- при сомнениях/пробелах — **warnings + Open Questions**, без hard fail.

---

## 2) Что именно оценивает DQI (5 компонент)
Каждая компонента даёт subscore 0–100:

1) **Completeness** (полнота) — 25%  
2) **Traceability** (трассируемость) — 25%  
3) **Plausibility/Validity** (валидность/правдоподобие) — 20%  
4) **Consistency** (согласованность) — 20%  
5) **Source Quality / Relevance** (качество/релевантность источников) — 10%

**Итоговая формула:**
DQI = round(0.25*C + 0.25*T + 0.20*P + 0.20*K + 0.10*S)

---

## 3) Traffic-light уровни и гейтинг (как влияет на пайплайн)
### 3.1 Уровни
- **Green**: 80–100  
- **Yellow**: 55–79  
- **Red**: 0–54  

### 3.2 Гейтинг для расчётов
**N_det разрешён только если:**
- DQI >= 55 (не Red),
- CVintra не является variability_range (т.е. CV задан/выведен/введён),
- есть чекбокс **cv.cvintra.confirmed_by_human = true**,
- и если CV derived_from_ci — есть **assumptions_confirmed_by_human = true**.

**Если DQI Red →**
- N_det не показывать как “рекомендуемый”, только как “что было бы при допущениях” (опционально),
- обязательный **N_risk**,
- обязательный блок Open Questions.

---

## 4) Hard “Red flags” (override -> Red)
Если выполнено любое из условий ниже — DQI принудительно становится Red (с указанием причины):

RF-1. **Нет traceability** для любого из критичных чисел:
- AUC, Cmax, t1/2 (или эквивалент), CVintra (или CI+n для CVfromCI), CI_low/CI_high/n (если используется derived_from_ci)
- отсутствует source ИЛИ evidence.

RF-2. **Не распознаны/сомнительны единицы** для AUC или Cmax (и не удалось привести к стандарту проекта).

RF-3. **Конфликт источников** по ключевым параметрам без “primary selection”:
- конфликт CV/AUC/Cmax/CI/n/условий/дизайна, и не выбран основной источник + причина.

RF-4. **CVfromCI** выбран, но не хватает обязательных полей:
- нет CI_low/CI_high или n_total, или CI_low >= CI_high,
- или не подтверждены допущения человеком (assumptions_confirmed_by_human=false).

RF-5. Извлечённые значения противоречат базовой математике:
- отрицательные AUC/Cmax/t1/2,
- CV < 0,
- CI_low <= 0 или CI_high <= 0 (для ratio-метрик).

---

## 5) Детализация подсчёта subscore’ов

### 5.1 Completeness (C, 0–100) — что должно быть, чтобы “спланировать BE”
Старт C=0, затем начисление:

**A) PK core (до 60 баллов)**
- AUC (существует численное значение) → +25  
- Cmax → +25  
- t1/2 или явный флаг long half-life (t1/2_hours) → +10  

**B) Контекст исследования (до 25 баллов)**
- популяция (healthy/patient) → +10  
- условия fed/fasted/both → +10  
- дизайн (2×2/replicate/parallel) или минимум признаки crossover/parallel → +5  

**C) Вариабельность (до 15 баллов)**
- CVintra reported/manual → +15  
- CV derived_from_ci (CI+n присутствуют) → +12  
- CV_range + confidence (variability layer) → +8  
- нет CV и нет CV_range → +0

**Штрафы (типовые пробелы)**
- fed/fasted = unknown → -10  
- дизайн неизвестен → -10  
- CV отсутствует полностью → -15 (даже если PK есть)

Ограничения: C не ниже 0 и не выше 100.

---

### 5.2 Traceability (T, 0–100) — ALCOA-совместимый минимум
Оценивается доля критичных полей, у которых есть:
- `source` (PMID/URL/идентификатор) и
- `evidence` (таблица/фрагмент текста/контекст + привязка).

**Критичные “численные” поля (Numerical Critical Set)**
- AUC_value
- Cmax_value
- t1/2_hours (или эквивалент)
- CVintra_value (если выбран CV source = reported/manual)
- если CV source = derived_from_ci:
  - CI90_low, CI90_high, n_total (и лучше — параметр AUC/Cmax)
- если выбран дизайн/популяция/условия — для них тоже желательны источники, но они “secondary”.

**Расчёт:**
T = round(100 * (#critical_fields_with_source_and_evidence / #critical_fields_expected))

Где `#critical_fields_expected` зависит от ветки:
- reported/manual CV: ожидаем CVintra_value
- derived_from_ci: ожидаем CI_low/CI_high/n_total вместо CVintra_value как первичную основу + пометка derived
- variability_range: ожидаем CV_range + confidence + rationale (как evidence)

---

### 5.3 Plausibility/Validity (P, 0–100) — “санити-чеки” без hard fail
Старт P=100, далее штрафы по warnings.

**Группа A: единицы/формат (жёстче)**
- unit_suspect for AUC/Cmax → -25  
- unit_missing → -20  
- suspicious_conversion → -20  

**Группа B: математические проверки**
- CI_low >= CI_high → -25  
- CI values out of plausible ratio bounds (например <0.5 или >2.0) → -10  
- CV слишком высок для typical BE контекста (например >1.0 = 100%) → -10  
- t1/2_hours > 200 или < 0.1 (как “подозрение”, не запрет) → -10  

**Группа C: пропуски**
- pk_missing_auc_or_cmax → -20  
- missing_design_metadata → -10  
- missing_fed_fasted → -10  

Нижняя граница: P >= 0.

---

### 5.4 Consistency (K, 0–100) — согласованность между источниками
Если источников по каждому числу ровно один → K=80 (нет конфликта, но и нет кросс-проверки).

Если источников несколько:
- старт K=100
- сравниваем диапазон значений по ключевым полям:

**AUC/Cmax**
- если (max/min - 1) > 0.20 → -20  
- если > 0.35 → -35  

**CV**
- если относительное расхождение > 30% → -20  
- если > 50% → -35  

**CI/n/дизайн/условия**
- конфликт CI_low/CI_high/n_total → -25  
- конфликт fed/fasted (например один источник fed, другой fasted без разделения) → -25  
- конфликт дизайна (parallel vs crossover) → -25  

Нижняя граница: K >= 0.

---

### 5.5 Source Quality / Relevance (S, 0–100) — “насколько этот источник вообще про планируемое BE”
S — максимум из оценок источников (или взвешенная, если будет время), в MVP можно брать “best primary source score”.

Рекомендуемая шкала:
- 95–100: Human, BE/PK_BE или чёткий PK у healthy, условия совпадают (fasted/fed), понятный дизайн  
- 80–90: Human, PK релевантен, но условия/популяция не полностью совпадают  
- 60–75: Review/secondary analysis, или human но методы/условия неясны  
- 30–50: Animal/in vitro (можно использовать как “биологический контекст”, но не как базу для N_det)

---

## 6) Что должен выдавать backend вместе с DQI (для UI/docx)
### 6.1 Структура summary (минимум)
- `data_quality.score` (0–100)
- `data_quality.level` (green/yellow/red)
- `data_quality.subscores`: { completeness, traceability, plausibility, consistency, source_quality }
- `data_quality.overrides`: список сработавших red flags (если есть)
- `warnings[]`: коды предупреждений
- `open_questions[]`: сформированный список (на основе warnings + reg-check + DQI)

### 6.2 “Explainability” (очень желательно)
- список причин, почему потеряны баллы (топ-5),
- список полей без evidence,
- список конфликтующих источников.

---

## 7) Пример маппинга warnings → DQI
Рекомендуемые machine codes:
- missing_evidence, missing_source
- unit_suspect, unit_missing, suspicious_conversion
- source_conflict, conflicting_values
- carryover_risk
- missing_design_metadata, missing_fed_fasted
- ci_missing, ci_bad_order, n_missing

---

## 8) Минимальные пороги для “жёлтого” сценария (MVP-практика)
Если DQI Yellow:
- показывать N_det **только после подтверждения CV человеком**
- параллельно показывать N_risk (как более устойчивую оценку)
- в синопсисе: “данные умеренного качества, требуются уточнения”.

Если DQI Green:
- можно рекомендовать N_det как основной (при confirmed CV)
- N_risk — как дополнительная секция чувствительности.

Если DQI Red:
- N_det не рекомендовать
- обязательные Open Questions + N_risk + пометка “высокая неопределённость”.

---