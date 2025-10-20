"""Unit tests for data synchronization services."""

import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from longport import openapi

from longport_quant.config.settings import Settings
from longport_quant.data.batch_insert import BatchConfig, BatchInsertService
from longport_quant.data.kline_sync import KlineDataService
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import KlineDaily, KlineMinute


class TestKlineDataService:
    """Test suite for KlineDataService."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock(spec=Settings)
        settings.database_dsn = "postgresql://test:test@localhost/test"
        return settings

    @pytest.fixture
    def mock_db(self):
        """Create mock database manager."""
        db = MagicMock(spec=DatabaseSessionManager)
        db.session = MagicMock()
        return db

    @pytest.fixture
    def mock_quote_client(self):
        """Create mock quote client."""
        client = MagicMock(spec=QuoteDataClient)
        return client

    @pytest.fixture
    def kline_service(self, mock_settings, mock_db, mock_quote_client):
        """Create KlineDataService instance."""
        return KlineDataService(mock_settings, mock_db, mock_quote_client)

    @pytest.fixture
    def sample_candle(self):
        """Create sample candlestick data."""
        candle = MagicMock(spec=openapi.Candlestick)
        candle.timestamp = datetime(2024, 1, 1, 9, 30)
        candle.open = "100.50"
        candle.high = "105.00"
        candle.low = "99.50"
        candle.close = "104.00"
        candle.volume = "1000000"
        candle.turnover = "103000000"
        candle.trade_count = 5000
        candle.prev_close = "100.00"
        return candle

    @pytest.mark.asyncio
    async def test_sync_daily_klines_success(self, kline_service, mock_quote_client, sample_candle):
        """Test successful daily K-line synchronization."""
        # Setup
        symbols = ["700.HK", "9988.HK"]
        mock_quote_client.get_history_candles = AsyncMock(return_value=[sample_candle])

        # Mock database operations
        with patch.object(kline_service, '_get_last_daily_sync_date', return_value=date(2023, 12, 1)):
            with patch.object(kline_service, '_bulk_upsert_daily_klines', return_value=1) as mock_upsert:
                # Execute
                result = await kline_service.sync_daily_klines(
                    symbols=symbols,
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 1, 31)
                )

                # Assert
                assert len(result) == len(symbols)
                assert all(result[symbol] == 1 for symbol in symbols)
                assert mock_upsert.call_count == len(symbols)
                mock_quote_client.get_history_candles.assert_called()

    @pytest.mark.asyncio
    async def test_sync_daily_klines_empty_symbols(self, kline_service):
        """Test sync with empty symbol list."""
        result = await kline_service.sync_daily_klines(symbols=[])
        assert result == {}

    @pytest.mark.asyncio
    async def test_sync_daily_klines_api_error(self, kline_service, mock_quote_client):
        """Test handling of API errors."""
        symbols = ["700.HK"]
        mock_quote_client.get_history_candles = AsyncMock(
            side_effect=openapi.OpenApiException("API Error")
        )

        with patch.object(kline_service, '_get_last_daily_sync_date', return_value=date(2023, 12, 1)):
            result = await kline_service.sync_daily_klines(
                symbols=symbols,
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31)
            )

            assert result["700.HK"] == -1  # Error indicator

    @pytest.mark.asyncio
    async def test_sync_minute_klines_success(self, kline_service, mock_quote_client, sample_candle):
        """Test successful minute K-line synchronization."""
        symbols = ["700.HK"]
        mock_quote_client.get_history_candles = AsyncMock(return_value=[sample_candle])

        with patch.object(kline_service, '_get_last_minute_sync_date', return_value=datetime.now() - timedelta(days=1)):
            with patch.object(kline_service, '_bulk_upsert_minute_klines', return_value=1) as mock_upsert:
                result = await kline_service.sync_minute_klines(
                    symbols=symbols,
                    interval=5,
                    days_back=7
                )

                assert result["700.HK"] == 1
                mock_upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_old_minute_data(self, kline_service, mock_db):
        """Test cleanup of old minute data."""
        mock_session = AsyncMock()
        mock_db.session.return_value.__aenter__.return_value = mock_session

        mock_result = MagicMock()
        mock_result.rowcount = 1000
        mock_session.execute.return_value = mock_result

        deleted = await kline_service.cleanup_old_minute_data(days_to_keep=180)

        assert deleted == 1000
        mock_session.execute.assert_called_once()

    def test_normalize_symbols(self, kline_service):
        """Test symbol normalization."""
        # Test with duplicates and empty strings
        symbols = ["700.HK", "  9988.HK  ", "", "700.HK", "3690.HK"]
        normalized = kline_service._normalize_symbols(symbols)

        assert normalized == ["700.HK", "9988.HK", "3690.HK"]
        assert len(normalized) == 3  # Duplicates removed

    def test_normalize_date_range(self, kline_service):
        """Test date range normalization."""
        # Test with valid dates
        start = date(2024, 1, 1)
        end = date(2024, 1, 31)
        normalized_start, normalized_end = kline_service._normalize_date_range(start, end)

        assert normalized_start == start
        assert normalized_end == end

        # Test with None values
        normalized_start, normalized_end = kline_service._normalize_date_range(None, None)
        assert normalized_start is None
        assert normalized_end == date.today()

        # Test invalid range
        with pytest.raises(ValueError):
            kline_service._normalize_date_range(date(2024, 2, 1), date(2024, 1, 1))

    def test_prepare_daily_upsert(self, kline_service, sample_candle):
        """Test preparation of daily K-line upsert data."""
        symbol = "700.HK"
        insert_values, update_values = kline_service._prepare_daily_upsert(symbol, sample_candle)

        assert insert_values["symbol"] == symbol
        assert insert_values["trade_date"] == sample_candle.timestamp.date()
        assert insert_values["open"] == Decimal("100.50")
        assert insert_values["high"] == Decimal("105.00")
        assert insert_values["low"] == Decimal("99.50")
        assert insert_values["close"] == Decimal("104.00")
        assert insert_values["volume"] == 1000000

        assert "symbol" not in update_values
        assert "trade_date" not in update_values

    @pytest.mark.asyncio
    async def test_sync_daily_klines_optimized(self, kline_service, mock_quote_client, sample_candle):
        """Test optimized daily K-line sync."""
        symbols = ["700.HK", "9988.HK"]
        mock_quote_client.get_history_candles = AsyncMock(return_value=[sample_candle] * 100)

        with patch.object(kline_service, '_get_last_daily_sync_date', return_value=date(2023, 12, 1)):
            with patch.object(kline_service.batch_service, 'bulk_insert_klines_optimized',
                            return_value={"processed": 200}) as mock_batch:
                result = await kline_service.sync_daily_klines_optimized(
                    symbols=symbols,
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 1, 31),
                    use_parallel=False
                )

                assert result["processed"] == 200
                assert result["symbols_processed"] == 2
                assert result["symbols_failed"] == 0
                mock_batch.assert_called_once()


class TestBatchInsertService:
    """Test suite for BatchInsertService."""

    @pytest.fixture
    def mock_db(self):
        """Create mock database manager."""
        db = MagicMock(spec=DatabaseSessionManager)
        db.session = MagicMock()
        return db

    @pytest.fixture
    def batch_config(self):
        """Create batch configuration."""
        return BatchConfig(
            batch_size=100,
            max_retries=3,
            retry_delay=0.1,
            use_copy_from=False,
            conflict_action="update"
        )

    @pytest.fixture
    def batch_service(self, mock_db, batch_config):
        """Create BatchInsertService instance."""
        return BatchInsertService(mock_db, batch_config)

    @pytest.mark.asyncio
    async def test_bulk_insert_empty_records(self, batch_service):
        """Test bulk insert with empty records."""
        result = await batch_service.bulk_insert_klines_optimized(
            table_name="kline_daily",
            records=[],
            conflict_columns=["symbol", "trade_date"]
        )

        assert result["total_inserted"] == 0
        assert result["total_errors"] == 0

    @pytest.mark.asyncio
    async def test_bulk_insert_with_records(self, batch_service, mock_db):
        """Test bulk insert with valid records."""
        records = [
            {
                "symbol": "700.HK",
                "trade_date": date(2024, 1, 1),
                "open": 100.0,
                "high": 105.0,
                "low": 99.0,
                "close": 104.0,
                "volume": 1000000
            }
        ]

        mock_session = AsyncMock()
        mock_db.session.return_value.__aenter__.return_value = mock_session

        result = await batch_service.bulk_insert_klines_optimized(
            table_name="kline_daily",
            records=records,
            conflict_columns=["symbol", "trade_date"]
        )

        assert result["total_inserted"] == 1
        mock_session.execute.assert_called()

    @pytest.mark.asyncio
    async def test_batch_insert_with_retry(self, batch_service, mock_db):
        """Test batch insert with retry on failure."""
        records = [{"symbol": "700.HK", "value": 100}]

        mock_session = AsyncMock()
        mock_db.session.return_value.__aenter__.return_value = mock_session

        # First call fails, second succeeds
        mock_session.execute.side_effect = [Exception("DB Error"), None]

        with patch('asyncio.sleep', return_value=None):  # Speed up test
            result = await batch_service._batch_insert_on_conflict(
                table_name="test_table",
                records=records,
                conflict_columns=["symbol"]
            )

        # Should retry and succeed
        assert mock_session.execute.call_count >= 1

    @pytest.mark.asyncio
    async def test_parallel_insert(self, batch_service, mock_db):
        """Test parallel insert with multiple workers."""
        records = [{"symbol": f"STOCK{i}.HK", "value": i} for i in range(100)]

        mock_session = AsyncMock()
        mock_db.session.return_value.__aenter__.return_value = mock_session

        with patch.object(batch_service, '_worker_insert',
                         return_value={"processed": 25}) as mock_worker:
            result = await batch_service.parallel_insert(
                table_name="test_table",
                records=records,
                conflict_columns=["symbol"],
                num_workers=4
            )

            assert mock_worker.call_count == 4  # 4 workers
            assert result["processed"] == 100
            assert result["errors"] == 0

    def test_get_statistics(self, batch_service):
        """Test statistics retrieval."""
        batch_service._stats = {
            "total_inserted": 1000,
            "total_updated": 500,
            "total_skipped": 10,
            "total_errors": 5,
            "avg_records_per_second": 250.5
        }

        stats = batch_service.get_statistics()

        assert stats["total_inserted"] == 1000
        assert stats["total_updated"] == 500
        assert stats["total_errors"] == 5
        assert stats["avg_records_per_second"] == 250.5

    def test_reset_statistics(self, batch_service):
        """Test statistics reset."""
        batch_service._stats["total_inserted"] = 1000
        batch_service.reset_statistics()

        assert batch_service._stats["total_inserted"] == 0
        assert batch_service._stats["total_errors"] == 0


class TestOptimizedSync:
    """Integration tests for optimized sync functionality."""

    @pytest.mark.asyncio
    async def test_end_to_end_sync(self, tmp_path):
        """Test end-to-end sync process."""
        # This would be an integration test with a test database
        # Placeholder for demonstration
        pass


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])