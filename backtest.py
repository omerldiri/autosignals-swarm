"""
Backtest Engine - FIXED evaluation harness (v3 - Walk-Forward + Guillotine Scoring).

This file CANNOT be modified by research agents.
It is the ground truth evaluation metric.

Scoring: The Guillotine (multiplicative, kills toxic strategies)
  base_reward = (norm_sharpe * 0.6) + (norm_pf * 0.4)
  penalty = drawdown penalty (0.0 if max_dd >= 25%, 1.0 if max_dd <= 10%, linear in between)
  score = base_reward * penalty

Walk-Forward Optimization (WFO):
  Window 1: Train 2021-2023, Test 2024
  Window 2: Train 2022-2024, Test 2025
  composite_score = in_sample_score (primary optimization target)
"""

import os
import sys
import json
import pandas as pd
import numpy as np
from typing import Dict, List

# Disable live API calls during backtesting (prevents 1000s of sequential HTTP requests)
os.environ['BACKTEST_MODE'] = '1'

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
INITIAL_CAPITAL = 100_000
TRANSACTION_COST = 0.001  # 0.1% per trade
MAX_POSITIONS = 20  # Max concurrent positions


def load_closes() -> pd.DataFrame:
    """Load price data as a wide DataFrame (dates x tickers)."""
    csv_path = os.path.join(DATA_DIR, 'sp500_closes.csv')
    if os.path.exists(csv_path):
        df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        return df

    parquet_path = os.path.join(DATA_DIR, 'prices.parquet')
    if os.path.exists(parquet_path):
        long_df = pd.read_parquet(parquet_path)
        wide = long_df.pivot(index='date', columns='ticker', values='close')
        return wide

    print("ERROR: No price data found. Run prepare.py first.", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Scoring: The Guillotine
# ---------------------------------------------------------------------------

def normalize(value: float, max_val: float = 3.0) -> float:
    """Normalize a value to [0, 1] range by dividing by max_val and clamping."""
    return min(max(value / max_val, 0.0), 1.0)


def calculate_sortino_ratio(daily_returns, annual_rf_rate=0.04):
    daily_rf_rate = (1 + annual_rf_rate) ** (1/252) - 1
    excess_returns = daily_returns - daily_rf_rate
    downside_returns = excess_returns[excess_returns < 0]
    if len(downside_returns) == 0 or downside_returns.std() == 0:
        return 0.0
    downside_deviation = np.sqrt(np.mean(downside_returns**2))
    annualized_sortino = (excess_returns.mean() / downside_deviation) * np.sqrt(252)
    return float(annualized_sortino)


def calculate_score(daily_returns, max_drawdown, profit_factor, time_in_market):
    # GATE 1: The Cowardice Hurdle
    total_days = len(daily_returns)
    cumulative_return = (1 + daily_returns).prod() - 1
    annualized_return = (1 + cumulative_return) ** (252 / total_days) - 1
    if annualized_return < 0.04 or time_in_market < 0.30:
        return 0.0

    # GATE 2: The Drawdown Guillotine
    HARD_MAX_DD = 0.25
    IDEAL_MAX_DD = 0.10
    if max_drawdown >= HARD_MAX_DD:
        return 0.0
    elif max_drawdown <= IDEAL_MAX_DD:
        dd_penalty = 1.0
    else:
        dd_penalty = 1.0 - ((max_drawdown - IDEAL_MAX_DD) / (HARD_MAX_DD - IDEAL_MAX_DD))

    # THE REWARD: Sortino-based scoring
    sortino = calculate_sortino_ratio(daily_returns)
    norm_sortino = min(max(sortino / 3.0, 0.0), 1.0)
    norm_pf = min(max((profit_factor - 1.0) / 2.0, 0.0), 1.0)
    base_score = (norm_sortino * 0.60) + (norm_pf * 0.40)

    return float(base_score * dd_penalty)


# ---------------------------------------------------------------------------
# Core backtest engine
# ---------------------------------------------------------------------------

def run_backtest(closes: pd.DataFrame, signals_func) -> Dict:
    """
    Run vectorized backtest over the full closes DataFrame.
    Kept for backward compatibility. Uses Guillotine scoring.

    Args:
        closes: DataFrame with dates as index, tickers as columns
        signals_func: Function(closes_df, date_idx) -> Dict[ticker, weight]
                      Returns target portfolio weights for each rebalance date.

    Returns:
        Dict with performance metrics and composite score.
    """
    dates = closes.index
    n_dates = len(dates)

    # Rebalance weekly (every 5 trading days)
    rebalance_days = list(range(252, n_dates, 5))  # Start after 1 year of lookback

    if not rebalance_days:
        return _empty_result()

    cash = INITIAL_CAPITAL
    positions = {}  # ticker -> shares
    equity_curve = []
    trades = []
    days_with_positions = 0
    total_trading_days = 0

    for i in range(252, n_dates):
        date = dates[i]
        current_prices = closes.iloc[i].dropna().to_dict()

        # Calculate current portfolio value
        holdings = sum(current_prices.get(t, 0) * s for t, s in positions.items())
        portfolio_value = cash + holdings
        equity_curve.append(portfolio_value)
        total_trading_days += 1
        if holdings > 0:
            days_with_positions += 1

        # Rebalance on rebalance days
        if i in rebalance_days:
            lookback = closes.iloc[max(0, i-252):i+1]

            try:
                target_weights = signals_func(lookback, i)
                if target_weights is None:
                    target_weights = {}
            except Exception as e:
                target_weights = {}

            # Normalize weights
            total_weight = sum(abs(w) for w in target_weights.values())
            if total_weight > 1:
                target_weights = {t: w/total_weight for t, w in target_weights.items()}

            # Limit positions
            if len(target_weights) > MAX_POSITIONS:
                sorted_weights = sorted(target_weights.items(), key=lambda x: abs(x[1]), reverse=True)
                target_weights = dict(sorted_weights[:MAX_POSITIONS])

            # Execute rebalance
            target_dollars = {t: portfolio_value * w for t, w in target_weights.items()}

            # Sell first
            for ticker in list(positions.keys()):
                if ticker not in target_weights or target_weights.get(ticker, 0) <= 0:
                    price = current_prices.get(ticker)
                    if price and positions[ticker] > 0:
                        proceeds = positions[ticker] * price
                        cost = proceeds * TRANSACTION_COST
                        cash += proceeds - cost
                        trades.append(('sell', ticker, positions[ticker], price))
                        del positions[ticker]

            # Buy / adjust
            for ticker, target_dollar in target_dollars.items():
                if target_dollar <= 0:
                    continue
                price = current_prices.get(ticker)
                if not price or price <= 0:
                    continue

                current_shares = positions.get(ticker, 0)
                current_dollar = current_shares * price
                diff = target_dollar - current_dollar

                if abs(diff) < 100:  # Skip tiny adjustments
                    continue

                shares_diff = diff / price
                cost = abs(diff) * TRANSACTION_COST

                if diff > 0 and cash >= diff + cost:
                    positions[ticker] = current_shares + shares_diff
                    cash -= diff + cost
                    trades.append(('buy', ticker, shares_diff, price))
                elif diff < 0 and current_shares > 0:
                    sell_shares = min(abs(shares_diff), current_shares)
                    positions[ticker] = current_shares - sell_shares
                    cash += sell_shares * price - cost
                    trades.append(('sell', ticker, sell_shares, price))

    time_in_market = days_with_positions / total_trading_days if total_trading_days > 0 else 0.0
    return _calculate_metrics(equity_curve, trades, INITIAL_CAPITAL, time_in_market)


def run_backtest_window(closes: pd.DataFrame, signals_func, window_start: str, window_end: str) -> Dict:
    """
    Run backtest for a specific date window.

    Includes up to 252 days of pre-window data for signal lookback computation,
    but measures performance only within [window_start, window_end].

    The signal lookback period (252 days) uses data from before the training window starts,
    satisfying the WFO requirement of no look-ahead bias.

    Args:
        closes: Full price DataFrame (dates x tickers)
        signals_func: Function(closes_df, date_idx) -> Dict[ticker, weight]
        window_start: ISO date string, start of measurement window (inclusive)
        window_end:   ISO date string, end of measurement window (inclusive)

    Returns:
        Dict with performance metrics and composite score for the window.
    """
    all_dates = closes.index

    win_start_ts = pd.Timestamp(window_start)
    win_end_ts = pd.Timestamp(window_end)

    # Locate window boundaries in the full date index
    win_start_idx = all_dates.searchsorted(win_start_ts)
    win_end_idx = all_dates.searchsorted(win_end_ts, side='right')

    if win_start_idx >= win_end_idx:
        return _empty_result()

    # Include 252 days of pre-window data so signals have full lookback
    lookback_start_idx = max(0, win_start_idx - 252)

    # Slice: [lookback_start, window_end]
    closes_slice = closes.iloc[lookback_start_idx:win_end_idx]
    dates = closes_slice.index
    n_dates = len(dates)

    # The measurement window begins at this offset within closes_slice
    window_offset = win_start_idx - lookback_start_idx  # <= 252

    # Rebalance weekly (every 5 trading days), starting from window_offset
    rebalance_days = set(range(window_offset, n_dates, 5))

    if not rebalance_days:
        return _empty_result()

    cash = INITIAL_CAPITAL
    positions = {}   # ticker -> shares
    equity_curve = []
    trades = []
    days_with_positions = 0
    total_trading_days = 0

    for i in range(window_offset, n_dates):
        current_prices = closes_slice.iloc[i].dropna().to_dict()

        # Current portfolio value
        holdings = sum(current_prices.get(t, 0) * s for t, s in positions.items())
        portfolio_value = cash + holdings
        equity_curve.append(portfolio_value)
        total_trading_days += 1
        if holdings > 0:
            days_with_positions += 1

        # Rebalance on rebalance days
        if i in rebalance_days:
            # Lookback window: up to 252 days before current position (no look-ahead)
            lookback = closes_slice.iloc[max(0, i - 252):i + 1]

            try:
                target_weights = signals_func(lookback, i)
                if target_weights is None:
                    target_weights = {}
            except Exception:
                target_weights = {}

            # Normalize weights
            total_weight = sum(abs(w) for w in target_weights.values())
            if total_weight > 1:
                target_weights = {t: w / total_weight for t, w in target_weights.items()}

            # Limit positions
            if len(target_weights) > MAX_POSITIONS:
                sorted_weights = sorted(target_weights.items(), key=lambda x: abs(x[1]), reverse=True)
                target_weights = dict(sorted_weights[:MAX_POSITIONS])

            target_dollars = {t: portfolio_value * w for t, w in target_weights.items()}

            # Sell first
            for ticker in list(positions.keys()):
                if ticker not in target_weights or target_weights.get(ticker, 0) <= 0:
                    price = current_prices.get(ticker)
                    if price and positions[ticker] > 0:
                        proceeds = positions[ticker] * price
                        cost = proceeds * TRANSACTION_COST
                        cash += proceeds - cost
                        trades.append(('sell', ticker, positions[ticker], price))
                        del positions[ticker]

            # Buy / adjust
            for ticker, target_dollar in target_dollars.items():
                if target_dollar <= 0:
                    continue
                price = current_prices.get(ticker)
                if not price or price <= 0:
                    continue

                current_shares = positions.get(ticker, 0)
                current_dollar = current_shares * price
                diff = target_dollar - current_dollar

                if abs(diff) < 100:  # Skip tiny adjustments
                    continue

                shares_diff = diff / price
                cost = abs(diff) * TRANSACTION_COST

                if diff > 0 and cash >= diff + cost:
                    positions[ticker] = current_shares + shares_diff
                    cash -= diff + cost
                    trades.append(('buy', ticker, shares_diff, price))
                elif diff < 0 and current_shares > 0:
                    sell_shares = min(abs(shares_diff), current_shares)
                    positions[ticker] = current_shares - sell_shares
                    cash += sell_shares * price - cost
                    trades.append(('sell', ticker, sell_shares, price))

    time_in_market = days_with_positions / total_trading_days if total_trading_days > 0 else 0.0
    return _calculate_metrics(equity_curve, trades, INITIAL_CAPITAL, time_in_market)


def _empty_result() -> Dict:
    return {
        'sharpe_ratio': 0.0,
        'sortino_ratio': 0.0,
        'annualized_return': 0.0,
        'time_in_market': 0.0,
        'max_drawdown': 1.0,
        'win_rate': 0.0,
        'profit_factor': 0.0,
        'composite_score': 0.0,
        'total_return': 0.0,
        'num_trades': 0,
    }


def _calculate_metrics(equity_curve: list, trades: list, initial_capital: float, time_in_market: float = 0.0) -> Dict:
    if len(equity_curve) < 10:
        return _empty_result()

    eq = np.array(equity_curve)
    returns = np.diff(eq) / eq[:-1]

    # Sharpe (annualized)
    mean_r = np.mean(returns)
    std_r = np.std(returns)
    sharpe = (mean_r / std_r) * np.sqrt(252) if std_r > 0 else 0

    # Sortino ratio
    sortino = calculate_sortino_ratio(returns)

    # Annualized return
    total_days = len(returns)
    cumulative_return = (1 + returns).prod() - 1
    annualized_return = (1 + cumulative_return) ** (252 / total_days) - 1 if total_days > 0 else 0.0

    # Max drawdown
    cummax = np.maximum.accumulate(eq)
    drawdowns = (eq - cummax) / cummax
    max_dd = abs(np.min(drawdowns)) if len(drawdowns) > 0 else 0

    # Win rate (from trades)
    buy_prices = {}
    wins = 0
    total_closed = 0
    for action, ticker, shares, price in trades:
        if action == 'buy':
            if ticker not in buy_prices:
                buy_prices[ticker] = price
        elif action == 'sell':
            entry = buy_prices.get(ticker)
            if entry:
                if price > entry:
                    wins += 1
                total_closed += 1
                del buy_prices[ticker]

    win_rate = wins / total_closed if total_closed > 0 else 0

    # Profit factor
    gains = returns[returns > 0].sum()
    losses = abs(returns[returns < 0].sum())
    profit_factor = gains / losses if losses > 0 else (gains if gains > 0 else 0)

    # Total return
    total_return = (eq[-1] - initial_capital) / initial_capital

    # Harness v3: Sortino + anti-cowardice + guillotine
    score = calculate_score(returns, max_dd, profit_factor, time_in_market)

    return {
        'sharpe_ratio': round(sharpe, 4),
        'sortino_ratio': round(sortino, 4),
        'annualized_return': round(float(annualized_return), 4),
        'time_in_market': round(time_in_market, 4),
        'max_drawdown': round(max_dd, 4),
        'win_rate': round(win_rate, 4),
        'profit_factor': round(profit_factor, 4),
        'composite_score': round(score, 6),
        'total_return': round(total_return, 4),
        'num_trades': len(trades),
    }


def _avg_metrics(metrics_list: List[Dict]) -> Dict:
    """Average a list of metric dicts. num_trades is summed, others averaged."""
    if not metrics_list:
        return _empty_result()
    keys = metrics_list[0].keys()
    result = {}
    for k in keys:
        vals = [m[k] for m in metrics_list]
        if k == 'num_trades':
            result[k] = sum(vals)
        else:
            avg = sum(vals) / len(vals)
            result[k] = round(avg, 6 if k == 'composite_score' else 4)
    return result


def main():
    from signals import generate_signals_v2

    print("Loading S&P 500 data...", file=sys.stderr)
    closes = load_closes()
    print(f"Loaded {closes.shape[1]} tickers, {closes.shape[0]} days", file=sys.stderr)

    # -----------------------------------------------------------------------
    # Walk-Forward Optimization (WFO)
    # Window 1: Train Years 1-3 (2021-2023), Test Year 4 (2024)
    # Window 2: Train Years 2-4 (2022-2024), Test Year 5 (2025)
    # Signal lookback (252 days) uses data from BEFORE each training window.
    # composite_score = in_sample_score (primary optimization target).
    # -----------------------------------------------------------------------
    wfo_windows = [
        {
            'label': 'WFO Window 1',
            'train_start': '2021-01-01', 'train_end': '2023-12-31',
            'test_start':  '2024-01-01', 'test_end':  '2024-12-31',
        },
        {
            'label': 'WFO Window 2',
            'train_start': '2022-01-01', 'train_end': '2024-12-31',
            'test_start':  '2025-01-01', 'test_end':  '2025-12-31',
        },
    ]

    print("Running Walk-Forward Optimization...", file=sys.stderr)

    window_results = []
    for w in wfo_windows:
        print(
            f"  {w['label']}: "
            f"train={w['train_start']}..{w['train_end']}  "
            f"test={w['test_start']}..{w['test_end']}",
            file=sys.stderr,
        )

        in_metrics  = run_backtest_window(closes, generate_signals_v2, w['train_start'], w['train_end'])
        oos_metrics = run_backtest_window(closes, generate_signals_v2, w['test_start'],  w['test_end'])

        window_results.append({'in_sample': in_metrics, 'out_of_sample': oos_metrics})

        print(f"    In-sample score:     {in_metrics['composite_score']:.6f}  "
              f"(sharpe={in_metrics['sharpe_ratio']:.3f}  max_dd={in_metrics['max_drawdown']:.2%})",
              file=sys.stderr)
        print(f"    Out-of-sample score: {oos_metrics['composite_score']:.6f}  "
              f"(sharpe={oos_metrics['sharpe_ratio']:.3f}  max_dd={oos_metrics['max_drawdown']:.2%})",
              file=sys.stderr)

    # Average across WFO windows
    avg_in  = _avg_metrics([r['in_sample']     for r in window_results])
    avg_oos = _avg_metrics([r['out_of_sample'] for r in window_results])

    in_sample_score     = avg_in['composite_score']
    out_of_sample_score = avg_oos['composite_score']

    result = {
        # Per-window detail
        'windows': [
            {
                'label':          w['label'],
                'in_sample':      window_results[i]['in_sample'],
                'out_of_sample':  window_results[i]['out_of_sample'],
            }
            for i, w in enumerate(wfo_windows)
        ],

        # Aggregated WFO metrics (averaged across windows)
        'in_sample':     avg_in,
        'out_of_sample': avg_oos,

        # Top-level scores
        'in_sample_score':     round(in_sample_score,     6),
        'out_of_sample_score': round(out_of_sample_score, 6),
        'composite_score':     round(in_sample_score,     6),  # Primary optimization target = in-sample

        # Legacy top-level fields (in-sample averages, for backward compatibility)
        'sharpe_ratio':      avg_in['sharpe_ratio'],
        'sortino_ratio':     avg_in['sortino_ratio'],
        'annualized_return': avg_in['annualized_return'],
        'time_in_market':    avg_in['time_in_market'],
        'max_drawdown':      avg_in['max_drawdown'],
        'win_rate':          avg_in['win_rate'],
        'profit_factor':     avg_in['profit_factor'],
        'total_return':      avg_in['total_return'],
        'num_trades':        avg_in['num_trades'],
    }

    print(json.dumps(result, indent=2))

    print(f"\n--- WALK-FORWARD BACKTEST RESULTS ---", file=sys.stderr)
    print(f"In-Sample Score (avg):      {in_sample_score:.6f}", file=sys.stderr)
    print(f"Out-of-Sample Score (avg):  {out_of_sample_score:.6f}", file=sys.stderr)
    print(f"Composite Score:            {result['composite_score']:.6f}  (= in-sample, optimization target)", file=sys.stderr)
    print(f"Sharpe Ratio (IS avg):      {avg_in['sharpe_ratio']:.4f}", file=sys.stderr)
    print(f"Max Drawdown (IS avg):      {avg_in['max_drawdown']:.2%}", file=sys.stderr)
    print(f"Win Rate (IS avg):          {avg_in['win_rate']:.2%}", file=sys.stderr)
    print(f"Total Return (IS avg):      {avg_in['total_return']:.2%}", file=sys.stderr)
    print(f"Num Trades (IS total):      {avg_in['num_trades']}", file=sys.stderr)


if __name__ == '__main__':
    main()
