# MailMind — Content Classification Remediation Plan

**Status:** Phase 1 SHIPPED (`ee6d70b`). Phase 2 BLOCKED on `OPENAI_API_KEY`. Phase 3 pending.
**Date:** 2026-06-05
**Prereq landed:** `f282a49` — router `fallback` no longer discards a confident LLM label; Tier-0 `rule` emails skip the paid LLM.

### Decisions (locked)
- **Phase 1 aggressiveness:** conservative — only label-free rules unblocked. **DONE** (`ee6d70b`).
- **LLM provider:** **OpenAI** (`LLM_PROVIDER=auto` uses OpenAI when `OPENAI_API_KEY` set, else DeepSeek). Switch wired (`ee6d70b`); **needs the key** to activate.
- **Backfill scope:** as broad as possible — **all 646 emails**.
- **Retrain cadence:** **scheduled job**.
- **Accuracy floor for model promotion:** **0.70** (`ML_MIN_ACCURACY`), my call.

---

## 1. Context / problem

Empirical state of the **local** DB (`~/.mailmind/mailmind.db`, 646 emails, 1,250 predictions):

- **100% of labels decided by the rules tier.** ML: 0. DeepSeek: 0.
- **Only 4 labels ever emitted:** NEWSLETTER 594, NOTIFICATION 558, MASS_EMAIL 71, CALENDAR 24 (+3 NULL). **Zero** URGENT / WORK / FINANCE / PERSONAL / DEFER / SPAMCANDIDATE, despite all being in `taxonomy.py`.
- **ML model** (`pass4_baseline.joblib`, trained 2026-06-01, 645 samples): knows only those same 4 classes, `accuracy=None` (never measured), and is **never invoked** at inference.
- **DeepSeek**: enabled in prod (`DEEPSEEK_API_KEY` set) but **never called**.

This is why classification feels sender/coarse-signal driven: **content never gets a vote.**

## 2. Root cause (precise)

Three compounding mechanisms:

1. **A content-free rule short-circuits the content tiers.** `rules.py::directly_addressed` matches whenever you're in the `To:` line, at `confidence=0.95`, but with `labels=[]`. The router's `_extract_rules_result` takes the **max confidence across all matched rules regardless of whether they assign a label**, so 0.95 ≥ `rules_threshold` (0.90) → Tier 1 "wins" → ML/LLM never run. `scorer._determine_primary_label` then finds no concrete label and **defaults to NOTIFICATION**. → the 558 NOTIFICATIONs.

2. **Echo chamber in training.** `train.py::_collect_training_data_from_db` uses `corrected_label > user_label > predictions.primary_label (rules guess)` as the target. With content tiers dead, the rules guess dominates, so the model learns to mimic the 4 rules labels.

3. **Sparse-label drop.** Training drops any label with `< 5 examples`. Your 44 corrections are spread thin across rich labels (FINANCE/PERSONAL/…), so none clears the bar → they're discarded → model stays at 4 classes.

**Conclusion:** to make content carry weight we must (a) stop label-free rules from gating classification, (b) generate enough rich-label training data to break the echo chamber, (c) actually run + measure ML.

## 3. Goals / non-goals

**Goals:** content tiers (ML + DeepSeek) decide labels for mail the rules can't classify; the full taxonomy becomes reachable; ML is retrained on real labels and its accuracy is measured; ongoing LLM spend stays trivial and bounded.

**Non-goals / unchanged (hard constraints — see `memory/project_mailmind_constraints.md`):** no Gmail deletes; `dry_run` default; `QueueManager` thresholds 0.90/0.65 frozen; LLM failure never crashes the pipeline; body cap 500 chars to the LLM; no network in tests (mock DeepSeek). Trust/autopilot stays decoupled from labeling. We respect the user preference: **content over sender** (`project_mailmind_preferences.md`).

---

## 4. Design — three phases

### Phase 1 — Routing fix: only *labeling* rules may short-circuit (≈ zero cost)

**Change:** in `classifier_router._extract_rules_result`, compute the Tier-1 short-circuit confidence **only over matched rules that carry a non-empty `labels` list.** Label-free matches (`directly_addressed`, and any future scoring-only rule) keep contributing to the **priority score** via the scorer (unchanged) but no longer gate classification.

**Effect:** an email that only matched `directly_addressed` now yields no labeling rule ≥0.90 → falls through to **Tier 2 (ML)** → **Tier 3 (DeepSeek)**. Newsletter/finance/calendar/mass-cc rules (which *do* carry labels) still short-circuit exactly as today, so the bulk newsletter/notification stream the rules already nail is untouched.

**Exact thresholds (all unchanged values, just applied to the right subset):**

| Knob | Value | Note |
|---|---|---|
| `rules_threshold` | 0.90 | unchanged; now measured over labeled matches only |
| `ml_threshold` | 0.65 | unchanged |
| `llm_skip_threshold` | 70 | unchanged; DeepSeek still skipped when priority score ≥ 70 |
| `llm_confidence_override` | 0.90 | unchanged; DeepSeek relabels only when ≥ 0.90 |
| `llm_max_calls_per_run` | 10 | unchanged for the live loop (cost guard) |

This is the single highest-leverage change and is low-risk: it cannot *reduce* the labeled-rule behavior, only *add* a fall-through for previously-unclassified mail.

### Phase 2 — Break the echo chamber + measure ML

1. **One-time DeepSeek labeling backfill** over the 646 existing emails → write a `scripts/backfill_llm_labels.py` (or `main.py` subcommand) that calls `DeepSeekClient.classify_email` for emails currently labeled NOTIFICATION/NULL (or all), storing the result as a *correction-grade* training signal (e.g. a new `llm_label` row or a `source='llm_backfill'` entry in `user_corrections`). **Idempotent, dry-run-able, with its own `--max-calls` cap** (the 10/run live cap must NOT throttle a deliberate backfill).
2. **Retrain** using `corrected_label > user_label > llm_backfill_label > rules guess` (insert LLM backfill *above* the rules guess so rich labels win). Keep the `<5` sparse drop — the backfill should push FINANCE/PERSONAL/etc. over the bar.
3. **Measure accuracy:** add a hold-out split (e.g. 80/20, stratified) in `train.py`, populate `ModelMetadata.accuracy` (currently `None`), and log a per-class report. Gate model promotion on accuracy ≥ a floor (e.g. 0.70) — otherwise keep the previous `model.pkl`.

### Phase 3 — Self-improving loop (ongoing)

DeepSeek runs on ambiguous mail (Phase 1 opens the gate) → its confident labels persist → periodic retrain (manual or scheduled) folds them in → ML confidence rises → fewer emails fall through to DeepSeek → spend trends down. The model stops echoing the rules and starts reflecting content + your corrections.

---

## 5. Cost estimate

Per DeepSeek call: ~450 input tokens (system prompt + subject/sender + 500-char body cap) + ~30 output. At deepseek-chat list prices (~$0.27 / 1M input, ~$1.10 / 1M output — **verify current pricing**): **≈ $0.00015 / email.**

- **One-time backfill (646 emails):** ≈ **$0.10**.
- **Ongoing:** only ambiguous mail hits DeepSeek. At ~30 such emails/day ≈ **$0.005/day ≈ $1.6/year**. The `llm_max_calls_per_run=10` cap bounds worst-case spend per loop regardless.

The 500-char body cap is what keeps this trivial; do not raise it for classification.

## 6. Backfill strategy

- New script, **opt-in**, never runs automatically. Flags: `--dry-run` (default), `--max-calls N`, `--only-label NOTIFICATION`, `--account`.
- Idempotent: skip emails that already have an `llm_backfill` label unless `--force`.
- Writes labels as a distinct source so they're auditable and reversible; never mutates Gmail.
- Respects the LLM fail-safe: any call that fails is logged and skipped, never crashes the run.

## 7. Rollback

- **Phase 1:** revert the one `_extract_rules_result` change (single function) → exact prior behavior. Guard behind a config flag (`CLASSIFY_LABELED_RULES_ONLY`, default on) for instant disable without a deploy.
- **Phase 2:** model promotion is gated on the accuracy floor and writes a *new* model file; the prior `model.pkl` is retained. Roll back by pointing `DEFAULT_MODEL_NAME` back / restoring the kept file. Backfill labels carry `source='llm_backfill'` so they can be deleted with one `DELETE WHERE source=...`.
- No phase changes any action-execution path, so there is no risk to Gmail state.

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Label churn on the ~558 NOTIFICATION emails (relabeled to FINANCE/PERSONAL/etc.) | Expected and desired; it's a re-classification, not a Gmail action. Review in the dashboard; corrections feed training. |
| DeepSeek mislabels | `llm_confidence_override=0.90` floor; low-confidence LLM keeps the rules/ML label; you can correct in-UI. |
| Cost runaway | `llm_max_calls_per_run=10` live cap; backfill is one-time and dry-run-first. |
| Autopilot auto-acts on a new high-confidence label | Autopilot still requires per-sender `auto_action_eligible` AND ≥0.90 — unchanged; delete hard-disabled. New labels don't bypass this. |
| Model regresses | Accuracy floor gates promotion; previous model retained. |

## 9. Test plan

- **Phase 1:** unit test that an email matching *only* `directly_addressed` routes past Tier 1 (source ≠ "rules") to ML/LLM; tests that newsletter/finance/calendar/mass-cc emails still short-circuit unchanged. Full suite stays green (645 today).
- **Phase 2:** test the training collector prefers `llm_backfill` over the rules guess; test accuracy is populated and promotion is gated on the floor; DeepSeek mocked throughout (no network).
- **Backfill script:** dry-run makes zero calls; `--max-calls` honored; idempotent re-run is a no-op.

## 10. Open questions for you

1. **Aggressiveness of Phase 1:** unblock only `directly_addressed`-style label-free matches (conservative, recommended), or also send *low-confidence labeled* matches (e.g. CALENDAR at 0.85) to DeepSeek?
2. **Backfill scope:** all 646 emails, or only NOTIFICATION/NULL (~561)? (Cost difference is pennies either way.)
3. **Retrain cadence:** manual command when you want it, or a scheduled job?
4. **Accuracy floor** for promoting a retrained model (proposed 0.70).
