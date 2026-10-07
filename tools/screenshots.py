#!/usr/bin/env python3
"""Capture README screenshots + hero GIF of a real mock run (headless chromium).

Assumes the server is running (make serve). Drives the real page: waits for
the city, triggers a mock run through the real API, and captures frames at
the scene beats. The GIF is assembled from the captured frames.

Output: docs/screenshots/{idle,smoke,repair,dawn}.png + docs/hero.gif
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from PIL import Image
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8808"
OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"


async def shot(page, path):
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(150)
    await page.screenshot(path=str(path), clip={"x": 0, "y": 0, "width": 980, "height": 620})
    print(f"captured {path.name}")


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    frames: list[Path] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 980, "height": 620})
        page.on("console", lambda m: print(f"[page {time.monotonic()-start if 'start' in dir() else 0:.1f}s] {m.text[:140]}", flush=True))
        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_timeout(2500)
        await shot(page, OUT / "idle.png")

        # trigger a real mock run through the page's own API path
        async with page.expect_response(lambda r: "/api/run" in r.url) as resp_info:
            await page.click("#runBtn")
        resp = await resp_info.value
        if resp.status != 200:
            raise SystemExit(f"/api/run returned {resp.status}: {await resp.text()} — "
                             "restart the server (fresh cooldown) and re-run")
        # capture the story beats by watching the page state
        beats = {"smoke": None, "repair": None, "dawn": None, "resolved": None}
        start = time.monotonic()
        burst_dir = OUT / "burst"
        burst_dir.mkdir(exist_ok=True)
        i = 0
        while time.monotonic() - start < 180:
            state = await page.evaluate("""() => window.state ? ({
                smoke: window.state.smokeOn,
                skyT: window.state.skyT,
                repair: window.state.repair.state,
                banner: window.state.banner ? window.state.banner.kind : null,
                resolvedShown: !document.getElementById('resolveBanner').classList.contains('hidden'),
            }) : {}""")
            i += 1
            fp = burst_dir / f"f{i:03d}.png"
            await shot(page, fp)
            frames.append(fp)
            print(f"  t={time.monotonic()-start:5.1f}s smoke={state['smoke']} "
                  f"repair={state['repair']} banner={state['banner']} skyT={state['skyT']:.2f}",
                  flush=True)
            if state["smoke"] and beats["smoke"] is None:
                beats["smoke"] = fp
                await shot(page, OUT / "smoke.png")
            if state["repair"] in ("walking", "hammering") and beats["repair"] is None:
                beats["repair"] = fp
                await shot(page, OUT / "repair.png")
            if state["resolvedShown"] and beats["resolved"] is None:
                await page.wait_for_timeout(4000)  # let dawn progress
                await shot(page, OUT / "dawn.png")
                beats["dawn"] = OUT / "dawn.png"
                break
            await page.wait_for_timeout(1200)
        await browser.close()

    # hero GIF: decimate the burst to <=40 frames, 12fps
    picked = frames[:: max(1, len(frames) // 40)]
    imgs = [Image.open(f).convert("P", palette=Image.ADAPTIVE).resize((640, 405))
            for f in picked]
    if imgs:
        imgs[0].save(OUT.parent / "hero.gif", save_all=True, append_images=imgs[1:],
                     duration=380, loop=0)
        print(f"hero.gif: {len(imgs)} frames")
    print("beats captured:", {k: str(v) for k, v in beats.items()})


if __name__ == "__main__":
    asyncio.run(main())
