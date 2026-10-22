"""Release notes for a tag: install and verify steps, then the commits since the previous tag.

Usage: python3 .github/release_notes.py vX.Y.Z > notes.md
Commits follow Conventional Commits; `chore: release` commits are left out.
"""

import re
import subprocess
import sys

REPO = "clesecq/nety"
# Section per commit type, in display order. Other types go under "Other changes".
SECTIONS = {"feat": "Features", "fix": "Fixes", "perf": "Performance"}
SKIP = re.compile(r"^chore(\(.*\))?: release\b")
SUBJECT = re.compile(r"^(?P<type>\w+)(\((?P<scope>[^)]*)\))?(?P<breaking>!)?: (?P<text>.+)$")

TEMPLATE = """\
## Download

| Platform | File |
|---|---|
| Linux x86_64 | `nety-linux-x86_64` |
| Linux arm64 | `nety-linux-arm64` |
| macOS (Apple Silicon) | `nety-macos-arm64` |
| Windows x86_64 | `nety-windows-x86_64.exe` |

On Linux and macOS:

```sh
chmod +x nety-linux-x86_64
mv nety-linux-x86_64 ~/.local/bin/nety
```

The macOS binary is not signed. Before the first run: `xattr -d com.apple.quarantine nety-macos-arm64`.

The Linux binaries need glibc 2.35 or later (Ubuntu 22.04, Debian 12, Fedora 36 and newer).

With uv instead: `uv tool install git+https://github.com/{repo}.git@{tag}`.

## Verify

```sh
sha256sum -c SHA256SUMS --ignore-missing
gh attestation verify nety-linux-x86_64 --repo {repo}
```

The attestation proves the file was built by this repository's `Release` workflow from tag `{tag}`.

## Changes

{changes}

**Full Changelog**: {compare}
"""


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def previous_tag(tag):
    """The closest v* tag before `tag`, or None for the first release."""
    try:
        return git("describe", "--tags", "--abbrev=0", "--match", "v*", f"{tag}^").strip()
    except subprocess.CalledProcessError:
        return None


def changes(commits):
    """Markdown list of `commits` ((hash, subject) pairs), grouped by type."""
    groups = {}
    for sha, subject in commits:
        if SKIP.match(subject):
            continue
        m = SUBJECT.match(subject)
        if m:
            text = m["text"][0].upper() + m["text"][1:]
            if m["breaking"]:
                text = f"**Breaking:** {text}"
            section = SECTIONS.get(m["type"], "Other changes")
        else:
            text, section = subject, "Other changes"
        groups.setdefault(section, []).append(f"- {text} ({sha})")
    order = [*SECTIONS.values(), "Other changes"]
    blocks = [f"### {name}\n\n" + "\n".join(groups[name]) for name in order if name in groups]
    return "\n\n".join(blocks) or "No user-facing changes."


def notes(tag):
    prev = previous_tag(tag)
    log = git("log", "--no-merges", "--format=%h %s", f"{prev}..{tag}" if prev else tag)
    commits = [line.split(" ", 1) for line in log.splitlines()]
    compare = (f"https://github.com/{REPO}/compare/{prev}...{tag}" if prev
               else f"https://github.com/{REPO}/commits/{tag}")
    return TEMPLATE.format(repo=REPO, tag=tag, changes=changes(commits), compare=compare)


if __name__ == "__main__":
    sys.stdout.write(notes(sys.argv[1]))
