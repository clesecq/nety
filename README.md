# nety

Signs Netypareo attendance sheets (émargement) on `extranet.cfai-alsace.fr`.

## Setup

1. Install [uv](https://docs.astral.sh/uv/).
2. [Install](#install) the command.
3. Run `nety settings init`. It asks for your login, a password (value or command) and a signature PNG (transparent background works best). Missing values are prompted for at each run.

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

Precedence: env vars `NETYPAREO_LOGIN` / `NETYPAREO_PASSWORD`, then the config file, then a prompt. `--signature` overrides the config `signature`, which overrides `./signature.png`.

Files written by `settings` are `chmod 600`. If you store a plain `password` in a file you made yourself, `chmod 600` it. The script warns when others can read it.

### Settings command

`settings` reads and writes the file in use (`--config`, else the first file found, else the per-platform path). `set` and `unset` keep your comments and formatting.

```sh
nety settings init                  # interactive setup
nety settings show                  # file in use, search paths, settings (password masked)
nety settings show --resolve        # also run *_command entries and check the signature
nety settings set login 040-xxxxx
nety settings set password          # prompts, keeps it out of shell history
nety settings set password_command "pass show cfai/netypareo"      # shell string
nety settings set password_command secret-tool lookup service x    # list, no shell
nety settings set signature ~/signature.png
nety settings unset password
nety settings path                  # print the config file path
nety settings edit                  # open in $VISUAL / $EDITOR (notepad on Windows)
```

Setting `password` removes `password_command`, and the other way round (same for `login`).

## Install

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

Without installing, replace `nety` with `uv run nety.py`.

## Usage

```sh
nety sign              # list today's unsigned sessions, pick which to sign
nety sign --all        # sign every open session without asking
nety sign --dry-run    # list and select, do not sign
nety sign --date 20/10/2026 --verbose
```

A table lists the day's unsigned sessions. The prompt offers only open ones; a single open session is pre-checked. Use Space to toggle, Enter to confirm, Ctrl-C to cancel.

`--config PATH` and `--verbose` work with every command. Running without a command prints help.

Exit codes: `0` OK or nothing to sign, `1` a signature failed, `2` config or login error.

## Security

After a login, `nety sign` keeps the session cookies in `~/.cache/nety/cookies.json` (`$XDG_CACHE_HOME`, `~/Library/Caches` on macOS, `%LOCALAPPDATA%` on Windows), mode `600`. Later runs reuse them and log in again only when they expire. Anyone who reads that file can act as you until the session ends: delete it to log out.

HAR files hold your password and session cookies in plain text. Do not share or commit them. Delete `*.har` files once you no longer need them.
