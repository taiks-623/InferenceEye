# Phase 3 設計：LightGBM モデル学習・ウォークフォワード検証

関連 Issue: #8

---

## 概要

`model/` ディレクトリ以下に、学習・評価・推論を担う3つのスクリプトを実装する。

| ファイル | 役割 |
|---------|------|
| `model/train.py` | LightGBM モデルの学習・ウォークフォワード検証・モデル保存 |
| `model/predict.py` | 保存済みモデルを使った推論・期待値計算・predictions テーブル保存 |
| `model/evaluate.py` | 回収率シミュレーション・AUC 集計・特徴量重要度出力 |

---

## モデル構成

- **モデルA（単勝）**: `win_label`（`finish_pos == 1`）を二値分類
- **モデルB（複勝）**: `place_label`（`finish_pos <= 3`）を二値分類

2モデルを独立して学習・管理する。

---

## 特徴量カラム定義

`feature_builder.py` の出力から以下のカラムを使用する。

```python
FEATURE_COLS = [
    # A. レース条件
    "distance", "course_type_enc", "direction_enc", "track_cond_enc",
    "race_class_rank", "num_horses", "venue_code", "weight_type_enc", "month",
    "gate_num", "horse_num", "burden_weight", "horse_weight", "weight_diff",
    # B. 馬の過去成績
    "career_runs", "career_win_rate", "career_place_rate", "career_avg_finish",
    "recent3_avg_finish", "recent5_avg_finish",
    "last_race_finish", "last_race_days", "last_race_class_rank",
    "avg_last3f_recent5", "is_first_race",
    "win_rate_same_dist", "place_rate_same_dist", "best_time_same_dist",
    "win_rate_same_course", "win_rate_same_venue", "win_rate_same_cond",
    # C. 騎手
    "jockey_win_rate_90d", "jockey_place_rate_90d", "jockey_win_rate_venue",
    "combo_runs", "jockey_horse_win_rate",
    # D. 調教師
    "trainer_win_rate_90d", "trainer_place_rate_90d", "trainer_win_rate_venue",
]

CATEGORICAL_COLS = [
    "course_type_enc", "direction_enc", "track_cond_enc",
    "venue_code", "weight_type_enc",
]
```

---

## LightGBM パラメータ（初期値）

```python
WIN_PARAMS = {
    "objective": "binary",
    "metric": "auc",
    "learning_rate": 0.05,
    "num_leaves": 63,
    "scale_pos_weight": 13,      # 単勝クラス不均衡補正（約1:13）
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "min_child_samples": 50,
    "cat_smooth": 10,
    "verbosity": -1,
}

PLACE_PARAMS = {
    **WIN_PARAMS,
    "scale_pos_weight": 4,       # 複勝クラス不均衡補正（約1:4）
}
```

---

## ウォークフォワード検証

固定分割ではなく時系列を崩さないウォークフォワード検証を使用する。

```
Step1: 2016〜2019 で学習 → 2020 で検証
Step2: 2016〜2020 で学習 → 2021 で検証
Step3: 2016〜2021 で学習 → 2022 で検証
Step4: 2016〜2022 で学習 → 2023 で検証
Step5: 2016〜2023 で学習 → 2024 で検証
```

各ステップで AUC・回収率を計算し、5ステップの平均を最終評価値とする。

### 注意: 直近データの重み付け

直近データほど傾向を反映しているため、`sample_weight` で重み付けする。

```python
# 年ごとの重み（直近1年=2.0倍、直近2年=1.5倍、それ以前=1.0倍）
def compute_sample_weights(df: pd.DataFrame, current_year: int) -> pd.Series:
    weights = pd.Series(1.0, index=df.index)
    weights[df["held_date"].dt.year >= current_year - 1] = 2.0
    weights[df["held_date"].dt.year == current_year - 2] = 1.5
    return weights
```

---

## 実装ファイル詳細

### `model/train.py`

```
python model/train.py --walk-forward      # ウォークフォワード検証
python model/train.py --train-final       # 全データで本番モデルを学習・保存
python model/train.py --year-from 2016 --year-to 2024 --walk-forward
```

**処理フロー（`--walk-forward`）:**
```
1. build_training_dataset(2016-01-01, 今日) でデータ取得
2. 年単位でウォークフォワードのウィンドウを定義
3. 各ステップで train/val を分割
4. LightGBM の cv または train+predict
5. AUC・回収率を計算し MLflow に記録
6. 全ステップ結果をまとめて表示
```

**処理フロー（`--train-final`）:**
```
1. 全データで学習（検証なし）
2. models/YYYYMM_win.lgb, models/YYYYMM_place.lgb に保存
3. 特徴量重要度を docs/ に出力
```

### `model/predict.py`

```
python model/predict.py --race-id 202606030401
```

**処理フロー:**
```
1. build_inference_features(race_id) で特徴量取得
2. 最新の models/*.lgb を読み込む
3. win_proba / place_proba を予測
4. 確率を合計=1 に正規化（同一レース内の相対確率）
5. 期待値 = proba × オッズ を計算
6. predictions テーブルに保存
```

### `model/evaluate.py`

回収率シミュレーション（EV > 1.0 で買い続けた場合）と特徴量重要度の可視化。

---

## 評価指標

| 指標 | 説明 | 目標値 |
|------|------|--------|
| AUC | 確率の分離能力 | 0.70 以上 |
| 回収率 | EV > 1.0 で全ベットした場合 | 85% 以上 |
| 的中率 | 予測1位の実際の1着割合 | 参考値のみ |

回収率が最終的な最重要指標。AUC は 0.70 を目安にするが、AUC が高くても回収率が悪いモデルは意味がない。

---

## モデルの保存・バージョン管理

```
model/
├── train.py
├── predict.py
├── evaluate.py
└── models/
    ├── 202604_win.lgb       # 本番モデル（2026年4月更新）
    └── 202604_place.lgb
```

- `models/*.lgb` は `.gitignore` で除外（容量大）
- モデルのパラメータ・精度は MLflow で記録

### 最新モデルの選択

```python
import glob, os
def load_latest_model(model_type: str) -> lgb.Booster:
    pattern = f"model/models/*_{model_type}.lgb"
    files = sorted(glob.glob(pattern))
    return lgb.Booster(model_file=files[-1])
```

---

## MLflow との連携

```python
import mlflow

with mlflow.start_run(run_name=f"wf_step_{val_year}"):
    mlflow.log_params({
        "train_years": f"2016-{val_year - 1}",
        "val_year": val_year,
        "num_leaves": params["num_leaves"],
        "learning_rate": params["learning_rate"],
    })
    mlflow.log_metrics({
        "win_auc": win_auc,
        "place_auc": place_auc,
        "win_recovery": win_recovery,
        "place_recovery": place_recovery,
    })
```

---

## 実装スコープ（Issue #8）

1. `model/train.py`
   - ウォークフォワード検証
   - 全データ学習・モデル保存
   - MLflow へのパラメータ・指標記録

2. `model/predict.py`
   - 特徴量取得（`build_inference_features` を利用）
   - 確率正規化・期待値計算
   - `predictions` テーブルへの保存

3. `model/evaluate.py`
   - 回収率シミュレーション
   - 特徴量重要度プロット

4. テスト
   - `compute_sample_weights` の単体テスト
   - 期待値計算ロジックのテスト

## 実装しないこと（Issue #8 スコープ外）

- ハイパーパラメータ最適化（Phase 4 の AI 実験ループで実施）
- AIフィルタ連携（Phase 4）
- Discord 通知連携（Phase 5）
- 調教・血統特徴量（追加データ取得後）

---

## 課題・検討事項

1. **cold start 問題**: 学習開始年（2016）の馬は過去成績がないため、`is_first_race` フラグと NaN 埋めで対応する。

2. **オッズのデータリーク**: 確定オッズ（results.win_odds）は結果が決まってからしか取れない。学習時は `win_odds` を特徴量に含めてよいが、推論時は当日の `odds` テーブルから取得する必要がある。この非対称性を `predict.py` で適切に処理する。

3. **クラス不均衡**: 単勝（1/頭数≈1/14）は強い不均衡がある。`scale_pos_weight` で補正するが、実際の頭数分布に合わせて調整が必要な場合がある。

4. **ウォークフォワードの検証窓**: 1年単位が標準的だが、データ量によっては半年単位も検討。
