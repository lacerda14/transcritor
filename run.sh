#!/usr/bin/env bash
# Transcritor de vídeo -> texto (local). Sobe o servidor e abre no navegador.
set -euo pipefail
cd "$(dirname "$0")"

# ffmpeg é obrigatório (extração/decodificação de áudio)
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "⚠  ffmpeg não encontrado. Instale com:  brew install ffmpeg"
  exit 1
fi

# cria o venv na primeira vez
if [ ! -d ".venv" ]; then
  echo "→ Criando ambiente virtual (primeira execução)…"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  echo "→ Instalando dependências…"
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

PORT="${PORT:-5005}"
URL="http://127.0.0.1:${PORT}"

# Se já estiver no ar (ex.: serviço launchd), só abre o navegador e sai —
# não tenta subir outro processo na mesma porta (evita o "fora do ar").
if curl -s -o /dev/null -w "%{http_code}" "$URL" 2>/dev/null | grep -q 200; then
  echo "→ já está no ar em ${URL} (serviço). Abrindo navegador…"
  command -v open >/dev/null 2>&1 && open "$URL" || true
  exit 0
fi

# abre o navegador depois que o servidor sobe
( sleep 2; command -v open >/dev/null 2>&1 && open "$URL" || true ) &

echo "→ Transcritor em ${URL}  (Ctrl+C para parar)"
exec ./.venv/bin/python app.py
