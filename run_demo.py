"""run_demo.py — Convenience launcher for demo mode."""
import os
os.environ.setdefault("RUN_MODE", "demo")

from main import main
if __name__ == "__main__":
    main()
