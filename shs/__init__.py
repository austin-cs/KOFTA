"""SHS experiment harness for KOFTA.

Modules:
  stats     -- pure statistics (median, Mann-Whitney U, Vargha-Delaney A12).
  loaders   -- read AFL plot_data / fuzzer_stats, kofta-opts CSV, sidecar JSON.
  tables    -- emit LaTeX rows for the four SHS tables (cov/magic/undoc/cost).
  service   -- LLM-grounded Semantic Hint Synthesis (prompt, cache, budget).

Nothing here fabricates results: every number is computed from real run
artifacts produced by a KOFTA campaign. With no artifacts, the emitters print
the original [\\;] placeholders unchanged.
"""
