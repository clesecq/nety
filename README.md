# auto-sign

Signs Netypareo attendance sheets (émargement) on `extranet.cfai-alsace.fr`.

## Setup

1. Install [uv](https://docs.astral.sh/uv/).
2. Put your signature image in `signature.png` (PNG, transparent background works best).
3. Provide credentials with one of these methods:
   - env vars `NETYPAREO_LOGIN` and `NETYPAREO_PASSWORD`
   - `config.toml`:
     ```toml
     login = "040-xxxxx"
     password = "..."
     ```
   - nothing: the script prompts for them.

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
