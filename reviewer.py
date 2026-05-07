import json
import os
import weave
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()
client = Anthropic()

# Runs once on import; Python module caching prevents re-runs
_weave_client = weave.init("invoice-review-poc")

# ── Rule definitions ──────────────────────────────────────────────────────────
# These are passed into review() so they can be swapped per client in the future.

RULES_TEXT = """
1. block_billing — Multiple distinct tasks grouped under a single time entry, such that you cannot tell how much time was spent on each task.

2. vague_narrative — The narrative lacks sufficient detail for a reviewer to assess what work was performed. Generic descriptions that could apply to any matter.

3. admin_as_legal — Non-legal administrative tasks (filing, photocopying, basic scheduling, mailing) billed at legal rates.

4. excessive_time — Time charged is unreasonable given the nature of the work described. The narrative is detailed enough to assess; the issue is the time, not the description.

5. intra_firm_comms — Billing for communication between lawyers at the same firm about the same matter (e.g., emails or meetings between colleagues). Communication with the client or opposing counsel is NOT this rule.
""".strip()


# ── Few-shot examples (v2 only) ───────────────────────────────────────────────
# Teaches the model to weight justifying context more heavily before flagging.
# Placed after rule definitions, before JSON instructions.

FEW_SHOT_EXAMPLES = """
EXAMPLES OF LINES THAT LOOK FLAG-WORTHY BUT SHOULD NOT BE FLAGGED:

Example 1:
Line: "Drafted comprehensive 40-page summary judgment motion with supporting brief and 12 expert declarations"
Hours: 18.0 | Rate: $750 | Timekeeper: Partner
Decision: NO FLAGS
Reasoning: Time is high, but the narrative specifies the scope (40-page motion, supporting brief, 12 declarations) which justifies the hours. Do not flag excessive_time when the narrative provides scope context.

Example 2:
Line: "Compiling and indexing privileged communications for production response"
Hours: 3.0 | Rate: $250 | Timekeeper: Paralegal
Decision: NO FLAGS
Reasoning: Words like "compiling" and "indexing" sound clerical, but "privileged communications" indicates legal-judgment work. Do not flag admin_as_legal when the work requires legal judgment, even if the verbs sound administrative.

Example 3:
Line: "Reviewed expert reports, deposition transcripts, and exhibit binders for trial preparation"
Hours: 5.0 | Rate: $525 | Timekeeper: Senior Associate
Decision: NO FLAGS
Reasoning: The "and" connects document types being reviewed, not separate billable tasks. The single task is "reviewed for trial preparation." Do not flag block_billing when the narrative lists scope of a single task.
""".strip()

# ── v3 few-shot example (quoted_text) ────────────────────────────────────────
# Shows the model the full flag shape including quoted_text.

FEW_SHOT_QUOTED_EXAMPLE = """
EXAMPLE OF A FLAG WITH quoted_text:

Line: "Work on matter — 3.0 hrs"
Decision: FLAG vague_narrative
quoted_text: "Work on matter"
Reasoning: No information about what was done.
""".strip()

# ── Prompt builder ────────────────────────────────────────────────────────────

@weave.op()
def _build_system_prompt(rules: str, prompt_version: str) -> str:
    """Assemble the system prompt for the given version."""
    base = f"""You are a legal invoice reviewer. Your job is to assess whether a single billing line item violates any of the billing rules listed below.

RULES:
{rules}"""

    if prompt_version in ("v2", "v3"):
        base += f"\n\n{FEW_SHOT_EXAMPLES}"

    if prompt_version == "v3":
        base += f"\n\n{FEW_SHOT_QUOTED_EXAMPLE}"

    if prompt_version == "v3":
        base += """

INSTRUCTIONS:
- A line item may violate multiple rules, or zero rules.
- For each violation found, provide the rule name, a severity (low, medium, or high), a quoted_text field, and a reasoning.
- quoted_text MUST be a verbatim substring of the line item's narrative — copy it exactly, character for character. Do not paraphrase or summarise. If the violation is about time being unreasonable (excessive_time), quote the phrase describing the work.
- Respond ONLY with valid JSON. No preamble, no explanation, no markdown code fences — just the raw JSON object.
- Use exactly this shape:
{
  "line_id": "<the line_id from the input>",
  "flags": [
    {"rule": "<rule_name>", "severity": "<low|medium|high>", "quoted_text": "<verbatim substring>", "reasoning": "<explanation>"}
  ]
}
- If the line is clean, return an empty list for flags: "flags": []"""
    else:
        base += """

INSTRUCTIONS:
- A line item may violate multiple rules, or zero rules.
- For each violation found, provide the rule name, a severity (low, medium, or high), and a short reasoning explaining why.
- Respond ONLY with valid JSON. No preamble, no explanation, no markdown code fences — just the raw JSON object.
- Use exactly this shape:
{
  "line_id": "<the line_id from the input>",
  "flags": [
    {"rule": "<rule_name>", "severity": "<low|medium|high>", "reasoning": "<explanation>"}
  ]
}
- If the line is clean, return an empty list for flags: "flags": []"""

    return base


def _validate_quoted_text(result: dict, narrative: str) -> dict:
    """
    For v3 responses: check each flag's quoted_text is a verbatim substring
    of the narrative. Sets flag["unverified"] = True/False in place.
    Flags without a quoted_text field are marked unverified=True.
    """
    for flag in result.get("flags", []):
        qt = flag.get("quoted_text", "")
        flag["unverified"] = not (qt and qt in narrative)
    return result


@weave.op()
def review(line_item: dict, rules: str, client_id: str = "default", prompt_version: str = "v1") -> dict:
    """
    Review a single invoice line item against the provided billing rules.
    prompt_version: "v1" (rules only) | "v2" (+ few-shot) | "v3" (+ quoted_text guardrail).
    Returns a dict with 'line_id' and 'flags' (list of rule violations).
    """

    system_prompt = _build_system_prompt(rules, prompt_version)

    user_prompt = f"""Review this invoice line item:

Line ID:     {line_item['line_id']}
Date:        {line_item['date']}
Timekeeper:  {line_item['timekeeper']}
Narrative:   {line_item['narrative']}
Hours:       {line_item['hours']}
Rate:        ${line_item['rate']}/hr
Task Code:   {line_item['task_code']}
"""

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        temperature=0,  # Deterministic output for reproducible results
        system=system_prompt,
        messages=[
            {"role": "user", "content": user_prompt}
        ]
    )

    raw = response.content[0].text.strip()

    # Log whether the model wrapped output in markdown fences
    print(f"  [{line_item['line_id']}] fenced: {'```' in raw}")

    # Strip markdown code fences if the model wrapped the JSON in them
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]          # drop opening fence + optional "json" tag
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip() # drop closing fence

    # Parse the JSON response; raise a clear error if it's malformed
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Claude returned invalid JSON for line {line_item['line_id']}.\n"
            f"Parse error: {e}\n"
            f"Raw response:\n{raw}"
        )

    # v3: validate quoted_text is a real substring of the narrative
    if prompt_version == "v3":
        result = _validate_quoted_text(result, line_item["narrative"])

    return result


# ── Smoke test ────────────────────────────────────────────────────────────────
# Three hand-picked line items to verify the reviewer works end-to-end.
# Expected: L001 → block_billing | L014 → clean | L023 → block_billing + admin_as_legal

SMOKE_TEST_LINES = [
    {
        "line_id": "L001",
        "date": "2025-03-04",
        "timekeeper": "John Smith, Partner",
        "narrative": "Reviewed motion to dismiss; attended call with client re: strategy; drafted response brief; filed response with court",
        "hours": 4.5,
        "rate": 750,
        "task_code": "L120"
    },
    {
        "line_id": "L014",
        "date": "2025-03-04",
        "timekeeper": "Sarah Lee, Associate",
        "narrative": "Reviewed motion to dismiss filed by opposing counsel",
        "hours": 1.5,
        "rate": 450,
        "task_code": "L120"
    },
    {
        "line_id": "L023",
        "date": "2025-03-25",
        "timekeeper": "Sarah Lee, Associate",
        "narrative": "Filed documents with court, scheduled hearing, photocopied exhibits, and mailed copies to client",
        "hours": 2.5,
        "rate": 450,
        "task_code": "L160"
    }
]

if __name__ == "__main__":
    print("Running smoke test — 3 line items\n" + "=" * 50)
    for line in SMOKE_TEST_LINES:
        result = review(line, RULES_TEXT)
        print(f"\n--- {result['line_id']} ---")
        print(json.dumps(result, indent=2))
    print("\n" + "=" * 50 + "\nDone.")
    print(f"\nWeave project: https://wandb.ai/{_weave_client.entity}/{_weave_client.project}/weave/calls")
