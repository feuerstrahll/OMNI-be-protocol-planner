# BE Planning MVP

Минимально рабочий MVP для планирования биоэквивалентности (BE) с 3 уровнями:
1) извлечение/валидация PK из PubMed/PMC через NCBI E-utilities,
2) rule-based оценка диапазона CVintra,
3) вероятностная оценка успеха (risk model) с учетом неопределенности CV.

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
- `frontend/` Streamlit UI
- `r/` R-скрипт PowerTOST
- `templates/` шаблон синопсиса
- `docs/` описание архитектуры и правил

## Ограничения MVP

- Извлечение PK происходит в основном из абстрактов (регулярные выражения).
- Правила вариабельности и дизайна базовые, требуют донастройки.
- Risk model использует приближенную формулу TOST.
- CVintra всегда требует ручного подтверждения.

## Выходные файлы

Синопсис сохраняется в `output/`.
