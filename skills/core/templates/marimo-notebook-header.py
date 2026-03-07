# Mycelium — Standard Marimo Notebook Header
# Copy this header to the top of new marimo notebooks.
# It sets up common imports, paths, and styling.

import marimo as mo

# --- Common imports ---
import numpy as np
import pandas as pd
from pathlib import Path
import matplotlib.pyplot as plt
import seaborn as sns

# --- Project paths ---
# Adjust ANALYSIS_NAME to match your analysis directory name
ANALYSIS_NAME = "my-analysis"
PROJECT_ROOT = Path(__file__).resolve().parents[3]  # Adjust depth as needed
ANALYSIS_DIR = PROJECT_ROOT / "analysis" / ANALYSIS_NAME
DATA_DIR = PROJECT_ROOT / "data"
ALGORITHMS_DIR = PROJECT_ROOT / "algorithms"
OUTPUT_DIR = ANALYSIS_DIR / "outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# --- Plot styling ---
sns.set_theme(style="whitegrid", context="notebook")
plt.rcParams.update({
    "figure.figsize": (8, 5),
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "font.size": 11,
})

# --- Reproducibility ---
RANDOM_SEED = 42
np.random.seed(RANDOM_SEED)
