#!/usr/bin/env bash
#
# Публикует ветку в GitHub в «публичном» виде.
#
#   tools/publish_github.sh [исходная-ветка]
#
# Публичная версия отличается от той, что лежит в GitLab:
#
#   * README.md заменяется на README.public.md — короткий текст для того,
#     кто просто хочет поставить интеграцию, без внутренней кухни;
#   * каталог docs/ вырезается ИЗ ВСЕЙ ИСТОРИИ — это рабочие записи
#     (разбор протокола, настройка раннера, снятие дампов).
#
# Про «из всей истории» — самое важное здесь.
#
# Раньше docs/ просто удалялся перед пушем, то есть отсутствовал ровно в
# одном, самом верхнем коммите. Все предыдущие коммиты уезжали на GitHub
# как есть, и содержимое docs/ открывалось переключением на любой из них.
# Удаление с вершины давало видимость, а не результат.
#
# Поэтому теперь история переписывается целиком через git-filter-repo:
# на GitHub уходит та же цепочка коммитов, но без docs/ в каждом из них.
# Преобразование детерминированное — одна и та же входная история даёт
# одни и те же хеши, так что повторные публикации ничего не «трясут».
#
# GitLab при этом НЕ МЕНЯЕТСЯ ВООБЩЕ: там остаётся и подробный README,
# и docs/ в main. Вся фильтрация происходит в одноразовом клоне.
#
# Один и тот же скрипт вызывается вручную и из CI, чтобы правила
# преобразования не разъезжались.

set -euo pipefail

REMOTE="${PUBLISH_REMOTE:-github}"
BRANCH="${PUBLISH_BRANCH:-main}"
SOURCE="${1:-$BRANCH}"

# Что вырезается из истории без следа.
STRIP_PATHS=(docs)

# --- проверки окружения -----------------------------------------------------

if command -v git-filter-repo >/dev/null 2>&1; then
  FILTER=(git filter-repo)
elif python3 -c "import git_filter_repo" >/dev/null 2>&1; then
  FILTER=(python3 -m git_filter_repo)
else
  echo "ОШИБКА: нужен git-filter-repo, он вырезает docs/ из истории." >&2
  echo "        Установка:  pip install git-filter-repo" >&2
  exit 1
fi

ROOT="$(git rev-parse --show-toplevel)"

# Адрес запоминаем заранее: filter-repo намеренно удаляет все remotes,
# чтобы переписанную историю нельзя было случайно отправить в исходный
# репозиторий. Нам это на руку — GitLab защищён от нас же.
REMOTE_URL="$(git remote get-url "$REMOTE")"

# В CI работаем от текущего checkout'а: он отсоединён от веток, и ветки
# с нужным именем локально может не быть. Вручную — от указанной ветки.
if [ -n "${CI:-}" ]; then
  SOURCE_SHA="$(git rev-parse HEAD)"
  echo "режим CI: публикую текущий checkout ($(git rev-parse --short HEAD))"
else
  SOURCE_SHA="$(git rev-parse "$SOURCE")"
  echo "готовлю публичную версию из '$SOURCE' ($(git rev-parse --short "$SOURCE"))"
fi

# --- одноразовый клон -------------------------------------------------------

WORK="$(mktemp -d)"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

# --no-local отключает жёсткие ссылки на объекты исходного репозитория:
# без этого переписывание истории в клоне могло бы задеть оригинал.
git clone --no-local --no-hardlinks --quiet "$ROOT" "$WORK/repo"
cd "$WORK/repo"

# Ветка нужна именно как ветка: filter-repo переписывает ссылки, а
# отсоединённый HEAD ссылкой не является и до фильтра не доживёт.
git checkout --quiet -B publish "$SOURCE_SHA"

# --- вырезаем из истории ----------------------------------------------------

FILTER_ARGS=(--force --invert-paths)
for p in "${STRIP_PATHS[@]}"; do
  FILTER_ARGS+=(--path "$p")
done

echo "  вырезаю из истории: ${STRIP_PATHS[*]}"
"${FILTER[@]}" "${FILTER_ARGS[@]}" >/dev/null

echo "  коммитов в публичной истории: $(git rev-list --count publish)"

# --- правим вершину ---------------------------------------------------------

git checkout --quiet publish

if [ -f README.public.md ]; then
  mv -f README.public.md README.md
  echo "  README заменён на публичный"
else
  echo "  README.public.md нет — README оставлен как есть"
fi

# Подстраховка на случай, если что-то из STRIP_PATHS появилось в дереве
# помимо истории.
for p in "${STRIP_PATHS[@]}"; do
  [ -e "$p" ] && rm -rf "$p"
done

git add -A
if git diff --cached --quiet; then
  echo "  на вершине править нечего"
else
  git -c user.name="publish" -c user.email="publish@local" \
      commit -q -m "Публичная версия: README для пользователей, без docs"
fi

# --- публикуем --------------------------------------------------------------

git remote add "$REMOTE" "$REMOTE_URL"

echo "публикую в $REMOTE/$BRANCH"
git push "$REMOTE" "HEAD:refs/heads/$BRANCH" --force

# Теги отправляются отсюда же, из отфильтрованной истории. Если пушить их
# из исходного репозитория, они укажут на коммиты с docs/ — и вся работа
# выше окажется напрасной.
if [ -n "$(git tag -l)" ]; then
  echo "публикую теги: $(git tag -l | tr '\n' ' ')"
  git push "$REMOTE" --tags --force
else
  echo "тегов нет"
fi
