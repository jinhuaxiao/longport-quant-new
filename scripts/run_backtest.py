#!/usr/bin/env python3
"""Run backtests for trading strategies."""

import asyncio
import argparse
from datetime import date, datetime, timedelta
from typing import List
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.backtest.engine import BacktestEngine, BacktestConfig
from longport_quant.backtest.metrics import MetricsCalculator, PerformanceMetrics
from longport_quant.config import Config

# Import strategies
from longport_quant.strategies.ma_crossover import MACrossoverStrategy
from longport_quant.strategies.rsi_reversal import RSIReversalStrategy
from longport_quant.strategies.volume_breakout import VolumeBreakoutStrategy
from longport_quant.strategies.bollinger_bands import BollingerBandsStrategy


async def run_single_backtest(
    strategy_name: str,
    symbols: List[str],
    start_date: date,
    end_date: date,
    initial_capital: float = 100000.0
):
    """Run backtest for a single strategy."""
    # Initialize database
    config = Config()
    db = DatabaseSessionManager(config.database_url)

    # Create strategy instance
    if strategy_name.lower() == 'ma_crossover':
        strategy = MACrossoverStrategy(
            fast_period=5,
            slow_period=20,
            position_size=0.1
        )
    elif strategy_name.lower() == 'rsi_reversal':
        strategy = RSIReversalStrategy(
            rsi_period=14,
            oversold_level=30,
            overbought_level=70,
            use_divergence=True
        )
    elif strategy_name.lower() == 'volume_breakout':
        strategy = VolumeBreakoutStrategy(
            volume_multiplier=2.0,
            price_breakout_pct=0.02,
            use_atr_stops=True
        )
    elif strategy_name.lower() == 'bollinger_bands':
        strategy = BollingerBandsStrategy(
            period=20,
            std_dev=2.0,
            use_squeeze=True,
            use_momentum=True
        )
    else:
        logger.error(f"Unknown strategy: {strategy_name}")
        return

    # Set database connection for strategy
    strategy.db = db

    # Configure backtest
    backtest_config = BacktestConfig(
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        commission_rate=0.001,  # 0.1%
        slippage_rate=0.0005,   # 0.05%
        max_position_size=0.2,  # 20% per position
        max_positions=5,
        use_minute_data=False,
        benchmark_symbol="SPY"  # S&P 500 ETF as benchmark
    )

    # Initialize backtest engine
    engine = BacktestEngine(db)

    # Run backtest
    logger.info(f"Starting backtest for {strategy.name}")
    logger.info(f"Symbols: {symbols}")
    logger.info(f"Period: {start_date} to {end_date}")
    logger.info(f"Initial capital: ${initial_capital:,.2f}")

    try:
        result = await engine.run_backtest(strategy, symbols, backtest_config)

        # Calculate additional metrics
        calculator = MetricsCalculator()

        # Build comprehensive metrics
        metrics = PerformanceMetrics()

        # Return metrics
        if result.equity_curve is not None and not result.equity_curve.empty:
            return_metrics = calculator.calculate_returns_metrics(
                result.equity_curve,
                initial_capital
            )
            for key, value in return_metrics.items():
                setattr(metrics, key, value)

            # Risk metrics
            risk_metrics = calculator.calculate_risk_metrics(result.equity_curve)
            for key, value in risk_metrics.items():
                setattr(metrics, key, value)

        # Trade metrics
        if result.trades:
            trade_metrics = calculator.calculate_trade_metrics(result.trades)
            for key, value in trade_metrics.items():
                if hasattr(metrics, key):
                    setattr(metrics, key, value)

        # Benchmark metrics
        metrics.alpha = result.alpha if result.alpha else 0
        metrics.benchmark_return = result.benchmark_return if result.benchmark_return else 0

        # Generate and print report
        report = calculator.generate_performance_report(metrics, output_format='text')
        print("\n" + report)

        # Print trade summary
        print(f"\nTotal Trades: {result.total_trades}")
        print(f"Winning Trades: {result.winning_trades} ({result.win_rate:.1%})")
        print(f"Losing Trades: {result.losing_trades}")
        print(f"Final Capital: ${result.final_capital:,.2f}")
        print(f"Total Commission: ${result.total_commission:,.2f}")
        print(f"Total Slippage: ${result.total_slippage:,.2f}")

        # Save results to file
        output_file = f"backtest_{strategy_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        if result.equity_curve is not None and not result.equity_curve.empty:
            result.equity_curve.to_csv(output_file, index=False)
            logger.info(f"Results saved to {output_file}")

    except Exception as e:
        logger.error(f"Backtest failed: {e}")
        import traceback
        traceback.print_exc()

    finally:
        await db.close()


async def compare_strategies(
    symbols: List[str],
    start_date: date,
    end_date: date,
    initial_capital: float = 100000.0
):
    """Run and compare multiple strategies."""
    strategies = [
        'ma_crossover',
        'rsi_reversal',
        'volume_breakout',
        'bollinger_bands'
    ]

    results = {}

    for strategy_name in strategies:
        logger.info(f"\n{'='*60}")
        logger.info(f"Testing {strategy_name}")
        logger.info(f"{'='*60}")

        await run_single_backtest(
            strategy_name,
            symbols,
            start_date,
            end_date,
            initial_capital
        )

    # TODO: Create comparison table


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Run strategy backtests')
    parser.add_argument(
        '--strategy',
        type=str,
        choices=['ma_crossover', 'rsi_reversal', 'volume_breakout', 'bollinger_bands', 'all'],
        default='ma_crossover',
        help='Strategy to backtest'
    )
    parser.add_argument(
        '--symbols',
        type=str,
        nargs='+',
        default=['AAPL', 'MSFT', 'GOOGL'],
        help='Symbols to trade'
    )
    parser.add_argument(
        '--start-date',
        type=str,
        default=(datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d'),
        help='Start date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--end-date',
        type=str,
        default=datetime.now().strftime('%Y-%m-%d'),
        help='End date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--capital',
        type=float,
        default=100000.0,
        help='Initial capital'
    )

    args = parser.parse_args()

    # Parse dates
    start_date = datetime.strptime(args.start_date, '%Y-%m-%d').date()
    end_date = datetime.strptime(args.end_date, '%Y-%m-%d').date()

    # Setup logging
    logger.add(
        f"backtest_{datetime.now().strftime('%Y%m%d')}.log",
        rotation="1 day",
        retention="7 days",
        level="DEBUG"
    )

    # Run backtest
    if args.strategy == 'all':
        asyncio.run(compare_strategies(
            args.symbols,
            start_date,
            end_date,
            args.capital
        ))
    else:
        asyncio.run(run_single_backtest(
            args.strategy,
            args.symbols,
            start_date,
            end_date,
            args.capital
        ))


if __name__ == '__main__':
    main()