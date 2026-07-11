"""
ADVANCED QUANT INDICATORS LIBRARY
===================================
Production-ready implementations of institutional-grade statistical
indicators. Pure math — no DB, no HTTP calls, no side effects.

Groups:
  1. Market microstructure (Hurst, Roll spread, Corwin-Schultz, Amihud)
  2. Flow toxicity (VPIN)
  3. Return distribution (realized skew/kurtosis, jump detection, Shannon entropy)
  4. Cross-sectional / factor-based (PCA, cross-sectional momentum z-score, absorption ratio)
  5. Options / vol surface (GEX, VRP, skew velocity, risk-neutral density)
"""

import numpy as np
import pandas as pd


# =====================================================================
# 1. MARKET MICROSTRUCTURE
# =====================================================================

def hurst_exponent(price_series: pd.Series, min_lag: int = 2, max_lag: int = 20) -> float:
    """
    Hurst exponent via R/S analysis applied to log-returns.

    H < 0.5: mean-reverting (anti-persistent)
    H ≈ 0.5: random walk (no edge)
    H > 0.5: trending (momentum persistent)

    NOTE: R/S analysis must be applied to log-returns, not raw price levels.
    Raw prices are non-stationary (trending by construction) and produce
    H > 1.0 for virtually every stock, which the np.clip then collapses to
    an identical 1.0 for all tickers. Log-returns are stationary and yield
    meaningful, ticker-specific H estimates in the [0, 1] range.

    Args:
        price_series: pandas Series of prices (not returns), chronological.
        min_lag / max_lag: lag range for the R/S regression.

    Returns:
        float, Hurst exponent in [0, 1].
    """
    price_arr = np.array(price_series.dropna(), dtype=float)
    if len(price_arr) < max_lag * 2 + 1:
        return 0.5
    price_arr = np.maximum(price_arr, 1e-10)
    series = np.diff(np.log(price_arr))
    if len(series) < max_lag * 2:
        return 0.5

    lags = range(min_lag, min(max_lag + 1, len(series) // 2))
    rs_vals = []
    for lag in lags:
        chunks = [series[i:i + lag] for i in range(0, len(series) - lag, lag)]
        rs_chunk = []
        for chunk in chunks:
            if len(chunk) < 2:
                continue
            mean = chunk.mean()
            dev = np.cumsum(chunk - mean)
            r = dev.max() - dev.min()
            s = chunk.std(ddof=1)
            if s > 0:
                rs_chunk.append(r / s)
        if rs_chunk:
            rs_vals.append((lag, np.mean(rs_chunk)))

    if len(rs_vals) < 2:
        return 0.5

    lags_arr = np.log([v[0] for v in rs_vals])
    rs_arr = np.log([v[1] for v in rs_vals])
    try:
        h = float(np.polyfit(lags_arr, rs_arr, 1)[0])
    except Exception:
        h = 0.5
    return float(np.clip(h, 0.0, 1.0))


def roll_spread_estimator(price_series: pd.Series) -> float:
    """
    Roll (1984) bid-ask spread estimator from price series alone.

    Estimates the effective bid-ask spread using the negative serial
    covariance of price changes. Works on any price series without
    needing an actual order book.

    Args:
        price_series: pandas Series of transaction/close prices.

    Returns:
        float, estimated spread as a fraction of price (e.g. 0.002 = 0.2%).
        Returns 0 if the covariance is positive (no spread detectable).
    """
    changes = price_series.diff().dropna()
    cov = float(np.cov(changes[:-1], changes[1:])[0, 1])
    if cov >= 0:
        return 0.0
    return 2.0 * float(np.sqrt(-cov))


def corwin_schultz_spread(high: pd.Series, low: pd.Series) -> pd.Series:
    """
    Corwin-Schultz (2012) high-low spread estimator.

    More accurate than Roll on daily OHLC data because it uses the
    full intraday range (not just close-to-close changes) and corrects
    for overnight variance contamination.

    Args:
        high / low: pandas Series of daily high/low prices, same index.

    Returns:
        pandas Series of per-period spread estimates (fraction of price).
        Negative estimates are floored to 0.
    """
    beta = (np.log(high / low) ** 2 + np.log(high.shift(1) / low.shift(1)) ** 2)
    gamma = np.log(pd.concat([high, high.shift(1)], axis=1).max(axis=1) /
                   pd.concat([low, low.shift(1)], axis=1).min(axis=1)) ** 2

    denom = 3.0 - 2.0 * np.sqrt(2.0)
    alpha = (np.sqrt(2.0 * beta) - np.sqrt(beta)) / denom - np.sqrt(gamma / denom)
    spread = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))
    return spread.clip(lower=0)


def amihud_illiquidity(returns: pd.Series, dollar_volume: pd.Series,
                        window: int = 21) -> pd.Series:
    """
    Amihud (2002) illiquidity ratio.

    |return| / dollar_volume. Higher = more price impact per dollar traded
    = less liquid. Normalized by the window mean so it's comparable
    across different price/vol regimes.

    Args:
        returns: pandas Series of period returns (pct, not log).
        dollar_volume: pandas Series of price × volume, same index.
        window: rolling window for the mean normalization.

    Returns:
        pandas Series of rolling Amihud illiquidity ratios.
    """
    ratio = returns.abs() / dollar_volume.replace(0, np.nan)
    return ratio.rolling(window).mean()


# =====================================================================
# 2. FLOW TOXICITY
# =====================================================================

def vpin(volume: pd.Series, price: pd.Series, bucket_size: int = None) -> pd.Series:
    """
    Volume-synchronized Probability of Informed Trading (VPIN).
    Easley, Lopez de Prado, O'Hara (2012).

    VPIN estimates the fraction of trading volume from informed traders
    by looking at imbalances between buy-initiated and sell-initiated
    volume within fixed-volume buckets. High VPIN → toxic flow / flash
    crash risk; low VPIN → balanced, uninformed two-sided flow.

    Args:
        volume: pandas Series of period volumes.
        price: pandas Series of prices, same index.
        bucket_size: number of periods per bucket (default: len / 50).

    Returns:
        pandas Series of VPIN values, same index as inputs.
    """
    if bucket_size is None:
        bucket_size = max(1, len(volume) // 50)

    price_chg = price.diff().fillna(0)
    buy_vol = volume * (price_chg > 0).astype(float)
    sell_vol = volume * (price_chg <= 0).astype(float)

    n = len(volume)
    vpin_vals = pd.Series(np.nan, index=volume.index)
    num_buckets = 50

    for i in range(bucket_size * num_buckets, n):
        window_buy = buy_vol.iloc[i - bucket_size * num_buckets:i]
        window_sell = sell_vol.iloc[i - bucket_size * num_buckets:i]
        total = (window_buy + window_sell).sum()
        if total > 0:
            vpin_vals.iloc[i] = abs(window_buy.sum() - window_sell.sum()) / total

    return vpin_vals


# =====================================================================
# 3. RETURN DISTRIBUTION
# =====================================================================

def realized_skew_kurtosis(returns: pd.Series, window: int = 21) -> pd.DataFrame:
    """
    Rolling realized skewness and excess kurtosis of returns.

    Skew < 0: left-tailed (crash risk / downside fat tail)
    Kurtosis > 3: leptokurtic (fat tails, more extreme events than normal)

    Args:
        returns: pandas Series of period returns.
        window: rolling window.

    Returns:
        pandas DataFrame with columns 'skew' and 'kurtosis'.
    """
    skew = returns.rolling(window).skew()
    kurt = returns.rolling(window).kurt()
    return pd.DataFrame({"skew": skew, "kurtosis": kurt})


def jump_detection_bipower(returns: pd.Series, threshold: float = 3.0) -> pd.Series:
    """
    Bipower variation jump detection (Barndorff-Nielsen & Shephard, 2004).

    Compares realized variance (sum of squared returns) to bipower
    variation (sum of |r_t| × |r_{t-1}|). The ratio identifies days
    where a discontinuous price jump (not continuous diffusion) drove
    the move. Useful for flagging earnings gaps, news shocks, halts.

    Args:
        returns: pandas Series of period returns.
        threshold: z-score threshold above which a jump is flagged.
                   3.0 ≈ 0.27% false positive rate under normality.

    Returns:
        pandas Series of booleans — True = jump detected on that period.

    NOTE: The z-score normalization uses rolling(252, min_periods=30).
    Callers should pass at least 60 bars; the more history the better.
    With fewer than 30 valid ratio values, the function falls back to a
    direct ratio threshold (ratio > 0.5) rather than returning all-False.
    """
    abs_ret = returns.abs()
    bpv = (abs_ret * abs_ret.shift(1)).rolling(21, min_periods=10).mean() * np.pi / 2
    rv = (returns ** 2).rolling(21, min_periods=10).mean()
    ratio = (rv - bpv) / (bpv + 1e-10)
    n_valid = int(ratio.notna().sum())
    if n_valid < 30:
        return ratio > 0.5
    z = (ratio - ratio.rolling(252, min_periods=30).mean()) / (ratio.rolling(252, min_periods=30).std(ddof=1) + 1e-10)
    return z.abs() > threshold


def shannon_entropy(returns: pd.Series, window: int = 21, n_bins: int = 10) -> pd.Series:
    """
    Shannon entropy of the return distribution in a rolling window.

    Low entropy = returns are clustered in a few bins = highly predictable
    directional pattern (trending or compressed before a move).
    High entropy = returns are spread uniformly = random, no edge.

    Args:
        returns: pandas Series of period returns.
        window: rolling window for entropy calculation.
        n_bins: number of histogram bins for the distribution estimate.

    Returns:
        pandas Series of entropy values (in bits, base-2 log).
        Max entropy = log2(n_bins).
    """
    def _ent(arr):
        arr = arr[~np.isnan(arr)]
        if len(arr) < 5:
            return np.nan
        counts, _ = np.histogram(arr, bins=n_bins)
        probs = counts / counts.sum()
        probs = probs[probs > 0]
        return float(-np.sum(probs * np.log2(probs)))

    return returns.rolling(window).apply(_ent, raw=True)


# =====================================================================
# 4. CROSS-SECTIONAL / FACTOR-BASED (needs a universe of tickers)
# =====================================================================

def pca_factor_decomposition(returns_matrix: pd.DataFrame, n_factors: int = 3):
    """
    PCA-based factor decomposition of a return matrix.

    Strips out market-wide / sector-wide common factors so you can see
    which stocks are moving for IDIOSYNCRATIC reasons (real news/flow)
    vs. just riding a broad sector or market wave.

    Args:
        returns_matrix: pandas DataFrame, rows = time, columns = tickers,
                         values = period returns.
        n_factors: number of principal components to extract.

    Returns:
        dict with:
          'factor_returns': DataFrame of the top n_factors PC time series
          'loadings': DataFrame of each ticker's exposure to each factor
          'idiosyncratic_returns': DataFrame, original returns minus the
                                    reconstructed factor-explained portion
          'explained_variance_ratio': fraction of variance each factor explains
    """
    clean = returns_matrix.dropna(axis=1, thresh=int(len(returns_matrix) * 0.9)).dropna()
    X = clean.values
    X_centered = X - X.mean(axis=0)

    cov = np.cov(X_centered, rowvar=False)
    eigvals, eigvecs = np.linalg.eigh(cov)
    order = np.argsort(eigvals)[::-1]
    eigvals, eigvecs = eigvals[order], eigvecs[:, order]

    top_vecs = eigvecs[:, :n_factors]
    factor_returns = X_centered @ top_vecs
    reconstructed = factor_returns @ top_vecs.T
    idiosyncratic = X_centered - reconstructed

    explained = eigvals[:n_factors] / eigvals.sum()

    return {
        "factor_returns": pd.DataFrame(factor_returns, index=clean.index,
                                        columns=[f"PC{i+1}" for i in range(n_factors)]),
        "loadings": pd.DataFrame(top_vecs, index=clean.columns,
                                  columns=[f"PC{i+1}" for i in range(n_factors)]),
        "idiosyncratic_returns": pd.DataFrame(idiosyncratic, index=clean.index,
                                               columns=clean.columns),
        "explained_variance_ratio": explained,
    }


def cross_sectional_momentum_zscore(returns_matrix: pd.DataFrame,
                                     lookback: int = 21) -> pd.DataFrame:
    """
    Cross-sectional momentum z-score: how strong is this stock's move
    RELATIVE to its peer universe, not just in absolute terms.

    Args:
        returns_matrix: pandas DataFrame, rows = time, columns = tickers.
        lookback: rolling lookback window for cumulative return calc.

    Returns:
        pandas DataFrame, same shape as input, of cross-sectional z-scores
        per period (z-score computed ACROSS columns at each timestamp).
    """
    cum_ret = (1 + returns_matrix).rolling(lookback).apply(lambda x: x.prod() - 1, raw=True)
    cross_mean = cum_ret.mean(axis=1)
    cross_std = cum_ret.std(axis=1)
    z = cum_ret.sub(cross_mean, axis=0).div(cross_std.replace(0, np.nan), axis=0)
    return z


def absorption_ratio(returns_matrix: pd.DataFrame, n_factors: int = 5) -> float:
    """
    Absorption Ratio (Kritzman et al., MIT) -- systemic risk measure.

    Fraction of total variance in the universe explained by the top
    N principal components. HIGH = fragile, correlated market.
    LOW = stock-picker's market, idiosyncratic moves dominate.

    Args:
        returns_matrix: pandas DataFrame, rows = time, columns = tickers.
        n_factors: number of top principal components to sum.

    Returns:
        float, absorption ratio in [0, 1].
    """
    clean = returns_matrix.dropna(axis=1, thresh=int(len(returns_matrix) * 0.9)).dropna()
    if clean.shape[1] < n_factors + 1:
        return 0.5
    cov = np.cov(clean.values, rowvar=False)
    eigvals = np.linalg.eigvalsh(cov)[::-1]
    n_factors = min(n_factors, len(eigvals))
    return float(eigvals[:n_factors].sum() / eigvals.sum())


# =====================================================================
# 5. OPTIONS / VOL SURFACE (requires a full options chain snapshot)
# =====================================================================

def variance_risk_premium(realized_vol: pd.Series, implied_vol: pd.Series) -> pd.Series:
    """
    Variance Risk Premium (VRP) = Implied Vol^2 - Realized Vol^2.

    Persistently positive and mean-reverting: options tend to overprice
    realized volatility on average. Large deviations = tradeable signal.

    Args:
        realized_vol: pandas Series of trailing realized vol (annualized).
        implied_vol: pandas Series of ATM implied vol, same index.

    Returns:
        pandas Series of VRP values (variance units).
    """
    return (implied_vol ** 2) - (realized_vol ** 2)


def skew_velocity(put_skew_25d: pd.Series, window: int = 5) -> pd.Series:
    """
    Skew velocity: rate of change of 25-delta put skew over time.

    The VELOCITY of skew change tells you whether fear is actively
    building or unwinding RIGHT NOW — more useful as a timing signal
    than the static skew level.

    Args:
        put_skew_25d: pandas Series of 25-delta put IV minus ATM IV.
        window: lookback window for the rate-of-change calc.

    Returns:
        pandas Series, skew velocity (change per period).
    """
    return put_skew_25d.diff(window) / window


def risk_neutral_density(strikes: np.ndarray, call_prices: np.ndarray,
                          r: float, t: float) -> np.ndarray:
    """
    Risk-neutral probability density via Breeden-Litzenberger (1978).

    Extracts the market's implied probability distribution of future
    price outcomes directly from the options chain.

    Args:
        strikes: 1D np.array of strikes, sorted ascending, evenly spaced.
        call_prices: 1D np.array of call mid-prices at each strike.
        r: risk-free rate (annualized decimal, e.g. 0.05).
        t: time to expiration in years.

    Returns:
        np.array of risk-neutral density values (first/last 2 are NaN).
    """
    dk = np.diff(strikes)
    if not np.allclose(dk, dk[0], rtol=1e-3):
        raise ValueError("strikes must be evenly spaced -- interpolate chain first")
    dk = dk[0]

    second_deriv = np.full_like(call_prices, np.nan, dtype=float)
    second_deriv[1:-1] = (
        call_prices[2:] - 2 * call_prices[1:-1] + call_prices[:-2]
    ) / (dk ** 2)

    density = np.exp(r * t) * second_deriv
    return density
