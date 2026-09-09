#!/data/data/com.termux/files/usr/bin/env bash
# my-termux installer — Termux-only phone AI workspace.
#
# Usage:   bash install.sh
#
# What it does (idempotent, safe to re-run):
#   1. Verifies you are on Termux (or forces with MYTERMUX_FORCE=1).
#   2. Installs required packages (python, git, termux-api).
#   3. Copies the source tree to ~/my-termux/app/.
#   4. Creates data folders under ~/my-termux/.
#   5. Requests storage permission and links Android-visible exports.
#   6. Installs required Python packages (httpx, rich, pyyaml).
#   7. Installs global commands into $PREFIX/bin (chat, menu, ... plus my-* aliases).
#   8. Adds prompt + tab-completion + auto-launch dashboard to ~/.bashrc.
#   9. Runs a first-run setup wizard to save OpenRouter key + optional GitHub PAT.
#  10. Initialises the SQLite DB and prints the dashboard.

set -e

# ---------- pretty output helpers ----------
if [ -t 1 ]; then
    C_RESET="\033[0m"; C_BOLD="\033[1m"; C_CYAN="\033[36m"; C_GREEN="\033[32m"
    C_YELLOW="\033[33m"; C_RED="\033[31m"; C_DIM="\033[2m"
else
    C_RESET=""; C_BOLD=""; C_CYAN=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_DIM=""
fi
say()    { printf "%b[Termux]%b %s\n" "$C_CYAN$C_BOLD" "$C_RESET" "$*"; }
ok()     { printf "%b   ✓%b %s\n" "$C_GREEN" "$C_RESET" "$*"; }
warn()   { printf "%b   !%b %s\n" "$C_YELLOW" "$C_RESET" "$*"; }
fail()   { printf "%b   ✗%b %s\n" "$C_RED" "$C_RESET" "$*"; }

# ---------- 1. environment detection ----------
say "checking environment"
IS_TERMUX=0
if [ -n "$PREFIX" ] && [ -d "$PREFIX/bin" ] && [ -d "/data/data/com.termux/files/usr" ]; then
    IS_TERMUX=1
fi
if [ "$IS_TERMUX" -ne 1 ] && [ "${MYTERMUX_FORCE:-0}" != "1" ]; then
    fail "This installer is Termux-only."
    warn "Install Termux from F-Droid, open it, then run this script inside it."
    warn "(Override for testing: MYTERMUX_FORCE=1 bash install.sh)"
    exit 2
fi
[ -z "$PREFIX" ] && PREFIX="/data/data/com.termux/files/usr"
BIN_DIR="$PREFIX/bin"
mkdir -p "$BIN_DIR"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
APP_HOME="$HOME/my-termux"
APP_DIR="$APP_HOME/app"

# ---------- 2. install pkg dependencies ----------
if [ "$IS_TERMUX" -eq 1 ]; then
    say "installing termux packages (python, git, termux-api)"
    yes | pkg update -y >/dev/null 2>&1 || warn "pkg update had warnings"
    for p in python git termux-api; do
        if pkg list-installed 2>/dev/null | grep -q "^$p/"; then
            ok "$p already installed"
        else
            pkg install -y "$p" >/dev/null 2>&1 && ok "installed $p" || warn "could not install $p"
        fi
    done
fi

# ---------- 3. copy source tree ----------
say "copying source tree into $APP_DIR"
mkdir -p "$APP_DIR"
# copy the mytermux python package
if [ -d "$SCRIPT_DIR/mytermux" ]; then
    rm -rf "$APP_DIR/mytermux"
    cp -r "$SCRIPT_DIR/mytermux" "$APP_DIR/mytermux"
    ok "source tree copied"
else
    fail "source folder $SCRIPT_DIR/mytermux not found"
    exit 3
fi
# copy the dispatch shim (used by all bin symlinks)
mkdir -p "$APP_DIR/bin"
cp "$SCRIPT_DIR/bin/mytermux-dispatch" "$APP_DIR/bin/mytermux-dispatch"
chmod +x "$APP_DIR/bin/mytermux-dispatch"
# copy bash completions
mkdir -p "$APP_DIR/completions"
cp "$SCRIPT_DIR/completions/mytermux.bash" "$APP_DIR/completions/mytermux.bash"
ok "completions copied"

# ---------- 4. create data folders ----------
say "creating data folders under $APP_HOME"
for d in projects sessions logs config backups cache; do
    mkdir -p "$APP_HOME/$d"
    ok "$APP_HOME/$d"
done

# ---------- 5. storage permission + Android-visible export path ----------
if [ "$IS_TERMUX" -eq 1 ]; then
    if [ ! -d "$HOME/storage" ]; then
        say "requesting storage permission (accept the popup)"
        termux-setup-storage >/dev/null 2>&1 || warn "termux-setup-storage skipped"
        sleep 1
    fi
    if [ -d "$HOME/storage/shared" ]; then
        mkdir -p "$HOME/storage/shared/MyTermux/exports" && ok "Android exports: /sdcard/MyTermux/exports"
    else
        warn "shared storage not available; exports will stay in ~/my-termux/exports/"
    fi
fi

# ---------- 6. Python dependencies ----------
say "installing python dependencies"
python -m pip install --upgrade pip >/dev/null 2>&1 || warn "pip upgrade skipped"
python -m pip install --quiet --upgrade httpx rich pyyaml cloudinary && ok "httpx, rich, pyyaml, cloudinary installed" \
    || warn "some pip installs failed; run \`fix\` later"

# ---------- 7. install global commands ----------
say "installing commands into $BIN_DIR"
# Ask the package itself which names to install. This is deliberate: the
# command list used to be duplicated here and drifted away from what the
# dashboard printed, so every "next step" suggestion was a dead command.
COMMANDS="$(PYTHONPATH="$APP_DIR" python -c \
    'from mytermux.commands import installed_names; print(" ".join(installed_names()))' 2>/dev/null || true)"
if [ -z "$COMMANDS" ]; then
    warn "could not read the command list from the package; using the built-in fallback"
    COMMANDS="termux start now chat ask resume menu status dev scan sync fix export import media cloud upgrade help
termux my-start my-now my-chat my-ask my-resume my-menu my-status my-dev my-scan my-sync my-fix my-export my-import my-media my-cloud my-upgrade
start-my-termux"
fi
for c in $COMMANDS; do
    ln -sf "$APP_DIR/bin/mytermux-dispatch" "$BIN_DIR/$c"
done
ok "$(echo "$COMMANDS" | wc -w | tr -d ' ') commands installed (canonical + my-* aliases)"

# ---------- 8. bashrc: prompt, completion, auto-launch ----------
BASHRC="$HOME/.bashrc"
MARK_BEGIN="# >>> my-termux auto-launch >>>"
MARK_END="# <<< my-termux auto-launch <<<"
touch "$BASHRC"
if grep -q "$MARK_BEGIN" "$BASHRC"; then
    # Replace the block rather than skipping it, so upgrading also upgrades
    # the startup hook. (An older block pointed at a command that was never
    # installed, which is why the dashboard silently stopped appearing.)
    say "refreshing the existing my-termux block in ~/.bashrc"
    python - "$BASHRC" "$MARK_BEGIN" "$MARK_END" <<'PY'
import re, sys
path, begin, end = sys.argv[1], sys.argv[2], sys.argv[3]
txt = open(path, "r", encoding="utf-8").read()
pat = re.compile(r"\n?" + re.escape(begin) + r".*?" + re.escape(end) + r"\n?", re.DOTALL)
open(path, "w", encoding="utf-8").write(pat.sub("\n", txt))
PY
fi
say "adding prompt, tab-completion and auto-launch to ~/.bashrc"
{
    echo ""
    echo "$MARK_BEGIN"
    echo "# my-termux: prompt, tab-completion, and a dashboard on interactive shells."
    echo "#   MYTERMUX_NO_AUTOSTART=1   do not print the dashboard on shell start"
    echo "#   MYTERMUX_QUICK=1          print it without the self-heal probe (faster)"
    echo '__mytermux_branch() {'
    echo '  local b'
    echo '  b="$(git symbolic-ref --short -q HEAD 2>/dev/null || git rev-parse --short HEAD 2>/dev/null)"'
    echo '  [ -n "$b" ] && printf " (%s)" "$b"'
    echo '}'
    echo 'export PS1='"'"'\[\e[36m\]my-termux\[\e[0m\] \[\e[32m\]\w\[\e[0m\]\[\e[35m\]$(__mytermux_branch)\[\e[0m\] $ '"'"''
    echo 'if [ -f "$HOME/my-termux/app/completions/mytermux.bash" ]; then'
    echo '    . "$HOME/my-termux/app/completions/mytermux.bash"'
    echo 'fi'
    echo 'if [ -z "$MYTERMUX_NO_AUTOSTART" ] && [ -t 1 ] && [[ $- == *i* ]]; then'
    echo '    if [ -n "$MYTERMUX_QUICK" ]; then'
    echo '        termux --quick || true'
    echo '    else'
    echo '        start-my-termux || true'
    echo '    fi'
    echo 'fi'
    echo "$MARK_END"
} >> "$BASHRC"
ok "prompt + completion + auto-launch installed"

# ---------- 9. first-run wizard ----------
say "first-run wizard (press ENTER to skip any question)"
python - <<'PY'
import sys
sys.path.insert(0, __import__('os').path.expanduser('~/my-termux/app'))
from mytermux.config import load_config, save_config
from mytermux import db, paths

paths.ensure_dirs()
db.init_db()
cfg = load_config()

def ask(msg, current=""):
    tail = " (leave blank to keep current)" if current else ""
    try:
        v = input(f"  {msg}{tail}: ").strip()
    except EOFError:
        return current
    return v or current

if not cfg.get("openrouter_api_key"):
    v = ask("OpenRouter API key (from https://openrouter.ai/keys)")
    if v:
        cfg["openrouter_api_key"] = v
else:
    print("  OpenRouter key: already set (skipping)")

if not cfg.get("github_token"):
    u = ask("GitHub username (optional)")
    if u:
        cfg["github_username"] = u
        t = ask("GitHub Personal Access Token (optional)")
        if t:
            cfg["github_token"] = t
else:
    print("  GitHub token: already set (skipping)")

# --- Cloudinary (optional, free tier) ---
if not cfg.get("cloudinary_cloud_name"):
    print("  Cloudinary sync is OPTIONAL (free tier at https://cloudinary.com/console).")
    print("  Press ENTER to skip; you can run `cloud setup` later.")
    cn = ask("Cloudinary cloud_name (optional)")
    if cn:
        ak = ask("Cloudinary api_key")
        sk = ask("Cloudinary api_secret")
        if ak and sk:
            cfg["cloudinary_cloud_name"] = cn
            cfg["cloudinary_api_key"] = ak
            cfg["cloudinary_api_secret"] = sk
else:
    print("  Cloudinary: already set (skipping)")

save_config(cfg)
print("  ✓ config saved to", paths.CONFIG_FILE)
PY

# ---------- 10. done ----------
say "installation complete!"
echo ""
echo -e "  ${C_BOLD}Try these now:${C_RESET}"
echo -e "    ${C_GREEN}termux${C_RESET}          open the dashboard"
echo -e "    ${C_GREEN}ask${C_RESET} \"...\"       one-shot question, no REPL"
echo -e "    ${C_GREEN}chat${C_RESET}            full agent session"
echo -e "    ${C_GREEN}now${C_RESET}             instant status card"
echo -e "    ${C_GREEN}dev${C_RESET}             battery / storage / termux-api"
echo -e "    ${C_GREEN}menu${C_RESET}            guided menu"
echo -e "    ${C_GREEN}termux help${C_RESET}     every command"
echo ""
echo -e "  ${C_DIM}Tab-completion is on: try typing ${C_GREEN}med<TAB>${C_RESET}"
echo -e "  ${C_DIM}Legacy my-* names still work: my-chat, my-menu, my-fix …${C_RESET}"
echo ""
echo -e "  ${C_DIM}Config:    ~/my-termux/config/config.yaml${C_RESET}"
echo -e "  ${C_DIM}Data:      ~/my-termux/{projects,sessions,logs,backups,cache}${C_RESET}"
echo -e "  ${C_DIM}Exports:   /sdcard/MyTermux/exports/${C_RESET}"
echo ""
say "restart Termux (or run \`exec bash\`) to see the dashboard on launch."
