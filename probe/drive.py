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

CHROME = os.environ.get(
    "GALLEY_PROBE_CHROME",
    os.path.expanduser("~/.cache/ms-playwright/chromium-1117/chrome-linux/chrome"),
)
PORT = int(os.environ.get("GALLEY_PROBE_CDP", "9223"))
APP = os.environ.get("GALLEY_PROBE_APP", "http://127.0.0.1:8127/")
HERE = os.environ.get("GALLEY_PROBE_DIR", "/tmp/galley-probe")

fails: list[str] = []

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

    async def shot(self, name):
        out = await self.send("Page.captureScreenshot", format="png")
        open(f"{HERE}/{name}.png", "wb").write(base64.b64decode(out["data"]))


async def main():
    # A fresh profile every run. The bundle is content-hashed but index.html is
    # not, so a kept cache serves the last build and the run silently checks
    # code that is no longer there — which cost an afternoon once already.
    shutil.rmtree(f"{HERE}/chrome", ignore_errors=True)
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

    check("nothing complained in the console", not page.noise, "; ".join(page.noise[:4]))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
