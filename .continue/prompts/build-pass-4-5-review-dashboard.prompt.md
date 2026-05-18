---
name: Build Pass 4.5 review dashboard
description: Build Pass 4.5: a lightweight observability + review layer for the existing MailMind codebase..

Context:
- Pass 4 is complete.
- Schema is aligned and real DB persistence tests pass.
- The system already stores predictions, action audit logs, rule matches, scoring breakdowns, pipeline_used, and ml_confidence.
- The next goal is NOT more ML. The goal is to make the current system observable and reviewable by a human.

Mission:
Create a small review dashboard that lets me inspect recent predictions and actions, understand why decisions were made, and manually review model/rule behavior before Pass 5.

Important constraints:
1. Inspect the actual repository structure before changing anything.
2. Reuse existing storage/database access patterns where possible.
3. Prefer additive changes over rewrites.
4. Do not change prediction schema unless absolutely necessary.
5. Do not add LLMs, external APIs, or new product scope.
6. Do not redesign the existing pipeline.
7. Keep the implementation lightweight and local-first.
8. Make the UI read-only for the first pass unless a tiny “mark as reviewed” flag already fits naturally.
9. If there is already a FastAPI app, prefer integrating a small review page or API into it.
10. If there is no usable web app yet, build the fastest safe local review UI with Streamlit.
11. Keep privacy in mind: do not expose full email bodies unnecessarily; prefer subject, sender, snippet/preview, labels, scores, action status, and scoring explanations.
12. Keep the code testable.

First do discovery:
- Inspect the repository and identify:
  - existing app entry points
  - whether FastAPI already exists
  - storage/database access layer
  - prediction model
  - actions_applied persistence
  - any existing scripts or dashboards
- Summarize the current architecture and recommend whether this should be implemented as:
  A. Streamlit app, or
  B. FastAPI read-only review page/API
- Choose the smallest safe option based on the actual codebase.

Then implement this scope:

A. Review interface
Build a lightweight local review dashboard showing:
- recent predictions
- recent actions
- top priority emails
- blocked/deferred actions
- filters for:
  - date range
  - primary_label
  - pipeline_used
  - action status
  - dry_run
- table/list columns should include:
  - email_gmail_id
  - sender
  - subject
  - primary_label
  - priority_score
  - confidence
  - ml_confidence
  - pipeline_used
  - action_suggested
  - latest action status/result
  - created_at

B. Detail view
When a prediction row is selected, show:
- sender
- subject
- snippet/preview if available
- labels
- rule_matches
- scoring_breakdown (pretty formatted JSON)
- pipeline_used
- confidence and ml_confidence
- related actions from actions_applied
- whether action was blocked, deferred, dry-run, succeeded, or failed

C. Data access layer
Add the smallest necessary query helpers to fetch:
- recent predictions
- recent actions
- prediction details by email_gmail_id or prediction id
- joined view of predictions + latest action
- simple aggregate stats:
  - count by primary_label
  - count by pipeline_used
  - count of blocked/deferred/succeeded actions in recent period

D. UX requirements
- Keep it simple and utilitarian.
- Default to newest-first.
- Add a small summary section with:
  - total predictions in range
  - high priority count
  - blocked actions count
  - hybrid predictions count
- Use clear labels, not jargon-heavy UI.
- No authentication needed for local-only MVP unless the existing app already has auth patterns.

E. Tests
Add tests for:
- any new query helpers
- serialization/formatting helpers if added
- dashboard data prep logic
If the UI framework is Streamlit, prioritize tests for the underlying query/data functions rather than brittle UI rendering tests.

F. Documentation
Update README or create a PASS_4_5_REVIEW_DASHBOARD.md that explains:
- what the dashboard does
- how to run it
- what tables/fields it depends on
- privacy limitations
- why this exists before Pass 5

Implementation preferences:
- If Streamlit is chosen:
  - create a small app entrypoint such as `mailmind/review_dashboard.py`
  - keep DB access separate from UI rendering
- If FastAPI is chosen:
  - expose minimal read-only endpoints and, if appropriate, one simple HTML page
- Keep functions small and typed.
- Reuse existing models and DB helpers.
- Do not duplicate business logic already present elsewhere.

Output format:
1. First, summarize the discovered architecture.
2. Then state whether you chose Streamlit or FastAPI and why.
3. Then provide a short implementation plan.
4. Then make the code changes.
5. Then list changed files.
6. Then list how to run the dashboard locally.
7. Then list follow-up improvements for Pass 5.

Important:
- Do not hallucinate file names or architecture.
- Adapt to the current repository instead of forcing a new structure.
- Optimize for the fastest useful local review tool, not a polished product.
invokable: true
---