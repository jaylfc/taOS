"""Take mobile screenshots of the desktop shell for UI review."""
import sys
import time
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:6969"
OUT = "/tmp/mobile-screenshots"

# iPhone 14 Pro viewport
MOBILE = {"width": 393, "height": 852}

def main():
    import os
    os.makedirs(OUT, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport=MOBILE,
            device_scale_factor=3,
            is_mobile=True,
            has_touch=True,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        )
        page = ctx.new_page()

        # 1. Home screen
        page.goto(f"{BASE}/desktop", wait_until="networkidle")
        time.sleep(2)
        page.screenshot(path=f"{OUT}/01-home.png", full_page=False)
        print(f"[1/8] Home screen saved")

        # 2. Open Messages app
        msgs_btn = page.locator("button", has_text="Messages").first
        if msgs_btn.is_visible():
            msgs_btn.click()
            time.sleep(1.5)
            page.screenshot(path=f"{OUT}/02-messages.png", full_page=False)
            print(f"[2/8] Messages app saved")
            # Go back
            back = page.locator("button", has_text="Back").first
            if back.is_visible():
                back.click()
                time.sleep(0.5)

        # 3. Open Settings
        settings_btn = page.locator("button", has_text="Settings").first
        if settings_btn.is_visible():
            settings_btn.click()
            time.sleep(1.5)
            page.screenshot(path=f"{OUT}/03-settings.png", full_page=False)
            print(f"[3/8] Settings saved")
            back = page.locator("button", has_text="Back").first
            if back.is_visible():
                back.click()
                time.sleep(0.5)

        # 4. Open Store
        store_btn = page.locator("button", has_text="Store").first
        if store_btn.is_visible():
            store_btn.click()
            time.sleep(1.5)
            page.screenshot(path=f"{OUT}/04-store.png", full_page=False)
            print(f"[4/8] Store saved")
            back = page.locator("button", has_text="Back").first
            if back.is_visible():
                back.click()
                time.sleep(0.5)

        # 5. Open Calculator
        calc_btn = page.locator("button", has_text="Calculator").first
        if calc_btn.is_visible():
            calc_btn.click()
            time.sleep(1.5)
            page.screenshot(path=f"{OUT}/05-calculator.png", full_page=False)
            print(f"[5/8] Calculator saved")
            back = page.locator("button", has_text="Back").first
            if back.is_visible():
                back.click()
                time.sleep(0.5)

        # 6. Open Files
        files_btn = page.locator("button", has_text="Files").first
        if files_btn.is_visible():
            files_btn.click()
            time.sleep(1.5)
            page.screenshot(path=f"{OUT}/06-files.png", full_page=False)
            print(f"[6/8] Files saved")
            back = page.locator("button", has_text="Back").first
            if back.is_visible():
                back.click()
                time.sleep(0.5)

        # 7. Open Agents
        agents_btn = page.locator("button", has_text="Agents").first
        if agents_btn.is_visible():
            agents_btn.click()
            time.sleep(1.5)
            page.screenshot(path=f"{OUT}/07-agents.png", full_page=False)
            print(f"[7/8] Agents saved")
            back = page.locator("button", has_text="Back").first
            if back.is_visible():
                back.click()
                time.sleep(0.5)

        # 8. Open Chess
        chess_btn = page.locator("button", has_text="Chess").first
        if chess_btn.is_visible():
            chess_btn.click()
            time.sleep(1.5)
            page.screenshot(path=f"{OUT}/08-chess.png", full_page=False)
            print(f"[8/8] Chess saved")

        browser.close()
        print(f"\nAll screenshots in {OUT}/")


if __name__ == "__main__":
    main()
