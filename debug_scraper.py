"""スクレイパーデバッグ用スクリプト（確認後に削除）"""
from playwright.sync_api import sync_playwright
from scraper.utils import parse_html

url = "https://race.netkeiba.com/top/race_list.html?kaisai_date=20260405"

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-zygote",
        ],
    )
    page = browser.new_page()
    page.goto(url, timeout=30000)
    page.wait_for_load_state("networkidle", timeout=15000)
    html = page.content()
    browser.close()

soup = parse_html(html)
links = soup.select("a[href*='race_id=']")
print(f"race_id= を含むリンク数: {len(links)}")
for link in links[:5]:
    print(link.get("href"))
