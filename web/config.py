"""Trading Console configuration."""

WINDOWS_IP = "192.168.1.15"
WINDOWS_PORT = 8080
SCAN_INTERVAL = 300  # 5 minutes
SENTIMENT_INTERVAL = 900  # 15 minutes
DATA_DIR = "data/"
WEB_PORT = 8420
SCID_RECORD_SIZE = 40  # bytes per tick record

# DH|S2 Checklist — aligned with replay strategy (259 trades, 71% WR, PF 11.1)
CHECKLIST = [
    {"name": "At confluent level", "key": "at_level"},
    {"name": "Delta confirms direction", "key": "delta_confirms"},
    {"name": "Cum delta momentum", "key": "cum_delta_momentum"},
    {"name": "VWAP aligned", "key": "vwap_aligned"},
    {"name": "ATR > 2 pips", "key": "atr_ok"},
]
