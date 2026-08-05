#!/usr/bin/env bash
# Controla o Transcritor como serviço sempre-no-ar (launchd).
# Uso:  ./service.sh {start|stop|restart|status|logs|install|uninstall}
set -euo pipefail

LABEL="com.pedro.transcritor"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
GUI="gui/$(id -u)"
URL="http://127.0.0.1:5005"
DIR="$(cd "$(dirname "$0")" && pwd)"

case "${1:-status}" in
  install|start)
    launchctl bootstrap "$GUI" "$PLIST" 2>/dev/null || launchctl kickstart "$GUI/$LABEL"
    launchctl enable "$GUI/$LABEL" 2>/dev/null || true
    sleep 2
    echo "→ no ar em $URL"
    ;;
  stop)
    launchctl bootout "$GUI/$LABEL" 2>/dev/null || true
    echo "→ parado (não reinicia sozinho até dar start de novo)"
    ;;
  restart)
    launchctl kickstart -k "$GUI/$LABEL"
    sleep 2
    echo "→ reiniciado em $URL"
    ;;
  uninstall)
    launchctl bootout "$GUI/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "→ serviço removido de vez"
    ;;
  logs)
    echo "== out.log =="; tail -n 30 "$DIR/logs/out.log" 2>/dev/null || echo "(vazio)"
    echo "== err.log =="; tail -n 30 "$DIR/logs/err.log" 2>/dev/null || echo "(vazio)"
    ;;
  status|*)
    if curl -s -o /dev/null -w "%{http_code}" "$URL" 2>/dev/null | grep -q 200; then
      echo "✅ NO AR — $URL"
    else
      echo "❌ fora do ar. Rode: ./service.sh start"
    fi
    launchctl print "$GUI/$LABEL" 2>/dev/null | grep -E 'state =|pid =|properties =' | head -3 || true
    ;;
esac
