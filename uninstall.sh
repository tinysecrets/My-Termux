#!/data/data/com.termux/files/usr/bin/env bash
# my-termux uninstaller — safely removes commands, .bashrc block, and (optionally) data.

set -e
PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
BIN_DIR="$PREFIX/bin"
APP_HOME="$HOME/my-termux"
APP_DIR="$APP_HOME/app"
BASHRC="$HOME/.bashrc"

echo "[Termux] removing global commands..."
# Ask the package for the list if it is still readable, otherwise fall back.
COMMANDS="$(PYTHONPATH="$APP_DIR" python -c \
    'from mytermux.commands import installed_names; print(" ".join(installed_names()))' 2>/dev/null || true)"
if [ -z "$COMMANDS" ]; then
    COMMANDS="termux start now chat ask resume menu status dev scan sync fix export import media cloud upgrade help
termux my-start my-now my-chat my-ask my-resume my-menu my-status my-dev my-scan my-sync my-fix my-export my-import my-media my-cloud my-upgrade
start-my-termux"
fi
removed=0
for c in $COMMANDS; do
    if [ -L "$BIN_DIR/$c" ] || [ -f "$BIN_DIR/$c" ]; then
        rm -f "$BIN_DIR/$c" && removed=$((removed + 1))
    fi
done
echo "  - $removed command link(s) removed"

echo "[my-termux] removing auto-launch block from ~/.bashrc..."
if [ -f "$BASHRC" ]; then
    python - <<'PY'
import re, os
p = os.path.expanduser("~/.bashrc")
try:
    txt = open(p, "r", encoding="utf-8").read()
except FileNotFoundError:
    raise SystemExit(0)
new = re.sub(r"\n?# >>> my-termux auto-launch >>>.*?# <<< my-termux auto-launch <<<\n?",
             "\n", txt, flags=re.DOTALL)
open(p, "w", encoding="utf-8").write(new)
print("  - bashrc cleaned")
PY
fi

read -r -p "Delete ALL data in $APP_HOME? [y/N] " ans
case "$ans" in
    y|Y|yes|YES)
        rm -rf "$APP_HOME"
        echo "  - $APP_HOME removed"
        ;;
    *)
        echo "  - keeping $APP_HOME (config, sessions, logs preserved)"
        ;;
esac

echo "[my-termux] uninstalled. Open a new shell for changes to take effect."
