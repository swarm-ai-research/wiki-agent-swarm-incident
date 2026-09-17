"""P3 boundary sweep (analysis/rogue-agent-explosion-tests.md): k = 0.5, finer d0 and premium grid."""
import warnings
import numpy as np
from rogue_selection_sim import run, classify

warnings.simplefilter("ignore")
d0s = (0.02, 0.04, 0.06, 0.08, 0.1, 0.15, 0.2)
print("class (aF/cF) per cell, k=0.5, 12 seeds, median; X = extinct")
print("prem  | " + "  ".join(f"d0={d:<11}" for d in d0s))
for premium in (1.1, 1.25, 1.5, 2.0, 3.0):
    cells = []
    for d0 in d0s:
        runs = [np.array(run(d0, 0.5, premium, s), float) for s in range(12)]
        aF, cF, _, pF = np.nanmedian(np.stack(runs), axis=0)[-1]
        cls = classify(aF, cF, pF)
        cells.append("X" + " " * 13 if cls == "extinct" else f"{cls[:6]:<6} {aF:.2f}/{cF:.2f}")
    print(f"{premium:<5} | " + "  ".join(cells))
