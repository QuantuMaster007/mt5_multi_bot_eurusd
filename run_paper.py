"""run_paper.py — Convenience launcher for paper mode."""
import os
os.environ.setdefault("RUN_MODE", "paper")

from main import main
if __name__ == "__main__":
    main()
