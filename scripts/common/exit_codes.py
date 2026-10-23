"""Shared exit-code constants for every tool (AUDIT 2026-10 F08).

0 = success; 1 = finished, but found something a human must look at;
2 = usage, input or config error, nothing ran; 3 = runtime failure after work started.
scripts/mode_b.py keeps its own documented 0/1/2/3 meanings and does not use these.
"""

EXIT_OK = 0
EXIT_ATTENTION = 1
EXIT_USAGE = 2
EXIT_RUNTIME = 3
