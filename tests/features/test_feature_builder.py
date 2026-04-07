"""feature_builder.py のユニットテスト（DB接続不要）"""

from features.feature_builder import (
    COURSE_TYPE_MAP,
    DIRECTION_MAP,
    RACE_CLASS_RANK,
    TRACK_COND_MAP,
    _map_race_class,
    _parse_last_corner_position,
)


class TestMapRaceClass:
    def test_g1(self):
        assert _map_race_class("G1") == 8

    def test_g2(self):
        assert _map_race_class("G2") == 7

    def test_g3(self):
        assert _map_race_class("G3") == 6

    def test_open(self):
        assert _map_race_class("オープン") == 5

    def test_class3(self):
        assert _map_race_class("3勝クラス") == 4

    def test_class2(self):
        assert _map_race_class("2勝クラス") == 3

    def test_class1(self):
        assert _map_race_class("1勝クラス") == 2

    def test_maiden(self):
        assert _map_race_class("未勝利") == 1

    def test_newborn(self):
        assert _map_race_class("新馬") == 0

    def test_partial_match(self):
        """部分一致: 'G1' が含まれる文字列でも取得できる。"""
        assert _map_race_class("天皇賞（春）G1") == 8

    def test_none_input(self):
        assert _map_race_class(None) is None

    def test_unknown_class(self):
        assert _map_race_class("特殊条件") is None


class TestParseLastCornerPosition:
    def test_four_corners(self):
        """4コーナー制のレース（通過順: 3-2-2-1）。"""
        assert _parse_last_corner_position("3-2-2-1") == 1

    def test_two_corners(self):
        """2コーナー制のレース（短距離）。"""
        assert _parse_last_corner_position("5-3") == 3

    def test_single(self):
        assert _parse_last_corner_position("1") == 1

    def test_none(self):
        assert _parse_last_corner_position(None) is None

    def test_empty(self):
        assert _parse_last_corner_position("") is None


class TestEncodingMaps:
    def test_course_type_covers_all(self):
        assert "芝" in COURSE_TYPE_MAP
        assert "ダート" in COURSE_TYPE_MAP

    def test_direction_covers_all(self):
        for d in ["右", "左", "直線"]:
            assert d in DIRECTION_MAP

    def test_track_cond_covers_all(self):
        for c in ["良", "稍重", "重", "不良"]:
            assert c in TRACK_COND_MAP

    def test_race_class_rank_ordered(self):
        """クラスランクが正しく昇順になっている。"""
        assert RACE_CLASS_RANK["新馬"] < RACE_CLASS_RANK["未勝利"]
        assert RACE_CLASS_RANK["未勝利"] < RACE_CLASS_RANK["1勝クラス"]
        assert RACE_CLASS_RANK["3勝クラス"] < RACE_CLASS_RANK["オープン"]
        assert RACE_CLASS_RANK["オープン"] < RACE_CLASS_RANK["G3"]
        assert RACE_CLASS_RANK["G3"] < RACE_CLASS_RANK["G2"]
        assert RACE_CLASS_RANK["G2"] < RACE_CLASS_RANK["G1"]
