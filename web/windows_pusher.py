"""Windows-side pusher — runs on Windows, uploads .scid tail to tmpfiles.org.
Mac-side Trading Console downloads from there.

Usage on Windows:
  cd C:\SierraChart\Data
  python windows_pusher.py

Uploads last 2MB of the .scid file every 5 minutes.
Mac fetcher pulls from the returned URL.
"""

import os
import sys
import time
import json
import struct
import subprocess
from datetime import datetime
from pathlib import Path

# Config
SCID_DIR = r"C:\SierraChart\Data"
UPLOAD_URL = "https://tmpfiles.org/api/v1/upload"
SIGNAL_FILE = os.path.join(SCID_DIR, "_relay_url.json")
INTERVAL = 300  # 5 minutes
TAIL_BYTES = 2 * 1024 * 1024  # 2MB tail (enough for ~50K ticks = several hours)
HEADER_SIZE = 56
RECORD_SIZE = 40


def find_contract():
    """Find the active 6E contract .scid file."""
    scid_files = list(Path(SCID_DIR).glob("6E*.CME.scid"))
    if not scid_files:
        print("ERROR: No 6E .scid files found")
        return None
    # Prefer short names (6EM6 not 6EM26), then by size
    short = [f for f in scid_files if len(f.stem.split('.')[0]) == 4]
    candidates = short if short else scid_files
    candidates.sort(key=lambda f: f.stat().st_size, reverse=True)
    return str(candidates[0])


def extract_tail(filepath):
    """Extract the tail of the .scid file (header + last N bytes, aligned to records)."""
    file_size = os.path.getsize(filepath)
    if file_size <= HEADER_SIZE:
        return None

    with open(filepath, 'rb') as f:
        # Read header
        header = f.read(HEADER_SIZE)

        # Read tail
        data_size = file_size - HEADER_SIZE
        tail_start = max(0, data_size - TAIL_BYTES)
        # Align to record boundary
        tail_start = (tail_start // RECORD_SIZE) * RECORD_SIZE

        f.seek(HEADER_SIZE + tail_start)
        tail_data = f.read()

        # Ensure complete records
        complete = (len(tail_data) // RECORD_SIZE) * RECORD_SIZE
        tail_data = tail_data[:complete]

    # Write header + tail to temp file
    tmp_path = os.path.join(SCID_DIR, "_tail.scid")
    with open(tmp_path, 'wb') as f:
        f.write(header)
        f.write(tail_data)

    n_records = complete // RECORD_SIZE
    print(f"  Extracted {n_records:,} records ({len(header) + complete:,} bytes)")
    return tmp_path


def upload(filepath):
    """Upload file to tmpfiles.org, return download URL."""
    try:
        result = subprocess.run(
            ["curl", "-s", "-F", f"file=@{filepath}", UPLOAD_URL],
            capture_output=True, text=True, timeout=60
        )
        data = json.loads(result.stdout)
        if data.get("status") == "success":
            url = data["data"]["url"]
            # Convert to direct download URL
            # http://tmpfiles.org/12345/file.scid -> https://tmpfiles.org/dl/12345/file.scid
            dl_url = url.replace("http://tmpfiles.org/", "https://tmpfiles.org/dl/")
            return dl_url
    except Exception as e:
        print(f"  Upload failed: {e}")
    return None


def write_signal(url, contract):
    """Write the relay URL to a signal file (Mac can also read this if direct access works)."""
    with open(SIGNAL_FILE, 'w') as f:
        json.dump({
            "url": url,
            "contract": contract,
            "timestamp": datetime.utcnow().isoformat(),
        }, f)


def main():
    print("=" * 60)
    print("  6E Trading Console — Windows Pusher")
    print("  Uploads .scid tail to cloud relay every 5 min")
    print("=" * 60)

    contract_path = find_contract()
    if not contract_path:
        return

    contract_name = Path(contract_path).name
    print(f"  Contract: {contract_name}")
    print(f"  Interval: {INTERVAL}s")
    print()

    while True:
        now = datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] Pushing...")

        # Extract tail
        tmp = extract_tail(contract_path)
        if not tmp:
            print("  ERROR: Could not extract tail")
            time.sleep(INTERVAL)
            continue

        # Upload
        url = upload(tmp)
        if url:
            print(f"  Uploaded: {url}")
            write_signal(url, contract_name)
        else:
            print("  Upload failed — will retry next cycle")

        # Clean up
        try:
            os.remove(tmp)
        except:
            pass

        # Wait for next interval (align to 5-min clock)
        now_ts = time.time()
        next_ts = ((now_ts // INTERVAL) + 1) * INTERVAL
        wait = max(1, next_ts - now_ts)
        print(f"  Next push in {int(wait)}s")
        time.sleep(wait)


if __name__ == "__main__":
    main()
