#!/usr/bin/env python3
"""
clear_cache.py

Clear Python bytecode cache to fix stale module issues.
"""

import os
import shutil
import sys

def clear_pycache(directory="."):
    """Recursively remove all __pycache__ directories."""
    removed = 0
    for root, dirs, files in os.walk(directory):
        if '__pycache__' in dirs:
            pycache_path = os.path.join(root, '__pycache__')
            try:
                shutil.rmtree(pycache_path)
                print(f"[CLEAN] Removed {pycache_path}")
                removed += 1
            except Exception as e:
                print(f"[ERROR] Failed to remove {pycache_path}: {e}")
    
    return removed

if __name__ == "__main__":
    print("[CLEAN] Clearing Python cache...")
    count = clear_pycache(".")
    print(f"[CLEAN] Removed {count} __pycache__ directories")
    print("[CLEAN] Done! Cache cleared.")
