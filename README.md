# nety

Signs Netypareo attendance sheets (émargement) on `extranet.cfai-alsace.fr`.

## Setup

1. [Install](#install) the command: a prebuilt binary, or with [uv](https://docs.astral.sh/uv/).
2. Run `nety settings init`. It asks for your login, a password (system keyring, value or command) and a signature: draw it in a window or pick a PNG (transparent background works best). Missing values are prompted for at each run.

## Configuration

The first config file found is used:

1. `--config PATH`
2. `./nety.toml` (current directory)
3. Per-platform:
   - Linux: `$XDG_CONFIG_HOME/nety/config.toml` (default `~/.config/nety/config.toml`)
   - macOS: `~/Library/Application Support/nety/config.toml`, then the Linux path
   - Windows: `%APPDATA%\nety\config.toml`

```toml
login = "040-xxxxx"

# Either a plain value...
# password = "..."
# ...or a command whose first output line is the secret.
# A string runs through the shell; a list runs without a shell.
password_command = "pass show cfai/netypareo"
# password_command = ["secret-tool", "lookup", "service", "netypareo"]

# Optional. Relative paths start from the config file's directory.
signature = "~/signature.png"
```

`login` and `password` each accept a `_command` variant (`login_command`, `password_command`). Set only one of the two forms.

Precedence: env vars `NETYPAREO_LOGIN` / `NETYPAREO_PASSWORD`, then the config file, then the system keyring (password only), then a prompt. `--signature` overrides the config `signature`, which overrides `./signature.png`.

The password can live in the system keyring (Secret Service on Linux, Keychain on macOS, Credential Manager on Windows) under service `nety`, one entry per login. After a successful login with a typed password, `nety sign` offers to save it there.

Files written by `settings` are `chmod 600`. If you store a plain `password` in a file you made yourself, `chmod 600` it. The script warns when others can read it.

### Settings command

`settings` reads and writes the file in use (`--config`, else the first file found, else the per-platform path). `set` and `unset` keep your comments and formatting.

```sh
nety settings init                  # interactive setup
nety settings show                  # file in use, search paths, settings (password masked)
nety settings show --resolve        # also run *_command entries and check the signature
nety settings set login 040-xxxxx
nety settings set password          # prompts, keeps it out of shell history
nety settings set password --keyring  # prompts, stores it in the system keyring
nety settings set password_command "pass show cfai/netypareo"      # shell string
nety settings set password_command secret-tool lookup service x    # list, no shell
nety settings set signature ~/signature.png
nety settings unset password
nety settings unset password --keyring
nety settings path                  # print the config file path
nety settings edit                  # open in $VISUAL / $EDITOR (notepad on Windows)
```

Setting `password` removes `password_command`, and the other way round (same for `login`).

### Signature command

```sh
nety signature draw                 # draw in a window, save signature.png next to the config file, set `signature`
nety signature draw --output ~/sig.png
```

Draw with the mouse, then press Enter or Save. Esc or Cancel quits without saving. The PNG has a transparent background and is cropped to the ink. `nety sign` offers to draw one when the signature file is missing.

The window needs a display and tkinter. The uv-managed Python that `uv tool install` uses ships it. A system Python may need a package such as `python3-tkinter`.

## Install

### Prebuilt binary

Download the file for your platform from the [Releases](https://github.com/clesecq/nety/releases) page (`nety-linux-x86_64`, `nety-linux-arm64`, `nety-macos-arm64`, `nety-windows-x86_64.exe`). No Python or uv needed. Check a download with `sha256sum -c SHA256SUMS --ignore-missing`, or check that CI built it with `gh attestation verify nety-linux-x86_64 --repo clesecq/nety`.

On Linux and macOS, make it executable and put it on your `PATH` (replace the file name with yours):

```sh
chmod +x nety-linux-x86_64
mv nety-linux-x86_64 ~/.local/bin/nety
```

The macOS binary is not signed. Remove the quarantine flag before the first run: `xattr -d com.apple.quarantine nety-macos-arm64` (before renaming it).

### With uv

```sh
uv tool install git+https://github.com/clesecq/nety.git # from GitHub; installs the `nety` command
uv tool upgrade nety                                    # update to the latest commit
```

Append `@<tag|branch|commit>` to the URL to pin a version.

From a clone:

```sh
uv tool install .            # installs the `nety` command
uv tool install --reinstall . # after pulling changes
```

Without installing, replace `nety` with `uv run nety` (from the repository checkout).

## Usage

```sh
nety sign              # list today's unsigned sessions, pick which to sign
nety sign --all        # sign every open session without asking
nety sign --dry-run    # list and select, do not sign
nety sign --date 20/10/2026 --verbose
```

A table lists the day's unsigned sessions. The prompt offers only open ones; a single open session is pre-checked. Use Space to toggle, Enter to confirm, Ctrl-C to cancel.

### Watch

```sh
nety watch                 # wait for the current (or next) session's sheet, sign it, exit
nety watch --buffer 60     # start watching 60 minutes before the listed start
nety watch --interval 300  # check every 5 minutes
nety watch --dry-run       # watch, but do not sign
```

`watch` picks the earliest unsigned session of the day that has not ended. Sessions usually start about 30 minutes before the time the schedule lists, so it starts checking 45 minutes before (`--buffer`). Until then it sleeps. It checks every 120 seconds (`--interval`, at least 30) until the session ends, and signs as soon as the sheet opens. It logs in again if the session expires, and retries after network errors.

To run it unattended (cron, systemd timer, tmux), store the password in the keyring, `password_command` or `NETYPAREO_PASSWORD`. A re-login cannot prompt when no one is at the terminal.

`--config PATH` and `--verbose` work with every command. Running without a command prints help.

Exit codes: `0` OK or nothing to sign, `1` a signature failed, `2` config or login error, `3` (`watch`) the session ended without an open sheet.

## Security

After a login, `nety sign` keeps the session cookies in `~/.cache/nety/cookies.json` (`$XDG_CACHE_HOME`, `~/Library/Caches` on macOS, `%LOCALAPPDATA%` on Windows), mode `600`. Later runs reuse them and log in again only when they expire. Anyone who reads that file can act as you until the session ends: delete it to log out.

HAR files hold your password and session cookies in plain text. Do not share or commit them. Delete `*.har` files once you no longer need them.

## Development

Code lives in `src/nety/`: `client.py` (extranet HTTP calls), `config.py`, `secrets.py` (login, password, keyring), `ui.py` (table and prompts), `watch.py` (which session `watch` waits for), `draw.py` (signature window), `selfcheck.py` (bundling checks CI runs on the release binaries), `cli.py` (commands).

```sh
uv run pytest        # tests use recorded responses in tests/fixtures, no network
uv run nety --help
```

To release, bump `__version__` in `src/nety/__init__.py`, commit, then `git tag vX.Y.Z && git push origin main --tags`. The tag must point to a commit on `main`. The `Release` workflow runs the tests, builds the binaries with PyInstaller and publishes them. Run it by hand (`workflow_dispatch`) to build without publishing.

Fixtures come from a real session with names, ids and tokens replaced. Never commit a raw `.har` file.
