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
#   * каталог docs/ не публикуется — это рабочие записи (разбор протокола,
#     настройка раннера, снятие дампов), пользователю они не нужны.
#
# GitLab остаётся полным: там и подробный README, и docs/.
#
# Один и тот же скрипт вызывается вручную и из CI, чтобы правила преобразования
# не разъезжались. Отличается только способ подготовки дерева:
#
#   локально — отдельный git worktree, рабочий каталог не трогается;
#   в CI     — прямо в одноразовом checkout'е (worktree там не поднять,
#              клон поверхностный и нужной ветки локально может не быть).

set -euo pipefail

REMOTE="${PUBLISH_REMOTE:-github}"
BRANCH="${PUBLISH_BRANCH:-main}"
SOURCE="${1:-$BRANCH}"

# Готовит публичный вид в текущем каталоге и коммитит отличия.
transform() {
  if [ -f README.public.md ]; then
    mv -f README.public.md README.md
    echo "  README заменён на публичный"
  else
    echo "  README.public.md нет — README оставлен как есть"
  fi

  if [ -d docs ]; then
    rm -rf docs
    echo "  каталог docs исключён"
  fi

  git add -A
  if git diff --cached --quiet; then
    echo "  различий нет, публикуется исходное состояние"
  else
    git -c user.name="publish" -c user.email="publish@local" \
        commit -q -m "Публичная версия: README для пользователей, без docs"
  fi
}

if [ -n "${CI:-}" ]; then
  echo "режим CI: преобразую текущий checkout"
  transform
  echo "публикую в $REMOTE/$BRANCH"
  git push "$REMOTE" "HEAD:refs/heads/$BRANCH" --force
else
  echo "готовлю публичную версию из '$SOURCE'"
  WT="$(mktemp -d)"
  cleanup() { git worktree remove --force "$WT" >/dev/null 2>&1 || true; }
  trap cleanup EXIT

  git worktree add --detach "$WT" "$SOURCE" >/dev/null
  (
    cd "$WT"
    transform
    echo "публикую в $REMOTE/$BRANCH"
    git push "$REMOTE" "HEAD:refs/heads/$BRANCH" --force
  )
fi
