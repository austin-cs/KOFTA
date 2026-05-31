"""Render LaTeX table bodies for the SHS section from real campaign artifacts.

Each function returns a list of "\\ "-terminated LaTeX rows that replace the
[\\;] placeholder rows in Sections/8.SemanticHintSynthesis_ENG.tex. A cell with
no run data is emitted as the original [\\;] placeholder so a partial campaign
still produces a compilable, honestly-incomplete table.
"""

from __future__ import annotations

from pathlib import Path

from shs import loaders, stats

PLACEHOLDER = r"[\;]"

# Config column keys -> directory names used under the campaign root.
CONFIGS = {
    "weifuzz": "weifuzz",
    "llmonly": "llmonly",
    "kofta": "kofta",
    "kshs": "kshs",
    "kshsng": "kshsng",
}

COV_TARGETS = ["objdump", "objcopy", "readelf", "xmllint", "tiffcp",
               "tiffcrop", "mutool", "img2sixel", "giftool"]
UNDOC_TARGETS = ["objdump", "objcopy", "readelf", "xmllint", "tiffcp", "tiffcrop"]
COST_TARGETS = ["objdump", "objcopy", "readelf"]
MAGIC_GATES = ["string, 2\\,B", "string, 4\\,B", "string, 8\\,B", "magic number"]


def _med_or_ph(vals: list[int]) -> str:
    return str(int(round(stats.median(vals)))) if vals else PLACEHOLDER


def _fmt_p(p: float) -> str:
    if p < 0.001:
        return r"$<$0.001"
    if p < 0.01:
        return r"$<$0.01"
    return f"{p:.3f}"


def _fmt_delta(treatment: list[int], baseline: list[int]) -> str:
    if not treatment or not baseline:
        return PLACEHOLDER
    mt, mb = stats.median(treatment), stats.median(baseline)
    if mb == 0:
        return PLACEHOLDER
    d = (mt - mb) / mb * 100.0
    return f"+{d:.1f}" if d >= 0 else f"{d:.1f}"


# ---- Table A: edge coverage (tab:cov) -------------------------------------

def table_cov(root: Path, metric: str, targets=COV_TARGETS) -> tuple[list[str], dict]:
    rows: list[str] = []
    facts = {"metric": metric, "R": None, "kshs_wins": 0, "n_targets": 0,
             "sig_targets": 0, "large_effect": 0, "deltas": [],
             "delta_vs_kofta": []}
    for tgt in targets:
        wf = loaders.discover_cov(root, tgt, CONFIGS["weifuzz"], metric)
        lo = loaders.discover_cov(root, tgt, CONFIGS["llmonly"], metric)
        ko = loaders.discover_cov(root, tgt, CONFIGS["kofta"], metric)
        ks = loaders.discover_cov(root, tgt, CONFIGS["kshs"], metric)

        # Delta% over strongest non-KOFTA baseline (weifuzz / llmonly).
        baselines = [v for v in (wf, lo) if v]
        best_base = max(baselines, key=stats.median) if baselines else []
        delta = _fmt_delta(ks, best_base)

        if ks and ko:
            cmp = stats.compare(ks, ko)
            a12 = f"{cmp['a12']:.2f}"
            pval_s = _fmt_p(cmp["p"])
            facts["n_targets"] += 1
            facts["R"] = facts["R"] or min(len(ks), len(ko))
            if cmp["median_treatment"] >= max(
                [stats.median(x) for x in (wf, lo, ko, ks) if x]):
                facts["kshs_wins"] += 1
            if cmp["p"] < 0.01:
                facts["sig_targets"] += 1
            if cmp["a12"] >= 0.71:
                facts["large_effect"] += 1
            if ko and stats.median(ko):
                facts["delta_vs_kofta"].append(
                    (cmp["median_treatment"] - stats.median(ko))
                    / stats.median(ko) * 100.0)
            a12_s = a12
        else:
            a12_s = PLACEHOLDER
            pval_s = PLACEHOLDER
        if delta != PLACEHOLDER:
            facts["deltas"].append(float(delta.rstrip("%")))

        delta_cell = PLACEHOLDER if delta == PLACEHOLDER else f"{delta}\\%"
        rows.append(
            f"{tgt:<9} & {_med_or_ph(wf)} & {_med_or_ph(lo)} & "
            f"{_med_or_ph(ko)} & {_med_or_ph(ks)} & "
            f"{delta_cell} & {a12_s} & {pval_s} \\\\")
    rows.append(r"\midrule")
    rows.append(_cov_mean_row(root, metric, targets))
    return rows, facts


def _cov_mean_row(root: Path, metric: str, targets) -> str:
    def col(cfg):
        meds = [stats.median(v) for t in targets
                if (v := loaders.discover_cov(root, t, CONFIGS[cfg], metric))]
        return str(int(round(sum(meds) / len(meds)))) if meds else PLACEHOLDER
    return (f"\\textbf{{Mean}} & {col('weifuzz')} & {col('llmonly')} & "
            f"{col('kofta')} & {col('kshs')} & [+\\,\\%] & [0.xx] & --- \\\\")


# ---- Table B: magic-value penetration (tab:magic) -------------------------

def table_magic(root: Path) -> tuple[list[str], dict]:
    planted = loaders.magic_planted(root)
    solved = {c: loaders.discover_magic(root, CONFIGS[c])
              for c in ("llmonly", "kofta", "kshs", "kshsng")}
    rows: list[str] = []
    totals = {c: 0 for c in solved}
    total_planted = 0
    facts = {"planted": planted, "by_config": {}}
    any_data = any(solved[c] for c in solved)
    for gate in MAGIC_GATES:
        npl = planted.get(gate)
        npl_s = str(npl) if npl is not None else PLACEHOLDER
        if npl:
            total_planted += npl
        cells = []
        for c in ("llmonly", "kofta", "kshs", "kshsng"):
            vals = solved[c].get(gate, [])
            if vals:
                m = int(round(stats.median(vals)))
                totals[c] += m
                cells.append(str(m))
            else:
                cells.append(PLACEHOLDER)
        rows.append(f"{gate}  & {npl_s} & " + " & ".join(cells) + r" \\")
    rows.append(r"\midrule")
    if any_data:
        tcells = " & ".join(str(totals[c]) for c in
                            ("llmonly", "kofta", "kshs", "kshsng"))
        tpl = str(total_planted) if total_planted else PLACEHOLDER
        rows.append(f"\\textbf{{Total}} & {tpl} & {tcells} \\\\")
        facts["by_config"] = {c: totals[c] for c in totals}
        facts["total_planted"] = total_planted
    else:
        rows.append(r"\textbf{Total} & [\;] & [\;] & [\;] & [\;] & [\;] \\")
    return rows, facts


# ---- Table C: undocumented optargs (tab:undoc) ----------------------------

def table_undoc(root: Path, targets=UNDOC_TARGETS) -> tuple[list[str], dict]:
    documented = loaders.documented_counts(root)
    rows: list[str] = []
    totals = {c: 0 for c in ("llmonly", "kofta", "kshs")}
    tot_doc = 0
    have = {c: False for c in totals}
    for tgt in targets:
        doc = documented.get(tgt)
        doc_s = str(doc) if doc is not None else PLACEHOLDER
        if doc is not None:
            tot_doc += doc
        cells = []
        for c in ("llmonly", "kofta", "kshs"):
            vals = loaders.discover_undoc(root, tgt, CONFIGS[c])
            if vals:
                m = int(round(stats.median(vals)))
                totals[c] += m
                have[c] = True
                cells.append(str(m))
            else:
                cells.append(PLACEHOLDER)
        rows.append(f"{tgt:<8} & {doc_s} & " + " & ".join(cells) + r" \\")
    rows.append(r"\midrule")
    tcells = " & ".join(str(totals[c]) if have[c] else PLACEHOLDER
                       for c in ("llmonly", "kofta", "kshs"))
    tdoc = str(tot_doc) if tot_doc else PLACEHOLDER
    rows.append(f"\\textbf{{Total}} & {tdoc} & {tcells} \\\\")
    return rows, {"documented": documented}


# ---- Table D: SHS cost (tab:cost) -----------------------------------------

def _cost_row(label: str, recs: list[dict]) -> tuple[str, dict] | tuple[str, None]:
    if not recs:
        return (f"{label} & {PLACEHOLDER} & {PLACEHOLDER} & {PLACEHOLDER} & "
                f"{PLACEHOLDER} & [\\,\\%] \\\\", None)
    import statistics as st
    def mean(key, default=0.0):
        return st.fmean([float(r.get(key, default)) for r in recs])
    calls = mean("llm_calls")
    cache_hits = mean("cache_hits")
    wall = mean("campaign_wall_s", 1.0) or 1.0
    shs_wall = mean("shs_wall_s")
    tokens = mean("tokens")
    lat = [x for r in recs for x in r.get("latency_s", [])]
    lat_mean = st.fmean(lat) if lat else 0.0
    calls_per_h = calls / (wall / 3600.0)
    total_q = calls + cache_hits
    hit_pct = (cache_hits / total_q * 100.0) if total_q else 0.0
    tokens_24h = tokens * (24 * 3600.0 / wall)
    wall_pct = shs_wall / wall * 100.0
    agg = {"calls_per_h": calls_per_h, "hit_pct": hit_pct,
           "latency_s": lat_mean, "tokens_24h": tokens_24h, "wall_pct": wall_pct}
    row = (f"{label} & {calls_per_h:.1f} & {hit_pct:.0f} & {lat_mean:.1f} & "
           f"{tokens_24h:.0f} & [{wall_pct:.1f}\\%] \\\\")
    return row, agg


def table_cost(root: Path, targets=COST_TARGETS) -> tuple[list[str], dict]:
    rows: list[str] = []
    aggs: list[dict] = []
    for tgt in targets:
        recs = loaders.discover_cost(root, tgt)
        row, agg = _cost_row(tgt, recs)
        rows.append(row)
        if agg:
            aggs.append(agg)
    rows.append(r"\multicolumn{6}{c}{$\cdots$} \\")
    rows.append(r"\midrule")
    if aggs:
        import statistics as st
        m = {k: st.fmean([a[k] for a in aggs]) for k in aggs[0]}
        rows.append(f"\\textbf{{Mean}} & {m['calls_per_h']:.1f} & "
                    f"{m['hit_pct']:.0f} & {m['latency_s']:.1f} & "
                    f"{m['tokens_24h']:.0f} & [{m['wall_pct']:.1f}\\%] \\\\")
    else:
        rows.append(r"\textbf{Mean} & [\;] & [\;] & [\;] & [\;] & [\,\%] \\")
    return rows, {"per_target": aggs}
