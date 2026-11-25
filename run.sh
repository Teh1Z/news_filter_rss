#!/usr/bin/env bash
# Запуск: ./run.sh  (первый запуск создаёт .venv и ставит зависимости)
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  if command -v uv >/dev/null; then
    uv venv .venv --python 3.13 && uv pip install --python .venv/bin/python -r requirements.txt
  else
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
  fi
fi
exec .venv/bin/python -m app.main
