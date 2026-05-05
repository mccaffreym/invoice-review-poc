"""
run_eval.py — Run the invoice reviewer over a labelled eval set and score results.

Usage:
    python run_eval.py

To add prompt versions later, pass a different rules string and output-column prefix
into run_eval(); the structure is already version-aware via the `version` parameter.
"""

import pandas as pd
import json

from reviewer import review, RULES_TEXT

# ── Config ────────────────────────────────────────────────────────────────────

PROMPT_VERSION = "v3"

# For v2+: load from the previous results file so existing version columns carry
# through to the output. For v1, set this to the original xlsx + SHEET_NAME.
EVAL_PATH  = "/Users/markmccaffrey/Documents/invoice-poc/invoice_eval_set_v1.2_with_v2_results.xlsx"
SHEET_NAME = 0   # results files use the default first sheet; use a named sheet string for the original xlsx

OUTPUT_PATH = "/Users/markmccaffrey/Documents/invoice-poc/invoice_eval_set_v1.2_with_v3_results.xlsx"

ALL_RULES = [
    "block_billing",
    "vague_narrative",
    "admin_as_legal",
    "excessive_time",
    "intra_firm_comms",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_expected(raw: str) -> set:
    """Turn the expected_flags cell ('block_billing, vague_narrative' or 'none') into a set."""
    if pd.isna(raw) or str(raw).strip().lower() == "none":
        return set()
    return {r.strip() for r in str(raw).split(",") if r.strip()}


def score_metrics(df: pd.DataFrame, version: str) -> dict:
    """
    Compute per-rule precision/recall/F1 and overall exact-match accuracy.
    Returns a dict keyed by rule name, plus an 'overall' entry.
    """
    pred_col = f"{version}_predicted_flags"
    results = {}

    for rule in ALL_RULES:
        tp = fp = fn = 0
        for _, row in df.iterrows():
            expected = parse_expected(row["expected_flags"])
            raw_pred = str(row[pred_col])
            # Skip rows that errored out
            if raw_pred.startswith("ERROR"):
                continue
            predicted = parse_expected(raw_pred)
            if rule in expected and rule in predicted:
                tp += 1
            elif rule not in expected and rule in predicted:
                fp += 1
            elif rule in expected and rule not in predicted:
                fn += 1

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1        = (2 * precision * recall / (precision + recall)
                     if (precision + recall) > 0 else 0.0)
        results[rule] = {"tp": tp, "fp": fp, "fn": fn,
                         "precision": precision, "recall": recall, "f1": f1}

    # Overall exact-match accuracy (only over non-errored rows)
    match_col = f"{version}_match"
    valid = df[~df[pred_col].astype(str).str.startswith("ERROR")]
    accuracy = valid[match_col].sum() / len(valid) if len(valid) > 0 else 0.0
    results["overall"] = {"accuracy": accuracy, "n_valid": len(valid), "n_total": len(df)}

    # Guardrail metrics (v3+): verified quote rate and lines with unverified flags
    unver_col = f"{version}_unverified_count"
    if unver_col in df.columns:
        total_flags    = sum(len(parse_expected(r)) if not str(r).startswith("ERROR") else 0
                             for r in df[pred_col])
        total_unver    = df[unver_col].apply(lambda x: int(x) if str(x).isdigit() else 0).sum()
        total_verified = total_flags - total_unver
        guardrail_rate = total_verified / total_flags if total_flags > 0 else 1.0
        lines_with_unver = (df[unver_col].apply(lambda x: int(x) if str(x).isdigit() else 0) > 0).sum()
        results["guardrail"] = {
            "total_flags": total_flags,
            "total_verified": total_verified,
            "total_unverified": total_unver,
            "guardrail_rate": guardrail_rate,
            "lines_with_unverified": int(lines_with_unver),
        }
    else:
        results["guardrail"] = None

    return results


def print_summary(metrics: dict, version: str):
    """Print a formatted summary table to the terminal."""
    print(f"\n{'=' * 62}")
    print(f"  Eval results — {version}")
    print(f"{'=' * 62}")
    print(f"  {'Rule':<22}  {'P':>6}  {'R':>6}  {'F1':>6}  {'TP':>4}  {'FP':>4}  {'FN':>4}")
    print(f"  {'-'*22}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*4}  {'-'*4}  {'-'*4}")
    for rule in ALL_RULES:
        m = metrics[rule]
        print(f"  {rule:<22}  {m['precision']:>6.2f}  {m['recall']:>6.2f}  {m['f1']:>6.2f}"
              f"  {m['tp']:>4}  {m['fp']:>4}  {m['fn']:>4}")
    o = metrics["overall"]
    print(f"{'=' * 62}")
    print(f"  Overall exact-match accuracy: {o['accuracy']:.1%}"
          f"  ({int(o['accuracy']*o['n_valid'])}/{o['n_valid']} lines correct)")
    if o["n_valid"] < o["n_total"]:
        print(f"  Note: {o['n_total'] - o['n_valid']} row(s) errored and were excluded from scoring.")
    g = metrics.get("guardrail")
    if g is not None:
        print(f"  Guardrail rate (verified quotes): {g['guardrail_rate']:.1%}"
              f"  ({g['total_verified']}/{g['total_flags']} flags)")
        print(f"  Lines with ≥1 unverified flag:    {g['lines_with_unverified']}")
    print(f"{'=' * 62}\n")


# ── Main eval loop ────────────────────────────────────────────────────────────

def run_eval(rules: str = RULES_TEXT, version: str = PROMPT_VERSION):
    """
    Load the eval set, run the reviewer on every row, score, and save.
    `version` controls the output column names (v1_predicted_flags, etc.)
    so future prompt versions can be added without rewriting this function.
    """
    print(f"Loading eval set from: {EVAL_PATH}")
    df = pd.read_excel(EVAL_PATH, sheet_name=SHEET_NAME, dtype=str)
    print(f"Loaded {len(df)} rows from sheet '{SHEET_NAME}'.\n")

    pred_flags_col      = f"{version}_predicted_flags"
    pred_severity_col   = f"{version}_predicted_severity"
    pred_quotes_col     = f"{version}_quoted_texts"
    pred_unverified_col = f"{version}_unverified_count"
    match_col           = f"{version}_match"

    predicted_flags_list      = []
    predicted_severity_list   = []
    predicted_quotes_list     = []
    predicted_unverified_list = []
    match_list                = []

    successes = 0
    errors    = 0

    for _, row in df.iterrows():
        line_id = str(row["line_id"])
        print(f"Processing {line_id}...", end=" ", flush=True)

        line_item = {
            "line_id":    line_id,
            "date":       str(row["date"]),
            "timekeeper": str(row["timekeeper"]),
            "narrative":  str(row["narrative"]),
            "hours":      row["hours"],
            "rate":       row["rate"],
            "task_code":  str(row["task_code"]),
        }

        try:
            result = review(line_item, rules, client_id=str(row.get("client_id", "default")),
                            prompt_version=version)

            flags      = [f["rule"]                      for f in result.get("flags", [])]
            severities = [f["severity"]                  for f in result.get("flags", [])]
            quotes     = [f.get("quoted_text", "")       for f in result.get("flags", [])]
            unverified = [f.get("unverified", False)     for f in result.get("flags", [])]

            pred_str    = ", ".join(flags)     if flags  else "none"
            sev_str     = ", ".join(severities) if flags else "none"
            quotes_str  = " | ".join(quotes)   if quotes else ""
            unver_count = sum(1 for u in unverified if u)

            expected_set  = parse_expected(row["expected_flags"])
            predicted_set = set(flags)
            is_match      = (expected_set == predicted_set)

            predicted_flags_list.append(pred_str)
            predicted_severity_list.append(sev_str)
            predicted_quotes_list.append(quotes_str)
            predicted_unverified_list.append(unver_count)
            match_list.append(is_match)

            status = "exact match" if is_match else f"MISMATCH (expected: {row['expected_flags']})"
            print(f"done — {pred_str}  [{status}]")
            successes += 1

        except Exception as e:
            error_msg = f"ERROR: {e}"
            predicted_flags_list.append(error_msg)
            predicted_severity_list.append(error_msg)
            predicted_quotes_list.append("")
            predicted_unverified_list.append(0)
            match_list.append(False)
            print(f"FAILED — {e}")
            errors += 1

    # Write results back to the dataframe
    df[pred_flags_col]      = predicted_flags_list
    df[pred_severity_col]   = predicted_severity_list
    df[pred_quotes_col]     = predicted_quotes_list
    df[pred_unverified_col] = predicted_unverified_list
    df[match_col]           = match_list

    # Save to new file (never overwrites the original)
    df.to_excel(OUTPUT_PATH, index=False)
    print(f"\nResults saved to: {OUTPUT_PATH}")

    # Score and print
    metrics = score_metrics(df, version)
    print_summary(metrics, version)

    print(f"  Rows succeeded: {successes}  |  Rows errored: {errors}\n")

    # Print lines where v1_match is False (useful for disagreement analysis)
    mismatches = df[df[match_col] == False]
    if not mismatches.empty:
        print(f"Lines where {match_col} = False:")
        for _, row in mismatches.iterrows():
            print(f"  {row['line_id']:<8}  expected: {row['expected_flags']:<40}  "
                  f"predicted: {row[pred_flags_col]}")
    else:
        print("All lines matched!")


if __name__ == "__main__":
    run_eval(version=PROMPT_VERSION)
