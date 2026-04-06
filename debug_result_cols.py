"""結果ページのカラム構造を確認するデバッグスクリプト（確認後に削除）"""
from scraper.utils import fetch_html, parse_html

race_id = "202606030401"
url = f"https://race.netkeiba.com/race/result.html?race_id={race_id}"

html = fetch_html(url)
soup = parse_html(html)

# テーブルヘッダー確認
table = soup.select_one(".ResultTableWrap table") or soup.select_one("table.RaceTable_01")
print("=== Header Row (th) ===")
if table:
    for i, th in enumerate(table.select("th")):
        print(f"  th[{i:2d}]: {repr(th.get_text(strip=True))}")

# col[10] の raw HTML を確認
print("\n=== col[10] raw HTML (first 3 rows) ===")
if table:
    for row_i, row in enumerate(table.select("tbody tr")[:3]):
        cols = row.find_all("td")
        if len(cols) > 10:
            print(f"row[{row_i}] col[10]: text={repr(cols[10].get_text(strip=True))!s:<15} html={str(cols[10])[:120]}")
        if len(cols) > 9:
            print(f"row[{row_i}] col[ 9]: text={repr(cols[9].get_text(strip=True))!s:<15}")
        print()

# 最初の5行の全体概要
print("=== First 5 rows summary ===")
if table:
    for row_i, row in enumerate(table.select("tbody tr")[:5]):
        cols = row.find_all("td")
        summary = {
            "fin": cols[0].get_text(strip=True) if len(cols) > 0 else "",
            "horse_num": cols[2].get_text(strip=True) if len(cols) > 2 else "",
            "time": cols[7].get_text(strip=True) if len(cols) > 7 else "",
            "col8": cols[8].get_text(strip=True) if len(cols) > 8 else "",
            "col9": cols[9].get_text(strip=True) if len(cols) > 9 else "",
            "col10": cols[10].get_text(strip=True) if len(cols) > 10 else "",
            "col11": cols[11].get_text(strip=True) if len(cols) > 11 else "",
            "col12": cols[12].get_text(strip=True) if len(cols) > 12 else "",
        }
        print(f"  row[{row_i}]: {summary}")

# RaceData01 raw HTML
print("\n=== RaceData01 raw HTML ===")
data1 = soup.select_one(".RaceData01")
if data1:
    print(str(data1)[:500])
