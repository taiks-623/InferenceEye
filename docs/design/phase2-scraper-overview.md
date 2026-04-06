# Phase 2 スクレイパー コード解説

Issue #5 で実装したスクレイパーのコード構成・役割・処理の流れを説明します。

---

## ファイル構成

```
scraper/
├── __init__.py          # パッケージ宣言のみ
├── utils.py             # 共通ユーティリティ（HTTP取得・HTML解析・型変換）
├── db.py                # DB接続・共通クエリ
├── scrape_calendar.py   # 開催カレンダー取得
└── scrape_results.py    # レース結果取得
```

---

## 各ファイルの役割

### `scraper/utils.py` — 共通ユーティリティ

他のスクレイパーから共通で使うヘルパーをまとめたモジュール。

| 関数 | 役割 |
|------|------|
| `fetch_html(url)` | HTTP GET でHTMLを取得。失敗時は指数バックオフでリトライ（最大3回）。リクエスト前に 1〜2 秒待機してサーバー負荷を下げる。 |
| `parse_html(html)` | HTML文字列を BeautifulSoup オブジェクトに変換する。 |
| `date_range(start, end)` | start〜end の日付を1日ずつ yield するジェネレータ。 |
| `parse_time_sec(text)` | `"1:23.4"` 形式のタイムを秒数（float）に変換する。 |
| `parse_int(text)` | 文字列を int に変換。変換できない場合は `None`。 |
| `parse_float(text)` | 文字列を float に変換。変換できない場合は `None`。 |

---

### `scraper/db.py` — DB接続・共通クエリ

PostgreSQL との接続管理と、テーブルへの書き込みをまとめたモジュール。

#### `get_conn()` — 接続コンテキストマネージャ

```python
with get_conn() as conn:
    # ... DB操作 ...
```

`with` ブロックを抜けると自動でコミット。例外が発生した場合は自動でロールバックしてから接続を閉じる。

#### 各クエリ関数

| 関数 | 対象テーブル | 動作 |
|------|------------|------|
| `race_exists(conn, race_id)` | races | race_id が存在するか確認（重複スキップ用） |
| `upsert_race_calendar(conn, date, is_scheduled)` | race_calendars | 開催日フラグをupsert（既存行は上書き） |
| `upsert_jockey(conn, ...)` | jockeys | 騎手をinsert（既存ならスキップ） |
| `upsert_trainer(conn, ...)` | trainers | 調教師をinsert（既存ならスキップ） |
| `upsert_horse(conn, horse_dict)` | horses | 馬をinsert（既存ならスキップ） |
| `insert_race(conn, race_dict)` | races | レース基本情報をinsert（既存ならスキップ） |
| `insert_entry(conn, entry_dict)` | entries | 出走馬情報をinsert（既存ならスキップ） |
| `insert_result(conn, result_dict)` | results | レース結果をinsert（既存ならスキップ） |

> **upsert と ON CONFLICT DO NOTHING の使い分け**  
> カレンダーは「再取得したら上書き」が必要なため upsert。馬・騎手・調教師・レース等は「一度保存したらそのまま」でよいため `DO NOTHING`。

---

### `scraper/scrape_calendar.py` — 開催カレンダー取得

netkeiba のレース一覧ページを確認し、「その日に競馬が開催されているか」を `race_calendars` テーブルに保存する。

#### なぜ Playwright が必要か

レース一覧ページ（`race.netkeiba.com/top/race_list.html?kaisai_date=YYYYMMDD`）は JavaScript でレンダリングされる。通常の HTTP リクエストでは初期HTMLしか取得できず、レースリンクが存在しない。Playwright でブラウザを起動し JS 実行後のHTMLを取得する必要がある。

#### 処理の流れ

```
引数: target_date (例: 2026-04-05)
       ↓
Firefox (headless) でページを開く
       ↓
page.wait_for_load_state("load")     ← ページの初期ロード完了を待つ
       ↓
page.wait_for_selector("a[href*='race_id=']", timeout=15000)
   ├─ 成功 → race_id リンクが出現した（開催日）
   └─ TimeoutError → リンクが存在しない（非開催日）→ 空リストを返す
       ↓
HTML を取得し BeautifulSoup でパース
       ↓
"race_id=" を含む href を正規表現で抽出 → race_id の一覧（例: ["202606030401", ...]）
       ↓
race_id が1件以上 → is_scheduled=True
race_id が0件   → is_scheduled=False
       ↓
upsert_race_calendar() で DB に保存
```

#### 実行方法

```bash
# 特定日
python scraper/scrape_calendar.py --date 20260405

# 年指定（全日スキャン）
python scraper/scrape_calendar.py --year 2026

# 期間指定
python scraper/scrape_calendar.py --date-from 20260101 --date-to 20261231
```

---

### `scraper/scrape_results.py` — レース結果取得

レース結果ページから全データを取得し、`races` / `entries` / `results` / `horses` / `jockeys` / `trainers` テーブルに保存する。

#### なぜ requests + BS4 で十分か

レース結果ページ（`race.netkeiba.com/race/result.html?race_id=...`）は静的HTML。JavaScriptによるレンダリングが不要なため、Playwright は使わず軽量な `requests` + `BeautifulSoup4` で処理する。

#### 処理の流れ（1日分）

```
引数: start_date, end_date
       ↓
date_range() で日付を1日ずつ処理
       ↓
fetch_race_ids_for_date(date)          ← scrape_calendar.py の関数を再利用
  └─ その日の race_id 一覧を取得（例: ["202606030401", ..., "202606030412"]）
       ↓
race_id ごとに scrape_one_race() を呼ぶ
```

#### `scrape_one_race()` の処理フロー

```
race_id, held_date
       ↓
fetch_html(result_url)                 ← requests で結果ページ取得
       ↓
race_exists(conn, race_id)
  └─ 既存なら即スキップ（冪等性の確保）
       ↓
parse_race_info(soup, race_id, held_date)
  ├─ レース名、コース種別、距離、方向、馬場状態、天気、クラス等を抽出
  └─ 障害レースは None を返してスキップ
       ↓
insert_race(conn, race)
       ↓
parse_entries_and_results(soup, race_id)
  ├─ 結果テーブルの各行をパース
  ├─ 着順、馬番、枠番、騎手ID、調教師ID、馬体重、タイム、上がり等を抽出
  └─ entries と results のリストを返す
       ↓
【FK 制約の順番を守った保存】
upsert_jockey()   ← entries より先に保存（外部キー）
upsert_trainer()  ← entries より先に保存（外部キー）
upsert_horse()    ← entries より先に保存（外部キー）
insert_entry()
insert_result()
```

> **外部キー制約の順番が重要**  
> `entries` テーブルは `horses`, `jockeys`, `trainers` を参照している。これらを `entries` より先に保存しないと外部キー違反エラーになる。

#### 実行方法

```bash
# 特定日
python scraper/scrape_results.py --date 20260405

# 年指定（過去データ一括取得）
python scraper/scrape_results.py --year 2016

# 期間指定
python scraper/scrape_results.py --date-from 20160101 --date-to 20161231
```

---

## モジュール間の依存関係

```
scrape_results.py
├── scrape_calendar.py   (fetch_race_ids_for_date を呼ぶ)
│   └── utils.py         (parse_html)
│       db.py            (upsert_race_calendar)
├── utils.py             (fetch_html, parse_html, parse_* 各関数)
└── db.py                (get_conn, insert_race, insert_entry, ...)
```

`scrape_results.py` はカレンダースクレイパーに依存しており、**まず race_id 一覧を取得してから**結果ページを1件ずつスクレイピングする設計になっている。

---

## データの流れ（DB）

```
scrape_calendar.py  →  race_calendars（開催日フラグ）

scrape_results.py   →  jockeys（騎手マスタ）
                    →  trainers（調教師マスタ）
                    →  horses（馬マスタ、名前のみ）
                    →  races（レース基本情報）
                    →  entries（出走情報）
                    →  results（着順・タイム等）
```

> 馬の詳細情報（父・母・誕生日等）は結果ページには含まれない。Phase 2 以降で `db.netkeiba.com/horse/{horse_id}/` から別途取得する予定。
