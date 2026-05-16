# Cleanup Checklist — Before Merging to Main

Cross-referenced README.md against actual tracked and untracked files on 2026-05-16.

---

## 1. Tracked root-level orphans — delete or move

| File | Reason |
|---|---|
| `_diagnose_financials.py` | Not documented anywhere. Underscore prefix signals a scratch file. Likely superseded by `infra/diagnose_fastapi.py`. |
| `backtest.bat` | Undocumented Windows batch shortcut at repo root. Not in README CLI Reference, no tracked equivalent. |

---

## 2. Tracked scripts not in the README — verify then delete

| File | Reason |
|---|---|
| `scripts/seed_synthetic_financials.py` | Not mentioned in README's CLI Reference. Unclear if still needed or superseded by `datamanager.py`. |
| `scripts/trigger_bulk_extraction.py` | Undocumented. Likely an ad-hoc script that was never promoted to the CLI. |

Read both before deleting to confirm they are not still in use.

---

## 3. Untracked infra scripts — stage for deletion or commit with docs

| File | Reason |
|---|---|
| `infra/ssh-ec2.bat` | Not in README. The README documents `start_ssm.bat` and `start_ssm.ps1` for the same purpose (SSM access). Looks like an older approach that was replaced. |
| `infra/check_surrealdb_secret.py` | Not in README's diagnostic scripts section, which lists only `diagnose_fastapi.py` and `get_console.py`. |

---

## 4. docs/ gitignore conflict — decision required

The entire `docs/` tree is in `.gitignore`, yet the README links to:

- `docs/signals.md`
- `docs/tech_design.v1.md`
- `docs/backtesting_pipeline/`
- `docs/cloud_deployment/`
- `docs/evaluation_framework/`
- `infra/RUNBOOK.md` (tracked separately — not affected)

These files exist only locally and will not travel with the repo. Choose one:

- **Un-gitignore `docs/`** and commit the referenced files.
- **Remove the README links** to docs that aren't tracked.

---

## Status

- [ ] Delete `_diagnose_financials.py`
- [ ] Delete `backtest.bat`
- [ ] Review and delete `scripts/seed_synthetic_financials.py`
- [ ] Review and delete `scripts/trigger_bulk_extraction.py`
- [ ] Delete or commit `infra/ssh-ec2.bat`
- [ ] Delete or commit `infra/check_surrealdb_secret.py`
- [ ] Resolve `docs/` gitignore conflict (un-gitignore or remove README links)
