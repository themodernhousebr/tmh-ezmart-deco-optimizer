#!/usr/bin/env python3
"""Checkout entry point. Standalone downloads use ezmart_deco_optimizer/x50_tpap.py."""
from pathlib import Path
import runpy


if __name__ == "__main__":
    source = Path(__file__).resolve().parents[1] / "ezmart_deco_optimizer/x50_tpap.py"
    if not source.is_file():
        raise SystemExit("Baixe ezmart_deco_optimizer/x50_tpap.py para executar o diagnóstico independente.")
    runpy.run_path(str(source), run_name="__main__")
