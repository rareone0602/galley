"""Build a throwaway project for the browser probe to open.

Never the real paper. The probe types into files, clicks things that write,
and leaves a server running on them; FLM has a dozen uncommitted paragraphs in
it at any moment and is not somewhere to find that out.

Every file here exists to answer one question about the rail and the editor,
and the question is in the comment beside it.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import sys
from pathlib import Path

README = """# Probe paper

A paragraph with **bold**, *italic*, `inline code` and a [link out](https://example.com).

## A list

- first item
- second item, with a [link to notes](notes.md)
- [ ] an unticked task
- [x] a ticked one

## A table

| cell | tokens | loss |
| --- | ---: | ---: |
| N9p1 | 1409 | 4.887 |
| N54  | 1345 | 6.708 |

## Code

```python
def loss_mask(x):
    return x != PAD
```

## A figure

![the plot](figures/plot.png)

## Raw HTML, which must not run

<script>window.__pwned = true</script>

And an [unsafe link](javascript:window.__pwned2=true).

> A blockquote, for good measure.
"""

CONFIG = """[paths]
paper_repo = "{paper}"
state_dir  = "{state}"

[paper]
main_branch = "master"
main_tex    = "main.tex"

[server]
bind = "127.0.0.1"
port = {port}

[usage]
enabled = true
"""

#: A build input that is not the prose. The probe saves this one to check that
#: a save rebuilds the paper — editing `main.tex` would put the probe's own
#: line into the sentence the merge pane is about to show.
PREAMBLE = """\\providecommand{\\probenote}[1]{}
"""

PAPER = """\\documentclass{article}
\\input{preamble}
\\begin{document}
The kernel either accepts a candidate as a proof of the statement or it does not.
Acceptance is therefore decided rather than scored.
We train on the serialisation of the term itself.
That serialisation is injective, so a distribution over strings is a distribution over terms.
\\end{document}
"""

#: What a turn would have written, for the review pane. Two sentences reworded
#: and two left exactly as they were, so the pane has both to show.
PROPOSED = [
    ("Acceptance is therefore decided rather than scored.",
     "Acceptance is decided, not scored."),
    ("That serialisation is injective, so a distribution over strings is a distribution over terms.",
     "The serialisation is injective, so a distribution over strings is one over terms."),
]

#: 16x16, so that "did the figure load" is answered by a real decode.
PLOT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAKklEQVR42mNk"
    "YPhfz0AEYBxVSF+FjAxQwMjIyMAwqnBUIX0VMjLQTyEAxRIH/8b7YzsAAAAASUVORK5CYII="
)


def build(root: Path, port: int) -> Path:
    """Make the project and its config under `root`. Returns the config path."""
    shutil.rmtree(root, ignore_errors=True)
    paper = root / "paper"
    (paper / "figures").mkdir(parents=True)

    # Prose, one sentence to a line, because the merge pane reads sentences and
    # the probe seeds a session that rewrites two of them.
    (paper / "main.tex").write_text(PAPER)
    (paper / "preamble.tex").write_text(PREAMBLE)
    (paper / "README.md").write_text(README)
    # House style for whoever writes here, agent included. Galley never reads
    # it; the agent does, because its cwd is a worktree of this project. The
    # probe checks it arrives, which is the part Galley is responsible for.
    (paper / "CLAUDE.md").write_text(
        "# CLAUDE.md — the probe project\n\nOne sentence per line in every .tex file.\n"
    )
    (paper / "notes.md").write_text("# Notes\nReached by following a link from the README.\n")
    # A config, for the JSON pane: nested, with a null and a false in it.
    (paper / "config.json").write_text(
        '{"agent": {"model": "opus", "effort": "xhigh", "fan_out_depth": 2, "stream": true},\n'
        ' "paths": {"paper_repo": "/x", "code_mirror": null},\n'
        ' "limits": {"max_concurrent_sessions": 2, "budgets": [1.5, 2, null, false]}}\n'
    )
    # A quoted comma, which is the whole reason the CSV parser is not a split.
    (paper / "table.csv").write_text(
        'name,value,note\nalpha,0.418,"a value, quoted"\nbeta,-1.159,plain\n'
    )
    (paper / "cells.tsv").write_text(
        "cell\tsteps\tloss\nN9p1_D1409\t1345\t4.887\nN54_D1409\t1345\t6.708\n"
    )
    # An extension the old allowlist had never heard of.
    (paper / "helper.ts").write_text("export const helper = (x: number) => x + 1\n")
    # Latin-1: readable, and ruinous to save back as UTF-8.
    (paper / "old.bib").write_bytes("@book{a, author = {Grüß}, title = {Ein Buch}}\n".encode("latin-1"))
    # NULs in the first few bytes: not text, whatever the extension says.
    (paper / "blob.txt").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00binary payload")
    # Past the two-megabyte ceiling, so only the front of it is read.
    (paper / "big.log").write_text(("x" * 79 + "\n") * 40_000)
    (paper / "figures" / "plot.png").write_bytes(PLOT_PNG)

    git = ("git", "-C", str(paper))
    subprocess.run([*git, "init", "-q", "-b", "master"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run(
        [*git, "-c", "user.email=probe@galley", "-c", "user.name=probe",
         "commit", "-qm", "the probe project"],
        check=True,
    )

    config = root / "galley.toml"
    config.write_text(CONFIG.format(paper=paper, state=root / "state", port=port))
    return config


if __name__ == "__main__":
    print(build(Path(sys.argv[1]), int(sys.argv[2])))
