#!/usr/bin/env python3
"""
life-agents-compress — Compress chat history into a dense summary.

Takes a history.md file and compresses older messages while preserving
recent ones. Uses a simple heuristic (no AI call needed — Claude does
the intelligent compression when it writes the summary).

This script handles the mechanical part: reading the file, splitting
by message boundaries, and outputting the structure for Claude to
summarize.
"""

import json
import sys
import os
import re
from datetime import datetime

def parse_history(content):
    """Parse history.md into individual entries."""
    entries = []
    current_entry = None
    
    for line in content.split("\n"):
        # Match timestamp headers like: ## [2026-05-19T16:45:00Z] User
        timestamp_match = re.match(r'^## \[(\d{4}-\d{2}-\d{2}T[\d:]+Z?)\]\s*(.*)', line)
        
        if timestamp_match:
            if current_entry:
                entries.append(current_entry)
            current_entry = {
                "timestamp": timestamp_match.group(1),
                "speaker": timestamp_match.group(2).strip(),
                "content": "",
            }
        elif current_entry:
            current_entry["content"] += line + "\n"
    
    if current_entry:
        entries.append(current_entry)
    
    return entries

def should_compress(entries, keep_recent=50):
    """Determine if compression is needed."""
    return len(entries) > keep_recent

def split_for_compression(entries, keep_recent=50):
    """Split entries into 'to compress' and 'to keep'."""
    if len(entries) <= keep_recent:
        return [], entries
    
    split_point = len(entries) - keep_recent
    return entries[:split_point], entries[split_point:]

def extract_decisions(entries):
    """Extract key decisions from entries for the decisions log."""
    decisions = []
    
    decision_keywords = [
        "decided", "decision", "chose", "selected", "went with",
        "approved", "rejected", "will use", "agreed", "confirmed",
        "decidimos", "elegimos", "aprobado", "rechazado", "confirmado",
    ]
    
    for entry in entries:
        content_lower = entry["content"].lower()
        if any(kw in content_lower for kw in decision_keywords):
            decisions.append({
                "timestamp": entry["timestamp"],
                "speaker": entry["speaker"],
                "content": entry["content"].strip()[:500],
            })
    
    return decisions

def format_for_summary(entries):
    """Format entries as a prompt-ready block for Claude to summarize."""
    output = "# Messages to Summarize\n\n"
    output += f"Total messages: {len(entries)}\n"
    
    if entries:
        output += f"Time range: {entries[0]['timestamp']} to {entries[-1]['timestamp']}\n\n"
    
    for entry in entries:
        output += f"**[{entry['timestamp']}] {entry['speaker']}:**\n"
        # Truncate very long entries
        content = entry["content"].strip()
        if len(content) > 1000:
            content = content[:1000] + "\n[... truncated ...]"
        output += content + "\n\n"
    
    return output

def format_kept_entries(entries):
    """Format the entries we're keeping as-is."""
    output = ""
    for entry in entries:
        output += f"## [{entry['timestamp']}] {entry['speaker']}\n"
        output += entry["content"]
        if not entry["content"].endswith("\n"):
            output += "\n"
        output += "\n"
    return output

def main():
    if len(sys.argv) < 2:
        print("Usage: compress_context.py <history.md> [--keep-recent N]", file=sys.stderr)
        sys.exit(1)
    
    history_path = sys.argv[1]
    keep_recent = 50
    
    if "--keep-recent" in sys.argv:
        idx = sys.argv.index("--keep-recent")
        keep_recent = int(sys.argv[idx + 1])
    
    with open(history_path, "r") as f:
        content = f.read()
    
    entries = parse_history(content)
    
    if not should_compress(entries, keep_recent):
        print(json.dumps({
            "needs_compression": False,
            "total_entries": len(entries),
            "message": "History is within limits, no compression needed."
        }))
        return
    
    to_compress, to_keep = split_for_compression(entries, keep_recent)
    decisions = extract_decisions(to_compress)
    
    output = {
        "needs_compression": True,
        "total_entries": len(entries),
        "compressing": len(to_compress),
        "keeping": len(to_keep),
        "summary_prompt": format_for_summary(to_compress),
        "kept_entries": format_kept_entries(to_keep),
        "extracted_decisions": decisions,
        "instructions": (
            "Claude should summarize the 'summary_prompt' content into a dense "
            "~500 token summary preserving: key decisions, code changes and rationale, "
            "open questions, action items. Then prepend it to 'kept_entries' as the "
            "new history.md, with a '# Summary of earlier sessions' header."
        ),
    }
    
    print(json.dumps(output, indent=2))

if __name__ == "__main__":
    main()
