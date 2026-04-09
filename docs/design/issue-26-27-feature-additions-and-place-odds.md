# Issue #26/#27 設計: 特徴量追加 + 複勝オッズ取得

## 目的

ウォークフォワード検証の現状（win_AUC=0.75、単勝回収率 65.9%）を改善するため:
- Issue #26: 予測に有効な特徴量を追加する
- Issue #27: 複勝オッズをスクレイピングして回収率を正しく評価できるようにする

---

## Issue #26: 特徴量追加

### 追加する特徴量一覧

| 特徴量 | 計算方法 | 追加先 |
|--------|---------|--------|
| `popularity_rank` | レース内での単勝オッズ昇順の順位 | ラベル結合時に results.popularity を利用 |
| `burden_weight_diff` | 自身の斤量 - レース平均斤量 | base_df 内で groupby 計算 |
| `prev_distance_diff` | 今回距離 - 前走距離 | `_HORSE_PAST_SQL` に前走距離を追加 |
| `prev_class_diff` | 今回クラスランク - 前走クラスランク | 同上 |
| `track_cond_win_rate` | 現在の馬場状態での過去勝率 | `_HORSE_COND_SQL` に追加 |

#### popularity_rank

`results` テーブルに既に `popularity` カラムがある。`_LABELS_SQL` で取得済みのため、
ラベル結合後にそのまま特徴量として利用できる。

```python
# _build_features_for_batch 内、ラベル結合後
base_df["popularity_rank"] = base_df["popularity"]
```

#### burden_weight_diff

```python
# base_df 内でレース平均斤量との差を計算
avg_burden = base_df.groupby("race_id")["burden_weight"].transform("mean")
base_df["burden_weight_diff"] = base_df["burden_weight"] - avg_burden
```

#### prev_distance_diff / prev_class_diff

`_HORSE_PAST_SQL` に前走距離・クラスを追加:

```sql
-- _HORSE_PAST_SQL の SELECT 節に追加
MAX(CASE WHEN rn = 1 THEN distance END)    AS last_race_distance,
MAX(CASE WHEN rn = 1 THEN race_class END)  AS last_race_class,  -- 既存
```

`feature_builder.py` での計算:

```python
past_all["prev_distance_diff"] = base_df["distance"] - past_all["last_race_distance"]
past_all["prev_class_diff"] = (
    base_df["race_class_rank"] - past_all["last_race_class"].apply(_map_race_class)
)
```

#### track_cond_win_rate

`_HORSE_COND_SQL` に現在馬場状態での勝率を追加（同馬場状態カラムは既存）:

```python
# _build_features_for_batch 内
cond_df["track_cond_win_rate"] = (
    cond_df["wins_same_cond"] / cond_df["runs_same_cond"]
).fillna(0)
```

### train.py の FEATURE_COLS への追加

```python
FEATURE_COLS = [
    ...
    # 追加分
    "popularity_rank",
    "burden_weight_diff",
    "prev_distance_diff",
    "prev_class_diff",
    "track_cond_win_rate",
]
```

`popularity_rank` は `CATEGORICAL_COLS` には含めない（連続値として扱う）。

---

## Issue #27: 複勝オッズ取得と回収率修正

### DBスキーマ変更

`results` テーブルに `place_odds` カラムを追加:

```sql
ALTER TABLE results ADD COLUMN place_odds NUMERIC(6, 1);
```

### スクレイピング

netkeiba の払戻情報ページ（`https://race.netkeiba.com/race/result.html?race_id=...`）から
複勝配当を取得する。

`scrape_results.py` の `_parse_results` 関数内で払戻テーブルから複勝配当を読み取り、
各馬の `place_odds` に格納する。

**払戻テーブルの構造:**
- 複勝は着順3頭分の配当が並ぶ
- `horse_num` を key に `results` テーブルへ upsert

### 回収率計算の修正

`train.py` の `compute_recovery_rate`:

```python
def walk_forward_validation(...):
    ...
    # 修正: 複勝回収率には place_odds を使用
    place_recovery = compute_recovery_rate(val_df, "place_proba", "place_odds", "place_label")
```

`_LABELS_SQL` にも `place_odds` を追加:

```sql
SELECT
    race_id, horse_num, finish_pos, win_odds, place_odds, popularity, ...
FROM results
WHERE race_id = ANY(%(race_ids)s)
```

---

## 実装順序

1. **Issue #26**: `feature_builder.py` + `train.py` の特徴量追加
2. **Issue #27**: DB マイグレーション → `scrape_results.py` の改修 → 過去データ再取得 → `train.py` の回収率修正
3. ウォークフォワード検証を再実行して効果測定

## 期待する改善

- `popularity_rank` は競馬予測で最重要特徴量の一つ。AUC の明確な改善が期待される
- `burden_weight_diff` はハンデ戦の予測精度向上に寄与
- `prev_distance_diff` / `prev_class_diff` は昇降級馬の評価改善に寄与
- 複勝回収率が正しく計算できることで、Phase 4 での指標の信頼性が上がる
