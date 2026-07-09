"""Unit tests for the Tier 4 search fallback (daalder/scraping/search.py) and
its wiring into extract_price(). Uses only the standard library (unittest +
unittest.mock) so it runs without adding a test-framework dependency.

Run with: python -m unittest discover -s tests
"""

from __future__ import annotations

import os
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "postgresql://test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from daalder.scraping import PriceResult, extract_price
from daalder.scraping.fetch import FetchResult
from daalder.scraping.search import (
    StoreCandidate,
    StoreSearchResult,
    _extract_last_json_value,
    find_other_stores_via_search,
    find_price_via_search,
)


class TestExtractLastJsonValue(unittest.TestCase):
    def test_straight_object(self):
        self.assertEqual(_extract_last_json_value('{"a": 1}'), {"a": 1})

    def test_fenced_object(self):
        raw = '```json\n{"a": 1}\n```'
        self.assertEqual(_extract_last_json_value(raw), {"a": 1})

    def test_narrated_then_object(self):
        raw = 'Ik heb gezocht en de prijs gevonden. Hier is het resultaat:\n{"found": true, "price": 12.5}'
        self.assertEqual(_extract_last_json_value(raw), {"found": True, "price": 12.5})

    def test_narrated_then_array(self):
        raw = 'Ik vond deze winkels:\n[{"domain": "a.nl"}, {"domain": "b.nl"}]'
        self.assertEqual(
            _extract_last_json_value(raw, brackets=("[", "]")),
            [{"domain": "a.nl"}, {"domain": "b.nl"}],
        )

    def test_unparsable_returns_none(self):
        self.assertIsNone(_extract_last_json_value("dit is geen JSON, sorry"))

    def test_nested_object_picks_full_balanced_block(self):
        raw = 'Resultaat:\n{"found": true, "meta": {"x": 1}, "price": 9.99}'
        self.assertEqual(
            _extract_last_json_value(raw),
            {"found": True, "meta": {"x": 1}, "price": 9.99},
        )


class TestFindPriceViaSearch(unittest.IsolatedAsyncioTestCase):
    async def test_found_with_valid_price(self):
        with patch(
            "daalder.scraping.search._run_search",
            new=AsyncMock(
                return_value='{"found": true, "name": "Widget", "price": 49.95, "currency": "EUR", "in_stock": true}'
            ),
        ):
            result = await find_price_via_search("https://shop.nl/widget", "shop.nl")
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.strategy, "search")
        self.assertEqual(result.price, Decimal("49.95"))
        self.assertEqual(result.name, "Widget")
        self.assertTrue(result.in_stock)

    async def test_not_found(self):
        with patch(
            "daalder.scraping.search._run_search",
            new=AsyncMock(return_value='{"found": false, "price": null}'),
        ):
            result = await find_price_via_search("https://shop.nl/widget", "shop.nl")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "not_found")

    async def test_invalid_json_response(self):
        with patch(
            "daalder.scraping.search._run_search",
            new=AsyncMock(return_value="ik kon niets vinden, sorry"),
        ):
            result = await find_price_via_search("https://shop.nl/widget", "shop.nl")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error, "invalid_search_json")

    async def test_call_raises_exception(self):
        with patch(
            "daalder.scraping.search._run_search",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await find_price_via_search("https://shop.nl/widget", "shop.nl")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error, "search_call_failed")


class TestFindOtherStoresViaSearch(unittest.IsolatedAsyncioTestCase):
    async def test_filters_excluded_domain_and_caps_results(self):
        raw = (
            '[{"domain": "excluded.nl", "url": "https://excluded.nl/p", "price": 10},'
            '{"domain": "a.nl", "url": "https://a.nl/p", "name": "A", "price": 11.5, "currency": "EUR"},'
            '{"domain": "b.nl", "url": "https://b.nl/p", "price": 12},'
            '{"domain": "c.nl", "url": "https://c.nl/p", "price": 13}]'
        )
        with patch("daalder.scraping.search._run_search", new=AsyncMock(return_value=raw)):
            result = await find_other_stores_via_search("Widget", ["excluded.nl"], max_results=2)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual([c.domain for c in result.candidates], ["a.nl", "b.nl"])
        self.assertEqual(result.candidates[0].price, Decimal("11.5"))

    async def test_empty_after_filtering_is_not_found(self):
        raw = '[{"domain": "excluded.nl", "url": "https://excluded.nl/p"}]'
        with patch("daalder.scraping.search._run_search", new=AsyncMock(return_value=raw)):
            result = await find_other_stores_via_search("Widget", ["excluded.nl"], max_results=5)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "not_found")

    async def test_non_list_json_is_error(self):
        with patch(
            "daalder.scraping.search._run_search", new=AsyncMock(return_value='{"not": "a list"}')
        ):
            result = await find_other_stores_via_search("Widget", [], max_results=5)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "error")


class TestExtractPriceSearchTierRouting(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_fetch_falls_back_to_search_and_succeeds(self):
        blocked_fetch = FetchResult(ok=False, status_code=403, html=None, blocked=True, final_url="https://shop.nl/p")
        search_ok = PriceResult(ok=True, status="ok", price=Decimal("9.99"), strategy="search")
        with patch("daalder.scraping.fetch.fetch", new=AsyncMock(return_value=blocked_fetch)), patch(
            "daalder.scraping.search.find_price_via_search", new=AsyncMock(return_value=search_ok)
        ):
            result = await extract_price("https://shop.nl/p", name_hint="Widget")
        self.assertTrue(result.ok)
        self.assertEqual(result.strategy, "search")
        self.assertEqual(result.price, Decimal("9.99"))

    async def test_blocked_fetch_search_also_fails_reports_blocked(self):
        blocked_fetch = FetchResult(ok=False, status_code=403, html=None, blocked=True, final_url="https://shop.nl/p")
        search_failed = PriceResult(ok=False, status="not_found", strategy="search")
        with patch("daalder.scraping.fetch.fetch", new=AsyncMock(return_value=blocked_fetch)), patch(
            "daalder.scraping.search.find_price_via_search", new=AsyncMock(return_value=search_failed)
        ):
            result = await extract_price("https://shop.nl/p")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "blocked")

    async def test_non_blocked_error_does_not_call_search(self):
        error_fetch = FetchResult(ok=False, status_code=None, html=None, blocked=False, final_url="https://shop.nl/p", error="timeout")
        search_mock = AsyncMock()
        with patch("daalder.scraping.fetch.fetch", new=AsyncMock(return_value=error_fetch)), patch(
            "daalder.scraping.search.find_price_via_search", new=search_mock
        ):
            result = await extract_price("https://shop.nl/p")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "error")
        search_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
