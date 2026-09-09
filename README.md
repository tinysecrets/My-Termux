# my-termux

A **Termux-only, phone-first AI agent workspace** that turns your Android phone
into a personalised terminal environment. It launches into a branded dashboard,
remembers your sessions and projects, uses **free OpenRouter models**, talks to
GitHub, thinks ahead about your next steps, and repairs itself when it breaks —
all stored locally on your phone, all free.

```
╭────────────────────────────────────────────────────────────────────────────╮
│   good afternoon  ·  Wed 09 Sep 14:22  ·  78% ▓▓▓▓▓▓▓░░░  ·  41.2G free    │
╰────────────────────────────────────────────────────────────────────────────╯
╭────────────────────────────────── status ──────────────────────────────────╮
│  OpenRouter API           ok             deepseek-chat-v3.1                │
│  GitHub token             ok             tester                            │
│  Project                  git            proj  (master · dirty)            │
│  Sessions                 ok             #12 of 12                         │
│  Pending tasks            ok             2                                 │
│  Media vault              ok             7 item(s)                         │
│  Disk free                ok             41.2G                             │
╰────────────────────────────────────────────────────────────────────────────╯
╭───────────────────────────── since last time ──────────────────────────────╮
│ 2 sessions, 14 messages since 3h ago                                       │
│ last answer: Use json.loads on the string:                                 │
╰────────────────────────────────────────────────────────────────────────────╯
╭─────────────────────────────────── next ───────────────────────────────────╮
│  1.   resume                                                               │
│       Pick up your last conversation with full history.  (Open session #12)│
│  2.   sync                                                                 │
│       Review them, then commit and push.  (proj has uncommitted changes.)  │
│  3.   ask "work on: finish the export command"                             │
│       Hand the next one to the agent.  (2 pending task(s).)                │
╰────────────────────────────────────────────────────────────────────────────╯
commands: chat  ask  now  resume  menu  fix  · menu for everything else
```

## Features

- **A launch that is worth looking at** — battery + charge state, free storage,
  git state of your current project, what changed since you last opened it, and
  next steps you can copy-paste. Width-aware: it renders properly in portrait
  (~50 cols) as well as landscape.
- **Fast startup** — self-heal runs at most once per 12 h and is replayed from
  cache otherwise, so opening a shell stays instant.
- **Tab-completion** for every command and subcommand. On a phone keyboard this
  is the difference between using the tool and not.
- **Named commands** instead of a bare terminal:
  `termux`, `start`, `now`, `chat`, `ask`, `resume`, `menu`, `status`, `dev`,
  `scan`, `sync`, `fix`, `export`, `import`, `media`, `cloud`.
  Legacy `my-` prefixed names (`my-chat`, `my-menu`, …) still work.
- **One-shot `ask`** — `ask "what does this repo do?"` runs a full agent turn
  and exits. No REPL, ideal for a phone.
- **Free OpenRouter routing** with automatic fallback across free models
  (`deepseek/deepseek-chat-v3.1:free` → `google/gemini-2.0-flash-exp:free` →
  `meta-llama/llama-3.3-70b-instruct:free` → `openrouter/auto`).
- **Real agent brain** (not just a chatbot): visible `<think>` reasoning, self-directed
  tool use — `shell`, `read_file`, `write_file`, `list_dir`, `scan_project`, `git`,
  `media_list`, `add_task`, `add_goal`, `notify`, `web_search`, `finish`. Multi-hop
  loop up to `MYTERMUX_AGENT_MAX_HOPS` (default 6). Dangerous shell / protected file
  writes ask you to confirm.
- **Local SQLite memory** for sessions, goals, tasks, logs, repairs and projects.
- **Proactive planner** that reads your *phone*, not just your files: low battery
  promotes `export session`, low storage promotes `fix`, a dirty repo promotes
  `sync`. Every suggestion is guaranteed to be an installed command.
- **Self-heal** — startup diagnostics + safe auto-repair, with backups.
- **GitHub over PAT** — clone, status, pull, commit, push right from the CLI.
- **Project scanner** that detects Python / Node / Rust / Go / Java / etc.
- **Android-visible exports** to `/sdcard/MyTermux/exports/` (sessions, config, whole projects).
- **Termux notifications** via `termux-notification` (stub for future WhatsApp/SMS hooks).
- **Degrades instead of breaking** — no `rich`, no `termux-api`, no network, or
  not even Android: every probe returns a dash and the dashboard still renders.

## Install (Termux, phone only)

### Quick install from a GitHub repo

```bash
pkg update && pkg install -y git
git clone https://github.com/<you>/<repo>.git ~/my-termux-src
cd ~/my-termux-src && bash install.sh
```

### Or copy the source to your phone manually
Zip the source, put it somewhere on your phone (Drive / USB / email), then:
```bash
cd ~
unzip /storage/shared/Download/my-termux.zip -d my-termux-src
cd my-termux-src && bash install.sh
```

### Finding the correct photo path
Camera photos live at `~/storage/shared/DCIM/Camera/` **after** you've granted
storage permission (the installer does this via `termux-setup-storage`). To find
a real filename, list the folder first — don't type `<some-photo>.jpg` literally:

```bash
ls ~/storage/shared/DCIM/Camera/
media add ~/storage/shared/DCIM/Camera/IMG_<TAB>     # Tab auto-completes
```

### What the installer does

1. Installs `python`, `git`, `termux-api`.
2. Copies source to `~/my-termux/app/`.
3. Creates `~/my-termux/{projects,sessions,logs,config,backups,cache}`.
4. Runs `termux-setup-storage` and links `/sdcard/MyTermux/exports/`.
5. `pip install httpx rich pyyaml cloudinary`.
6. Installs global commands into `$PREFIX/bin/` — the list is read from the
   package (`mytermux.commands.installed_names()`), so it can never drift away
   from what the dashboard tells you to type. Both canonical names (`chat`) and
   legacy `my-` names (`my-chat`) are installed.
7. Adds a block to `~/.bashrc`: git-aware prompt, **tab-completion** for every
   command and subcommand, and the auto-dashboard on shell start.
8. Runs a **first-run wizard** to save your OpenRouter key and optional GitHub PAT
   into `~/my-termux/config/config.yaml`.

Environment switches for the auto-launch:

| Variable | Effect |
| --- | --- |
| `MYTERMUX_NO_AUTOSTART=1` | don't print the dashboard when a shell opens |
| `MYTERMUX_QUICK=1` | print it without the self-heal probe (fastest) |
| `MYTERMUX_FORCE=1` | let `install.sh` run outside Termux (testing only) |

Re-running `install.sh` **refreshes** the `~/.bashrc` block rather than skipping
it, so upgrading also upgrades your startup hook.

Skip auto-dashboard temporarily with `MYTERMUX_NO_AUTOSTART=1 bash`.
Uninstall with `bash uninstall.sh` (asks before deleting data).

## Commands

| Command             | What it does                                                          |
| ------------------- | --------------------------------------------------------------------- |
| `termux`            | Dashboard: banner + status + next actions. `--quick` skips self-heal  |
| `start`             | Full startup: self-heal (if stale) then dashboard — what `.bashrc` runs |
| `now`               | Instant status card. No heal, no pip probe, no network                |
| `chat`              | Interactive agent chat (streaming, thinking, tools)                   |
| `ask "QUESTION"`    | **One-shot**: run one agent turn, print the answer, exit              |
| `resume`            | Resume the last chat session (with full history)                      |
| `menu`              | Numeric guided menu (settings, scan, sync, fix, export…)              |
| `status`            | Same status card as the dashboard, no banner                          |
| `dev`               | What your phone reports: battery, temperature, storage, clipboard     |
| `scan [PATH]`       | Scan a project, detect kind + git, register it, set current           |
| `sync [PATH]`       | `git status`; optional `--pull`, `--commit "msg"`, `--push`           |
| `fix`               | Diagnostics + safe self-repair; JSON log in `~/my-termux/logs/`       |
| `export [WHAT]`     | Export `session` / `config` / `project` to `/sdcard/MyTermux/exports/` |
| `import WHAT PATH`  | Import a previous export                                              |
| `media …`           | Local media vault: `add`, `list`, `info`, `open`, `rm`, `attach`, `capture`, `record` |
| `cloud …`           | Optional Cloudinary sync: `setup`, `status`, `sync`, `up`, `pull`, `rm`, `list` |
| `upgrade [PATH]`    | Check this app's own repo for updates                                 |
| `termux help`       | List every command                                                    |

Every command also works with the legacy `my-` prefix (`my-chat`, `my-menu`,
`my-fix`, …), and `start-my-termux` is the name the shell-startup hook calls.
The single source of truth is `mytermux/commands.py`; `install.sh`, the
dashboard, the planner, tab-completion and the tests all read from it.

Inside chat, slash-commands work too: `/help /new /resume /project X /goal X /task X /suggest /plain /agent /tools /q`.

## File & media storage

### Local vault (always on, offline, free)

```bash
media add ~/Downloads/photo.jpg              # copy into ~/my-termux/media/images/
media add ~/song.mp3 --tags "music,relax"    # tag on import
media list --kind image                      # filter by kind
media open 3                                 # open with phone's default app
media capture                                # snap a photo (needs termux-api)
media record 15                              # record 15s of audio
media attach 3 --session 12 --project foo    # link media to a chat/project
media rm 3 --keep-file                       # unregister but keep the file
```

Files land under `~/my-termux/media/{images,video,audio,docs,other}/` and are
mirrored to `~/storage/shared/MyTermux/media/…` so they show up in your Android
Gallery / Files app automatically (after `termux-setup-storage` — the installer
does that for you).

### Optional Cloudinary cloud sync (free tier, 25 GB)

Cloud sync is **entirely optional**. Everything above works fully offline
without it. When you're ready:

1. Sign up free at [cloudinary.com](https://cloudinary.com/console).
2. From your Dashboard copy `cloud_name`, `api_key`, `api_secret`.
3. Run:
   ```bash
   cloud setup           # paste the three values once
   cloud sync            # upload every un-synced local media asset
   cloud list            # see what's in the cloud
   cloud pull 12         # restore a specific asset back to the phone
   cloud rm 12 --also-local   # delete from cloud (optionally locally too)
   ```

Behind the scenes:

- images → `resource_type=image`
- videos → `resource_type=video`
- audio  → `resource_type=video` (Cloudinary treats audio as video)
- docs / other → `resource_type=raw`
- Every asset lives under `my-termux/<kind>/<basename>` in your Cloudinary account,
  so it's easy to find and delete from the Cloudinary dashboard too.

If creds are missing, `my-cloud` prints a helpful error and the vault keeps
working locally — no network, no crash.



```
~/my-termux/
├── app/                # installed source (do not edit — reinstall to update)
├── projects/           # any working projects you create locally
├── sessions/           # future: per-session exports
├── logs/               # repair logs (repair-YYYYMMDD-hhmmss.json)
├── config/config.yaml  # your API keys and preferences
├── backups/            # rolling config backups
├── cache/              # startup caches (device snapshot, heal verdict, last-open)
├── media/              # local media vault
│   ├── images/
│   ├── video/
│   ├── audio/
│   ├── docs/
│   └── other/
└── mytermux.db         # SQLite: sessions, messages, goals, tasks, logs, repairs, projects, media

~/storage/shared/MyTermux/
├── exports/            # session/config/project exports (Android-visible)
└── media/              # mirror of the local media vault (Android-visible)
```

## Config file (`~/my-termux/config/config.yaml`)

```yaml
openrouter_api_key: sk-or-v1-...        # required for chat
openrouter_title: my-termux
model_order:
  - deepseek/deepseek-chat-v3.1:free
  - google/gemini-2.0-flash-exp:free
  - meta-llama/llama-3.3-70b-instruct:free
  - openrouter/auto
github_token: ghp_...                    # optional, for git push over HTTPS
github_username: your-user
current_project: /data/data/com.termux/files/home/projects/foo
auto_dashboard: true
notifications: true
theme: dark
cloudinary_cloud_name: ""                # optional, for `my-cloud`
cloudinary_api_key: ""
cloudinary_api_secret: ""
media_auto_sync: false
```

## Phone-number / messaging integration (v1 notes)

Per your own recommendation, **v1 ships with local Termux notifications only**
(`termux-notification`). A `notify` stub is exposed in `mytermux/notify.py` so
future integrations (WhatsApp, SMS, Telegram) can be added without touching
the rest of the codebase — just add another sender behind the same `notify()`
call.

## OpenRouter free models — recommendation

Currently the best free default is `deepseek/deepseek-chat-v3.1:free` for
reasoning/coding, with the fast `google/gemini-2.0-flash-exp:free` and
multilingual `meta-llama/llama-3.3-70b-instruct:free` as fallbacks, and finally
the `openrouter/auto` router. This ordered list lives in your config; edit it
freely — the client falls back automatically on transient errors and honours
`Retry-After` on 429s.

## License

MIT — do whatever you want, but don't ship your API key to anyone.
