"""scrape_results.py のユニットテスト（フィクスチャ HTML を使用・ネットワーク接続不要）"""

from datetime import date

from bs4 import BeautifulSoup

from scraper.scrape_results import (
    extract_venue_code,
    parse_distance_and_course,
    parse_entries_and_results,
    parse_race_info,
)


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


def _make_race_data01(content: str) -> BeautifulSoup:
    html = f'<html><body><div class="RaceData01">{content}</div></body></html>'
    return BeautifulSoup(html, "lxml")


def test_parse_race_info_direction_outside_span():
    """方向がスパン外のテキストにある場合も取得できる。"""
    html = """
    <html><body>
    <div class="RaceData01">
      09:00 / <span>ダ1800m</span>(右) / 天候:晴 / 馬場:良
    </div>
    <div class="RaceData02"></div>
    </body></html>
    """
    soup = BeautifulSoup(html, "lxml")
    race = parse_race_info(soup, "202606030401", date(2026, 6, 3))
    assert race is not None
    assert race["course_type"] == "ダート"
    assert race["distance"] == 1800
    assert race["direction"] == "右"
    assert race["weather"] == "晴"
    assert race["track_cond"] == "良"


def test_parse_race_info_track_cond_fullwidth_colon():
    """馬場状態が全角コロン形式（馬場：良）でも取得できる。"""
    html = """
    <html><body>
    <div class="RaceData01">
      09:00 / <span>芝1600m</span>(左) / 天候：曇 / 馬場：稍重
    </div>
    <div class="RaceData02"></div>
    </body></html>
    """
    soup = BeautifulSoup(html, "lxml")
    race = parse_race_info(soup, "202606030401", date(2026, 6, 3))
    assert race is not None
    assert race["track_cond"] == "稍重"
    assert race["weather"] == "曇"


def test_parse_race_info_num_horses_and_prize():
    """頭数と1着賞金を RaceData02 からパースできる。"""
    html = """
    <html><body>
    <div class="RaceData01">
      09:00 / <span>芝2000m</span>(右) / 天候:晴 / 馬場:良
    </div>
    <div class="RaceData02">
      <span>4歳以上</span>
      <span>オープン</span>
      <span>18頭</span>
      <span>本賞金:590,240,150,89,59万円</span>
    </div>
    </body></html>
    """
    soup = BeautifulSoup(html, "lxml")
    race = parse_race_info(soup, "202606030401", date(2026, 6, 3))
    assert race is not None
    assert race["num_horses"] == 18
    assert race["prize_1st"] == 590
    assert race["race_class"] == "オープン"


def _make_result_table(rows_html: str) -> BeautifulSoup:
    html = f"""
    <html><body>
    <div class="ResultTableWrap">
      <table>
        <tbody>
          {rows_html}
        </tbody>
      </table>
    </div>
    </body></html>
    """
    return BeautifulSoup(html, "lxml")


def _make_result_row(
    finish_pos="1",
    gate="1",
    horse_num="1",
    horse_id="2023100001",
    horse_name="テスト馬",
    jockey_id="01170",
    jockey_name="テスト騎手",
    time="1:12.3",
    margin="",
    popularity="2",
    win_odds="3.6",
    last_3f="36.9",
    passing_order="2-1",
    trainer_id="01108",
    trainer_name="テスト調教師",
    weight="450(-2)",
) -> str:
    return f"""
    <tr>
      <td>{finish_pos}</td>
      <td>{gate}</td>
      <td>{horse_num}</td>
      <td><a href="https://db.netkeiba.com/horse/{horse_id}/">{horse_name}</a></td>
      <td>牡3</td>
      <td>56.0</td>
      <td><a href="https://db.netkeiba.com/jockey/result/recent/{jockey_id}/">{jockey_name}</a></td>
      <td>{time}</td>
      <td>{margin}</td>
      <td>{popularity}</td>
      <td class="Odds Txt_R"><span class="Odds_Ninki">{win_odds}</span></td>
      <td>{last_3f}</td>
      <td>{passing_order}</td>
      <td><a href="https://db.netkeiba.com/trainer/result/{trainer_id}/">{trainer_name}</a></td>
      <td>{weight}</td>
    </tr>
    """


def test_parse_entries_and_results_basic():
    """基本的な結果行をパースできる。"""
    row = _make_result_row()
    soup = _make_result_table(row)
    entries, results = parse_entries_and_results(soup, "202606030401")

    assert len(entries) == 1
    assert len(results) == 1

    entry = entries[0]
    assert entry["horse_num"] == 1
    assert entry["horse_id"] == "2023100001"
    assert entry["jockey_id"] == "01170"
    assert entry["trainer_id"] == "01108"
    assert entry["horse_weight"] == 450
    assert entry["weight_diff"] == -2

    result = results[0]
    assert result["finish_pos"] == 1
    assert result["win_odds"] == 3.6
    assert result["popularity"] == 2
    assert result["last_3f"] == 36.9
    assert result["passing_order"] == "2-1"
    assert result["time_sec"] == 72.3


def test_parse_entries_jockey_id_from_url():
    """騎手 ID が URL の末尾数値から正しく取得される。"""
    row = _make_result_row(jockey_id="01170")
    soup = _make_result_table(row)
    entries, _ = parse_entries_and_results(soup, "202606030401")
    assert entries[0]["jockey_id"] == "01170"


def test_parse_entries_trainer_id_from_url():
    """調教師 ID が URL の末尾数値から正しく取得される。"""
    row = _make_result_row(trainer_id="01108")
    soup = _make_result_table(row)
    entries, _ = parse_entries_and_results(soup, "202606030401")
    assert entries[0]["trainer_id"] == "01108"
