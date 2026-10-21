import importlib.util
from pathlib import Path
from bs4 import BeautifulSoup

spec = importlib.util.spec_from_file_location('catalog_scraper', Path(__file__).parents[1] / 'qscrape' / 'catalog.py')
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


def test_unselected_weekdays_are_not_class_meetings():
    html = '''<div><div aria-label="Week Days">
      <span aria-label="Monday, selected"></span>
      <span aria-label="Tuesday, not selected"></span>
      <span aria-label="Wednesday, unselected"></span>
    </div>10:00 am - 11:15 am</div>'''
    assert catalog.parse_meetings(BeautifulSoup(html, 'html.parser')) == [
        {'days': ['M'], 'time': '10:00am-11:15am'}]
