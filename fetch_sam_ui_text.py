#!/usr/bin/env python3
"""Quick script to fetch and display UI text from a SAM.gov link."""

import sys
from main import fetch_ui_link_data

url = sys.argv[1] if len(sys.argv) > 1 else "https://sam.gov/workspace/contract/opp/9760d8cc12a74d5ba976add385aa1b74/view"

print(f"Fetching UI text from: {url}\n")
data = fetch_ui_link_data(url, use_cache=False)

if data:
    print(f"Title: {data.get('title', 'N/A')}")
    print(f"Text length: {len(data.get('text_content', ''))} chars\n")
    print("=" * 80)
    print(data.get("text_content", "No text found")[:5000])
    print("=" * 80)
    if len(data.get("text_content", "")) > 5000:
        print(f"\n... ({len(data['text_content'])} total chars, showing first 5000)")
else:
    print("Failed to fetch UI text.")
