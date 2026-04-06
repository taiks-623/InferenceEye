"""scrape_results.py のユニットテスト（フィクスチャ HTML を使用・ネットワーク接続不要）"""

from scraper.scrape_results import extract_venue_code, parse_distance_and_course


def test_extract_venue_code():
    assert extract_venue_code("202305010101") == "05"  # 東京
    assert extract_venue_code("202309020301") == "09"  # 阪神


def test_parse_distance_and_course_turf():
    course_type, distance, direction = parse_distance_and_course("芝1600m（右）")
    assert course_type == "芝"
    assert distance == 1600
    assert direction == "右"


def test_parse_distance_and_course_dirt():
    course_type, distance, direction = parse_distance_and_course("ダ1400m（左）")
    assert course_type == "ダート"
    assert distance == 1400
    assert direction == "左"


def test_parse_distance_and_course_straight():
    course_type, distance, direction = parse_distance_and_course("芝1000m（直線）")
    assert course_type == "芝"
    assert distance == 1000
    assert direction == "直線"


def test_parse_distance_and_course_obstacle():
    """障害レースは全て None を返す"""
    course_type, distance, direction = parse_distance_and_course("障3270m")
    assert course_type is None
    assert distance is None
    assert direction is None
