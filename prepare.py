"""
Data Preparation - Download historical price data

Downloads 5 years of daily price data for:
- S&P 500 components
- Held tickers: META, GOOG, AMZN, TSLA, BTC-USD, IAU

Caches to data/prices.parquet for fast backtesting.
"""

import os
import sys
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from typing import List


# ============================================================================
# Configuration
# ============================================================================

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
HELD_TICKERS = ['META', 'GOOG', 'AMZN', 'TSLA', 'BTC-USD', 'IAU']
LOOKBACK_YEARS = 5


# ============================================================================
# S&P 500 Components
# ============================================================================

def get_sp500_tickers() -> List[str]:
    """
    Fetch S&P 500 component tickers from Wikipedia.
    
    Returns:
        List of ticker symbols
    """
    try:
        url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
        tables = pd.read_html(url)
        sp500_table = tables[0]
        tickers = sp500_table['Symbol'].tolist()
        
        # Clean up tickers (replace dots with dashes for Yahoo Finance)
        tickers = [ticker.replace('.', '-') for ticker in tickers]
        return tickers
    except Exception as e:
        print(f"Warning: Failed to fetch S&P 500 tickers: {e}", file=sys.stderr)
        return []


# ============================================================================
# Price Data Download
# ============================================================================

def download_price_data(tickers: List[str], start_date: str, end_date: str) -> pd.DataFrame:
    """
    Download historical price data for given tickers.
    
    Args:
        tickers: List of ticker symbols
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
    
    Returns:
        DataFrame with columns: ticker, date, open, high, low, close, volume
    """
    all_data = []
    
    total = len(tickers)
    success_count = 0
    
    for i, ticker in enumerate(tickers, 1):
        try:
            print(f"[{i}/{total}] Downloading {ticker}...", end='', file=sys.stderr)
            
            df = yf.download(ticker, start=start_date, end=end_date, progress=False)
            
            if df.empty:
                print(" No data", file=sys.stderr)
                continue
            
            df = df.reset_index()
            
            # Handle MultiIndex columns (yfinance sometimes returns these)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            
            # Standardize column names
            df.columns = [col.lower() if isinstance(col, str) else str(col).lower() for col in df.columns]
            df = df.rename(columns={'adj close': 'adj_close'})
            
            # Add ticker column
            df['ticker'] = ticker
            
            # Select relevant columns
            df = df[['ticker', 'date', 'open', 'high', 'low', 'close', 'volume']]
            
            all_data.append(df)
            success_count += 1
            print(f" OK ({len(df)} days)", file=sys.stderr)
            
        except Exception as e:
            print(f" ERROR: {e}", file=sys.stderr)
    
    if not all_data:
        print("ERROR: No data downloaded", file=sys.stderr)
        sys.exit(1)
    
    print(f"\nSuccessfully downloaded {success_count}/{total} tickers", file=sys.stderr)
    
    combined_df = pd.concat(all_data, ignore_index=True)
    return combined_df


# ============================================================================
# Mock Factor Data (for initial testing)
# ============================================================================

def generate_mock_factors(price_df: pd.DataFrame) -> pd.DataFrame:
    """
    Generate mock factor data for testing.
    
    In production, this would fetch real insider data, earnings data, etc.
    For now, we generate random factors that signal_generator can use.
    
    Args:
        price_df: Price DataFrame
    
    Returns:
        Factor DataFrame with columns: ticker, date, insider_buys, insider_sells, 
                                       earnings_surprise, earnings_quality, sector_momentum
    """
    factors = []
    
    for ticker in price_df['ticker'].unique():
        ticker_prices = price_df[price_df['ticker'] == ticker].copy()
        
        for _, row in ticker_prices.iterrows():
            factors.append({
                'ticker': ticker,
                'date': row['date'],
                'insider_buys': 0,  # Mock: no insider buys
                'insider_sells': 0,  # Mock: no insider sells
                'earnings_surprise': 0.0,  # Mock: no earnings surprise
                'earnings_quality': 50.0,  # Mock: neutral quality
                'sector_momentum': 0.0,  # Mock: no sector momentum
            })
    
    return pd.DataFrame(factors)


# ============================================================================
# Main
# ============================================================================

def main():
    """Download and cache historical data."""
    os.makedirs(DATA_DIR, exist_ok=True)
    
    # Date range
    end_date = datetime.now()
    start_date = end_date - timedelta(days=LOOKBACK_YEARS * 365)
    
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')
    
    print(f"Downloading data from {start_str} to {end_str}", file=sys.stderr)
    print("", file=sys.stderr)
    
    # Get tickers
    print("Fetching S&P 500 component list...", file=sys.stderr)
    sp500_tickers = get_sp500_tickers()
    
    # Combine with held tickers
    all_tickers = list(set(sp500_tickers + HELD_TICKERS))
    print(f"Total tickers to download: {len(all_tickers)}", file=sys.stderr)
    print("", file=sys.stderr)
    
    # Download price data
    price_df = download_price_data(all_tickers, start_str, end_str)
    
    # Save price data
    price_cache = os.path.join(DATA_DIR, 'prices.parquet')
    price_df.to_parquet(price_cache, index=False)
    print(f"\nSaved price data to {price_cache}", file=sys.stderr)
    print(f"Total rows: {len(price_df):,}", file=sys.stderr)
    
    # Generate mock factor data
    print("\nGenerating mock factor data...", file=sys.stderr)
    factor_df = generate_mock_factors(price_df)
    
    # Save factor data
    factor_cache = os.path.join(DATA_DIR, 'factors.parquet')
    factor_df.to_parquet(factor_cache, index=False)
    print(f"Saved factor data to {factor_cache}", file=sys.stderr)
    print(f"Total rows: {len(factor_df):,}", file=sys.stderr)
    
    print("\nData preparation complete!", file=sys.stderr)
    print(f"Ready to run backtests with {len(price_df['ticker'].unique())} tickers", file=sys.stderr)


if __name__ == '__main__':
    main()
