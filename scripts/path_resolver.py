#!/usr/bin/env python3
"""Resolve paths using os.path.realpath() for macOS compatibility."""

import os
import sys

def main():
    if len(sys.argv) < 2:
        print("", file=sys.stderr)
        sys.exit(1)
    
    path = sys.argv[1]
    # Use os.path.realpath() for canonical macOS path resolution
    # This resolves symlinks and returns absolute path
    resolved = os.path.realpath(path)
    print(resolved)

if __name__ == "__main__":
    main()