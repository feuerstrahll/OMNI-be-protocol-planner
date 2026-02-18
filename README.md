# BE Planning MVP

Минимально рабочий MVP для планирования биоэквивалентности (BE) с обновленным pipeline:
1) извлечение/валидация PK из PubMed/PMC через NCBI E-utilities (без scraping),
2) Data Quality Index (0-100) + причины,
3) оценка CVintra (rule-based) и derivation из 90% CI через `PowerTOST::CVfromCI()` при наличии CI,
4) правила дизайна и регуляторные проверки из YAML (design/reg/validation/variability rules),
5) разделение размера выборки на `N_det` (детерминированный) и `N_risk` (Monte Carlo),
6) единый отчет `FullReport.json` + синопсис `synopsis.docx`.

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

## Настройки

Скопируйте `.env.example` в `.env` и заполните:
- `NCBI_API_KEY` (опционально)
- `NCBI_EMAIL` (желательно)
- `NCBI_TOOL`

## Пример INN

Попробуйте `metformin` или `atorvastatin`.

## PowerTOST (R) — обязательный шаг для точного N

Для точного расчета N используется `PowerTOST` через `Rscript`.
Если `Rscript` недоступен, сервис использует приближенную формулу и вернет предупреждение.

Обязательные шаги:
1. Установите R (Windows).
2. Укажите путь к `Rscript` через переменную среды `RSCRIPT_PATH` (или добавьте `Rscript` в `PATH`).
   Пример (PowerShell):
   ```powershell
   $env:RSCRIPT_PATH="C:\\Program Files\\R\\R-4.5.2\\bin\\Rscript.exe"
   ```
   Рекомендуется также задать `R_LIBS_USER` (если пакеты установлены в пользовательскую библиотеку):
   ```powershell
   $env:R_LIBS_USER="C:\\Users\\<USER>\\Documents\\R\\win-library\\4.5"
   ```
3. Установите пакеты:
   ```powershell
   & "C:\\Program Files\\R\\R-4.5.2\\bin\\Rscript.exe" -e "install.packages(c('PowerTOST','jsonlite'), repos='https://cran.rstudio.com')"
   ```

## Структура

- `backend/` FastAPI API и сервисы
- `backend/rules/` YAML правила дизайна/регуляторики/валидации/вариабельности
- `frontend/` Streamlit UI
- `r/` R-скрипт PowerTOST
- `templates/` шаблон синопсиса
- `docs/` спецификации извлечения, критериев качества, тест-кейсов и схем

## Ограничения MVP

- Извлечение PK происходит в основном из абстрактов (регулярные выражения).
- Все числовые значения должны иметь источник и evidence (PMID/URL + контекст).
- Нет hard-fail при отсутствии данных: только предупреждения и Open Questions.
- CVintra всегда требует ручного подтверждения, даже если получен из CI.

## Выходные файлы

- `output/FullReport.json` — единая схема отчета по расчетам и проверкам.
- `output/synopsis.docx` — синопсис протокола (docxtpl).
