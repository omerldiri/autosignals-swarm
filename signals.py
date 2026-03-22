"""
Trading Signals v5.0 — Phase 4: Clean Architecture Reset

Simple ensemble: momentum + value scoring, minimal pre-filter, regime gate.
Target: OOS > 0.15, IS-OOS gap < 0.25.
"""

import pandas as pd
import numpy as np
import json
import os
from typing import Dict, Optional
from datetime import datetime, timedelta


# ============================================================================
# FUNDAMENTALS LOADER (runs once at import time)
# ============================================================================

_FUND_DATA = {}
_FUND_LOADED = False

def _load_fundamentals():
    global _FUND_DATA, _FUND_LOADED
    if _FUND_LOADED:
        return
    fpath = os.path.join(os.path.dirname(__file__), 'data', 'fundamentals.json')
    if os.path.exists(fpath):
        with open(fpath) as f:
            _FUND_DATA = json.load(f)
    _FUND_LOADED = True

_load_fundamentals()

PUBLICATION_LAG_DAYS = 45
_FUND_CACHE = {}

def get_fundamentals(ticker: str, as_of_date: str) -> Optional[dict]:
    cache_key = (ticker, as_of_date[:7])
    if cache_key in _FUND_CACHE:
        return _FUND_CACHE[cache_key]
    records = _FUND_DATA.get(ticker, [])
    if not records:
        _FUND_CACHE[cache_key] = None
        return None
    try:
        dt = datetime.strptime(as_of_date[:10], '%Y-%m-%d')
        lag_date = (dt - timedelta(days=PUBLICATION_LAG_DAYS)).strftime('%Y-%m-%d')
    except (ValueError, TypeError):
        lag_date = as_of_date
    for r in records:
        if r.get('date', '9999') <= lag_date:
            _FUND_CACHE[cache_key] = r
            return r
    _FUND_CACHE[cache_key] = None
    return None


# ============================================================================
# PARAMS
# ============================================================================

PARAMS = {
    'momentum_lookback': 100,   # ~5 months
    'momentum_skip': 1,         # skip recent 1 day
    'regime_sma_fast': 10,
    'regime_sma_slow': 26,
    'top_n': 12,
    'min_price': 10.0,
    'max_exposure': 0.52,
    'min_margin': 0.02,
    'max_debt_ratio': 0.90,
}

WEIGHTS = {
    'momentum': 1.0,
}


# ============================================================================
# SIGNAL GENERATION
# ============================================================================

def detect_regime(spy: pd.Series) -> float:
    """Regime with 20d momentum bypass for near-SMA conditions"""
    slow_p = PARAMS['regime_sma_slow']
    if len(spy) < slow_p:
        return 0.3
    sma_slow = spy.rolling(slow_p).mean().iloc[-1]
    current = spy.iloc[-1]
    if sma_slow <= 0:
        return 0.3
    pct_above = (current - sma_slow) / sma_slow
    sma_signal = max(0.0, min(1.0, pct_above / 0.05))
    # Fast SMA confirmation: halve signal if fast SMA well below slow
    if len(spy) >= 15:
        sma_fast = spy.rolling(15).mean().iloc[-1]
        if sma_fast < sma_slow * 0.98:
            sma_signal *= 0.5
    # When price is near/below SMA but 20d momentum is positive, allow minimal trading
    if sma_signal < 0.30 and len(spy) >= 20:
        mom = (spy.iloc[-1] - spy.iloc[-20]) / spy.iloc[-20]
        if mom > 0.030:
            return 0.33
    return sma_signal


def passes_filter(fund: Optional[dict]) -> bool:
    """Minimal pre-filter: just exclude clearly bad stocks."""
    if fund is None:
        return True
    margin = fund.get('netProfitMargin')
    if margin is not None and margin < PARAMS['min_margin']:
        return False
    # Debt filter removed - let scoring handle it
    gpm = fund.get('grossProfitMargin')
    if gpm is not None and gpm < 0.15:
        return False
    # High PE filter
    pe = fund.get('priceToEarningsRatio')
    # PE filter removed — helps OOS generalization
    # if pe is not None and pe > 150:
    #     return False
    # High P/S filter
    ps = fund.get('priceToSalesRatio')
    if ps is not None and ps > 12:
        return False
    # High EV multiple filter
    evm = fund.get("enterpriseValueMultiple")
    if evm is not None and evm > 30:
        return False
    # High debt filter
    da = fund.get('debtToAssetsRatio')
    if da is not None and da > 0.66:
        return False
    # Low liquidity filter
    cr = fund.get('currentRatio')
    if cr is not None and cr < 0.65:
        return False
    # High PEG filter (overpriced for growth)
    peg = fund.get('priceToEarningsGrowthRatio')
    if peg is not None and peg > 2.5:
        return False
    # Extreme P/B filter
    pb = fund.get('priceToBookRatio')
    # P/B filter removed — helps OOS generalization
    # if pb is not None and pb > 20:
    #     return False
    # Forward PEG filter
    fpeg = fund.get('forwardPriceToEarningsGrowthRatio')
    # Forward PEG filter removed — helps OOS generalization
    # if fpeg is not None and fpeg > 2.2:
    #     return False
    # Low quick ratio filter
    qr = fund.get('quickRatio')
    if qr is not None and qr < 0.3:
        return False
    # Very low cash ratio filter
    cash_r = fund.get('cashRatio')
    if cash_r is not None and cash_r < 0.12:
        return False
    # High financial leverage filter
    fl = fund.get('financialLeverageRatio')
    if fl is not None and fl > 4.7:
        return False
    # Low debt service coverage filter
    dsc = fund.get('debtServiceCoverageRatio')
    if dsc is not None and 0 < dsc < 0.9:
        return False
    # High dividend payout filter (unsustainable)
    dpr = fund.get('dividendPayoutRatio')
    if dpr is not None and dpr > 0.4:
        return False
    return True


def generate_signals_v2(lookback_df: pd.DataFrame, date_idx: int) -> Dict[str, float]:
    benchmark = 'SPY'
    current_date = str(lookback_df.index[-1].date()) if hasattr(lookback_df.index[-1], 'date') else str(lookback_df.index[-1])[:10]

    # Regime
    if benchmark in lookback_df.columns:
        regime = detect_regime(lookback_df[benchmark].dropna())
    else:
        regime = 0.3
    if regime <= 0.28:
        return {}

    # Seasonal exposure adjustment
    month = int(current_date[5:7])
    seasonal_factor = 1.0
    if month in (4, 10):
        seasonal_factor = 6.80
    elif month in (1, 11):
        seasonal_factor = 1.50
    elif month in (2, 3, 5, 6, 7, 8, 9, 12):
        seasonal_factor = 0.0

    # SPY momentum for conviction
    spy_mom = 0.0
    if benchmark in lookback_df.columns:
        spy = lookback_df[benchmark].dropna()
        if len(spy) >= 30:
            spy_ret = (spy.iloc[-1] - spy.iloc[-30]) / spy.iloc[-30]
            spy_mom = max(0, min(spy_ret / 0.18, 1.0))  # 0-1, 1 at +18% in 30d

    # Volatility dampener: reduce exposure in high-vol environments
    if benchmark in lookback_df.columns:
        spy = lookback_df[benchmark].dropna()
        if len(spy) >= 55:
            vol_20 = spy.pct_change().iloc[-55:].std() * (252 ** 0.5)
            if vol_20 > 0.16:  # annualized vol > 16%
                regime *= max(0.5, 1.0 - (vol_20 - 0.16) * 3)

    # Long-term vol dampener: additional reduction for sustained high vol
    if benchmark in lookback_df.columns:
        spy = lookback_df[benchmark].dropna()
        if len(spy) >= 90:
            vol_60 = spy.pct_change().iloc[-90:].std() * (252 ** 0.5)
            if vol_60 > 0.18:
                regime *= max(0.7, 1.0 - (vol_60 - 0.18) * 1.5)

    # Ultra-long vol dampener: reduce further for persistently high vol
    if benchmark in lookback_df.columns:
        spy = lookback_df[benchmark].dropna()
        if len(spy) >= 120:
            vol_120 = spy.pct_change().iloc[-120:].std() * (252 ** 0.5)
            if vol_120 > 0.19:
                regime *= max(0.85, 1.0 - (vol_120 - 0.19) * 2.2)

    # Macro vol dampener: reduce for very persistent high vol
    if benchmark in lookback_df.columns:
        spy = lookback_df[benchmark].dropna()
        if len(spy) >= 150:
            vol_150 = spy.pct_change().iloc[-150:].std() * (252 ** 0.5)
            if vol_150 > 0.21:
                regime *= max(0.85, 1.0 - (vol_150 - 0.21) * 2.0)

    # Extended vol dampener: reduce for very long-term high vol
    if benchmark in lookback_df.columns:
        spy = lookback_df[benchmark].dropna()
        if len(spy) >= 180:
            vol_180 = spy.pct_change().iloc[-180:].std() * (252 ** 0.5)
            if vol_180 > 0.22:
                regime *= max(0.88, 1.0 - (vol_180 - 0.22) * 1.5)

    # Ultra-extended vol dampener: 200-day
    if benchmark in lookback_df.columns:
        spy = lookback_df[benchmark].dropna()
        if len(spy) >= 200:
            vol_200 = spy.pct_change().iloc[-200:].std() * (252 ** 0.5)
            if vol_200 > 0.20:
                regime *= max(0.90, 1.0 - (vol_200 - 0.20) * 1.3)

    # 250-day vol dampener
    if benchmark in lookback_df.columns:
        spy = lookback_df[benchmark].dropna()
        if len(spy) >= 250:
            vol_250 = spy.pct_change().iloc[-250:].std() * (252 ** 0.5)
            if vol_250 > 0.19:
                regime *= max(0.90, 1.0 - (vol_250 - 0.20) * 1.8)

    # Score all stocks
    scores = {}
    lb = PARAMS['momentum_lookback']
    skip = PARAMS['momentum_skip']

    for ticker in lookback_df.columns:
        if ticker == benchmark:
            continue
        prices = lookback_df[ticker].dropna()
        if len(prices) < lb + 30:
            continue
        if prices.iloc[-1] < PARAMS['min_price'] or np.isnan(prices.iloc[-1]):
            continue

        # Pre-filter
        fund = get_fundamentals(ticker, current_date)
        if not passes_filter(fund):
            continue

        # Momentum: 6-month return, skip recent week
        end = prices.iloc[-skip] if skip > 0 else prices.iloc[-1]
        start = prices.iloc[-lb]
        if start <= 0:
            continue
        mom = min((end - start) / start, 0.90)  # cap at 90%
        if mom < 0:
            mom *= 9.0  # amplify negative momentum penalty

        # Short-term momentum filter: skip stocks in active decline
        if len(prices) >= 15:
            st_ret = (prices.iloc[-1] - prices.iloc[-9]) / prices.iloc[-9]
            if st_ret < -0.030:
                continue

        # Value component: earnings yield (1/PE) + P/S yield
        value = 0.0
        if fund:
            pe = fund.get('priceToEarningsRatio')
            if pe is not None and pe > 0:
                value = min(1.0 / pe, 0.25)  # cap at 25% yield
            # Add P/S based value for stocks without good PE
            ps = fund.get('priceToSalesRatio')
            if ps is not None and ps > 0 and value < 0.04:
                ps_val = min(1.0 / ps, 0.15)  # use P/S as fallback
                value = max(value, ps_val * 0.6)

        # P/OCF yield bonus (fallback for low-value stocks)
        if fund and value < 0.05:
            p_ocf = fund.get('priceToOperatingCashFlowRatio')
            if p_ocf is not None and p_ocf > 0:
                ocf_yield = min(1.0 / p_ocf, 0.20)
                value = max(value, ocf_yield * 0.9)
        # P/OCF additive bonus for all stocks
        elif fund:
            p_ocf = fund.get('priceToOperatingCashFlowRatio')
            if p_ocf is not None and 0 < p_ocf < 20:
                value += min(1.0 / p_ocf, 0.19) * 0.10

        # Book value yield bonus
        if fund and value < 0.06:
            pb = fund.get('priceToBookRatio')
            if pb is not None and pb > 0:
                bv_yield = min(1.0 / pb, 0.19)
                value = max(value, bv_yield * 0.6)

        # Pretax profit margin quality bonus
        if fund:
            ptm = fund.get('pretaxProfitMargin')
            if ptm is not None and ptm > 0.10:
                value += min(ptm, 0.20) * 0.05

        # OCF/Sales quality bonus
        if fund:
            ocf_sales = fund.get('operatingCashFlowSalesRatio')
            if ocf_sales is not None and ocf_sales > 0.12:
                ocfs_cap = 0.36 if regime > 0.60 else 0.44
                value += min(ocf_sales, ocfs_cap) * 0.03

        # EBIT margin bonus
        if fund:
            ebit_m = fund.get('ebitMargin')
            if ebit_m is not None and ebit_m > 0.15:
                value += min(ebit_m, 0.25) * 0.05

        # Operating profit margin bonus
        if fund:
            opm = fund.get('operatingProfitMargin')
            if opm is not None and opm > 0.15:
                opm_cap = 0.22 if regime > 0.60 else 0.25
                value += min(opm, opm_cap) * 0.03

        # Net income yield (EPS / price)
        if fund:
            ni_ps = fund.get('netIncomePerShare')
            if ni_ps is not None and ni_ps > 0 and prices.iloc[-1] > 0:
                ni_yield = ni_ps / prices.iloc[-1]
                ni_t = 0.03 if regime > 0.60 else 0.027
                if ni_yield > ni_t:
                    value += min(ni_yield, 0.15) * 0.08

        # Efficiency bonus: high asset turnover
        efficiency = 0.0
        if fund:
            at = fund.get('assetTurnover')
            if at is not None and at > 0:
                efficiency = min(at, 1.5) / 1.5  # normalize: 0 at 0, 1.0 at 2.0+

        # 52-week high proximity factor
        high_prox = 0.0
        if len(prices) >= 250:
            high_250 = prices.iloc[-250:].max()
            if high_250 > 0:
                high_prox = prices.iloc[-1] / high_250  # 0-1, 1 = at 52w high

        # Recent drawdown penalty: stocks that dropped from 60d high
        dd_look = 120 if regime > 0.60 else 100
        if mom > 0 and len(prices) >= dd_look:
            recent_high = prices.iloc[-dd_look:].max()
            if recent_high > 0:
                dd_from_high = (recent_high - prices.iloc[-1]) / recent_high
                if dd_from_high > 0.12:  # >12% drawdown from recent high
                    mom *= max(0.4, 1.0 - dd_from_high)  # scale down mom

        # Quality momentum bonus: strong momentum + decent value (continuous)
        qm_bonus = 0.0
        if mom > 0.10 and value > 0.05:
            qm_cap = 0.35 if regime > 0.60 else 0.40
            qm_bonus = min(mom * value * 2.5 * (1 + high_prox * 0.45), qm_cap)

        # Dividend yield quality tilt
        if fund:
            div_y = fund.get('dividendYield')
            if div_y is not None and div_y > 0.018:
                dy_c = 0.25 if regime > 0.60 else 0.40
                value += min(div_y, 0.035) * dy_c

        # FCF yield bonus
        if fund and prices.iloc[-1] > 0:
            fcf_ps = fund.get('freeCashFlowPerShare')
            if fcf_ps is not None and fcf_ps > 0:
                fcf_yield = fcf_ps / prices.iloc[-1]
                if fcf_yield > 0.04:
                    value += min(fcf_yield, 0.15) * 0.06

        # Interest debt per share penalty
        if fund and prices.iloc[-1] > 0:
            idps = fund.get('interestDebtPerShare')
            if idps is not None and idps > 0:
                debt_yield = idps / prices.iloc[-1]
                if debt_yield > 0.35:
                    value *= max(0.75, 1.0 - (debt_yield - 0.35) * 0.90)

        # Debt-to-market-cap penalty on value
        if fund:
            dtm = fund.get('debtToMarketCap')
            if dtm is not None and dtm > 0.52:
                value *= max(0.60, 1.0 - (dtm - 0.52) * 0.8)

        # Capex intensity bonus (investing in growth)
        if fund and prices.iloc[-1] > 0:
            capex_ps = fund.get('capexPerShare')
            if capex_ps is not None and capex_ps > 0:
                capex_yield = capex_ps / prices.iloc[-1]
                cx_t = 0.04 if regime > 0.60 else 0.03
                if capex_yield > cx_t:
                    cx_c = 0.000 if regime > 0.60 else 0.04
                    value += min(capex_yield, 0.06) * cx_c

        # Inventory turnover bonus (lean operations)
        if fund:
            it = fund.get('inventoryTurnover')
            if it is not None and it > 12:
                efficiency += min(it / 40, 0.10) * 0.10

        # Dividend yield percentage bonus
        if fund:
            dyp = fund.get('dividendYieldPercentage')
            if dyp is not None and dyp > 2.5:
                value += min(dyp, 4.0) * 0.001

        # Dividend payout ratio bonus (moderate payout = quality)
        if fund:
            dpr = fund.get('dividendPayoutRatio')
            if dpr is not None and 0.25 < dpr < 3.00:
                value += (3.00 - abs(dpr - 0.32)) * 0.0055

        # Dividend per share yield bonus
        if fund and prices.iloc[-1] > 0:
            dps = fund.get('dividendPerShare')
            if dps is not None and dps > 0:
                dps_yield = dps / prices.iloc[-1]
                if dps_yield > 0.020:
                    dps_coeff = 1.00 + min(value, 0.10) * 3.0
                    value += min(dps_yield, 0.06) * dps_coeff

        # Regime-dividend interaction: boost dividend stocks in mild regimes
        if fund and 0.30 < regime < 0.80:
            div_y_r = fund.get('dividendYield')
            if div_y_r is not None and div_y_r > 0.015:
                value += min(div_y_r, 0.028) * 2.00

        # Regime-PTM interaction: boost profitability in mild regimes
        if fund and 0.30 < regime < 0.80:
            ptm_r = fund.get('pretaxProfitMargin')
            if ptm_r is not None and ptm_r > 0.15:
                rptm_c = 0.000 if regime > 0.60 else 0.008
                value += min(ptm_r, 0.25) * rptm_c

        # Regime-EBIT interaction: boost EBIT margin in mild regimes
        if fund and 0.30 < regime < 0.80:
            ebit_r = fund.get('ebitMargin')
            if ebit_r is not None and ebit_r > 0.15:
                ebit_r_cap = 0.18 if regime > 0.60 else 0.30
                rebit_c = 0.010 if regime > 0.60 else 0.014
                value += min(ebit_r, ebit_r_cap) * rebit_c

        # Regime-capex interaction
        if fund and 0.30 < regime < 0.80 and prices.iloc[-1] > 0:
            capex_r = fund.get('capexPerShare')
            if capex_r is not None and capex_r > 0:
                capex_r_yield = capex_r / prices.iloc[-1]
                if capex_r_yield > 0.03:
                    cap_r_c = 0.01 if regime > 0.60 else 0.12
                    value += min(capex_r_yield, 0.06) * cap_r_c

        # Regime-debt penalty: penalize high debt more in mild regimes
        if fund and 0.30 < regime < 0.80:
            dtm_r = fund.get('debtToMarketCap')
            dtmr_t = 0.28 if regime > 0.60 else 0.25
            if dtm_r is not None and dtm_r > dtmr_t:
                value *= max(0.80, 1.0 - (dtm_r - dtmr_t) * 0.70)

        # Regime-P/S interaction
        if fund and 0.30 < regime < 0.80:
            ps_r = fund.get('priceToSalesRatio')
            if ps_r is not None and 0 < ps_r < 5:
                rps_c = 0.016 if regime > 0.60 else 0.01
                value += min(1.0 / ps_r, 0.15) * rps_c

        # Regime-inventory turnover interaction
        if fund and 0.30 < regime < 0.80:
            it_r = fund.get('inventoryTurnover')
            if it_r is not None and it_r > 15:
                value += min(it_r / 50, 0.10) * 0.06

        # Regime-fixed asset turnover interaction
        if fund and 0.30 < regime < 0.80:
            fat_r = fund.get('fixedAssetTurnover')
            if fat_r is not None and fat_r > 3:
                value += min(fat_r / 15, 0.10) * 0.02

        # Regime-OCF yield interaction
        if fund and 0.30 < regime < 0.80 and prices.iloc[-1] > 0:
            ocf_ps_r = fund.get('operatingCashFlowPerShare')
            if ocf_ps_r is not None and ocf_ps_r > 0:
                ocf_yield_r = ocf_ps_r / prices.iloc[-1]
                if ocf_yield_r > 0.05:
                    ocf_r_c = 0.012 if regime > 0.60 else 0.025
                    value += min(ocf_yield_r, 0.15) * ocf_r_c

        # Price distance from 25DMA factor
        if len(prices) >= 50 and value > 0.05 and 0.30 < regime < 0.80:
            ma25 = prices.iloc[-25:].mean()
            ma50 = prices.iloc[-50:].mean()
            if ma25 > 0 and prices.iloc[-1] > ma50:
                dist = (prices.iloc[-1] - ma25) / ma25
                if 0 < dist < 0.05:
                    value += dist * 0.20

        # Low-vol value boost: reward low-volatility value stocks
        if len(prices) >= 60 and value > 0.05:
            stock_vol = prices.pct_change().iloc[-60:].std() * (252 ** 0.5)
            if stock_vol < 0.25:
                lv_coeff = 0.32 if regime > 0.60 else 0.40
                value *= 1.0 + (0.25 - stock_vol) * lv_coeff

        # Bollinger band position: reward stocks in lower half of band
        if len(prices) >= 25 and 0.30 < regime < 0.80 and value > 0.05:
            bb_ma = prices.iloc[-20:].mean()
            bb_std = prices.iloc[-20:].std()
            if bb_std > 0 and bb_ma > 0:
                bb_pos = (prices.iloc[-1] - bb_ma) / (2 * bb_std)  # -1 to +1 range
                if -0.5 < bb_pos < 0.2:
                    if bb_pos < 0:
                        bb_coeff = 0.06 if regime > 0.60 else 0.12
                        value += abs(bb_pos) * bb_coeff
                    else:
                        bb_pos_c = 0.02 if regime > 0.60 else 0.04
                        value += (0.2 - bb_pos) * bb_pos_c

        # Regime × stock-vol interaction: boost steady stocks more in mild regimes
        if len(prices) >= 60 and value > 0.05 and 0.30 < regime < 0.55:
            sv = prices.pct_change().iloc[-60:].std() * (252 ** 0.5)
            if sv < 0.20:
                value += (0.20 - sv) * 0.30

        # Short-term OCF coverage in mild
        if fund and 0.30 < regime < 0.55:
            stocf = fund.get('shortTermOperatingCashFlowCoverageRatio')
            if stocf is not None and stocf > 1.5:
                value += min(stocf / 10, 0.08) * 0.04

        # Dividend consistency bonus
        if fund:
            div_signals = 0
            if fund.get('dividendYield') and fund['dividendYield'] > 0.01:
                div_signals += 1
            if fund.get('dividendPerShare') and fund['dividendPerShare'] > 0:
                div_signals += 1
            if fund.get('dividendPayoutRatio') and 0.25 < fund['dividendPayoutRatio'] < 1.0:
                div_signals += 1
            if div_signals >= 3:
                value += 0.004

        # Interaction: momentum × value synergy (boosted by efficiency and QM)
        val_boost = min(value, 0.15) * 0.9 if value > 0.05 else 0
        mom_eff = max(mom, 0) * efficiency * 1.0 if mom > 0.10 and efficiency > 0.15 else 0
        syn_qm = 0.00 if regime > 0.60 else 0.55
        syn_hp = 0.65 if regime > 0.60 else 0.45
        synergy = max(mom, 0) * max(value, 0) * (1 + efficiency * 1.2 + qm_bonus * syn_qm + high_prox * syn_hp + val_boost + mom_eff)

        # Regime-spy_mom value boost
        if 0.30 < regime < 0.80 and spy_mom > 0.2:
            sm_c = 1.00 if regime > 0.60 else 0.70
            value += spy_mom * sm_c

        # Blend
        scores[ticker] = 0.24 * mom + 0.19 * value + 0.09 * efficiency + 0.30 * synergy + 0.18 * qm_bonus

    if not scores:
        return {}

    # Top N by score, must be positive
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    # Dynamic top_n: fewer stocks in moderate regimes
    dyn_n = PARAMS['top_n'] if regime > 0.60 else max(6, int(PARAMS['top_n'] * regime / 0.60))
    top = [(t, s) for t, s in sorted_scores[:dyn_n] if s > 0.04]
    if not top:
        return {}

    # Score-cubed weighting (more concentrated)
    sq_top = [(t, s**21.8) for t, s in top]
    total_score = sum(s for _, s in sq_top)
    # Score spread bonus: if top score is much higher, increase exposure slightly
    if len(top) >= 3:
        spread = top[0][1] / max(top[2][1], 0.001)
        spread_mult = min(1.0 + (spread - 1.0) * 0.25, 1.35)
    else:
        spread_mult = 1.0
    # Conviction-regime interaction: boost when both are strong
    conviction_boost = 1.0 + max(0, regime - 0.68) * max(0, spread_mult - 1.05) * 8.0 * (1 + spy_mom * 1.35)
    exposure = PARAMS['max_exposure'] * (regime ** 1.05) * spread_mult * min(conviction_boost, 2.00) * seasonal_factor
    exposure = max(exposure, 0.003)  # dynamic minimum exposure
    if total_score <= 0:
        return {}
    max_weight = min(0.042 * spread_mult, 0.14)  # dynamic cap based on conviction
    raw = {t: (s / total_score) for t, s in sq_top}
    # Cap and redistribute
    capped = {t: min(w, max_weight) for t, w in raw.items()}
    cap_total = sum(capped.values())
    if cap_total <= 0:
        return {}
    return {t: (w / cap_total) * exposure for t, w in capped.items()}
