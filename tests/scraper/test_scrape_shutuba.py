"""scrape_shutuba.py のユニットテスト"""

from bs4 import BeautifulSoup

from scraper.scrape_shutuba import parse_shutuba


def _make_shutuba_html(rows: list[str]) -> str:
    """テスト用の出馬表 HTML を生成する。"""
    rows_html = "\n".join(rows)
    return f"""
    <html><body>
    <table class="Shutuba_Table">
      <tbody>
        {rows_html}
      </tbody>
    </table>
    </body></html>
    """


def test_parse_shutuba_basic():
    """基本的な出馬表行をパースできる。"""
    row = """
    <tr>
      <td>1</td>
      <td>1</td>
      <td></td>
      <td><a href="/horse/2023100001/">テスト馬</a></td>
      <td>牡3</td>
      <td>56.0</td>
      <td><a href="/jockey/00123/">テスト騎手</a></td>
      <td></td>
      <td></td>
      <td></td>
      <td></td>
      <td></td>
      <td></td>
      <td></td>
      <td></td>
      <td></td>
      <td></td>
      <td></td>
      <td><a href="/trainer/00456/">テスト調教師</a></td>
    </tr>
    """
    html = _make_shutuba_html([row])
    soup = BeautifulSoup(html, "lxml")
    entries, horses, jockeys, trainers = parse_shutuba(soup, "202606030401")

    assert len(entries) == 1
    assert entries[0]["race_id"] == "202606030401"
    assert entries[0]["horse_num"] == 1
    assert entries[0]["gate_num"] == 1
    assert entries[0]["horse_id"] == "2023100001"
    assert entries[0]["jockey_id"] == "00123"
    assert entries[0]["trainer_id"] == "00456"
    assert entries[0]["scratch"] is False


def test_parse_shutuba_no_table():
    """テーブルが存在しない場合は空リストを返す。"""
    soup = BeautifulSoup("<html><body></body></html>", "lxml")
    entries, horses, jockeys, trainers = parse_shutuba(soup, "202606030401")
    assert entries == []
    assert horses == []


def test_parse_shutuba_invalid_row():
    """列数が足りない行はスキップされる。"""
    row = "<tr><td>1</td><td>2</td></tr>"
    html = _make_shutuba_html([row])
    soup = BeautifulSoup(html, "lxml")
    entries, horses, jockeys, trainers = parse_shutuba(soup, "202606030401")
    assert entries == []
