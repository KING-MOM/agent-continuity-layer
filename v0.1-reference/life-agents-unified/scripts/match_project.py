#!/usr/bin/env python3
"""
life-agents-match — Match current working directory to a known project on the VM.

Compares:
1. Directory name against project names (fuzzy)
2. CLAUDE.md content hash
3. Top-level file listing hash
4. Time proximity (recently active projects score higher)

Outputs JSON with match result and confidence score.
"""

import json
import os
import sys
import hashlib
from datetime import datetime, timezone
from difflib import SequenceMatcher

def get_current_context():
    """Gather context from the current working directory."""
    cwd = os.getcwd()
    dir_name = os.path.basename(cwd)
    
    # Read local CLAUDE.md if it exists
    claude_md = ""
    claude_md_path = os.path.join(cwd, "CLAUDE.md")
    if os.path.exists(claude_md_path):
        with open(claude_md_path, "r") as f:
            claude_md = f.read()
    
    # Also check .claude/CLAUDE.md
    project_claude = os.path.join(cwd, ".claude", "CLAUDE.md")
    if os.path.exists(project_claude):
        with open(project_claude, "r") as f:
            claude_md += "\n" + f.read()
    
    # Get top-level file listing
    try:
        files = sorted([
            f for f in os.listdir(cwd) 
            if not f.startswith('.') and f not in ('node_modules', '__pycache__', 'venv', '.git')
        ])
    except Exception:
        files = []
    
    files_hash = hashlib.sha256("|".join(files).encode()).hexdigest()[:16]
    claude_hash = hashlib.sha256(claude_md.encode()).hexdigest()[:16] if claude_md else ""
    
    return {
        "dir_name": dir_name,
        "claude_md_hash": claude_hash,
        "claude_md_preview": claude_md[:500] if claude_md else "",
        "files_hash": files_hash,
        "files_list": files[:20],  # Top 20 files
        "full_path": cwd,
    }

def score_match(context, project):
    """Score how well the current context matches a known project."""
    scores = {}
    
    # 1. Name similarity (0-40 points)
    name_ratio = SequenceMatcher(
        None, 
        context["dir_name"].lower(), 
        project.get("name", "").lower()
    ).ratio()
    scores["name"] = name_ratio * 40
    
    # Also check against UUID slug
    uuid_slug = project.get("uuid", "").replace("proj-", "").split("-")[0] if project.get("uuid") else ""
    uuid_ratio = SequenceMatcher(None, context["dir_name"].lower(), uuid_slug).ratio()
    scores["name"] = max(scores["name"], uuid_ratio * 40)
    
    # 2. Files fingerprint match (0-30 points)
    if context["files_hash"] and project.get("files_fingerprint"):
        if context["files_hash"] == project["files_fingerprint"]:
            scores["files"] = 30
        else:
            scores["files"] = 0
    else:
        scores["files"] = 0  # Can't compare, neutral
    
    # 3. CLAUDE.md match (0-20 points)
    if context["claude_md_hash"] and project.get("claude_md_hash"):
        if context["claude_md_hash"] == project["claude_md_hash"]:
            scores["claude_md"] = 20
        else:
            scores["claude_md"] = 0
    else:
        scores["claude_md"] = 0
    
    # 4. Recency bonus (0-10 points)
    last_active = project.get("last_active", "")
    if last_active:
        try:
            last_dt = datetime.fromisoformat(last_active.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            hours_ago = (now - last_dt).total_seconds() / 3600
            if hours_ago < 1:
                scores["recency"] = 10
            elif hours_ago < 24:
                scores["recency"] = 7
            elif hours_ago < 72:
                scores["recency"] = 4
            elif hours_ago < 168:  # 1 week
                scores["recency"] = 2
            else:
                scores["recency"] = 0
        except Exception:
            scores["recency"] = 0
    else:
        scores["recency"] = 0
    
    total = sum(scores.values())
    return total, scores

def main():
    # Read registry from stdin
    try:
        registry = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(json.dumps({
            "match": None, 
            "confidence": 0, 
            "reason": "Could not parse project registry"
        }))
        return
    
    projects = registry.get("projects", [])
    if not projects:
        print(json.dumps({
            "match": None, 
            "confidence": 0, 
            "reason": "No projects registered yet"
        }))
        return
    
    context = get_current_context()
    
    # Score all projects
    results = []
    for project in projects:
        score, breakdown = score_match(context, project)
        results.append({
            "project": project,
            "score": score,
            "breakdown": breakdown,
        })
    
    # Sort by score
    results.sort(key=lambda x: x["score"], reverse=True)
    
    best = results[0]
    confidence = min(best["score"], 100)
    
    output = {
        "match": best["project"] if confidence >= 50 else None,
        "confidence": confidence,
        "breakdown": best["breakdown"],
        "current_context": {
            "dir_name": context["dir_name"],
            "files_count": len(context["files_list"]),
        },
        "alternatives": [
            {
                "name": r["project"].get("name", "Unknown"),
                "uuid": r["project"].get("uuid", ""),
                "score": r["score"],
                "last_active": r["project"].get("last_active", ""),
            }
            for r in results[:5]
        ]
    }
    
    print(json.dumps(output, indent=2))

if __name__ == "__main__":
    main()
