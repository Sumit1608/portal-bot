"""Step 1 check: can Python start a real browser and open a page?"""
import asyncio

from playwright.async_api import async_playwright


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # visible window
        page = await browser.new_page()
        await page.goto("https://github.com/login")
        print("OK - browser opened page with title:", await page.title())
        await page.wait_for_timeout(3000)  # keep window open 3s so you can see it
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
