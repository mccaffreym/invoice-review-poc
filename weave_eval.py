"""weave_eval.py — Weave-native evaluation harness for invoice-review-poc.

Usage:
    python weave_eval.py          # runs with PROMPT_VERSION defined below

Change PROMPT_VERSION to v1 / v2 / v3 to run different prompt versions.
Run all three to get a comparison view in the Weave dashboard.
"""

import asyncio
import pandas as pd
import weave

from reviewer import review, RULES_TEXT

# ── Config ────────────────────────────────────────────────────────────────────
EVAL_PATH      = "invoice_eval_set_v1.xlsx"
EVAL_SHEET     = "eval_set_v1.2"
PROMPT_VERSION = "v3"          # change to v1, v2, or v3
WEAVE_PROJECT  = "invoice-review-poc"

ALL_RULES = [
    "block_billing",
    "vague_narrative",
    "admin_as_legal",
    "excessive_time",
    "intra_firm_comms",
]

# ── Weave init ────────────────────────────────────────────────────────────────
# reviewer.py also calls weave.init() on import; Weave treats a same-project
# second call as a no-op and returns the existing client.
_weave_client = weave.init(WEAVE_PROJECT)


# ── Dataset ───────────────────────────────────────────────────────────────────

def _parse_expected(raw) -> list:
    """'block_billing, vague_narrative' → sorted list; 'none'/NaN → []."""
    if pd.isna(raw) or str(raw).strip().lower() == "none":
        return []
    return sorted(r.strip() for r in str(raw).split(",") if r.strip())


def load_dataset() -> weave.Dataset:
    df = pd.read_excel(EVAL_PATH, sheet_name=EVAL_SHEET, dtype=str)
    rows = []
    for _, row in df.iterrows():
        # Weave passes top-level row keys as kwargs to predict() — keep flat.
        rows.append({
            "id": str(row["line_id"]),
            "line_item": {
                "line_id":    str(row["line_id"]),
                "date":       str(row["date"]),
                "timekeeper": str(row["timekeeper"]),
                "narrative":  str(row["narrative"]),
                "hours":      row["hours"],
                "rate":       row["rate"],
                "task_code":  str(row["task_code"]),
            },
            "rules": RULES_TEXT,
            "expected": {"flags": _parse_expected(row["expected_flags"])},
        })
    return weave.Dataset(name="invoice-eval-v1.2", rows=rows)


# ── Models ────────────────────────────────────────────────────────────────────

class InvoiceReviewer(weave.Model):
    prompt_version: str
    model_name: str = "claude-haiku-4-5"

    @weave.op()
    def predict(self, line_item: dict, rules: str) -> dict:
        return review(line_item, rules, prompt_version=self.prompt_version)


class InvoiceReviewerV1(InvoiceReviewer):
    prompt_version: str = "v1"


class InvoiceReviewerV2(InvoiceReviewer):
    prompt_version: str = "v2"


class InvoiceReviewerV3(InvoiceReviewer):
    prompt_version: str = "v3"


# ── Scorer ────────────────────────────────────────────────────────────────────

@weave.op()
def flag_scorer(output: dict, expected: dict) -> dict:
    predicted    = {f["rule"] for f in output.get("flags", [])}
    expected_set = set(expected.get("flags", []))
    metrics = {
        f"{rule}_correct": (rule in predicted) == (rule in expected_set)
        for rule in ALL_RULES
    }
    metrics["exact_match"] = (predicted == expected_set)
    return metrics


# ── Runner ────────────────────────────────────────────────────────────────────

_VERSION_MAP = {
    "v1": InvoiceReviewerV1,
    "v2": InvoiceReviewerV2,
    "v3": InvoiceReviewerV3,
}


async def main():
    dataset = load_dataset()
    model   = _VERSION_MAP[PROMPT_VERSION]()

    evaluation = weave.Evaluation(
        dataset=dataset,
        scorers=[flag_scorer],
    )

    print(f"Running evaluation — prompt version: {PROMPT_VERSION}")
    print(f"Model: {model.__class__.__name__} | Rows: {len(dataset.rows)}\n")

    results = await evaluation.evaluate(model)

    print("\nSummary:")
    print(results)
    print(
        f"\nWeave evaluations: "
        f"https://wandb.ai/{_weave_client.entity}/{_weave_client.project}/weave/evaluations"
    )


if __name__ == "__main__":
    asyncio.run(main())
