"""DTC Protocol client for Sierra Chart.
Implements the binary DTC protocol to fetch historical data.
Reference: https://dtcprotocol.org/
"""

import struct
import socket
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import pandas as pd
import numpy as np

logger = logging.getLogger("trading-monitor.dtc")

# DTC Message Types
LOGON_REQUEST = 1
LOGON_RESPONSE = 2
HEARTBEAT = 3
ENCODING_REQUEST = 6
ENCODING_RESPONSE = 7
HISTORICAL_PRICE_DATA_REQUEST = 800
HISTORICAL_PRICE_DATA_RESPONSE_HEADER = 801
HISTORICAL_PRICE_DATA_RECORD_RESPONSE = 803
HISTORICAL_PRICE_DATA_REJECT = 802

# DTC encoding types
BINARY_ENCODING = 0
BINARY_WITH_VARIABLE_LENGTH_STRINGS = 1

# Record intervals
INTERVAL_TICK = 0
INTERVAL_1_SECOND = 1
INTERVAL_1_MINUTE = 60
INTERVAL_5_MINUTE = 300
INTERVAL_15_MINUTE = 900
INTERVAL_60_MINUTE = 3600
INTERVAL_DAILY = 86400


def _pack_string(s: str, max_len: int) -> bytes:
    """Pack a string to fixed-length bytes."""
    encoded = s.encode("utf-8")[:max_len - 1]
    return encoded + b'\x00' * (max_len - len(encoded))


def _read_exactly(sock: socket.socket, n: int) -> bytes:
    """Read exactly n bytes from socket."""
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Connection closed")
        data += chunk
    return data


class DTCClient:
    """Client for Sierra Chart's DTC Protocol server."""

    def __init__(self, host: str, port: int = 11099, hist_port: int = 11098):
        self.host = host
        self.port = port
        self.hist_port = hist_port
        self._sock = None
        self._hist_sock = None

    def connect(self) -> bool:
        """Connect and perform logon handshake."""
        try:
            # Connect main socket
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(10)
            self._sock.connect((self.host, self.port))
            logger.info(f"Connected to {self.host}:{self.port}")

            # Send encoding request (binary encoding)
            self._send_encoding_request(self._sock)
            self._recv_encoding_response(self._sock)

            # Send logon
            self._send_logon(self._sock)
            resp = self._recv_logon_response(self._sock)
            logger.info(f"Logon response: result={resp.get('result')}, server={resp.get('server_name')}")

            return resp.get("result", 0) == 1  # 1 = success

        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False

    def connect_historical(self) -> bool:
        """Connect to historical data port."""
        try:
            self._hist_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._hist_sock.settimeout(30)
            self._hist_sock.connect((self.host, self.hist_port))
            logger.info(f"Connected to historical port {self.host}:{self.hist_port}")

            self._send_encoding_request(self._hist_sock)
            self._recv_encoding_response(self._hist_sock)

            self._send_logon(self._hist_sock)
            resp = self._recv_logon_response(self._hist_sock)
            logger.info(f"Historical logon: result={resp.get('result')}")

            return resp.get("result", 0) == 1

        except Exception as e:
            logger.error(f"Historical connection failed: {e}")
            return False

    def _send_encoding_request(self, sock):
        """Send encoding request - request binary encoding."""
        # Type 6: encoding request
        # Size(2) + Type(2) + ProtocolVersion(4) + Encoding(4) + ProtocolType(32)
        size = 2 + 2 + 4 + 4 + 32
        msg = struct.pack("<HHiI", size, ENCODING_REQUEST, 8, BINARY_ENCODING)
        msg += _pack_string("DTC", 32)
        sock.sendall(msg)

    def _recv_encoding_response(self, sock):
        """Receive encoding response."""
        header = _read_exactly(sock, 4)
        size, msg_type = struct.unpack("<HH", header)
        if size > 4:
            _read_exactly(sock, size - 4)
        logger.debug(f"Encoding response: type={msg_type}, size={size}")

    def _send_logon(self, sock):
        """Send logon request."""
        # Logon request fields
        protocol_version = 8
        username = ""
        password = ""
        general_text = "trading-monitor"
        heartbeat_interval = 60
        client_name = "trading-monitor-py"

        # Build message with fixed-length string fields
        body = struct.pack("<i", protocol_version)  # 4
        body += _pack_string(username, 32)  # 32
        body += _pack_string(password, 32)  # 32
        body += _pack_string(general_text, 64)  # 64
        body += struct.pack("<i", heartbeat_interval)  # 4
        body += struct.pack("<i", 0)  # unused1
        body += struct.pack("<i", 0)  # trade_mode
        body += _pack_string("", 32)  # trade account
        body += _pack_string("", 64)  # hardware identifier
        body += _pack_string(client_name, 32)  # client name

        size = 4 + len(body)
        header = struct.pack("<HH", size, LOGON_REQUEST)
        sock.sendall(header + body)

    def _recv_logon_response(self, sock) -> dict:
        """Receive and parse logon response."""
        header = _read_exactly(sock, 4)
        size, msg_type = struct.unpack("<HH", header)

        if msg_type == LOGON_RESPONSE and size > 4:
            body = _read_exactly(sock, size - 4)
            result = struct.unpack_from("<i", body, 0)[0] if len(body) >= 4 else 0
            server_name = body[36:68].split(b'\x00')[0].decode("utf-8", errors="ignore") if len(body) >= 68 else ""
            return {"result": result, "server_name": server_name, "msg_type": msg_type}
        elif size > 4:
            _read_exactly(sock, size - 4)

        return {"result": 0, "msg_type": msg_type}

    def fetch_historical(
        self,
        symbol: str,
        exchange: str,
        interval_seconds: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        max_bars: int = 0,
    ) -> pd.DataFrame:
        """Fetch historical price data.
        Returns DataFrame with: open, high, low, close, volume, num_trades, bid_volume, ask_volume.
        """
        sock = self._hist_sock or self._sock
        if sock is None:
            raise RuntimeError("Not connected")

        request_id = 1

        # Build historical data request
        # request_id(4) + symbol(64) + exchange(16) + record_interval(4) +
        # start_dt(8) + end_dt(8) + max_records(4) + use_zlib(1) + padding(3)
        body = struct.pack("<I", request_id)
        body += _pack_string(symbol, 64)
        body += _pack_string(exchange, 16)
        body += struct.pack("<I", interval_seconds)

        if start_date:
            start_ts = int(start_date.replace(tzinfo=timezone.utc).timestamp())
        else:
            start_ts = 0
        if end_date:
            end_ts = int(end_date.replace(tzinfo=timezone.utc).timestamp())
        else:
            end_ts = 0

        body += struct.pack("<q", start_ts)  # start datetime
        body += struct.pack("<q", end_ts)    # end datetime
        body += struct.pack("<I", max_bars)  # max records (0=all)
        body += struct.pack("<B", 0)         # use zlib compression
        body += b'\x00' * 3                  # padding
        body += struct.pack("<I", 0)         # integer to float divisor (0=no conversion)

        size = 4 + len(body)
        header = struct.pack("<HH", size, HISTORICAL_PRICE_DATA_REQUEST)

        logger.info(f"Requesting {symbol}/{exchange} interval={interval_seconds}s")
        sock.sendall(header + body)

        # Receive response
        return self._recv_historical_data(sock, request_id)

    def _recv_historical_data(self, sock, request_id: int) -> pd.DataFrame:
        """Receive historical data records."""
        records = []
        sock.settimeout(30)

        while True:
            try:
                header = _read_exactly(sock, 4)
                size, msg_type = struct.unpack("<HH", header)

                if size <= 4:
                    continue

                body = _read_exactly(sock, size - 4)

                if msg_type == HISTORICAL_PRICE_DATA_RESPONSE_HEADER:
                    req_id = struct.unpack_from("<I", body, 0)[0]
                    use_zlib = struct.unpack_from("<B", body, 4)[0] if len(body) > 4 else 0
                    no_records = struct.unpack_from("<B", body, 5)[0] if len(body) > 5 else 0
                    logger.info(f"History header: req={req_id}, zlib={use_zlib}, no_records={no_records}")
                    if no_records:
                        logger.warning("Server says no records available")
                        break

                elif msg_type == HISTORICAL_PRICE_DATA_RECORD_RESPONSE:
                    record = self._parse_record(body)
                    if record:
                        records.append(record)

                elif msg_type == HISTORICAL_PRICE_DATA_REJECT:
                    reason = body[4:].split(b'\x00')[0].decode("utf-8", errors="ignore")
                    logger.warning(f"Historical data rejected: {reason}")
                    break

                elif msg_type == HEARTBEAT:
                    pass  # Ignore heartbeats

                else:
                    logger.debug(f"Unknown msg type {msg_type}, size={size}")

            except socket.timeout:
                logger.info(f"Timeout — received {len(records)} records")
                break
            except Exception as e:
                logger.warning(f"Error receiving data: {e}")
                break

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime").sort_index()
        logger.info(f"Received {len(df)} bars ({df.index[0]} to {df.index[-1]})")
        return df

    def _parse_record(self, body: bytes) -> Optional[dict]:
        """Parse a historical price data record.
        Fields: RequestID(4) + DateTime(8) + Open(8) + High(8) + Low(8) + Close(8) +
                Volume(8) + NumTrades(4) + BidVolume(8) + AskVolume(8) + IsFinalRecord(1)
        """
        if len(body) < 56:
            return None

        try:
            offset = 0
            # Request ID
            offset += 4

            # DateTime (DTC datetime = unix timestamp as double)
            dt_val = struct.unpack_from("<d", body, offset)[0]
            offset += 8

            # OHLC as doubles
            open_p = struct.unpack_from("<d", body, offset)[0]; offset += 8
            high_p = struct.unpack_from("<d", body, offset)[0]; offset += 8
            low_p = struct.unpack_from("<d", body, offset)[0]; offset += 8
            close_p = struct.unpack_from("<d", body, offset)[0]; offset += 8

            # Volume as double
            volume = struct.unpack_from("<d", body, offset)[0]; offset += 8

            # Num trades as uint32
            num_trades = struct.unpack_from("<I", body, offset)[0] if len(body) > offset + 3 else 0
            offset += 4

            # Bid/Ask volume as doubles
            bid_vol = struct.unpack_from("<d", body, offset)[0] if len(body) > offset + 7 else 0
            offset += 8
            ask_vol = struct.unpack_from("<d", body, offset)[0] if len(body) > offset + 7 else 0
            offset += 8

            # is_final
            is_final = struct.unpack_from("<B", body, offset)[0] if len(body) > offset else 0

            # Convert DTC datetime (seconds since unix epoch) to datetime
            if dt_val > 0:
                dt = datetime.fromtimestamp(dt_val, tz=timezone.utc)
            else:
                return None

            return {
                "datetime": dt,
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "volume": volume,
                "num_trades": num_trades,
                "bid_volume": bid_vol,
                "ask_volume": ask_vol,
                "is_final": is_final,
            }

        except Exception as e:
            logger.debug(f"Record parse error: {e}")
            return None

    def close(self):
        """Close all connections."""
        for sock in [self._sock, self._hist_sock]:
            if sock:
                try:
                    sock.close()
                except:
                    pass
        self._sock = None
        self._hist_sock = None


def fetch_sierra_data(
    host: str,
    symbol: str,
    exchange: str = "CME",
    intervals: list = None,
    start_date: datetime = None,
    port: int = 11099,
    hist_port: int = 11098,
) -> dict:
    """High-level function to fetch all timeframes for a symbol.
    Returns dict of {interval_name: DataFrame}.
    """
    if intervals is None:
        intervals = [
            ("1m", INTERVAL_1_MINUTE),
            ("5m", INTERVAL_5_MINUTE),
            ("15m", INTERVAL_15_MINUTE),
            ("1h", INTERVAL_60_MINUTE),
            ("1d", INTERVAL_DAILY),
        ]

    client = DTCClient(host, port, hist_port)

    # Try main port first for logon
    if not client.connect():
        logger.error("Main logon failed")
        client.close()
        return {}

    # Try historical port
    has_hist = client.connect_historical()
    if not has_hist:
        logger.info("No separate historical port — using main connection")

    results = {}
    for name, interval_sec in intervals:
        logger.info(f"Fetching {symbol} {name}...")
        try:
            df = client.fetch_historical(
                symbol=symbol,
                exchange=exchange,
                interval_seconds=interval_sec,
                start_date=start_date,
            )
            if not df.empty:
                results[name] = df
                logger.info(f"  {name}: {len(df)} bars")
            else:
                logger.warning(f"  {name}: no data")
        except Exception as e:
            logger.warning(f"  {name}: failed — {e}")

    client.close()
    return results
