import io
import logging
from typing import Dict, List, Optional

import fitz  # PyMuPDF
import requests

logger = logging.getLogger("trading-monitor.sentiment")

# Reports to fetch — add more URLs here as needed
REPORT_SOURCES = [
    {
        "name": "Scotia G10 FX Daily",
        "url": "https://scotiaequityresearch.com/FX/G10_FX_Daily.pdf",
        "relevant_keywords": {
            "6E": ["EUR", "euro", "EURUSD", "ECB"],
            "DXY": ["USD", "dollar", "DXY", "Fed", "FOMC", "greenback"],
        },
    },
]


class ReportFeed:
    """Fetch and parse PDF research reports for FX sentiment."""

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (trading-monitor/1.0)"
        })

    def fetch_reports(self, instrument_key: str) -> List[Dict]:
        """Fetch all configured PDF reports and extract text relevant to the instrument.
        Returns list of dicts with 'source', 'text', 'title', 'date'.
        """
        results = []

        for report in REPORT_SOURCES:
            try:
                text = self._fetch_and_extract(report["url"])
                if not text:
                    continue

                # Filter to relevant sections
                keywords = report["relevant_keywords"].get(instrument_key, [])
                relevant_text = self._extract_relevant_sections(text, keywords)

                if relevant_text:
                    # Extract date from first line if possible
                    lines = text.strip().split("\n")
                    title = report["name"]
                    date_str = ""
                    for line in lines[:10]:
                        line = line.strip()
                        if any(month in line for month in [
                            "January", "February", "March", "April", "May", "June",
                            "July", "August", "September", "October", "November", "December",
                        ]):
                            date_str = line
                            break

                    results.append({
                        "source": report["name"],
                        "title": f"{title} ({date_str})" if date_str else title,
                        "text": relevant_text[:2000],  # Cap at 2000 chars for LLM
                        "full_text_length": len(text),
                    })

            except Exception as e:
                logger.warning(f"Report fetch failed for {report['name']}: {e}")

        return results

    def _fetch_and_extract(self, url: str) -> Optional[str]:
        """Download PDF and extract all text."""
        try:
            resp = self._session.get(url, timeout=20)
            resp.raise_for_status()

            doc = fitz.open(stream=resp.content, filetype="pdf")
            text_parts = []
            for page in doc:
                text_parts.append(page.get_text())
            doc.close()

            full_text = "\n".join(text_parts)
            logger.info(f"Extracted {len(full_text)} chars from {url}")
            return full_text

        except Exception as e:
            logger.warning(f"PDF extraction failed for {url}: {e}")
            return None

    def _extract_relevant_sections(
        self, text: str, keywords: List[str], context_chars: int = 300
    ) -> str:
        """Extract paragraphs/sections containing instrument keywords.
        Returns concatenated relevant sections.
        """
        if not keywords:
            # No keywords — return the executive summary (first ~1000 chars)
            return text[:1000]

        text_lower = text.lower()
        sections = []
        seen_positions = set()

        for keyword in keywords:
            kw_lower = keyword.lower()
            start = 0
            while True:
                pos = text_lower.find(kw_lower, start)
                if pos == -1:
                    break

                # Check if we already captured this area
                bucket = pos // context_chars
                if bucket not in seen_positions:
                    seen_positions.add(bucket)

                    # Extract surrounding context
                    section_start = max(0, pos - context_chars)
                    section_end = min(len(text), pos + len(keyword) + context_chars)

                    # Try to align to sentence boundaries
                    while section_start > 0 and text[section_start] not in ".!?\n":
                        section_start -= 1
                    section_start = min(section_start + 1, pos)

                    while section_end < len(text) and text[section_end] not in ".!?\n":
                        section_end += 1
                    section_end = min(section_end + 1, len(text))

                    sections.append(text[section_start:section_end].strip())

                start = pos + 1

        if not sections:
            # Fallback: return opening summary
            return text[:1000]

        return "\n---\n".join(sections[:10])  # Cap at 10 sections
