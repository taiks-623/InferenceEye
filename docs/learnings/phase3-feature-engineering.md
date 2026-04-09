# Phase 3 特徴量エンジニアリング メモ

## データリーク防止が最重要

過去成績の集計クエリは必ず `held_date < current_date` でフィルタする。
このフィルタを忘れると、評価データで「未来の情報」を使うデータリークが発生し、
検証時は高精度に見えるが本番で全く機能しないモデルが出来上がる。

```sql
-- NG: 全期間の成績を使う（データリーク）
WHERE e.horse_id = ANY(%(horse_ids)s)

-- OK: 当該レースより前のみ
WHERE e.horse_id = ANY(%(horse_ids)s)
  AND rc.held_date < %(current_date)s
```

---

## 過去成績の集計は SQL で行う

Python ループでのレコード処理ではなく、SQL の GROUP BY / 条件付き集計で一括処理する。
10年分（～10万レース、～100万馬出走）を Python ループで処理すると数時間かかる。

```sql
-- 通算成績・直近N走・条件別成績を1クエリで集計
SELECT
    horse_id,
    COUNT(*) AS career_runs,
    SUM(CASE WHEN finish_pos = 1 THEN 1 ELSE 0 END) AS career_wins,
    AVG(CASE WHEN rn <= 5 THEN finish_pos END) AS recent5_avg_finish,
    ...
FROM horse_races
GROUP BY horse_id
```

---

## NULL は LightGBM に任せる

馬体重不明（計不）、新馬（過去成績なし）等は NaN のまま DataFrame に渡す。
LightGBM はツリー分岐時に NaN を特別なノードとして扱えるため、無理に埋めない。

```python
# NG: 無理に 0 で埋める（新馬と古馬の区別がつかなくなる）
df["career_wins"].fillna(0)

# OK: NaN のまま + is_first_race フラグで判別させる
df["is_first_race"] = (df["career_runs"] == 0).astype(int)
```

---

## venue_code はカテゴリ変数として扱う

venue_code（"01"〜"10"）は文字列だが、LightGBM の `categorical_feature` に指定することで
ラベルエンコーディングよりも適切な木構造を学習できる。
数値として扱うと「05（東京）と 10（小倉）の中間は 07.5 に相当する競馬場がある」という
意味不明な距離になってしまう。

---

## 騎手・調教師の統計ウィンドウは 90 日

直近 90 日（約 20 開催）を集計ウィンドウとする。
短すぎると（例: 30日）サンプル数が少なくノイジーな統計になる。
長すぎると（例: 1年）季節性や調子の変化を捉えられない。
最終的には Phase 4 の実験ループでハイパーパラメータとして調整する。
