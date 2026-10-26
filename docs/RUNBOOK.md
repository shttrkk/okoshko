# О!кно: runbook

## Запуск для жюри

```sh
cp .env.example .env
docker compose up --build
```

Открыть http://localhost:8080. `/health` → `{"status":"ok"}`. Сиды и миграции автоматические, интернет нужен только для публичных образов и зависимостей при сборке. LLM ключ не нужен.

## Разработка

`make up`, `make down`, `make seed`, `make pipeline`, `make eval`, `make smoke`, `make demo-video`.
Для локального Python: `python3.12 -m venv .venv`, `.venv/bin/pip install -r requirements-dev.lock`.
Frontend: `cd frontend`, `npm ci`, `npm run dev`. Сервис API остаётся на 8080.
Миграции: `docker compose exec app python -m backend.boot` не использовать для ручного второго сервера; `docker compose exec app python -m backend.seed` для безопасного идемпотентного seed после реализации S3.

## Контрольные точки

- `date '+%Y-%m-%d %H:%M:%S %Z'`; проверить `docs/reviews/REVIEW_*.md`.
- После каждого merge: `make lint`, `make test`, `docker compose up --build -d`, `make smoke`.
- Проверить отсутствие >50 МБ файлов, `.env` в tracked files и реальных токенов в diff/history.
- 27.10 12:00: сценарий Маши без ключа. 28.10 18:00: freeze. 29.10 до 10:00: submission.

## Данные

Исходники скачиваются в gitignored `data/raw/`. Открытые лицензии и фактические выгрузки — `data/seed/manifest.json`, `/sources` и README. Сборка приложения никогда не запускает pipeline.
Контракты seed: `docs/CONTRACTS.md` §3. Реальные фото — только с лицензией конкретного файла. OTA не читаем.

## Диагностика

```sh
docker compose ps
docker compose logs --tail=100 app
docker compose logs --tail=100 db
curl -f http://localhost:8080/health
```

Не удалять volume автоматически: там ответы и паспорта. Для намеренного полного сброса демо `docker compose down -v` (все локальные изменения будут потеряны).

## Видео и сдача

`make demo-video`: Playwright 1920×1080 → webm → ffmpeg mp4. Выход `demo/okno-demo.mp4`, не коммитится. Текст озвучки `demo/SCRIPT.md`. Ссылка общедоступного хостинга в README, проверить инкогнито.
Перед submission: чистый клон, пустой build cache, секундомер `docker compose up --build`; smoke и сценарий вручную. Записать фактические время и размер образа в STATUS и README.
Remote ещё не предоставлен. Тег ставить только после финального полного прогона на последнем коммите main; `git push origin main --tags`, проверить удалённые SHA. Не отмечать сдачу выполненной до успешного push.

