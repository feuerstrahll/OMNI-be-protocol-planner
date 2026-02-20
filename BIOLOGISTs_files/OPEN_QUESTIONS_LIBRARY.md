# OPEN_QUESTIONS_LIBRARY.md
Версия: 1.0 (MVP)  
Назначение: библиотека шаблонных вопросов пользователю, которые формируются из `warnings[]`, `reg-check` и `Data Quality Index (DQI)`.

---

## 0) Как использовать (для backend/UI/docx)
### 0.1 Формат элемента библиотеки (рекомендуемый)
- **ID**: `OQ-XXX`
- **Tags**: для фильтрации и группировки (source, pk, cv, design, fed, nti, hvd, integrity)
- **Severity**: MUST / SHOULD / CONSIDER
- **Trigger**: псевдо-условие (по полям FullReport JSON и/или warnings)
- **Question**: текст вопроса пользователю
- **What to attach**: что попросить приложить (PMID/таблица/фрагмент/CSR/label)
- **Used in**: UI / Docx / Audit

> В MVP достаточно отдавать в результатах `open_questions[]` как массив строк (вопросов), но хранить библиотеку лучше в структурированном виде.

### 0.2 Общие правила генерации
- Если **DQI = red** → включать минимум 3 вопроса из блоков: *Sources*, *PK/CV completeness*, *Conflicts*.
- Если `cv_source=derived_from_ci` → **всегда** включать блок допущений (OQ-110).
- Если **CVw ≥ 30%** → включать HVD блок (OQ-130).
- Если `drug.narrow_therapeutic_index=true` → включать NTI блок (OQ-140).
- Если `t12_hours ≥ 24` или `carryover_risk` → включать long half-life блок (OQ-150).
- Если `study.fed_fasted=unknown` → включать fed/fasted блок (OQ-160).

---

## 1) Источники, трассируемость, конфликты (Source & Traceability)

### OQ-101 — Primary source selection
- **Tags**: source, traceability
- **Severity**: MUST  
- **Trigger**: `warnings contains missing_source` OR `warnings contains missing_evidence` OR `DQI<55`
- **Question**: Какие источники считаем **первичными и релевантными** для планирования (human BE/PK, сопоставимые условия fed/fasted)? Укажите PMID(ы)/URL и почему они выбраны primary.
- **What to attach**: PMID/URL + номер таблицы/фигуры или фрагмент текста
- **Used in**: UI, Docx, Audit

### OQ-102 — Evidence for each key number
- **Tags**: evidence, integrity
- **Severity**: MUST  
- **Trigger**: `warnings contains missing_evidence`
- **Question**: Для каждого ключевого числа (AUC, Cmax, t1/2, CV/CI/n) предоставьте **source + evidence** (таблица/фрагмент/контекст), чтобы обеспечить трассируемость.
- **What to attach**: PMID + “table/figure/text excerpt”
- **Used in**: UI, Docx

### OQ-103 — Units & conversions check
- **Tags**: units, plausibility
- **Severity**: MUST  
- **Trigger**: `warnings contains unit_suspect` OR `warnings contains suspicious_conversion`
- **Question**: Подтвердите **единицы измерения** для AUC и Cmax и корректность конверсии в стандарт проекта (например ng/mL, ng·h/mL и т.п.).
- **What to attach**: скрин/фрагмент с единицами + пояснение конверсии
- **Used in**: UI, Audit

### OQ-104 — Resolve conflicts between sources
- **Tags**: conflicts, consistency
- **Severity**: MUST  
- **Trigger**: `warnings contains source_conflict` OR `warnings contains conflicting_values`
- **Question**: В источниках есть **расхождения** по PK/CV/CI/n/условиям/дизайну. Какое значение принимаем как итоговое и почему (популяция/условия/дизайн/качество)?
- **What to attach**: список источников + выбранный primary + rationale
- **Used in**: UI, Docx, Audit

### OQ-105 — Human data requirement
- **Tags**: relevance, source_quality
- **Severity**: SHOULD  
- **Trigger**: `source_quality<60` OR `warnings contains non_human_only`
- **Question**: Есть ли **human** BE/PK данные по INN (или близкой форме/дозе/условиям)? Если нет — подтвердите, что дальнейшее планирование будет основано на risk-based допущениях.
- **What to attach**: PMID/CSR/label или подтверждение “human данных нет”
- **Used in**: UI, Docx

---

## 2) PK параметры и контекст исследования (PK Completeness & Context)

### OQ-201 — Endpoint definition (AUC type)
- **Tags**: pk, endpoints
- **Severity**: SHOULD  
- **Trigger**: `pk.auc.exists=true AND pk.auc.parameter_name missing`
- **Question**: Уточните, какой AUC используется как основной endpoint (AUC(0–t), AUC(0–72h), AUC(0–inf), AUCtau) и почему это соответствует планируемому дизайну.
- **What to attach**: источник/фрагмент, где указано определение AUC
- **Used in**: Docx

### OQ-202 — Population match
- **Tags**: population, relevance
- **Severity**: SHOULD  
- **Trigger**: `warnings contains population_mismatch` OR `study.population=unknown`
- **Question**: Подтвердите, что популяция источника (healthy/patient) сопоставима с планируемой. Если нет — какие поправки/риски принимаем?
- **What to attach**: описание популяции в источнике
- **Used in**: UI, Docx

### OQ-203 — Single vs multiple-dose justification
- **Tags**: design, conduct
- **Severity**: CONSIDER  
- **Trigger**: `warnings contains multiple_dose_only` OR `study.dosing_regimen=multiple AND justification missing`
- **Question**: Если исследование планируется multiple-dose: обоснуйте, почему single-dose не подходит (например, ограничения биоаналитики/чувствительности), и как это влияет на оценку BE.
- **What to attach**: rationale + ссылка на метод/LOD/LOQ (если причина — биоаналитика)
- **Used in**: Docx

---

## 3) CV / Variability (reported, manual, derived, range)

### OQ-301 — CV missing (manual input)
- **Tags**: cv, variability
- **Severity**: MUST  
- **Trigger**: `cv.cvintra.source missing OR cv.cvintra.source=variability_range AND confidence low`
- **Question**: CVintra не подтверждён надёжным источником. Введите **ожидаемый CVintra** для AUC и Cmax (ручной ввод или пресет 20/30/40/50%) и отметьте основание (внутренние данные/экспертная оценка).
- **What to attach**: CSR/внутренние данные или краткое обоснование
- **Used in**: UI, Audit

### OQ-302 — Parameter specificity (AUC vs Cmax)
- **Tags**: cv, endpoints
- **Severity**: SHOULD  
- **Trigger**: `cv.cvintra.value exists AND cv.cvintra.parameter missing`
- **Question**: Уточните, к какому endpoint относится CV (AUC, Cmax или оба). Если CV различаются по endpoint — укажите отдельно.
- **What to attach**: фрагмент/таблица, где описан CV
- **Used in**: UI, Docx

---

## 4) CVfromCI (derived_from_ci) — обязательные допущения и проверки

### OQ-110 — CVfromCI assumptions checklist
- **Tags**: cvfromci, assumptions, integrity
- **Severity**: MUST  
- **Trigger**: `cv.cvintra.source=derived_from_ci`
- **Question**: Подтвердите корректность допущений для CVfromCI:  
  1) CI — **90% CI** для GMR (T/R) по AUC и/или Cmax;  
  2) анализ выполнен на **log-scale** (лог-трансформация PK параметров);  
  3) корректный **n** (evaluable subjects) и дизайн, к которому относится CI;  
  4) условия (fasted/fed) и популяция соответствуют планируемым.
- **What to attach**: PMID + таблица/фрагмент с CI и n + описание дизайна/анализа
- **Used in**: UI, Docx, Audit  
- **Reg note**: BE обычно оценивают по 90% CI на log-scale; derived подход допустим только при явной валидации допущений (см. ICH M13A).  

---

## 5) HVD (highly variable) и дизайн replicate

### OQ-130 — HVD actions (CVw ≥ 30%)
- **Tags**: hvd, replicate, scaling
- **Severity**: MUST  
- **Trigger**: `cv.cvintra.value >= 0.30`
- **Question**: CVw ≥ 30% (HVD). Подтвердите стратегию:  
  - используем ли **replicate cross-over**;  
  - рассматриваем ли **widened acceptance range для Cmax** (только при клиническом обосновании);  
  - фиксируем ли это prospectively в протоколе;  
  - подтверждаем ли, что widening не применяется к AUC.
- **What to attach**: rationale (клиническое обоснование) + план дизайна
- **Used in**: UI, Docx  
- **Reg note**: EMA требует replicate design и CVw(reference, Cmax) > 30% для widening; widening возможно для Cmax, не для AUC.

---

## 6) NTI (narrow therapeutic index)

### OQ-140 — NTI confirmation and limits
- **Tags**: nti, acceptance_limits
- **Severity**: MUST  
- **Trigger**: `drug.narrow_therapeutic_index=true`
- **Question**: Подтвердите статус **NTI** и какие endpoints критичны. Требуется ли ужесточение acceptance interval до **90.00–111.11%** для AUC и (при клинической важности) Cmax?
- **What to attach**: клиническое обоснование NTI/label/внутренний консенсус
- **Used in**: UI, Docx  
- **Reg note**: EMA описывает tighter limits для NTI и подход case-by-case.

---

## 7) Long half-life / carry-over / washout / truncated AUC

### OQ-150 — Long half-life plan
- **Tags**: half_life, carryover, washout
- **Severity**: MUST  
- **Trigger**: `pk.t12_hours >= 24 OR warnings contains carryover_risk`
- **Question**: t1/2 ≥ 24ч / риск carry-over. Уточните:  
  - какой washout планируется (в кратности t1/2) и почему достаточен;  
  - как контролируем pre-dose концентрации и carry-over;  
  - нужен ли **truncated AUC(0–72h)** как основной endpoint;  
  - если crossover непрактичен — рассматриваем ли parallel design.
- **What to attach**: расчёт washout + план контроля pre-dose
- **Used in**: UI, Docx  
- **Reg note**: ICH M13A рекомендует truncation AUC(0–72h) для long half-life (≈24h и более).

---

## 8) Fed/Fasted и food effect

### OQ-160 — Fed/Fasted requirement
- **Tags**: fed, conduct, label
- **Severity**: MUST  
- **Trigger**: `study.fed_fasted=unknown OR warnings contains fed_fasted_unclear`
- **Question**: Требуется ли fed BE и/или fasted BE? Подтвердите режим приёма по инструкции/label/SmPC (fasted, fed или оба).
- **What to attach**: ссылка/цитата из label/SmPC или продукт-специфичного гайда
- **Used in**: UI, Docx

### OQ-161 — Fed meal standardization (если fed)
- **Tags**: fed, conduct
- **Severity**: SHOULD  
- **Trigger**: `study.fed_fasted in [fed, both] AND fed_meal_spec missing`
- **Question**: Если проводится fed BE/food-effect: подтвердите параметры стандартизированного приёма пищи (high-calorie, high-fat meal) и тайминги относительно дозирования.
- **What to attach**: meal spec (калории/жиры/время) + SOP/протокол
- **Used in**: Docx  
- **Reg note**: FDA рекомендует high-calorie/high-fat meal для food-effect/fed BE.

---

## 9) Статистика и анализ (log-scale, CI, outliers)

### OQ-170 — Log-transform and CI reporting
- **Tags**: stats, analysis
- **Severity**: SHOULD  
- **Trigger**: `warnings contains analysis_scale_unclear`
- **Question**: Подтвердите, что анализ PK endpoints выполняется на log-scale и результаты представляются как GMR (T/R) с 90% CI.
- **What to attach**: SAP фрагмент/описание анализа из источника
- **Used in**: Docx

### OQ-171 — Outlier handling / exclusions
- **Tags**: stats, integrity
- **Severity**: CONSIDER  
- **Trigger**: `warnings contains outliers_possible OR dropout_high_expected`
- **Question**: Как будет выполняться обработка выбросов/исключений и missing data? Будут ли критерии исключения и sensitivity analyses pre-specified?
- **What to attach**: SAP/SOP фрагмент
- **Used in**: Docx

---

## 10) Product-specific guidance (EMA PSG) — если есть
### OQ-180 — Check EMA product-specific guidance
- **Tags**: psg, regulatory
- **Severity**: SHOULD  
- **Trigger**: `warnings contains psg_not_checked`
- **Question**: Проверяли ли EMA product-specific bioequivalence guidance для данного INN? Если да — какие особые требования (endpoints/режимы/dissolution/BCS)?
- **What to attach**: ссылка/выжимка требований
- **Used in**: UI, Docx

---

## 11) Маппинг warning codes → рекомендуемые OQ
- missing_source / missing_evidence → OQ-101, OQ-102  
- unit_suspect / suspicious_conversion → OQ-103  
- source_conflict / conflicting_values → OQ-104  
- non_human_only → OQ-105  
- fed_fasted_unclear → OQ-160 (и OQ-161 если fed/both)  
- carryover_risk → OQ-150  
- analysis_scale_unclear → OQ-170  
- psg_not_checked → OQ-180  
- (cv_source=derived_from_ci) → OQ-110  
- (cv>=0.30) → OQ-130  
- (nti=true) → OQ-140  

---

## 12) Reference pointers (для команды)
- ICH M13A (в т.ч. truncated AUC(0–72h) для long half-life, общий подход к BE endpoints): https://www.ema.europa.eu/en/documents/scientific-guideline/ich-m13a-guideline-bioequivalence-immediaterelease-solid-oral-dosage-forms-step-5_en.pdf
- EMA BE guideline Rev.1 (NTI, HVD/widening/replicate, общие ожидания): https://www.ema.europa.eu/en/documents/scientific-guideline/guideline-investigation-bioequivalence-rev1_en.pdf
- FDA Food-Effect BA & Fed BE studies (high-fat/high-calorie meal): https://www.fda.gov/files/drugs/published/Food-Effect-Bioavailability-and-Fed-Bioequivalence-Studies.pdf
- EMA product-specific BE guidance compilation (проверять при наличии): https://www.ema.europa.eu/en/documents/scientific-guideline/compilation-individual-product-specific-guidance-demonstration-bioequivalence-revision-3_en.pdf