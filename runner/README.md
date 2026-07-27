# GitLab Runner: как это устроено и как поднять

## Как оно работает

```
       ┌──────────┐   1. "есть работа?"   ┌─────────┐
       │  Runner  │ ────────────────────> │ GitLab  │
       │ (агент)  │ <──────────────────── │         │
       └────┬─────┘   2. вот джоба        └─────────┘
            │
            │ 3. поднимает контейнер под джобу
            v
       ┌──────────────────────┐
       │ python:3.12-alpine   │  <- образ из .gitlab-ci.yml
       │  выполняет script:   │
       └──────────────────────┘
            │ 4. логи и статус обратно в GitLab
            v
```

Четыре вещи, которые стоит усвоить сразу:

**1. Связь всегда исходящая.** GitLab не подключается к раннеру. Раннер сам
опрашивает `POST /api/v4/jobs/request` в цикле. Поэтому раннер работает из-за
NAT, из домашней сети, откуда угодно — порты открывать не надо.

**2. Раннер сам ничего не выполняет.** Он делегирует *executor'у*. Основные:

| Executor | Что делает | Когда брать |
|---|---|---|
| `docker` | под каждую джобу — свежий контейнер | почти всегда |
| `shell` | выполняет прямо на хосте | когда нужен доступ к железу хоста |
| `kubernetes` | джоба = pod | если уже есть кластер |

`docker` хорош тем, что джобы изолированы и не тащат мусор между запусками:
образ берётся из `image:` в `.gitlab-ci.yml`.

**3. Раннеру нужен доступ к докер-демону.** Отсюда монтирование
`/var/run/docker.sock`. Это фактически права root на хосте — на публичном
CI так делать нельзя, для личного проекта на своей машине приемлемо.

**4. Теги решают, кто возьмёт джобу.** У раннера есть список тегов, у джобы —
свой. Джоба без тегов достанется раннеру только если у того включено
**«Run untagged jobs»**. Это самая частая причина, почему пайплайн вечно висит
в `pending`: раннер есть, а джобу он не берёт.

В нашем `.gitlab-ci.yml` теги не проставлены, поэтому галка обязательна.

## Поднятие

### Шаг 1. Создать раннер в GitLab

Проект → **Settings → CI/CD → Runners** → **Create project runner**.

| Поле | Значение |
|---|---|
| Tags | оставить пустым |
| **Run untagged jobs** | **включить** — иначе джобы не подхватятся |
| Description | например `home-pc-docker` |

GitLab покажет токен вида `glrt-...`. Он показывается **один раз**.

### Шаг 2. Зарегистрировать

Из папки `runner/`:

```bash
docker compose run --rm gitlab-runner register \
  --non-interactive \
  --url https://gitlab.3dkv.ru \
  --token "<glrt-токен>" \
  --executor docker \
  --docker-image python:3.12-alpine \
  --docker-pull-policy if-not-present
```

Это создаст `runner/config/config.toml` — в нём личность раннера и токен.
Файл в `.gitignore`, наружу не уедет.

### Шаг 3. Запустить

```bash
docker compose up -d
docker compose logs -f
```

В логах должно появиться `Configuration loaded` и далее периодические
`Checking for jobs... received` / `nothing`.

В GitLab на странице Runners раннер станет **online** (зелёный).

## Проверка

```bash
git switch main
git merge --ff-only development
git push origin main
```

CI/CD → Pipelines: джобы `validate:manifest`, `validate:python`, `mirror:github`.

## Чего ожидать

**Раннер живёт на твоей машине.** Выключен компьютер — пайплайны стоят в
очереди и ждут. Для личного проекта нормально, просто помни об этом: если
хочется, чтобы работало круглосуточно, раннер надо ставить туда же, где живёт
сам GitLab, или на любой другой всегда включённый хост.

**Первый запуск джобы небыстрый** — тянется образ `python:3.12-alpine`.
Дальше он кэшируется на хосте (`--docker-pull-policy if-not-present`).

## Полезные команды

```bash
docker compose logs -f                          # что делает раннер
docker compose exec gitlab-runner gitlab-runner list    # зарегистрированные
docker compose exec gitlab-runner gitlab-runner verify  # жив ли на стороне GitLab
docker compose restart                          # после правки config.toml
docker compose down                             # остановить
```

Снять раннер с регистрации:

```bash
docker compose exec gitlab-runner gitlab-runner unregister --all-runners
```
