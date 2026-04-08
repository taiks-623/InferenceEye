# 初回バッチ実行時のパフォーマンス問題と対策

## 何をしようとしたか

10年分（2016〜2026年）の過去レースデータを `scrape_results.py --year YYYY` で年単位に取得する初回バッチを実行した。

---

## 何が起きたか

2016〜2023年あたりまでは正常に動作していたが、長時間実行後（24時間以上）に以下のエラーが頻発するようになった。

```
WARNING Failed to fetch calendar for 20240610: Page.goto: Timeout 60000ms exceeded.
WARNING Failed to fetch calendar for 20240611: NS_ERROR_UNKNOWN_HOST
```

- `Timeout 60000ms exceeded` — ページ取得が60秒以内に完了しない
- `NS_ERROR_UNKNOWN_HOST` — DNS名前解決の失敗（ネットワーク的なエラー）

同じWi-Fi に接続している別の端末はネットが速く、バッチを実行しているMacだけが遅かった。

---

## なぜ起きたか

### 根本原因: Playwright/Firefox のリソース消費

`scrape_calendar.py` は `fetch_race_ids_for_date()` を1日1回呼ぶ。
この関数は毎回 Firefox ブラウザを **起動 → ページ読み込み → 終了** するサイクルを繰り返す。

```python
def fetch_race_ids_for_date(target_date: date) -> list[str]:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)  # 毎回起動
        ...
        browser.close()  # 毎回終了
```

1年分 = 約365回のFirefox起動/終了。10年分では約3,650回。

Firefox はブラウザとして非常に重いプロセスであり、起動/終了のたびに：
- **メモリを確保・解放** するが、OSへの返却が完全でない場合がある（フラグメンテーション）
- **CPUスパイク** が発生する

長時間実行を続けると：
1. **使用可能メモリが減少** → スワップが発生し I/O が増加
2. **システム全体の処理が遅くなる** → ネットワーク処理もキューに積まれる
3. **DNS解決も遅延・失敗** → `NS_ERROR_UNKNOWN_HOST`
4. **ページ取得が60秒を超える** → `Timeout 60000ms exceeded`

### システムの状態（実測）

| 指標 | 値 | 評価 |
|------|---|------|
| Load Avg（1分） | 3.79 | 高い（処理待ち積み上がり） |
| CPU idle | 67% | そこまで高くないが Load Avg と矛盾あり（I/O waitが多い） |
| 空きメモリ | 少 | スワップ発生の可能性 |

別端末が速いにもかかわらずMacだけ遅い → ネットワーク問題ではなくMac内リソース問題が確定。

---

## どう対処したか（暫定）

1. バッチを停止（Ctrl+C）
2. Mac を再起動
3. Docker を再起動
4. 同じコマンドで再実行（取得済みの `race_id` は `race_exists` でスキップされるため続きから再開）

---

## 恒久対策（今後の実装課題）

### 1. ブラウザを使い回す（最も効果的）

現状: 1日1回 Firefox を起動・終了（3,650回/10年）
改善: 1回起動して複数日を処理し、最後に1回終了

```python
# 改善案: scrape_results.py 側でブラウザを持ち回す
def scrape_year(year: int) -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        for date in date_range(date(year, 1, 1), date(year, 12, 31)):
            race_ids = fetch_race_ids_with_browser(browser, date)  # ブラウザを渡す
            ...
        browser.close()  # 年の最後に1回だけ閉じる
```

これにより Firefox の起動/終了コストが 1/365 になる。

### 2. 年ごとに再起動を挟む

すぐ実装できる暫定対策として、年をまたぐ際に自動でDockerを再起動するシェルスクリプトを用意する。

```bash
for year in 2017 2018 2019 2020 2021 2022 2023 2024 2025 2026; do
    echo "=== Starting $year ==="
    docker compose exec app python scraper/scrape_results.py --year $year
    echo "=== Done $year, restarting Docker ==="
    docker compose restart app
    sleep 30  # 再起動待ち
done
```

### 3. Playwright タイムアウトの延長

`NS_ERROR_UNKNOWN_HOST` が一時的なものであっても、60秒で失敗して次の日に進む。
ページ取得のタイムアウトを 90〜120秒に延ばし、加えて失敗時に短い待機後リトライするとエラーを減らせる。

```python
# scrape_calendar.py
page.goto(url, timeout=90000)  # 60000 → 90000ms
```

### 4. `caffeinate` を使う

Mac のスリープを防ぐ（すでに実施済み）。

```bash
caffeinate -i docker compose exec app python scraper/scrape_results.py --year 2024
```

---

## まとめ

| 問題 | 原因 | 対策 |
|------|------|------|
| タイムアウト多発 | Firefox 起動コスト蓄積によるシステム負荷 | ブラウザ使い回し・タイムアウト延長 |
| DNS 解決失敗 | システム全体が重くなりネットワーク処理が遅延 | 年単位でDocker再起動 |
| 別端末は速い | Mac固有のリソース逼迫（ネットワーク障害ではない） | Mac・Docker の定期再起動 |
| バッチ再開 | — | `race_exists` チェックで取得済みをスキップするため同コマンドを再実行するだけでOK |
