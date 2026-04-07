"""Tests for Kalshi cursor-based pagination in tools/kalshi.py.

Verifies _fetch with max_pages > 1 follows cursors correctly, stops
when the cursor is empty, and respects the configured page limit.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.kalshi import KalshiTool


def _mock_response(markets: list, cursor: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"markets": markets, "cursor": cursor}
    return resp


def _make_market(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "title": f"Test market {ticker}",
        "status": "settled",
        "result": "yes",
        "last_price_dollars": "0.50",
        "volume_fp": "100.00",
        "volume_24h_fp": "0.00",
        "open_time": "2026-01-01T00:00:00Z",
        "close_time": "2026-06-01T00:00:00Z",
        "event_ticker": "KXTEST",
    }


# --- Pagination ---

class TestPagination:
    def test_single_page_when_max_pages_is_one(self):
        tool = KalshiTool(mock_mode=False)
        with patch("tools.kalshi.requests.get") as mock_get:
            mock_get.return_value = _mock_response(
                markets=[_make_market("A"), _make_market("B")],
                cursor="next-page-token",  # cursor present but ignored
            )
            result = tool._fetch(status="settled", series_ticker="KXTEST",
                                  limit=200, max_pages=1)
            assert len(result) == 2
            assert mock_get.call_count == 1

    def test_follows_cursor_when_max_pages_greater(self):
        tool = KalshiTool(mock_mode=False)
        with patch("tools.kalshi.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response(markets=[_make_market("A1")], cursor="page2"),
                _mock_response(markets=[_make_market("A2")], cursor="page3"),
                _mock_response(markets=[_make_market("A3")], cursor=""),  # last page
            ]
            result = tool._fetch(status="settled", series_ticker="KXTEST",
                                  limit=200, max_pages=5)
            assert len(result) == 3
            assert mock_get.call_count == 3
            # Verify cursor was passed in pages 2 and 3
            second_call_params = mock_get.call_args_list[1].kwargs["params"]
            assert second_call_params.get("cursor") == "page2"
            third_call_params = mock_get.call_args_list[2].kwargs["params"]
            assert third_call_params.get("cursor") == "page3"

    def test_stops_at_max_pages(self):
        tool = KalshiTool(mock_mode=False)
        with patch("tools.kalshi.requests.get") as mock_get:
            # Always returns a cursor — would loop forever if not bounded
            mock_get.side_effect = [
                _mock_response(markets=[_make_market(f"M{i}")], cursor=f"c{i+1}")
                for i in range(20)
            ]
            result = tool._fetch(status="settled", series_ticker="KXTEST",
                                  limit=200, max_pages=3)
            assert len(result) == 3
            assert mock_get.call_count == 3

    def test_stops_on_empty_page(self):
        tool = KalshiTool(mock_mode=False)
        with patch("tools.kalshi.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response(markets=[_make_market("A1")], cursor="page2"),
                _mock_response(markets=[], cursor=""),  # empty result, stop
            ]
            result = tool._fetch(status="settled", series_ticker="KXTEST",
                                  limit=200, max_pages=10)
            assert len(result) == 1
            assert mock_get.call_count == 2

    def test_first_call_does_not_send_cursor(self):
        tool = KalshiTool(mock_mode=False)
        with patch("tools.kalshi.requests.get") as mock_get:
            mock_get.return_value = _mock_response(markets=[], cursor="")
            tool._fetch(status="settled", series_ticker="KXTEST", max_pages=1)
            first_params = mock_get.call_args.kwargs["params"]
            assert "cursor" not in first_params

    def test_default_max_pages_is_one(self):
        """Backwards compat: existing callers without max_pages get one page."""
        tool = KalshiTool(mock_mode=False)
        with patch("tools.kalshi.requests.get") as mock_get:
            mock_get.return_value = _mock_response(
                markets=[_make_market("A")], cursor="more-pages",
            )
            result = tool._fetch(status="settled", series_ticker="KXTEST")
            assert len(result) == 1
            assert mock_get.call_count == 1


# --- fetch_known_series uses pagination ---

class TestFetchKnownSeriesPagination:
    def test_fetch_known_series_paginates(self, monkeypatch):
        """fetch_known_series should pull max_pages_per_series pages per series."""
        tool = KalshiTool(mock_mode=False)
        # Patch _fetch to track calls and return varying data
        call_log = []

        def fake_fetch(status, series_ticker, limit, max_pages):
            call_log.append((series_ticker, max_pages))
            # Return one fake market per series — distinct ticker so dedupe works
            return [{
                "ticker": f"{series_ticker}-26APR-T0.5",
                "title": f"Test {series_ticker}",
                "status": "settled",
                "result": "yes",
                "last_price_dollars": "0.50",
                "volume_fp": "1000.00",
                "open_time": "2026-01-01T00:00:00Z",
                "close_time": "2026-06-01T00:00:00Z",
                "event_ticker": series_ticker,
            }]

        monkeypatch.setattr(tool, "_fetch", fake_fetch)
        result = tool.fetch_known_series(status="settled", max_pages_per_series=7)

        # Every call should request max_pages=7
        assert all(mp == 7 for _, mp in call_log)
        # Should have called _fetch once per known series
        from tools.kalshi import KNOWN_SERIES
        assert len(call_log) == len(KNOWN_SERIES)
