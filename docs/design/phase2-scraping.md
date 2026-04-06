# Phase 2: スクレイピング実装 詳細設計

関連 Issue: #5 #6

---

## 概要

netkeiba からレースに関わる全データを取得し PostgreSQL に保存する。
Phase 3 のモデル学習に必要な「過去 10 年分の生データ」を揃えることがゴール。

### 完了の定義

- `scrape_calendar.py` が動作し `race_calendars` テーブルにデータが入る
- `scrape_results.py` が動作し `races` / `entries` / `results` / `horses` テーブルにデータが入る
- `scrape_shutuba.py` が動作し出馬表データが取得できる
- `scrape_training.py` が動作し調教タイムが取得できる
- `scrape_odds.py` が動作しオッズが取得できる
- `scrape_bbs.py` が動作し掲示板コメントが取得できる
- 初回バッチ（2016〜現在）が完了し `races` テーブルに約 10 万件以上入っている
- 中断・再開が正しく動作する（取得済みの race_id をスキップする）

---

## 1. netkeiba の URL 構造

```
レース一覧:    https://race.netkeiba.com/top/race_list.html?kaisai_date=YYYYMMDD
レース結果:    https://race.netkeiba.com/race/result.html?race_id={race_id}
出馬表:        https://race.netkeiba.com/race/shutuba.html?race_id={race_id}
オッズ(単勝):  https://race.netkeiba.com/odds/index.html?type=b1&race_id={race_id}
オッズ(複勝):  https://race.netkeiba.com/odds/index.html?type=b2&race_id={race_id}
馬の過去成績:  https://db.netkeiba.com/horse/{horse_id}/
調教タイム:    https://race.netkeiba.com/race/oikiri.html?race_id={race_id}
馬掲示板:      https://community.netkeiba.com/?pid=community&id={horse_id}
```

### race_id の構造

```
202305010101
└─┬─┘└┬┘└┬┘└┬┘
  年  場  回  日  R
```

例: `202305010101` = 2023年・東京(05)・1回・1日・1R

---

## 2. スクレイパー一覧と実行タイミング

| スクリプト | 取得内容 | 保存テーブル | 実行タイミング |
|-----------|---------|------------|-------------|
| `scrape_calendar.py` | 開催カレンダー | `race_calendars` | 毎週火曜 10:00 |
| `scrape_results.py` | レース結果 | `races` / `entries` / `results` / `horses` / `jockeys` / `trainers` | 毎週火曜 08:00（＋初回バッチ） |
| `scrape_shutuba.py` | 出馬表・エントリー | `entries` / `horses` | 前日 17:00〜18:00 |
| `scrape_training.py` | 調教タイム | `training_times` | 前日 13:30 |
| `scrape_odds.py` | オッズスナップショット | `odds` | 当日 08:00 ＋ 発走30分前〜毎分 |
| `scrape_bbs.py` | netkeiba 掲示板 | `ai_assessments`（の入力） | 発走10分前 |

---

## 3. 技術方針

### ライブラリの使い分け

| ページ種別 | 理由 | ライブラリ |
|----------|------|----------|
| 静的ページ（HTML が最初から存在する） | 軽量・高速 | `requests + BeautifulSoup4` |
| 動的ページ（JavaScript で描画される） | JS 実行が必要 | `Playwright` |

**静的ページの例:** レース結果・出馬表・馬の過去成績・調教タイム  
**動的ページの例:** オッズ（リアルタイム更新のため JS で描画）

### リクエスト制御

```python
import time
import random

# リクエスト間隔: 1〜2 秒のランダムスリープ
time.sleep(random.uniform(1.0, 2.0))
```

サーバーへの負荷軽減・アクセスブロック回避のため必須。

### エラーハンドリング・リトライ

```python
import time

def fetch_with_retry(url: str, max_retries: int = 3) -> str:
    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response.text
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt  # 指数バックオフ: 1秒 → 2秒 → 4秒
            time.sleep(wait)
```

### 重複取得の防止

取得済みの `race_id` は DB で管理し、すでに存在する場合はスキップする。

```python
def is_already_scraped(conn, race_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM races WHERE race_id = %s", (race_id,))
        return cur.fetchone() is not None
```

---

## 4. ディレクトリ構成

```
scraper/
├── __init__.py
├── db.py                   # DB 接続・共通クエリ
├── utils.py                # リトライ・スリープ等の共通ユーティリティ
├── scrape_calendar.py      # 開催カレンダー取得
├── scrape_results.py       # レース結果取得（メインスクレイパー）
├── scrape_shutuba.py       # 出馬表取得
├── scrape_training.py      # 調教タイム取得
├── scrape_odds.py          # オッズ取得（Playwright）
└── scrape_bbs.py           # 掲示板取得
```

### db.py（DB 接続の共通化）

```python
import os
import psycopg2
from contextlib import contextmanager

@contextmanager
def get_conn():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

---

## 5. 各スクレイパーの設計

### scrape_calendar.py

**処理の流れ:**

```
1. 対象期間の月ごとにループ
2. netkeiba のカレンダーページにアクセス
3. 開催日（土・日）を取得
4. race_calendars テーブルに upsert
```

**URL:** `https://race.netkeiba.com/top/race_list.html?kaisai_date=YYYYMMDD`

このページに開催レースの一覧がある日 = 開催日。

---

### scrape_results.py（最重要・初回バッチの主体）

**処理の流れ:**

```
1. race_calendars から開催日一覧を取得
2. 各開催日のレース一覧ページから race_id を収集
3. 取得済み race_id はスキップ（重複防止）
4. 各 race_id に対して result ページをスクレイピング
5. races / entries / results / horses / jockeys / trainers に保存
```

**取得する情報:**

```
レース情報（races テーブル）:
  - 距離、コース種別、馬場状態、天気、クラス、賞金 等

エントリー情報（entries テーブル）:
  - 馬番、枠番、騎手、調教師、斤量、馬体重、馬体重増減

結果情報（results テーブル）:
  - 着順、タイム、着差、通過順位、上がり3F、オッズ、人気

馬情報（horses テーブル）:
  - 馬名、性別、毛色、生年月日、父・母、調教師、馬主
  - ※ 新馬が出てきたときだけ INSERT（既存はスキップ）
```

---

### scrape_shutuba.py

**処理の流れ:**

```
1. 翌日の開催レースの race_id 一覧を取得
2. 各 race_id の出馬表ページをスクレイピング
3. entries テーブルに upsert（当日追加・変更がある可能性）
4. 新馬がいれば horses テーブルにも INSERT
```

---

### scrape_training.py

**処理の流れ:**

```
1. 翌日の開催レースの race_id 一覧を取得
2. 各 race_id の調教タイムページをスクレイピング
3. training_times テーブルに upsert
```

---

### scrape_odds.py（Playwright 使用）

**処理の流れ:**

```
1. 当日の開催レースの race_id 一覧を取得
2. Playwright で各 race_id のオッズページを開く
3. 単勝・複勝オッズを取得
4. odds テーブルに INSERT（fetched_at とセットで記録）
5. 発走30分前からは毎分繰り返す
```

**Playwright の使い方（基本）:**

```python
from playwright.sync_api import sync_playwright

def fetch_odds(race_id: str) -> list[dict]:
    with sync_playwright() as p:
        # Chromium は ARM Docker で不安定なため Firefox を使用
        browser = p.firefox.launch(headless=True)
        page = browser.new_page()
        page.goto(f"https://race.netkeiba.com/odds/index.html?type=b1&race_id={race_id}", timeout=60000)
        page.wait_for_load_state("load", timeout=30000)
        # networkidle は広告リクエストで達成されないため selector wait を使う
        page.wait_for_selector("[data-odds]", timeout=15000)
        # ... HTML を取得して BeautifulSoup でパース
        browser.close()
```

---

### scrape_bbs.py

**処理の流れ:**

```
1. 発走10分前の対象レース出走馬の horse_id 一覧を取得
2. 各馬の掲示板ページにアクセス
3. 発走40分前〜10分前のコメントを取得
4. ai_filter に渡す用のテキストとして返す（DB 保存は ai_filter 側）
```

---

## 6. 初回バッチ設計

### 対象期間

2016年1月〜現在（約10年分）

### 推定規模

| 項目 | 推定値 |
|------|-------|
| 開催日数 | 約 500 日 |
| レース数 | 約 100,000〜120,000 件 |
| リクエスト数 | 約 400,000〜500,000 回（結果 + 馬情報 等） |
| 所要時間（sleep 1.5秒として） | 約 170〜200 時間（7〜9日） |

### 実行方法

年単位で分割して実行する。途中で中断しても再開可能。

```bash
# コンテナ内で実行
docker compose exec app python scraper/scrape_results.py --year 2016
docker compose exec app python scraper/scrape_results.py --year 2017
# ... 年ごとに実行
```

### 中断・再開の仕組み

```python
# 取得済み race_id は DB を確認してスキップ
if is_already_scraped(conn, race_id):
    print(f"Skip: {race_id} already exists")
    continue
```

---

## 7. 実装の注意点

### netkeiba へのアクセス制限について

- **必ず sleep を入れること**（1〜2秒）
- 短時間に大量リクエストを送ると IP ブロックされる可能性がある
- 初回バッチは夜間・早朝に実行するのが望ましい

### HTML のパースについて

netkeiba のページは定期的に HTML 構造が変わることがある。
スクレイピングが失敗したときは、まずブラウザで対象ページを開いて HTML 構造を確認する。

### 障害レースの除外

`course_type` が `'障'`（障害）のレースは除外する。
`races` テーブルへの INSERT 時にフィルタリングする。

---

## 8. テスト方針

各スクレイパーに単体テストを作成する。

```python
# tests/test_scrape_results.py

def test_parse_race_info():
    """レース情報のパースが正しく動作すること"""
    html = open("tests/fixtures/result_sample.html").read()
    race_info = parse_race_info(html)
    assert race_info["course_type"] in ["芝", "ダート"]
    assert race_info["distance"] > 0
```

実際の netkeiba にアクセスするテストは CI では実行しない（フィクスチャ HTML を用意する）。

---

## 9. 実装手順

```
1. scraper/db.py と scraper/utils.py を実装（共通処理）
2. scrape_calendar.py を実装・動作確認
3. scrape_results.py を実装（まず1日分で動作確認）
4. 初回バッチを年単位で実行
5. scrape_shutuba.py を実装
6. scrape_training.py を実装
7. scrape_odds.py を実装（Playwright）
8. scrape_bbs.py を実装
```

---

## 10. 関連ドキュメント

- `InferenceEye_design.md` — 全体設計（ローカルのみ）
- `docs/design/db-schema.md` — テーブル定義・ER 図
- `docs/decisions/003-playwright-for-odds.md` — なぜオッズだけ Playwright か（作業中に作成）
- `notes/learnings/scraping.md` — 作業中に学んだことを記録
