"""scrape_odds.py のユニットテスト"""

from datetime import datetime, timezone

from bs4 import BeautifulSoup

from scraper.scrape_odds import parse_place_odds, parse_win_odds


def _make_odds_html(table_id: str, rows: list[str]) -> str:
    rows_html = "\n".join(rows)
    return f"""
    <html><body>
    <div id="{table_id}">
      <table>
        <tbody>
          {rows_html}
        </tbody>
      </table>
    </div>
    </body></html>
    """


FETCHED_AT = datetime(2026, 4, 5, 9, 0, 0, tzinfo=timezone.utc)


def test_parse_win_odds_basic():
    """単勝オッズをパースできる。"""
    rows = [
        "<tr><td>1</td><td>テスト馬</td><td>3.5</td></tr>",
        "<tr><td>2</td><td>テスト馬2</td><td>12.0</td></tr>",
    ]
    html = _make_odds_html("odds_tan_b", rows)
    soup = BeautifulSoup(html, "lxml")
    result = parse_win_odds(soup, "202606030401", FETCHED_AT)

    assert len(result) == 2
    assert result[0]["odds_type"] == "win"
    assert result[0]["odds_low"] == 3.5
    assert result[0]["odds_high"] is None
    assert result[0]["horse_num"] == 1
    assert result[0]["fetched_at"] == FETCHED_AT


def test_parse_place_odds_range():
    """複勝オッズ（範囲）をパースできる。"""
    rows = [
        "<tr><td>1</td><td>テスト馬</td><td>1.5 - 2.3</td></tr>",
    ]
    html = _make_odds_html("odds_fuku_b", rows)
    soup = BeautifulSoup(html, "lxml")
    result = parse_place_odds(soup, "202606030401", FETCHED_AT)

    assert len(result) == 1
    assert result[0]["odds_type"] == "place"
    assert result[0]["odds_low"] == 1.5
    assert result[0]["odds_high"] == 2.3


def test_parse_win_odds_no_table():
    """テーブルが存在しない場合は空リストを返す。"""
    soup = BeautifulSoup("<html><body></body></html>", "lxml")
    result = parse_win_odds(soup, "202606030401", FETCHED_AT)
    assert result == []
