from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class DivergenceType(Enum):
    REGULAR_BULLISH = "regular_bullish"   # Price lower low, RSI higher low
    REGULAR_BEARISH = "regular_bearish"   # Price higher high, RSI lower high
    HIDDEN_BULLISH = "hidden_bullish"     # Price higher low, RSI lower low
    HIDDEN_BEARISH = "hidden_bearish"     # Price lower high, RSI higher high


class SignalStrength(Enum):
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


@dataclass
class SwingPoint:
    index: int
    timestamp: datetime
    price: float
    rsi: float
    is_high: bool


@dataclass
class Divergence:
    type: DivergenceType
    instrument: str
    timeframe: str
    swing_a: SwingPoint  # Earlier swing
    swing_b: SwingPoint  # Later swing (current/recent)
    strength: SignalStrength
    bars_apart: int


@dataclass
class PivotLevel:
    name: str        # "R1", "S1", "PP", etc.
    value: float
    level_type: str  # "resistance", "support", "pivot"


@dataclass
class PivotProximity:
    level: PivotLevel
    distance: float
    distance_atr_ratio: float
    is_near: bool


@dataclass
class SentimentResult:
    source: str       # "reddit", "news", "tradingview", "twitter"
    instrument: str
    score: float      # -1.0 (bearish) to +1.0 (bullish)
    confidence: float # 0.0 to 1.0
    summary: str
    sample_texts: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class CompositeSentiment:
    instrument: str
    overall_score: float
    overall_confidence: float
    sources: List[SentimentResult]
    summary: str


@dataclass
class Alert:
    timestamp: datetime
    instrument: str
    timeframe: str
    divergence: Optional[Divergence]
    pivot_proximity: Optional[List[PivotProximity]]
    sentiment: Optional[CompositeSentiment]
    tv_summary: Optional[dict]
    confluence_score: float  # 0-100
    headline: str
