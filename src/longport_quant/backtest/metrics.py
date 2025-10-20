"""Performance metrics and statistics for backtesting."""

from __future__ import annotations

from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, date, timedelta
import pandas as pd
import numpy as np
from dataclasses import dataclass, field

from loguru import logger


@dataclass
class PerformanceMetrics:
    """Comprehensive performance metrics."""

    # Returns
    total_return: float = 0.0
    annual_return: float = 0.0
    monthly_return: float = 0.0
    daily_return_avg: float = 0.0
    daily_return_std: float = 0.0

    # Risk metrics
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    information_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_duration: int = 0  # days
    value_at_risk: float = 0.0  # 95% VaR
    conditional_value_at_risk: float = 0.0  # CVaR

    # Trade statistics
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_win_loss_ratio: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    consecutive_wins_max: int = 0
    consecutive_losses_max: int = 0

    # Position metrics
    avg_holding_period: timedelta = timedelta(0)
    avg_position_size: float = 0.0
    max_position_size: float = 0.0
    position_turnover: float = 0.0

    # Market exposure
    time_in_market: float = 0.0  # Percentage of time with positions
    long_exposure_avg: float = 0.0
    short_exposure_avg: float = 0.0

    # Benchmark comparison
    alpha: float = 0.0
    beta: float = 0.0
    correlation: float = 0.0
    tracking_error: float = 0.0

    # Monthly/Yearly breakdown
    monthly_returns: pd.DataFrame = field(default_factory=pd.DataFrame)
    yearly_returns: pd.DataFrame = field(default_factory=pd.DataFrame)
    rolling_sharpe: pd.Series = field(default_factory=pd.Series)


class MetricsCalculator:
    """Calculate comprehensive performance metrics."""

    @staticmethod
    def calculate_returns_metrics(
        equity_curve: pd.DataFrame,
        initial_capital: float
    ) -> Dict[str, float]:
        """
        Calculate return-based metrics.

        Args:
            equity_curve: DataFrame with date and total_value columns
            initial_capital: Starting capital

        Returns:
            Dictionary of return metrics
        """
        if equity_curve.empty:
            return {}

        metrics = {}

        # Total return
        final_value = equity_curve.iloc[-1]['total_value']
        metrics['total_return'] = (final_value - initial_capital) / initial_capital

        # Calculate daily returns
        equity_curve['daily_return'] = equity_curve['total_value'].pct_change()
        daily_returns = equity_curve['daily_return'].dropna()

        if not daily_returns.empty:
            metrics['daily_return_avg'] = daily_returns.mean()
            metrics['daily_return_std'] = daily_returns.std()

            # Annual return (assuming 252 trading days)
            days = len(equity_curve)
            if days > 0:
                years = days / 252
                if years > 0:
                    metrics['annual_return'] = (1 + metrics['total_return']) ** (1/years) - 1

            # Monthly returns
            if 'date' in equity_curve.columns:
                equity_curve['date'] = pd.to_datetime(equity_curve['date'])
                equity_curve.set_index('date', inplace=True)
                monthly_returns = equity_curve['total_value'].resample('M').last().pct_change()
                metrics['monthly_return'] = monthly_returns.mean()

        return metrics

    @staticmethod
    def calculate_risk_metrics(
        equity_curve: pd.DataFrame,
        risk_free_rate: float = 0.02
    ) -> Dict[str, float]:
        """
        Calculate risk-based metrics.

        Args:
            equity_curve: DataFrame with daily returns
            risk_free_rate: Annual risk-free rate

        Returns:
            Dictionary of risk metrics
        """
        if 'daily_return' not in equity_curve.columns:
            equity_curve['daily_return'] = equity_curve['total_value'].pct_change()

        daily_returns = equity_curve['daily_return'].dropna()

        if daily_returns.empty:
            return {}

        metrics = {}

        # Sharpe Ratio
        if daily_returns.std() > 0:
            excess_returns = daily_returns - risk_free_rate / 252
            metrics['sharpe_ratio'] = np.sqrt(252) * excess_returns.mean() / daily_returns.std()

        # Sortino Ratio (using downside deviation)
        downside_returns = daily_returns[daily_returns < 0]
        if not downside_returns.empty:
            downside_std = downside_returns.std()
            if downside_std > 0:
                metrics['sortino_ratio'] = np.sqrt(252) * daily_returns.mean() / downside_std

        # Maximum Drawdown
        cumulative_returns = (1 + daily_returns).cumprod()
        running_max = cumulative_returns.expanding().max()
        drawdown = (cumulative_returns - running_max) / running_max
        metrics['max_drawdown'] = drawdown.min()

        # Maximum Drawdown Duration
        drawdown_start = None
        max_duration = 0
        current_duration = 0

        for i, dd in enumerate(drawdown):
            if dd < 0:
                if drawdown_start is None:
                    drawdown_start = i
                current_duration = i - drawdown_start
            else:
                if current_duration > max_duration:
                    max_duration = current_duration
                drawdown_start = None
                current_duration = 0

        metrics['max_drawdown_duration'] = max_duration

        # Calmar Ratio
        if metrics['max_drawdown'] < 0 and 'annual_return' in metrics:
            metrics['calmar_ratio'] = metrics.get('annual_return', 0) / abs(metrics['max_drawdown'])

        # Value at Risk (95% confidence)
        metrics['value_at_risk'] = daily_returns.quantile(0.05)

        # Conditional Value at Risk (CVaR)
        var_threshold = metrics['value_at_risk']
        tail_returns = daily_returns[daily_returns <= var_threshold]
        if not tail_returns.empty:
            metrics['conditional_value_at_risk'] = tail_returns.mean()

        return metrics

    @staticmethod
    def calculate_trade_metrics(trades: List[Any]) -> Dict[str, Any]:
        """
        Calculate trade-based metrics.

        Args:
            trades: List of trade positions

        Returns:
            Dictionary of trade metrics
        """
        if not trades:
            return {}

        metrics = {}

        # Separate winning and losing trades
        winning_trades = [t for t in trades if t.pnl > 0]
        losing_trades = [t for t in trades if t.pnl < 0]

        metrics['total_trades'] = len(trades)
        metrics['winning_trades'] = len(winning_trades)
        metrics['losing_trades'] = len(losing_trades)

        # Win rate
        metrics['win_rate'] = len(winning_trades) / len(trades) if trades else 0

        # Average win/loss
        if winning_trades:
            metrics['avg_win'] = np.mean([t.pnl for t in winning_trades])
            metrics['largest_win'] = max(t.pnl for t in winning_trades)

        if losing_trades:
            metrics['avg_loss'] = np.mean([t.pnl for t in losing_trades])
            metrics['largest_loss'] = min(t.pnl for t in losing_trades)

        # Win/Loss ratio
        if metrics.get('avg_loss', 0) != 0:
            metrics['avg_win_loss_ratio'] = abs(metrics.get('avg_win', 0) / metrics['avg_loss'])

        # Profit factor
        gross_profit = sum(t.pnl for t in winning_trades) if winning_trades else 0
        gross_loss = abs(sum(t.pnl for t in losing_trades)) if losing_trades else 1
        metrics['profit_factor'] = gross_profit / gross_loss if gross_loss > 0 else 0

        # Expectancy
        if trades:
            metrics['expectancy'] = sum(t.pnl for t in trades) / len(trades)

        # Consecutive wins/losses
        metrics['consecutive_wins_max'] = MetricsCalculator._max_consecutive(trades, True)
        metrics['consecutive_losses_max'] = MetricsCalculator._max_consecutive(trades, False)

        # Holding period
        holding_periods = []
        for t in trades:
            if hasattr(t, 'exit_date') and hasattr(t, 'entry_date'):
                if t.exit_date and t.entry_date:
                    holding_periods.append(t.exit_date - t.entry_date)

        if holding_periods:
            metrics['avg_holding_period'] = sum(holding_periods, timedelta()) / len(holding_periods)

        return metrics

    @staticmethod
    def _max_consecutive(trades: List[Any], wins: bool) -> int:
        """Calculate maximum consecutive wins or losses."""
        if not trades:
            return 0

        max_consecutive = 0
        current_consecutive = 0

        for trade in trades:
            if wins and trade.pnl > 0:
                current_consecutive += 1
                max_consecutive = max(max_consecutive, current_consecutive)
            elif not wins and trade.pnl < 0:
                current_consecutive += 1
                max_consecutive = max(max_consecutive, current_consecutive)
            else:
                current_consecutive = 0

        return max_consecutive

    @staticmethod
    def calculate_exposure_metrics(
        equity_curve: pd.DataFrame,
        positions_history: List[Dict[str, Any]]
    ) -> Dict[str, float]:
        """
        Calculate market exposure metrics.

        Args:
            equity_curve: DataFrame with equity history
            positions_history: List of position snapshots

        Returns:
            Dictionary of exposure metrics
        """
        metrics = {}

        if not positions_history:
            return metrics

        # Time in market
        days_with_positions = sum(1 for p in positions_history if p.get('positions', 0) > 0)
        total_days = len(positions_history)
        metrics['time_in_market'] = days_with_positions / total_days if total_days > 0 else 0

        # Average position count
        avg_positions = np.mean([p.get('positions', 0) for p in positions_history])
        metrics['avg_positions'] = avg_positions

        return metrics

    @staticmethod
    def calculate_benchmark_metrics(
        strategy_returns: pd.Series,
        benchmark_returns: pd.Series
    ) -> Dict[str, float]:
        """
        Calculate metrics relative to benchmark.

        Args:
            strategy_returns: Strategy daily returns
            benchmark_returns: Benchmark daily returns

        Returns:
            Dictionary of benchmark comparison metrics
        """
        if strategy_returns.empty or benchmark_returns.empty:
            return {}

        # Align the series
        aligned = pd.DataFrame({
            'strategy': strategy_returns,
            'benchmark': benchmark_returns
        }).dropna()

        if aligned.empty:
            return {}

        metrics = {}

        # Alpha and Beta (using linear regression)
        if len(aligned) > 20:
            covariance = aligned['strategy'].cov(aligned['benchmark'])
            benchmark_variance = aligned['benchmark'].var()

            if benchmark_variance > 0:
                metrics['beta'] = covariance / benchmark_variance
                metrics['alpha'] = aligned['strategy'].mean() - metrics['beta'] * aligned['benchmark'].mean()
                metrics['alpha'] = metrics['alpha'] * 252  # Annualize

        # Correlation
        metrics['correlation'] = aligned['strategy'].corr(aligned['benchmark'])

        # Tracking error
        tracking_diff = aligned['strategy'] - aligned['benchmark']
        metrics['tracking_error'] = tracking_diff.std() * np.sqrt(252)

        # Information ratio
        if metrics['tracking_error'] > 0:
            metrics['information_ratio'] = (tracking_diff.mean() * 252) / metrics['tracking_error']

        return metrics

    @staticmethod
    def calculate_rolling_metrics(
        equity_curve: pd.DataFrame,
        window: int = 252
    ) -> Dict[str, pd.Series]:
        """
        Calculate rolling performance metrics.

        Args:
            equity_curve: DataFrame with equity history
            window: Rolling window size (default 252 days = 1 year)

        Returns:
            Dictionary of rolling metric series
        """
        if 'daily_return' not in equity_curve.columns:
            equity_curve['daily_return'] = equity_curve['total_value'].pct_change()

        daily_returns = equity_curve['daily_return'].dropna()

        if len(daily_returns) < window:
            return {}

        rolling_metrics = {}

        # Rolling Sharpe Ratio
        rolling_mean = daily_returns.rolling(window).mean()
        rolling_std = daily_returns.rolling(window).std()
        rolling_metrics['rolling_sharpe'] = np.sqrt(252) * rolling_mean / rolling_std

        # Rolling Maximum Drawdown
        rolling_max = equity_curve['total_value'].rolling(window).max()
        rolling_dd = (equity_curve['total_value'] - rolling_max) / rolling_max
        rolling_metrics['rolling_max_dd'] = rolling_dd

        # Rolling Win Rate (if trades data available)
        # This would need trade-level data with timestamps

        return rolling_metrics

    @staticmethod
    def generate_performance_report(
        metrics: PerformanceMetrics,
        output_format: str = 'dict'
    ) -> Any:
        """
        Generate a formatted performance report.

        Args:
            metrics: PerformanceMetrics object
            output_format: 'dict', 'dataframe', or 'text'

        Returns:
            Formatted performance report
        """
        report_data = {
            'Returns': {
                'Total Return': f"{metrics.total_return:.2%}",
                'Annual Return': f"{metrics.annual_return:.2%}",
                'Monthly Return (avg)': f"{metrics.monthly_return:.2%}",
                'Daily Return (avg)': f"{metrics.daily_return_avg:.4%}",
                'Daily Return (std)': f"{metrics.daily_return_std:.4%}",
            },
            'Risk Metrics': {
                'Sharpe Ratio': f"{metrics.sharpe_ratio:.2f}",
                'Sortino Ratio': f"{metrics.sortino_ratio:.2f}",
                'Calmar Ratio': f"{metrics.calmar_ratio:.2f}",
                'Max Drawdown': f"{metrics.max_drawdown:.2%}",
                'Max DD Duration': f"{metrics.max_drawdown_duration} days",
                'Value at Risk (95%)': f"{metrics.value_at_risk:.2%}",
                'CVaR': f"{metrics.conditional_value_at_risk:.2%}",
            },
            'Trade Statistics': {
                'Win Rate': f"{metrics.win_rate:.2%}",
                'Profit Factor': f"{metrics.profit_factor:.2f}",
                'Expectancy': f"{metrics.expectancy:.2f}",
                'Avg Win': f"{metrics.avg_win:.2f}",
                'Avg Loss': f"{metrics.avg_loss:.2f}",
                'Win/Loss Ratio': f"{metrics.avg_win_loss_ratio:.2f}",
                'Largest Win': f"{metrics.largest_win:.2f}",
                'Largest Loss': f"{metrics.largest_loss:.2f}",
                'Max Consecutive Wins': f"{metrics.consecutive_wins_max}",
                'Max Consecutive Losses': f"{metrics.consecutive_losses_max}",
            },
            'Market Exposure': {
                'Time in Market': f"{metrics.time_in_market:.2%}",
                'Avg Holding Period': str(metrics.avg_holding_period),
            }
        }

        if metrics.alpha != 0 or metrics.beta != 0:
            report_data['Benchmark Comparison'] = {
                'Alpha': f"{metrics.alpha:.2%}",
                'Beta': f"{metrics.beta:.2f}",
                'Correlation': f"{metrics.correlation:.2f}",
                'Tracking Error': f"{metrics.tracking_error:.2%}",
                'Information Ratio': f"{metrics.information_ratio:.2f}",
            }

        if output_format == 'dict':
            return report_data
        elif output_format == 'dataframe':
            # Flatten the nested dict for DataFrame
            flat_data = []
            for category, metrics_dict in report_data.items():
                for metric, value in metrics_dict.items():
                    flat_data.append({
                        'Category': category,
                        'Metric': metric,
                        'Value': value
                    })
            return pd.DataFrame(flat_data)
        elif output_format == 'text':
            lines = []
            lines.append("=" * 60)
            lines.append("PERFORMANCE REPORT")
            lines.append("=" * 60)

            for category, metrics_dict in report_data.items():
                lines.append(f"\n{category}")
                lines.append("-" * 40)
                for metric, value in metrics_dict.items():
                    lines.append(f"  {metric:<25} {value:>15}")

            lines.append("=" * 60)
            return "\n".join(lines)
        else:
            return report_data