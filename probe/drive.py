"""Drive the real browser, and read back what is on the screen.

`ui/` has no test runner. A green `./check.sh` means the types agree and the
bundle builds; it has never meant that a feature works, and twice now a change
has shipped clean and done nothing at all. This is the answer: open the page
Galley really serves, click what a person would click, and check what is drawn.

Run it with `./probe/run.sh`. It wants a Chromium — Playwright's is fine — and
it never touches the real paper; `fixture.py` builds a throwaway project for it.

Each check is one sentence about what you should see. When one fails, the
sentence is the bug report.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

import websockets

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixture  # noqa: E402  — the probe and the fixture agree on the sentences

CHROME = os.environ.get(
    "GALLEY_PROBE_CHROME",
    os.path.expanduser("~/.cache/ms-playwright/chromium-1117/chrome-linux/chrome"),
)
PORT = int(os.environ.get("GALLEY_PROBE_CDP", "9223"))
APP = os.environ.get("GALLEY_PROBE_APP", "http://127.0.0.1:8127/")
HERE = os.environ.get("GALLEY_PROBE_DIR", "/tmp/galley-probe")

fails: list[str] = []
#: The worktree the seeded session works in, so a check can look inside it.
REVIEW_TREE = None

def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f"  <- {detail}"))
    if not ok:
        fails.append(name)

class Page:
    def __init__(self, ws):
        self.ws, self.n, self.noise = ws, 0, []

    def _note(self, msg):
        method = msg.get("method")
        if method == "Runtime.exceptionThrown":
            self.noise.append(msg["params"]["exceptionDetails"].get("text", "exception"))
        elif method == "Runtime.consoleAPICalled" and msg["params"]["type"] in ("error", "warning"):
            args = " ".join(str(a.get("value", a.get("description", ""))) for a in msg["params"]["args"])
            self.noise.append(f'{msg["params"]["type"]}: {args}')

    async def send(self, method, **params):
        self.n += 1
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            self._note(msg)
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    async def js(self, expr):
        out = await self.send(
            "Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True
        )
        if "exceptionDetails" in out:
            raise RuntimeError(out["exceptionDetails"].get("text"), expr[:80])
        return out["result"].get("value")

    async def until(self, expr, seconds=10):
        end = time.time() + seconds
        while time.time() < end:
            if await self.js(expr):
                return True
            await asyncio.sleep(0.1)
        return False

    async def open_file(self, path):
        await self.js(f'document.querySelector(\'[data-path="{path}"]\').click()')
        await self.until(f'document.querySelector(".editor-bar .path")?.title === "{path}"')
        await asyncio.sleep(0.4)

    async def click_at(self, selector, fx=0.5, fy=0.5):
        """A real click at a point inside an element, in page coordinates.

        Not `element.click()`: *where* in the sentence you clicked is the thing
        under test, and a synthetic click carries no coordinates."""
        spot = await self.js(
            "(() => { const r = document.querySelector(%r)?.getBoundingClientRect();"
            " return r ? {x: r.left + r.width * %s, y: r.top + r.height * %s} : null })()"
            % (selector, fx, fy)
        )
        if not spot:
            return False
        for kind in ("mousePressed", "mouseReleased"):
            await self.send("Input.dispatchMouseEvent", type=kind, x=spot["x"], y=spot["y"],
                            button="left", clickCount=1)
        await asyncio.sleep(0.25)
        return True

    async def chord(self, key, code, vk, modifiers=0):
        for kind in ("keyDown", "keyUp"):
            await self.send("Input.dispatchKeyEvent", type=kind, key=key, code=code,
                            windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk,
                            modifiers=modifiers)
        await asyncio.sleep(0.35)

    async def ctrl_s(self):
        await self.chord("s", "KeyS", 83, modifiers=2)

    async def shot(self, name):
        out = await self.send("Page.captureScreenshot", format="png")
        open(f"{HERE}/{name}.png", "wb").write(base64.b64decode(out["data"]))


def seed_review():
    """A session with changes in it, made without starting an agent.

    The review pane is the surface that matters most and the one nothing could
    reach: putting a diff in front of the browser used to mean paying for a
    turn. `start: false` builds the worktree and stops there; the commit below
    is the one the turn would have ended with, so the pane cannot tell the
    difference. Returns the worktree path, or None if the server refused.
    """
    body = json.dumps({"prompt": "probe: rewrite two sentences", "start": False}).encode()
    request = urllib.request.Request(
        f"{APP}api/sessions", body, {"Content-Type": "application/json"}
    )
    row = json.load(urllib.request.urlopen(request))
    tree = row["worktree_path"]
    target = f"{tree}/main.tex"
    with open(target, encoding="utf-8") as handle:
        text = handle.read()
    for was, now in fixture.PROPOSED:
        assert was in text, was
        text = text.replace(was, now)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(text)
    subprocess.run(
        ["git", "-C", tree, "-c", "user.email=probe@galley", "-c", "user.name=probe",
         "commit", "-qam", "probe: proposed changes"],
        check=True,
    )
    return tree


async def main():
    # A fresh profile every run. The bundle is content-hashed but index.html is
    # not, so a kept cache serves the last build and the run silently checks
    # code that is no longer there — which cost an afternoon once already.
    shutil.rmtree(f"{HERE}/chrome", ignore_errors=True)
    # Before the page loads, so the session is in the first list it fetches.
    global REVIEW_TREE
    REVIEW_TREE = seed_review()
    print(f"seeded a session to review: {REVIEW_TREE}")
    chrome = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={PORT}", "--no-sandbox",
         "--disable-gpu", "--window-size=1600,1000", f"--user-data-dir={HERE}/chrome", APP],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1)
                break
            except Exception:
                await asyncio.sleep(0.3)
        target = None
        for _ in range(60):
            pages = [t for t in json.load(urllib.request.urlopen(
                f"http://127.0.0.1:{PORT}/json/list")) if t["type"] == "page"]
            if pages:
                target = pages[0]
                break
            await asyncio.sleep(0.3)
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as ws:
            page = Page(ws)
            await page.send("Runtime.enable")
            await page.send("Page.enable")
            await run(page)
    finally:
        chrome.terminate()
    print("\n" + ("ALL GREEN" if not fails else f"{len(fails)} FAILED: " + ", ".join(fails)))
    return 1 if fails else 0


async def run(page):
    check("the app loads", await page.until('!!document.querySelector(".ide")', 20))
    check("the rail lists the project",
          await page.until('!!document.querySelector(\'[data-path="README.md"]\')'))

    print("\n-- markdown --")
    await page.open_file("README.md")
    check("a preview pane appears", await page.until('!!document.querySelector(".preview-pane")'))
    check("it says which kind", await page.js('document.querySelector(".preview-pane .chip")?.textContent') == "Markdown")
    check("the heading is drawn", await page.js('document.querySelector(".md h1")?.textContent') == "Probe paper")
    check("bold survives", await page.js('!!document.querySelector(".md strong")'))
    check("the table has both rows",
          await page.js('document.querySelectorAll(".md tbody tr").length') == 2)
    check("numbers are right-aligned",
          await page.js('getComputedStyle(document.querySelector(".md tbody td:last-child")).textAlign') == "right")
    check("the code block is drawn",
          "loss_mask" in (await page.js('document.querySelector(".md pre.md-code")?.textContent') or ""))
    check("a task list gets checkboxes",
          await page.js('document.querySelectorAll(".md li.md-task input").length') == 2)
    check("a task sits on one line with its box",
          await page.js('(()=>{const li=document.querySelector(".md li.md-task");'
                        'const box=li.querySelector("input").getBoundingClientRect();'
                        'const label=li.querySelector("span").getBoundingClientRect();'
                        'return Math.abs(box.top - label.top) < 8 && box.right <= label.left + 1'
                        ' && box.width < 30})()'))
    check("a plain list item is one line too",
          await page.js('document.querySelectorAll(".md ul li > p").length') == 0)
    check("the ticked one is ticked",
          await page.js('[...document.querySelectorAll(".md li.md-task input")].map(i=>i.checked).join()') == "false,true")
    src = await page.js('document.querySelector(".md img")?.getAttribute("src")')
    check("a repo figure is served by Galley", (src or "").startswith("/api/blob"), src)
    check("the figure actually loads",
          await page.until('document.querySelector(".md img")?.naturalWidth > 0'))
    check("an outside link stays a link",
          await page.js('document.querySelector(\'.md a[href^="https"]\')?.target') == "_blank")

    print("\n-- markdown cannot run --")
    check("no script from the file ran", await page.js('window.__pwned === undefined'))
    check("raw HTML is shown as text",
          "<script>" in (await page.js('document.querySelector(".md .md-html")?.textContent') or ""))
    check("no script element was created",
          await page.js('document.querySelectorAll(".md script").length') == 0)
    check("a javascript: link is not a link",
          await page.js('!!document.querySelector(\'.md a[href^="javascript"]\')') is False)

    print("\n-- the preview follows what you type --")
    await page.js('document.querySelector(".cm-content").focus()')
    await page.send("Input.insertText", text="ZZTOPMARKER ")
    ok = await page.until('document.querySelector(".md")?.textContent.includes("ZZTOPMARKER")', 5)
    check("typing reaches the preview", ok)
    check("the pane says it is live",
          "live" in (await page.js('document.querySelector(".preview-pane .pdf-bar")?.textContent') or ""))
    # Put it back so the file on disk is never dirty for the next checks.
    for _ in range(12):
        await page.send("Input.dispatchKeyEvent", type="keyDown", windowsVirtualKeyCode=8, nativeVirtualKeyCode=8, key="Backspace")
        await page.send("Input.dispatchKeyEvent", type="keyUp", windowsVirtualKeyCode=8, nativeVirtualKeyCode=8, key="Backspace")
    await asyncio.sleep(0.4)

    print("\n-- a link inside the preview opens the file --")
    await page.js('[...document.querySelectorAll(".md button.md-link")].find(b=>b.title==="notes.md").click()')
    opened = await page.until('document.querySelector(".editor-bar .path")?.title === "notes.md"', 5)
    check("a relative link opens that file", opened)
    check("and the preview follows it",
          await page.until('document.querySelector(".md h1")?.textContent === "Notes"', 5))

    print("\n-- json --")
    await page.open_file("config.json")
    check("the JSON pane appears", await page.until('!!document.querySelector(".json-view")'))
    check("keys are coloured apart from values",
          await page.js('document.querySelectorAll(".json-view .j-key").length') >= 8)
    check("it is indented, not one line",
          (await page.js('document.querySelector(".json-view pre")?.textContent') or "").count("\n") > 8)
    check("null keeps its own colour",
          await page.js('!!document.querySelector(".json-view .j-null")'))

    print("\n-- tables --")
    await page.open_file("table.csv")
    check("a csv is a table", await page.until('!!document.querySelector(".table-view table")'))
    check("a quoted comma stays one cell",
          await page.js('document.querySelector(".table-view tbody tr td:last-child")?.textContent') == "a value, quoted")
    check("the footer counts the rows",
          "2 of 2 rows" in (await page.js('document.querySelector(".table-foot")?.textContent') or ""))
    await page.open_file("cells.tsv")
    check("a tsv is a table too",
          await page.js('document.querySelectorAll(".table-view tbody tr").length') == 2)

    print("\n-- files the editor used to refuse --")
    await page.open_file("helper.ts")
    check("a .ts opens as text",
          "helper" in (await page.js('document.querySelector(".cm-content")?.textContent') or ""))
    check("and is editable",
          await page.js('document.querySelector(".editor-bar")?.textContent.includes("read-only")') is False)
    check("no preview for it",
          await page.js('!!document.querySelector(".preview-pane")') is False)

    await page.open_file("old.bib")
    check("a non-UTF-8 file is readable",
          "@book" in (await page.js('document.querySelector(".cm-content")?.textContent') or ""))
    check("and locked, with the reason",
          "read-only · not UTF-8" in (await page.js('document.querySelector(".editor-bar")?.textContent') or ""))

    await page.open_file("big.log")
    check("a huge file shows its front",
          "read-only · first part only" in (await page.js('document.querySelector(".editor-bar")?.textContent') or ""))

    await page.open_file("blob.txt")
    check("a payload is not decoded",
          "read-only · not text" in (await page.js('document.querySelector(".editor-bar")?.textContent') or ""))
    check("and is not handed to the renderer",
          await page.js('document.querySelectorAll(".binary-view iframe").length') == 0)
    check("it says so in words instead",
          "25 bytes" in (await page.js('document.querySelector(".binary-view")?.textContent') or ""))

    await page.open_file("figures/plot.png")
    check("a picture is still drawn",
          await page.until('document.querySelector(".binary-view img")?.naturalWidth > 0', 5))

    print("\n-- back to the paper --")
    await page.open_file("main.tex")
    check("a .tex leaves the PDF alone",
          await page.js('!!document.querySelector(".preview-pane")') is False)
    check("the PDF pane is still there",
          await page.js('!!document.querySelector(".pdf-pane")'))

    await page.open_file("README.md")
    await page.until('!!document.querySelector(".preview-pane")')
    await asyncio.sleep(0.6)
    await page.shot("readme")
    await page.open_file("config.json")
    await asyncio.sleep(0.4)
    await page.shot("json")
    await page.open_file("table.csv")
    await asyncio.sleep(0.4)
    await page.shot("csv")

    await page.open_file("README.md")
    await page.until('!!document.querySelector(".preview-pane")')
    await page.js('[...document.querySelectorAll(".preview-pane button")].find(b=>b.textContent.includes("PDF")).click()')
    check("PDF › puts the preview away",
          await page.until('!!document.querySelector(".preview-pane") === false', 5))
    await page.open_file("notes.md")
    check("and the next file still previews",
          await page.until('!!document.querySelector(".preview-pane")', 5))

    print("\n-- Ctrl-S from outside the text --")
    await page.open_file("helper.ts")
    await page.js('document.querySelector(".cm-content").focus()')
    await page.send("Input.insertText", text="// probe\n")
    check("typing makes the file unsaved",
          await page.until('!!document.querySelector(".editor-bar .badge.amber")', 5))
    # Stand somewhere else, the way you do after clicking a file or the PDF.
    await page.js('document.querySelector(".rail")?.click(); document.activeElement.blur()')
    check("focus really left the editor",
          await page.js('document.activeElement?.classList.contains("cm-content")') is not True)
    await page.ctrl_s()
    check("Ctrl S still saves",
          await page.until('!document.querySelector(".editor-bar .badge.amber")', 5))
    check("and the bar says it saved",
          "saved" in (await page.js('document.querySelector(".editor-bar")?.textContent') or ""))

    # The agent's house style comes from the project, not from Galley: its cwd
    # is this worktree, so a CLAUDE.md at the project root is read every turn
    # and Galley stays project-agnostic. Getting the file there is Galley's job.
    check("the project's own CLAUDE.md reaches the agent's worktree",
          os.path.isfile(f"{REVIEW_TREE}/CLAUDE.md"), f"{REVIEW_TREE}/CLAUDE.md")

    print("\n-- the review pane: rewrite in place, Claude's still beside you --")
    await page.js('document.querySelector(".session")?.click()')
    clicked = await page.until(
        '(() => { const b = [...document.querySelectorAll(".tabs button")]'
        '.find(b => b.textContent.startsWith("Review")); if (!b || b.disabled) return false;'
        ' b.click(); return true })()', 8)
    check("the review tab opens", clicked)
    check("both changes are listed",
          await page.until('document.querySelectorAll(".drow.change").length === 2', 8))
    check("your sentence and Claude's are side by side",
          await page.js('!!document.querySelector(".drow.change .cell.old") '
                        '&& !!document.querySelector(".drow.change .cell.new")'))

    # Reading a sentence more closely is not a decision. This was a real
    # regression the moment a single click opened the box: a stray click ticked
    # a change off the counter without a word being typed.
    await page.click_at(".drow.change .cell.old .txt", fx=0.5, fy=0.5)
    check("clicking in opens a box",
          await page.until('!!document.querySelector(".drow.change.editing textarea")', 5))
    await page.chord("Escape", "Escape", 27)
    check("leaving it untouched answers nothing",
          await page.js('document.querySelectorAll(".drow.change.rewritten").length') == 0)
    check("and both changes are still to go",
          "2 to go" in (await page.js('document.querySelector(".where")?.textContent') or ""),
          await page.js('document.querySelector(".where")?.textContent'))

    # Click near the start of your own sentence, the way you would to change a
    # word at the front of it.
    await page.click_at(".drow.change .cell.old .txt", fx=0.08, fy=0.5)
    check("clicking your sentence opens a box in that same cell",
          await page.until('document.querySelectorAll(".drow.change.editing .cell.old.writing textarea").length === 1', 5))
    check("the row keeps its three columns",
          await page.js('document.querySelector(".drow.change.editing")?.children.length') == 3)
    check("Claude's wording is still on screen beside it",
          (await page.js('document.querySelector(".drow.change.editing .cell.new")?.textContent') or "")
          .find("Acceptance is decided") >= 0)
    caret = await page.js('document.querySelector(".drow.change.editing textarea")?.selectionStart')
    length = await page.js('document.querySelector(".drow.change.editing textarea")?.value.length')
    check("the caret lands where you clicked, not at the end",
          isinstance(caret, int) and isinstance(length, int) and caret < length / 2,
          f"caret {caret} of {length}")
    check("the box has your wording in it, not Claude's",
          (await page.js('document.querySelector(".drow.change.editing textarea")?.value') or "")
          .startswith("Acceptance is therefore"))
    check("and no scrollbar hides half the comparison",
          await page.js('(() => { const t = document.querySelector(".drow.change.editing textarea");'
                        ' return t ? t.scrollHeight <= t.clientHeight + 2 : false })()'))
    await page.shot("review-editing")

    await page.send("Input.insertText", text="Kernel a")
    check("typing lands at the caret",
          (await page.js('document.querySelector(".drow.change.editing textarea")?.value') or "")
          .startswith("Kernel aAcceptance"))
    check("and marks the change as yours, rewritten",
          await page.until('!!document.querySelector(".drow.change.rewritten")', 5))

    await page.ctrl_s()
    check("Ctrl S inside the box shows the save plan, not the browser's",
          await page.until('!!document.querySelector(".save-plan")', 5))
    check("the plan names the file it would write",
          "main.tex" in (await page.js('document.querySelector(".save-plan")?.textContent') or ""))
    await page.js('[...document.querySelectorAll(".save-plan button")].find(b=>b.textContent.startsWith("Write")).click()')
    check("Save writes it",
          await page.until('!!document.querySelector(".save-plan") === false', 8))

    await page.open_file("main.tex")
    check("and the rewrite is really in the file now",
          "Kernel aAcceptance" in (await page.js('document.querySelector(".cm-content")?.textContent') or ""))

    print("\n-- a build that fails, and the button that hands it to Claude --")
    if shutil.which("latexmk") is None:
        print("  skip  latexmk is not installed, so there is no build to fail")
    else:
        await run_a_failing_build(page)

    check("nothing complained in the console", not page.noise, "; ".join(page.noise[:4]))


async def run_a_failing_build(page):
    """Break the paper, compile it, and check what the pane offers.

    The button is never pressed. Pressing it starts a real agent turn on a real
    model, and nothing else in this probe spends money; what can be checked for
    free is that the button appears exactly when there is something to send,
    and that the words behind it name the line that broke.
    """
    paper = f"{HERE}/project/paper/main.tex"
    with open(paper, encoding="utf-8") as handle:
        whole = handle.read()
    broken = whole.replace(
        "We train on the serialisation of the term itself.",
        "We train on the \\thisCommandDoesNotExist of the term itself.",
    )
    assert broken != whole, "the fixture sentence moved"
    line = next(
        n for n, text in enumerate(broken.splitlines(), 1) if "thisCommandDoesNotExist" in text
    )
    with open(paper, "w", encoding="utf-8") as handle:
        handle.write(broken)

    async def recompile():
        return await page.js(
            '(() => { const b = [...document.querySelectorAll(".pdf-bar button")]'
            '.find(b => b.textContent.startsWith("Recompile"));'
            ' if (!b || b.disabled) return false; b.click(); return true })()'
        )

    check("Recompile runs", await recompile())
    check("the failure is reported",
          await page.until('!!document.querySelector(".pdf-problems.failed")', 90))
    listed = await page.js('document.querySelector(".pdf-problem-list")?.textContent') or ""
    check("the error names the line that broke", f"main.tex:{line}" in listed, listed[:120])
    check("and the pane offers to hand it to Claude",
          await page.until('!!document.querySelector(".ask-fix")', 5))
    check("the button is ready to press",
          await page.js('document.querySelector(".ask-fix")?.disabled') is False)
    check("the count is not inflated by latexmk's own verdict",
          "1 error" in (await page.js('document.querySelector(".pdf-problems-head")?.textContent') or ""),
          await page.js('document.querySelector(".pdf-problems-head")?.textContent'))
    await page.shot("compile-failed")

    asked = json.load(urllib.request.urlopen(f"{APP}api/compile")).get("fix_prompt") or ""
    check("the request names the file and the line", f"main.tex:{line}" in asked, asked[:160])
    check("it carries the log as well", "The end of the log" in asked)
    check("and tells Claude not to reword the paper",
          "do not reword, reformat or rewrap anything you are not fixing" in asked)

    with open(paper, "w", encoding="utf-8") as handle:
        handle.write(whole)
    check("Recompile runs again", await recompile())
    check("a paper that builds stops asking",
          await page.until('!document.querySelector(".ask-fix")', 90))
    check("and nothing is left marked failed",
          await page.js('!document.querySelector(".pdf-problems.failed")'))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
