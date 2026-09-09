# bash completion for my-termux.
# Installed by install.sh and sourced from the auto-launch block in ~/.bashrc.
# Typing `med<TAB>` on a phone keyboard is the difference between using this
# every day and not using it at all.
#
# NOTE on indexing: bash sets COMP_WORDS[0] to the command name and COMP_CWORD
# to the index of the word being completed. So `media <TAB>` is
# COMP_WORDS=(media "") COMP_CWORD=1 — the *first argument*, not the command.
# Command-name completion (`med<TAB>`) never reaches this function at all; bash
# resolves that from $PATH, where install.sh put every command.

_mytermux_complete() {
    local cur cmd
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"

    # normalise legacy `my-` prefixed names so both spellings complete alike
    cmd="${COMP_WORDS[0]}"
    case "$cmd" in
        start-my-termux) cmd="start" ;;
        my-*)            cmd="${cmd#my-}" ;;
    esac

    # ---- first argument: the sub-action ------------------------------------
    if [ "$COMP_CWORD" -eq 1 ]; then
        case "$cmd" in
            media)
                COMPREPLY=( $(compgen -W "add list info open rm attach capture record" -- "$cur") )
                return 0
                ;;
            cloud)
                COMPREPLY=( $(compgen -W "setup status sync up pull rm list" -- "$cur") )
                return 0
                ;;
            export|import)
                COMPREPLY=( $(compgen -W "session config project" -- "$cur") )
                return 0
                ;;
            sync)
                COMPREPLY=( $(compgen -W "--pull --commit --push" -- "$cur") )
                return 0
                ;;
            termux|dashboard)
                COMPREPLY=( $(compgen -W "--quick \
now chat ask resume menu status dev scan sync fix export import media cloud help" -- "$cur") )
                return 0
                ;;
            start)
                # `start` takes no arguments; the MYTERMUX_QUICK env var is the
                # switch for skipping the heal probe on shell start
                return 0
                ;;
        esac
        # scan / ask / etc. take a path or free text
        COMPREPLY=( $(compgen -f -- "$cur") )
        return 0
    fi

    # ---- deeper arguments: flags stay available, otherwise a path ----------
    case "$cmd" in
        sync)
            COMPREPLY=( $(compgen -W "--pull --commit --push" -- "$cur") )
            return 0
            ;;
        termux|dashboard)
            COMPREPLY=( $(compgen -W "--quick" -- "$cur") )
            return 0
            ;;
        media)
            COMPREPLY=( $(compgen -W "--kind --tags --project --move --keep-file --session --camera" -- "$cur") )
            return 0
            ;;
        cloud)
            COMPREPLY=( $(compgen -W "--force --also-local --limit" -- "$cur") )
            return 0
            ;;
    esac
    COMPREPLY=( $(compgen -f -- "$cur") )
    return 0
}

# canonical names
complete -F _mytermux_complete \
    termux start now chat ask resume menu status dev scan sync fix export import media cloud

# legacy `my-` prefixed names — still installed, still completed
complete -F _mytermux_complete \
    my-termux start-my-termux my-start my-now my-chat my-ask my-resume my-menu \
    my-status my-dev my-scan my-sync my-fix my-export my-import my-media my-cloud
