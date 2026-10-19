# netypareo-auto-sign-in

Signs Netypareo attendance sheets (émargement) on `extranet.cfai-alsace.fr`.

## Setup

1. Install [uv](https://docs.astral.sh/uv/).
2. [Install](#install) the command.
3. Run `netypareo-auto-sign-in settings init`. It asks for your login, a password (value or command) and a signature PNG (transparent background works best). Missing values are prompted for at each run.

## Configuration

The first config file found is used:

1. `--config PATH`
2. `./netypareo-auto-sign-in.toml` (current directory)
3. Per-platform:
   - Linux: `$XDG_CONFIG_HOME/netypareo-auto-sign-in/config.toml` (default `~/.config/netypareo-auto-sign-in/config.toml`)
   - macOS: `~/Library/Application Support/netypareo-auto-sign-in/config.toml`, then the Linux path
   - Windows: `%APPDATA%\netypareo-auto-sign-in\config.toml`

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
netypareo-auto-sign-in settings init                  # interactive setup
netypareo-auto-sign-in settings show                  # file in use, search paths, settings (password masked)
netypareo-auto-sign-in settings show --resolve        # also run *_command entries and check the signature
netypareo-auto-sign-in settings set login 040-xxxxx
netypareo-auto-sign-in settings set password          # prompts, keeps it out of shell history
netypareo-auto-sign-in settings set password_command "pass show cfai/netypareo"      # shell string
netypareo-auto-sign-in settings set password_command secret-tool lookup service x    # list, no shell
netypareo-auto-sign-in settings set signature ~/signature.png
netypareo-auto-sign-in settings unset password
netypareo-auto-sign-in settings path                  # print the config file path
netypareo-auto-sign-in settings edit                  # open in $VISUAL / $EDITOR (notepad on Windows)
```

Setting `password` removes `password_command`, and the other way round (same for `login`).

## Install

```sh
uv tool install .            # from a clone; installs the `netypareo-auto-sign-in` command
uv tool install --reinstall . # after pulling changes
```

Without installing, replace `netypareo-auto-sign-in` with `uv run netypareo_auto_sign_in.py`.

## Usage

```sh
netypareo-auto-sign-in sign              # list today's unsigned sessions, pick which to sign
netypareo-auto-sign-in sign --all        # sign every open session without asking
netypareo-auto-sign-in sign --dry-run    # list and select, do not sign
netypareo-auto-sign-in sign --date 20/10/2026 --verbose
```

At the prompt, enter `1,3`, `1-3`, or `a` for all open sessions. Press Enter with no input to cancel.

`--config PATH` and `--verbose` work with every command. Running without a command prints help.

Exit codes: `0` OK or nothing to sign, `1` a signature failed, `2` config or login error.

## Security

HAR files hold your password and session cookies in plain text. Do not share or commit them. Delete `*.har` files once you no longer need them.
