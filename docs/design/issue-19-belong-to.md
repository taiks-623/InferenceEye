# Issue #19 設計：騎手・調教師の所属（belong_to）取得

## 目的

`jockeys` / `trainers` テーブルの `belong_to` カラム（現在 NULL）を補完する。
初回バッチ（scrape_results）で保存された全員の所属を一括で取得・更新する。

## 取得元 URL

| 種別 | URL |
|------|-----|
| 騎手 | `https://db.netkeiba.com/jockey/{jockey_id}/` |
| 調教師 | `https://db.netkeiba.com/trainer/{trainer_id}/` |

どちらも静的 HTML（requests + BS4 で取得可能）。

## belong_to の値

DB のカラムコメントは `'関東' / '関西' / '地方' / '外国'` だが、ページには「美浦」「栗東」などの具体的な所属が表示される。実際の値をそのまま保存する方針とする（美浦/栗東/地方競馬/海外 等）。

## 実装ファイル

`scraper/scrape_person_profiles.py`（新規）

## 処理フロー

```
1. DB から belong_to IS NULL の jockey_id 一覧を取得
2. 各騎手プロフィールページを requests で取得
3. HTML をパースして所属を抽出
4. UPDATE jockeys SET belong_to = ? WHERE jockey_id = ?
5. 調教師も同様に処理
```

## パース方針

netkeiba のプロフィールページには `<dl>` や `<table>` 形式でプロフィール情報が並んでいる。「所属」というラベルの隣にあるテキストを取得する。

実際の HTML 構造は実装前に確認が必要。

## DB 更新クエリ

```python
# db.py に追加
def update_jockey_belong_to(conn, jockey_id: str, belong_to: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jockeys SET belong_to = %s WHERE jockey_id = %s",
            (belong_to, jockey_id),
        )

def update_trainer_belong_to(conn, trainer_id: str, belong_to: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE trainers SET belong_to = %s WHERE trainer_id = %s",
            (belong_to, trainer_id),
        )
```

## レートリミット対策

- リクエスト間に 1〜2 秒待機（utils.py の `fetch_html` が既に対応）
- 全員分（数百〜数千人）を一括処理しても数時間以内に完了見込み

## 実行方法（想定）

```bash
# 騎手のみ
python scraper/scrape_person_profiles.py --jockeys

# 調教師のみ
python scraper/scrape_person_profiles.py --trainers

# 両方
python scraper/scrape_person_profiles.py --all
```

## テスト方針

フィクスチャ HTML を使ったユニットテスト（ネットワーク接続不要）を書く。
`test_scrape_person_profiles.py` に `parse_belong_to()` 関数のテストを追加する。
