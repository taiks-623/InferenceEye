# Phase 3 設計：特徴量生成（feature_builder.py）

関連 Issue: #7

---

## 概要

`features/feature_builder.py` はDBに蓄積されたレース結果データから、LightGBMに投入する特徴量 DataFrame を生成するモジュール。

- **学習時**: 過去レース全件の特徴量＋ラベル（finish_pos == 1 / <= 3）を生成
- **推論時**: 当日出馬表の特徴量のみを生成（ラベルなし）

---

## 主要関数

```python
def build_training_dataset(
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """期間内の全レース・全馬の特徴量 + ラベルを含む DataFrame を返す。
    1行 = 1頭 × 1レース。
    """

def build_inference_features(race_id: str) -> pd.DataFrame:
    """当日レースの出馬表から特徴量 DataFrame を返す（ラベルなし）。"""
```

---

## 出力 DataFrame の構造

```
race_id | horse_num | <特徴量列> ... | win_label | place_label
--------|-----------|----------------|-----------|------------
20260406...01 |  1  |  ...          |     0     |      1
20260406...01 |  2  |  ...          |     1     |      1
```

- `win_label`: finish_pos == 1 → 1, else → 0（推論時は存在しない）
- `place_label`: finish_pos <= 3 → 1, else → 0（推論時は存在しない）
- 取消・除外馬は除外する（scratch == True または finish_pos IS NULL + finish_status != '完走'）

---

## 特徴量一覧

### A. レース条件（races + entries テーブル）

| 特徴量名 | 型 | 元データ | 備考 |
|---------|---|---------|------|
| `distance` | int | races.distance | |
| `course_type` | cat | races.course_type | 芝=0, ダート=1 |
| `direction` | cat | races.direction | 右=0, 左=1, 直線=2 |
| `track_cond` | cat | races.track_cond | 良=0, 稍重=1, 重=2, 不良=3 |
| `race_class_rank` | int | races.race_class | 下記マッピングで数値化 |
| `num_horses` | int | races.num_horses | |
| `venue_code` | cat | races.venue_code | |
| `weight_type` | cat | races.weight_type | 馬齢=0, ハンデ=1, 別定=2 |
| `month` | int | races.held_date | 季節性を捉える |
| `gate_num` | int | entries.gate_num | |
| `horse_num` | int | entries.horse_num | |
| `burden_weight` | float | entries.burden_weight | |
| `horse_weight` | int | entries.horse_weight | |
| `weight_diff` | int | entries.weight_diff | |

**race_class_rank のマッピング:**

| クラス | rank |
|-------|------|
| 新馬 | 0 |
| 未勝利 | 1 |
| 1勝クラス | 2 |
| 2勝クラス | 3 |
| 3勝クラス | 4 |
| オープン | 5 |
| G3 | 6 |
| G2 | 7 |
| G1 | 8 |

### B. 馬の過去成績（results + races を集計）

過去成績は**当該レースの held_date より前**のデータのみ使用（データリーク防止）。

| 特徴量名 | 集計対象 | 備考 |
|---------|---------|------|
| `career_runs` | 通算出走数 | |
| `career_wins` | 通算勝利数 | |
| `career_win_rate` | 通算勝率 | career_wins / career_runs |
| `career_place_rate` | 通算複勝率 | finish_pos <= 3 の割合 |
| `recent3_avg_finish` | 直近3走の平均着順 | |
| `recent5_avg_finish` | 直近5走の平均着順 | |
| `last_race_finish` | 前走着順 | |
| `last_race_days` | 前走からの間隔（日数） | |
| `last_race_class_rank` | 前走クラスランク | クラス昇降を捉える |
| `avg_last3f_recent5` | 直近5走の上がり3F平均 | |
| `best_time_same_dist` | 同距離のベストタイム（秒） | |
| `win_rate_same_dist` | 同距離の勝率 | |
| `place_rate_same_dist` | 同距離の複勝率 | |
| `win_rate_same_venue` | 当競馬場の勝率 | |
| `win_rate_same_course` | 芝/ダート別勝率 | |
| `win_rate_same_cond` | 馬場状態別勝率 | |
| `avg_4c_position_recent5` | 直近5走の4コーナー通過順平均 | 脚質指標（passing_orderの最後の数値） |
| `is_first_race` | 今走が初出走か | 新馬フラグ |

### C. 騎手実績

| 特徴量名 | 集計対象 | 備考 |
|---------|---------|------|
| `jockey_win_rate_90d` | 騎手の直近90日勝率 | |
| `jockey_place_rate_90d` | 騎手の直近90日複勝率 | |
| `jockey_win_rate_same_venue` | 騎手の当競馬場勝率 | |
| `jockey_horse_runs` | 騎手×馬のコンビ出走数 | |
| `jockey_horse_win_rate` | 騎手×馬のコンビ勝率 | |

### D. 調教師実績

| 特徴量名 | 集計対象 | 備考 |
|---------|---------|------|
| `trainer_win_rate_90d` | 調教師の直近90日勝率 | |
| `trainer_place_rate_90d` | 調教師の直近90日複勝率 | |
| `trainer_win_rate_same_venue` | 調教師の当競馬場勝率 | |

### E. 市場評価（推論時のみ使用）

| 特徴量名 | 元データ | 備考 |
|---------|---------|------|
| `win_odds` | odds.odds_low (win) | 直近スナップショット |
| `popularity` | odds から計算 | |
| `odds_rank_ratio` | popularity / num_horses | |

> 学習時は results.win_odds を使用（確定オッズ）。推論時は odds テーブルから最新スナップショットを使用。

### F. Phase 3 では未実装（将来追加）

- 調教タイム特徴量（training_times の実データ検証後）
- トラックバイアス（track_bias_log の計算ロジック実装後）
- 血統特徴量（horse detail スクレイパー実装後）

---

## 実装方針

### ディレクトリ構成

```
features/
├── __init__.py
└── feature_builder.py
```

### NULL 値の扱い

| ケース | 処理 |
|------|------|
| 新馬（初出走） | 過去成績系特徴量を 0 or NaN、`is_first_race=1` |
| track_cond 等が NULL | LightGBM は NaN をそのまま扱える → そのまま NaN で渡す |
| 馬体重が NULL（計不） | NaN のまま |

LightGBM は NaN を特別な分岐ノードとして処理できるため、無理に埋めない。

### 過去成績の SQL 設計

過去成績はレース単位でまとめて SQL で集計し、DataFrame にマージする方針（Python ループより大幅に高速）。

```sql
-- 例: 1レースの全馬の通算成績を一括取得
SELECT
    e.horse_id,
    COUNT(r.finish_pos)                                          AS career_runs,
    SUM(CASE WHEN r.finish_pos = 1 THEN 1 ELSE 0 END)           AS career_wins,
    AVG(r.finish_pos)                                            AS career_avg_finish,
    ...
FROM entries e
JOIN results r USING (race_id, horse_num)
JOIN races rc ON e.race_id = rc.race_id
WHERE e.horse_id IN (SELECT horse_id FROM entries WHERE race_id = :current_race_id)
  AND rc.held_date < :current_held_date
GROUP BY e.horse_id
```

### カテゴリ変数の扱い

文字列のカテゴリ変数（course_type, venue_code 等）は数値にエンコードしてから LightGBM に渡す。
LightGBM の `categorical_feature` パラメータを使い、ツリー内で適切に分岐させる。

---

## 実装スコープ（Issue #7）

1. `features/feature_builder.py` の実装
   - `build_training_dataset(start_date, end_date)` → DataFrame
   - `build_inference_features(race_id)` → DataFrame
   - 特徴量 A・B・C・D を実装（E は推論パイプラインで追加）

2. テスト
   - 特徴量計算のユニットテスト（フィクスチャDBデータを使用）
   - データリーク検証（held_date フィルタが効いているか）

3. 設計ドキュメント（本文書）

## 実装しないこと（Issue #7 スコープ外）

- モデル学習・保存（Issue #8）
- リアルタイムオッズの組み込み（Phase 5）
- 調教タイム特徴量（実データ検証後に追加）
- 血統特徴量（horse detail スクレイパー実装後）

---

## 課題・検討事項

1. **horse_weight が NULL のケース**: 出馬表では馬体重は未確定のため、推論時は weight_diff=0 / horse_weight=NaN になる可能性が高い。受け入れる。

2. **新馬戦の扱い**: 過去成績系の特徴量が全て NULL になる。`is_first_race` フラグで LightGBM に判断させる。

3. **騎手・調教師の過去成績ウィンドウ**: 90日（約20開催分）が標準的だが、最終的にはハイパーパラメータとして調整する余地を残す。

4. **カテゴリ変数の数**: venue_code（10種）/ course_type（2種）/ direction（3種）/ track_cond（4種）/ weight_type（3種）は LightGBM の `categorical_feature` で処理。

5. **race_class の取りこぼし**: スクレイパーが取得できなかった race_class は NULL になる。この場合 race_class_rank も NaN とする。
