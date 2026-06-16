"""Shared Playwright browser helpers (avoids circular imports with scrapers)."""

from __future__ import annotations

import logging
import sys

from playwright.async_api import BrowserContext, Page, Playwright

logger = logging.getLogger(__name__)

POPUP_CLOSE_SELECTORS = [
    'button[aria-label="Close"]',
    'button[aria-label="close"]',
    'button[aria-label="Dismiss"]',
    'button[aria-label="Dismiss dialog"]',
    '[aria-label="Close"]',
    '[aria-label="Dismiss"]',
    '[data-dismiss="modal"]',
    '[data-testid="close"]',
    '[data-testid="close-button"]',
    "button.close",
    ".modal-close",
    ".popup-close",
    ".dialog-close",
    ".fancybox-close",
    '[role="dialog"] button[aria-label*="close" i]',
    '[role="dialog"] button[aria-label*="dismiss" i]',
    'button:has-text("×")',
    'button:has-text("✕")',
    'button:has-text("Close")',
    'button:has-text("No thanks")',
    'button:has-text("No Thanks")',
    'button:has-text("Not now")',
    'button:has-text("Not Now")',
    'button:has-text("Maybe later")',
    'button:has-text("Skip")',
    'button:has-text("Continue without accepting")',
    'button:has-text("Decline")',
    'a.close',
    'a.popup-close',
    'button:has-text("Accept")',
    'button:has-text("Accept all")',
    'button:has-text("Accept All")',
    'button:has-text("I agree")',
    'button:has-text("I Agree")',
    'button:has-text("Got it")',
    'button:has-text("OK")',
    'button:has-text("Allow all")',
    'button:has-text("Allow All")',
]


async def launch_context(playwright: Playwright, browser: str) -> tuple[BrowserContext, str]:
    label = browser.lower()
    viewport = {"width": 1366, "height": 900}
    launch_args = ["--disable-blink-features=AutomationControlled"]

    if label in ("auto", "chromium", "chrome"):
        try:
            browser_obj = await playwright.chromium.launch(
                headless=False,
                channel="chrome",
                args=launch_args,
            )
            context = await browser_obj.new_context(viewport=viewport)
            return context, "chrome"
        except Exception as exc:
            logger.warning("Chrome launch failed (%s), trying Chromium.", exc)
            try:
                browser_obj = await playwright.chromium.launch(
                    headless=False,
                    args=launch_args,
                )
                context = await browser_obj.new_context(viewport=viewport)
                return context, "chromium"
            except Exception as exc2:
                logger.warning("Chromium launch failed (%s), trying Firefox.", exc2)

    browser_obj = await playwright.firefox.launch(headless=False)
    context = await browser_obj.new_context(viewport=viewport)
    return context, "firefox"


async def dismiss_page_obstructions(page: Page, *, max_rounds: int = 4) -> int:
    """Close landing-page pop-ups, modals, and cookie banners."""
    closed = 0
    for _ in range(max_rounds):
        closed_this_round = False

        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(250)
        except Exception:
            pass

        for sel in POPUP_CLOSE_SELECTORS:
            try:
                btn = page.locator(sel).first
                if not await btn.is_visible(timeout=600):
                    continue
                await btn.click(timeout=2500)
                await page.wait_for_timeout(400)
                closed += 1
                closed_this_round = True
                logger.info("  Closed pop-up/obstruction: %s", sel)
                break
            except Exception:
                continue

        if not closed_this_round:
            break

    return closed


def scrape_complete_beep() -> None:
    """Short audible cue when one scrape unit finishes (company site or CA profile)."""
    try:
        if sys.platform == "win32":
            import winsound

            winsound.Beep(880, 150)
        else:
            sys.stdout.write("\a")
            sys.stdout.flush()
    except Exception:
        pass


def pipeline_complete_beep() -> None:
    """Longer audible cue when a full pipeline command finishes."""
    try:
        if sys.platform == "win32":
            import winsound

            winsound.Beep(523, 800)
            winsound.Beep(659, 1000)
        else:
            sys.stdout.write("\a\a")
            sys.stdout.flush()
    except Exception:
        pass


def pipeline_complete_voice(message: str) -> None:
    """Speak a short completion message (Windows SAPI; no extra pip packages)."""
    text = (message or "").strip()
    if not text:
        return
    try:
        if sys.platform == "win32":
            import subprocess

            safe = text.replace("'", "''")
            ps = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$s.Speak('{safe}')"
            )
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                check=False,
                timeout=60,
                creationflags=flags,
            )
        else:
            import shutil
            import subprocess as sp

            if shutil.which("espeak"):
                sp.run(["espeak", text], check=False, timeout=60)
            elif shutil.which("say"):
                sp.run(["say", text], check=False, timeout=60)
    except Exception:
        pass
