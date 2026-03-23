"""Incremental .scid fetcher — pulls only new bytes from Windows PC."""

import logging
import re
from pathlib import Path
from datetime import datetime

import httpx
import numpy as np

from . import config

logger = logging.getLogger("trading-console.fetcher")

SCID_HEADER = 56
RECORD_SIZE = config.SCID_RECORD_SIZE  # 40 bytes


def _base_url() -> str:
    return f"http://{config.WINDOWS_IP}:{config.WINDOWS_PORT}"


async def detect_contract() -> str | None:
    """Auto-detect the front-month 6E contract from the HTTP directory listing."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{_base_url()}/")
            resp.raise_for_status()
            # Parse HTML directory listing for 6E*.scid files
            # Match standard Sierra format: 6E + single letter code + single digit year
            # e.g., 6EM6.CME.scid (correct), not 6EM26.CME.scid (wrong format)
            all_matches = re.findall(r'(6E[HMUZ]\d{1,2}\.CME\.scid)', resp.text)
            # Deduplicate
            matches = list(dict.fromkeys(all_matches))
            if not matches:
                logger.warning("No 6E .scid files found in directory listing")
                return None

            # Prefer short names (6EM6) over long ones (6EM26) — standard CME format
            short = [m for m in matches if len(m.split('.')[0]) == 4]  # "6EM6" = 4 chars
            if short:
                matches = short

            if len(matches) == 1:
                logger.info(f"Auto-detected contract: {matches[0]}")
                return matches[0]

            # Multiple contracts — pick by quarterly cycle closest to current date
            month_map = {"H": 3, "M": 6, "U": 9, "Z": 12}
            now = datetime.now()
            best = None
            best_dist = 999
            for m in matches:
                code = m[2]  # e.g., 'M' from '6EM6'
                if code in month_map:
                    exp_month = month_map[code]
                    dist = (exp_month - now.month) % 12
                    if dist == 0:
                        dist = 12
                    if dist < best_dist:
                        best_dist = dist
                        best = m
            logger.info(f"Auto-detected front-month from {len(matches)} contracts: {best}")
            return best
    except Exception as e:
        logger.error(f"Failed to detect contract: {e}")
        return None


async def check_connection() -> bool:
    """Check if Windows PC HTTP server is reachable."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{_base_url()}/")
            return resp.status_code == 200
    except Exception:
        return False


async def fetch_full(contract: str, save_path: str) -> tuple[int, bool]:
    """Full download of .scid file. Returns (file_size, success)."""
    url = f"{_base_url()}/{contract}"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.content
            # Validate: at least header + 1 record
            if len(data) < SCID_HEADER + RECORD_SIZE:
                logger.error(f"File too small: {len(data)} bytes")
                return 0, False
            # Truncate to complete records
            data_len = len(data) - SCID_HEADER
            complete_records = (data_len // RECORD_SIZE) * RECORD_SIZE
            truncated = data[:SCID_HEADER + complete_records]
            Path(save_path).write_bytes(truncated)
            logger.info(f"Full download: {len(truncated):,} bytes ({complete_records // RECORD_SIZE:,} records)")
            return len(truncated), True
    except Exception as e:
        logger.error(f"Full download failed: {e}")
        return 0, False


async def fetch_incremental(contract: str, save_path: str, last_offset: int) -> tuple[int, int, bool]:
    """Incremental fetch using HTTP Range header.
    Returns (new_records_count, new_offset, success).
    """
    url = f"{_base_url()}/{contract}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            headers = {"Range": f"bytes={last_offset}-"}
            resp = await client.get(url, headers=headers)
            if resp.status_code == 416:
                # Range not satisfiable — file hasn't grown
                return 0, last_offset, True
            if resp.status_code not in (200, 206):
                logger.error(f"Unexpected status: {resp.status_code}")
                return 0, last_offset, False

            new_data = resp.content

            if resp.status_code == 200:
                # Server doesn't support Range — got full file
                # Truncate and save
                data_len = len(new_data) - SCID_HEADER
                complete = (data_len // RECORD_SIZE) * RECORD_SIZE
                Path(save_path).write_bytes(new_data[:SCID_HEADER + complete])
                new_offset = SCID_HEADER + complete
                n_records = complete // RECORD_SIZE
                logger.info(f"Full response (no Range support): {n_records:,} records")
                return n_records, new_offset, True

            # Partial response — append new bytes
            # Align to record boundary
            complete_bytes = (len(new_data) // RECORD_SIZE) * RECORD_SIZE
            if complete_bytes == 0:
                return 0, last_offset, True

            # Append to existing file
            with open(save_path, "ab") as f:
                f.write(new_data[:complete_bytes])

            new_offset = last_offset + complete_bytes
            n_records = complete_bytes // RECORD_SIZE
            logger.info(f"Incremental: +{n_records:,} records ({complete_bytes:,} bytes)")
            return n_records, new_offset, True

    except Exception as e:
        logger.error(f"Incremental fetch failed: {e}")
        return 0, last_offset, False


def get_file_size(path: str) -> int:
    """Get current .scid file size, or 0 if not found."""
    p = Path(path)
    return p.stat().st_size if p.exists() else 0
