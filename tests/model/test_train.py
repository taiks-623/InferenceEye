"""model/train.py のユニットテスト（DB・LightGBM 接続不要）"""

import pandas as pd
import pytest

from model.train import compute_recovery_rate, compute_sample_weights


class TestComputeSampleWeights:
    def _make_df(self, years: list[int]) -> pd.DataFrame:
        return pd.DataFrame({"held_date": pd.to_datetime([f"{y}-06-01" for y in years])})

    def test_recent1year_weight(self):
        """直近1年（val_year - 1）は重み 2.0 になる。"""
        df = self._make_df([2023])
        weights = compute_sample_weights(df, val_year=2024)
        assert weights.iloc[0] == pytest.approx(2.0)

    def test_recent2year_weight(self):
        """直近2年（val_year - 2）は重み 1.5 になる。"""
        df = self._make_df([2022])
        weights = compute_sample_weights(df, val_year=2024)
        assert weights.iloc[0] == pytest.approx(1.5)

    def test_older_weight(self):
        """それ以前は重み 1.0 になる。"""
        df = self._make_df([2018])
        weights = compute_sample_weights(df, val_year=2024)
        assert weights.iloc[0] == pytest.approx(1.0)

    def test_multiple_years(self):
        """複数年が混在する場合に正しく割り当てられる。"""
        df = self._make_df([2018, 2022, 2023])
        weights = compute_sample_weights(df, val_year=2024)
        assert weights.iloc[0] == pytest.approx(1.0)
        assert weights.iloc[1] == pytest.approx(1.5)
        assert weights.iloc[2] == pytest.approx(2.0)


class TestComputeRecoveryRate:
    def _make_df(
        self,
        probas: list[float],
        odds: list[float],
        labels: list[int],
    ) -> pd.DataFrame:
        return pd.DataFrame({"proba": probas, "odds": odds, "label": labels})

    def test_perfect_prediction(self):
        """EV > 1.0 かつ全的中の場合、回収率 = オッズの平均 × 100%。"""
        df = self._make_df([0.5, 0.5], [3.0, 3.0], [1, 1])
        rate = compute_recovery_rate(df, "proba", "odds", "label", ev_threshold=1.0)
        assert rate == pytest.approx(300.0)

    def test_no_bets(self):
        """EV 閾値を超えるものがない場合は NaN を返す。"""
        df = self._make_df([0.1, 0.1], [5.0, 5.0], [1, 0])
        rate = compute_recovery_rate(df, "proba", "odds", "label", ev_threshold=2.0)
        import math

        assert math.isnan(rate)

    def test_partial_hits(self):
        """2回買って1回的中の場合の回収率を正しく計算する。"""
        # proba=0.5, odds=3.0 → EV=1.5 > 1.0 → 購入
        # 1勝1敗 → return = 3.0, bet = 2 → recovery = 150%
        df = self._make_df([0.5, 0.5], [3.0, 3.0], [1, 0])
        rate = compute_recovery_rate(df, "proba", "odds", "label", ev_threshold=1.0)
        assert rate == pytest.approx(150.0)

    def test_ev_threshold_filter(self):
        """EV 閾値で正しくフィルタされる。"""
        # row0: EV=0.5*3.0=1.5 > 1.2 → 購入
        # row1: EV=0.3*3.0=0.9 < 1.2 → 非購入
        df = self._make_df([0.5, 0.3], [3.0, 3.0], [1, 1])
        rate = compute_recovery_rate(df, "proba", "odds", "label", ev_threshold=1.2)
        # 1回買って1回的中 → return = 3.0, bet = 1 → recovery = 300%
        assert rate == pytest.approx(300.0)
