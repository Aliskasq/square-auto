"""Simple candlestick chart generator for Binance Square posts.

Style: black background, no grid, no frame, bright red/green candles.
Header: coin name in red + orange Binance logo + "BINANCE" text.
"""
import io
import gc
import logging
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
import numpy as np
import os

logger = logging.getLogger(__name__)

# Binance logo path (small orange icon)
_LOGO_DIR = os.path.dirname(os.path.abspath(__file__))
_LOGO_PATH = os.path.join(_LOGO_DIR, "binance_logo.png")

# Colors
BG_COLOR = '#000000'
BULL_COLOR = '#00FF41'      # bright green
BEAR_COLOR = '#FF0000'      # bright red
TITLE_COLOR = '#FF0000'     # coin name in red
BINANCE_COLOR = '#F0B90B'   # Binance orange/yellow
WICK_ALPHA = 1.0
BODY_WIDTH = 0.7
WICK_WIDTH = 0.8


def generate_chart(candles: list[list], symbol: str, output_path: str = None) -> bytes | None:
    """Generate a candlestick chart image.
    
    Args:
        candles: List of [timestamp, open, high, low, close, volume, ...]
                 (standard Binance kline format). Last 100 used.
        symbol: e.g. "ACEUSDT" — will be formatted as "ACE / USDT"
        output_path: If set, save PNG to this path. Otherwise return bytes.
    
    Returns:
        PNG bytes if output_path is None, else None (saves to file).
    """
    if not candles or len(candles) < 10:
        logger.error(f"Not enough candles for chart: {len(candles) if candles else 0}")
        return None
    
    # Take last 100 candles
    data = candles[-100:]
    
    opens = np.array([float(c[1]) for c in data])
    highs = np.array([float(c[2]) for c in data])
    lows = np.array([float(c[3]) for c in data])
    closes = np.array([float(c[4]) for c in data])
    
    n = len(data)
    fig = None
    
    try:
        fig, ax = plt.subplots(figsize=(12, 6), facecolor=BG_COLOR)
        ax.set_facecolor(BG_COLOR)
        
        # Draw candles
        for i in range(n):
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            color = BULL_COLOR if c >= o else BEAR_COLOR
            
            # Wick (shadow)
            ax.plot([i, i], [l, h], color=color, linewidth=WICK_WIDTH, 
                    alpha=WICK_ALPHA, solid_capstyle='round')
            
            # Body
            body_bottom = min(o, c)
            body_height = abs(c - o)
            if body_height < (h - l) * 0.005:  # doji — thin line
                body_height = (h - l) * 0.005
            ax.bar(i, body_height, bottom=body_bottom, color=color, 
                   width=BODY_WIDTH, edgecolor=color, linewidth=0)
        
        # Remove all axes, frames, grids
        ax.set_xlim(-1, n)
        ax.axis('off')
        ax.margins(x=0.01, y=0.05)
        
        # Remove all spines
        for spine in ax.spines.values():
            spine.set_visible(False)
        
        # --- Header: coin name + Binance logo + "BINANCE" ---
        short = symbol.replace("USDT", "")
        pair_name = f"{short} / USDT"
        
        # Coin name — bright red, left side
        fig.text(0.06, 0.93, pair_name, color=TITLE_COLOR, 
                 fontsize=22, fontweight='bold', ha='left', va='top',
                 fontfamily='sans-serif')
        
        # Binance logo (if exists) + "BINANCE" text
        # Position after the coin name
        if os.path.exists(_LOGO_PATH):
            try:
                logo_img = plt.imread(_LOGO_PATH)
                # Place logo as inset
                logo_ax = fig.add_axes([0.75, 0.88, 0.05, 0.08])  # [left, bottom, width, height]
                logo_ax.imshow(logo_img)
                logo_ax.axis('off')
                fig.text(0.81, 0.93, "BINANCE", color=BINANCE_COLOR,
                         fontsize=16, fontweight='bold', ha='left', va='top',
                         fontfamily='sans-serif')
            except Exception as e:
                logger.warning(f"Could not load logo: {e}")
                # Fallback: just text with a diamond symbol
                fig.text(0.75, 0.93, "◆ BINANCE", color=BINANCE_COLOR,
                         fontsize=16, fontweight='bold', ha='left', va='top',
                         fontfamily='sans-serif')
        else:
            # No logo file — use diamond symbol
            fig.text(0.75, 0.93, "◆ BINANCE", color=BINANCE_COLOR,
                     fontsize=16, fontweight='bold', ha='left', va='top',
                     fontfamily='sans-serif')
        
        plt.subplots_adjust(left=0.02, right=0.98, top=0.88, bottom=0.02)
        
        if output_path:
            fig.savefig(output_path, dpi=150, facecolor=BG_COLOR, 
                       bbox_inches='tight', pad_inches=0.1)
            logger.info(f"Chart saved: {output_path} ({os.path.getsize(output_path)} bytes)")
            return None
        else:
            buf = io.BytesIO()
            fig.savefig(buf, format='png', dpi=150, facecolor=BG_COLOR,
                       bbox_inches='tight', pad_inches=0.1)
            buf.seek(0)
            return buf.read()
    
    except Exception as e:
        logger.error(f"Chart generation error for {symbol}: {e}")
        return None
    finally:
        if fig:
            fig.clf()
        plt.close('all')
        gc.collect()


async def fetch_mexc_klines(symbol: str, interval: str = "15m", limit: int = 100) -> list | None:
    """Fetch klines from MEXC (no geo-restrictions)."""
    import httpx
    url = "https://api.mexc.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                raw = resp.json()
                if not raw:
                    return None
                # MEXC format: [timestamp, open, high, low, close, volume, ...]
                return raw
            else:
                logger.error(f"MEXC klines {symbol} HTTP {resp.status_code}")
                return None
    except Exception as e:
        logger.error(f"MEXC klines error {symbol}: {e}")
        return None


async def generate_chart_for_symbol(symbol: str, output_path: str = None) -> str | None:
    """Fetch 15m candles from MEXC and generate chart.
    
    Args:
        symbol: e.g. "ACEUSDT" or "ACE"
        output_path: Where to save. Defaults to /tmp/{symbol}_chart.png
    
    Returns:
        Path to saved chart PNG, or None on failure.
    """
    if not symbol.endswith("USDT"):
        symbol = symbol + "USDT"
    
    symbol = symbol.upper()
    
    if output_path is None:
        output_path = f"/tmp/{symbol}_chart.png"
    
    # Fetch 100 candles on 15m from MEXC
    candles = await fetch_mexc_klines(symbol, "15m", 100)
    if not candles:
        logger.error(f"Failed to fetch 15m klines for {symbol}")
        return None
    
    generate_chart(candles, symbol, output_path)
    
    if os.path.exists(output_path):
        return output_path
    return None
