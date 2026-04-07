# Phase 2 スクレイパー コード解説

Issue #5・#6 で実装したスクレイパーのコード構成・役割・処理の流れを説明します。

---

## ファイル構成

```
scraper/
├── __init__.py           # パッケージ宣言のみ
├── utils.py              # 共通ユーティリティ（HTTP取得・HTML解析・型変換）
├── db.py                 # DB接続・共通クエリ
├── scrape_calendar.py    # 開催カレンダー取得（Playwright）
├── scrape_results.py     # レース結果取得（requests+BS4）
├── scrape_shutuba.py     # 出馬表取得（requests+BS4）
├── scrape_training.py    # 調教タイム取得（requests+BS4）
├── scrape_odds.py        # 単勝・複勝オッズ取得（Playwright）
└── scrape_bbs.py         # netkeiba 掲示板取得（requests+BS4）
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
  ├─ RaceData01: コース種別・距離（spanから）、方向（全文検索）、天候・馬場（全角/半角コロン両対応）
  ├─ RaceData02: race_class / age_cond / sex_cond / weight_type / num_horses / prize_1st
  └─ 障害レースは None を返してスキップ
       ↓
insert_race(conn, race)
       ↓
parse_entries_and_results(soup, race_id)
  ├─ 結果テーブル（15列）の各行をパース
  ├─ 着順・馬番・枠番・騎手ID・調教師ID・馬体重・タイム・人気・単勝オッズ・上がり・通過順を抽出
  ├─ 騎手/調教師IDはURLの末尾数値から取得: r"/(\d+)/?$"
  └─ entries と results のリストを返す
       ↓
【FK 制約の順番を守った保存】
upsert_jockey()   ← entries より先に保存（外部キー）
upsert_trainer()  ← entries より先に保存（外部キー）
upsert_horse()    ← entries より先に保存（外部キー）
insert_entry()
insert_result()
```

#### 結果テーブルの列構成（15列）

| col | 内容 | 備考 |
|-----|------|------|
| 0 | 着順 | 「取消」「除外」等の文字列もある |
| 1 | 枠番 | |
| 2 | 馬番 | |
| 3 | 馬名 | href: `/horse/{horse_id}/` |
| 4 | 性齢 | |
| 5 | 斤量 | |
| 6 | 騎手 | href: `/jockey/result/recent/{id}/` |
| 7 | タイム | `"1:23.4"` 形式 |
| 8 | 着差 | |
| 9 | 人気 | |
| 10 | 単勝オッズ | class="Odds Txt_R", span class="Odds_Ninki" |
| 11 | 上がり3F | |
| 12 | コーナー通過順 | |
| 13 | 調教師 | href: `/trainer/result/{id}/` |
| 14 | 馬体重 | `"450(-2)"` 形式 |

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
scrape_calendar.py
├── utils.py   (parse_html)
└── db.py      (upsert_race_calendar)

scrape_results.py
├── scrape_calendar.py   (fetch_race_ids_for_date を呼ぶ)
├── utils.py             (fetch_html, parse_html, parse_* 各関数)
└── db.py                (get_conn, insert_race, insert_entry, ...)

scrape_shutuba.py
├── scrape_calendar.py   (fetch_race_ids_for_date)
├── utils.py             (fetch_html, parse_html, ...)
└── db.py                (upsert_entry, upsert_horse, ...)

scrape_training.py
├── scrape_calendar.py   (fetch_race_ids_for_date)
├── utils.py             (fetch_html, parse_html)
└── db.py                (upsert_training_time)

scrape_odds.py
├── scrape_calendar.py   (fetch_race_ids_for_date)
├── utils.py             (parse_html, parse_int)
└── db.py                (insert_odds)

scrape_bbs.py
├── utils.py             (fetch_html, parse_html)
└── db.py                (get_conn — horse_id 取得用)
```

calendar scraper は Playwright を使うため、**全スクレイパーが間接的に Playwright に依存している**。ただし playwright のインポートは関数内に閉じているため、テスト時に `ModuleNotFoundError` にはならない。

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

scrape_shutuba.py   →  jockeys / trainers / horses（upsert）
                    →  entries（upsert、前日時点の出走確定情報）

scrape_training.py  →  training_times（調教日・コース・タイム・ランク）

scrape_odds.py      →  odds（fetched_at 付きスナップショット）
                        ※ 単勝（win）・複勝（place）を別行で保存

scrape_bbs.py       →  DB 保存なし（ai_filter への入力テキストとして返す）
```

> 馬の詳細情報（父・母・誕生日等）は各スクレイパーには含まれない。将来的に `db.netkeiba.com/horse/{horse_id}/` から別途取得予定。

---

## Issue #6 で追加したスクレイパー詳細

### `scraper/scrape_shutuba.py` — 出馬表取得

出走確定後（前日 17〜18 時）に取得。結果スクレイパーと同じく `race_id` 一覧を `scrape_calendar.py` から取得してから各ページを処理する。

**処理フロー:**
```
fetch_race_ids_for_date(date)   ← Playwright で race_id 一覧取得
       ↓
fetch_html(shutuba_url)          ← requests でページ取得
       ↓
parse_shutuba(soup, race_id)
  ├─ .Shutuba_Table のテーブル行をパース
  ├─ gate_num, horse_num, horse_id, jockey_id, trainer_id, burden_weight を抽出
  └─ horse_weight は出馬表時点では未確定 → NULL
       ↓
upsert_jockey / upsert_trainer / upsert_horse  ← FK 制約のため先に保存
upsert_entry                                   ← 既存行は更新（騎手変更等に対応）
```

---

### `scraper/scrape_training.py` — 調教タイム取得

前日 13:30 頃に取得。`oikiri.html` ページから各馬の調教データを抽出する。

**処理フロー:**
```
fetch_race_ids_for_date(date)
       ↓
fetch_html(oikiri_url)
       ↓
parse_training(soup, race_id)
  ├─ 調教テーブル（.OikiriTable 等）をパース
  ├─ training_date, venue_code（調教場）, course_type（坂路/CW等）を抽出
  ├─ time_4f / time_3f / time_1f をパース
  └─ rank（S/A/B/C）、騎乗者を抽出
       ↓
upsert_training_time（horse_id + training_date + course_type が PK）
```

---

### `scraper/scrape_odds.py` — オッズ取得（Playwright）

オッズは JavaScript でリアルタイム更新されるため Playwright/Firefox を使用。

**処理フロー:**
```
scrape_odds_for_race(race_id)
       ↓
fetch_odds_html(race_id, "win")   ← Firefox で単勝ページ取得
       ↓
parse_win_odds(soup)
  ├─ #odds_tan_b テーブルをパース
  └─ horse_num, odds_low（倍率）を抽出
       ↓
fetch_odds_html(race_id, "place")  ← Firefox で複勝ページ取得
       ↓
parse_place_odds(soup)
  ├─ #odds_fuku_b テーブルをパース
  └─ "1.5 - 2.3" 形式の範囲オッズを odds_low / odds_high に分解
       ↓
insert_odds（fetched_at タイムスタンプ付きで保存）
```

> 発走30分前から毎分取得する場合は、`scrape_odds_for_race(race_id)` をループで呼び出す。APScheduler との連携は Phase 5 で実装予定。

---

### `scraper/scrape_bbs.py` — 掲示板取得

発走10分前に実行。各馬の netkeiba 掲示板コメントを取得して AI フィルターに渡す。

**処理フロー:**
```
scrape_bbs_for_race(race_id)
       ↓
get_horse_ids_for_race(race_id)    ← entries テーブルから horse_id 一覧取得
       ↓
fetch_html(bbs_url)                ← 各馬の掲示板ページ
       ↓
parse_bbs_comments(soup, since=12時間前)
  ├─ .Community_DetailList_Item 等からコメントを抽出
  └─ 投稿日時でフィルタ
       ↓
{horse_num: [comment, ...]} の辞書を返す（DB 保存なし）
```

> BBS コメントは ai_filter（Phase 4）で Claude API に渡してセンチメント分析を行う。
