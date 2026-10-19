# auto-sign

Signs Netypareo attendance sheets (émargement) on `extranet.cfai-alsace.fr`.

## Setup

1. Install [uv](https://docs.astral.sh/uv/).
2. Put your signature image in `signature.png` (PNG, transparent background works best).
3. Provide credentials (see [Configuration](#configuration)). Missing values are prompted for.

## Configuration

The first config file found is used:

1. `--config PATH`
2. `./auto-sign.toml` (current directory)
3. Per-platform:
   - Linux: `$XDG_CONFIG_HOME/auto-sign/config.toml` (default `~/.config/auto-sign/config.toml`)
   - macOS: `~/Library/Application Support/auto-sign/config.toml`, then the Linux path
   - Windows: `%APPDATA%\auto-sign\config.toml`

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

If you store a plain `password`, run `chmod 600` on the file. The script warns when others can read it.

## Usage

```sh
uv run auto_sign.py              # list today's unsigned sessions, pick which to sign
uv run auto_sign.py --all        # sign every open session without asking
uv run auto_sign.py --dry-run    # list and select, do not sign
uv run auto_sign.py --date 20/10/2026 --verbose
```

At the prompt, enter `1,3`, `1-3`, or `a` for all open sessions. Press Enter with no input to cancel.

Exit codes: `0` OK or nothing to sign, `1` a signature failed, `2` login failed.

## Security

HAR files hold your password and session cookies in plain text. Do not share or commit them. Delete `*.har` files once you no longer need them.
