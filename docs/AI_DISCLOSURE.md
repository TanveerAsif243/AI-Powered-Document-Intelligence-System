# AI-assisted development disclosure

- This codebase was written with Claude (Anthropic) acting as the coding assistant, driven by the assignment text. All code, tests and docs were produced in that session.
- Verification performed in-session: the processing core was executed against generated PDFs, images and spreadsheets; 44 unit tests and the evaluation harness were run and their failures were fixed (date fallback, line-item grounding, spreadsheet row handling, OCR fallback).
- Not verified in-session: API/database/queue/frontend (see README, "Test status"). A human must run and review them before relying on them.
- Metrics are produced by `evaluation/evaluate.py`, not typed by hand.

## Runtime AI usage
Optional LLM extraction via the Anthropic API (`app/core/llm.py`). Prompts: `app/core/prompts.py` (system prompt forbids inference, requires null for missing values, treats document text as untrusted). Outputs are post-processed by the grounding check in `app/core/extract.py`.

## Development prompts (summary)
1. "Build the full Document Intelligence System from the assignment (Tasks 1-7)."
2. Follow-ups: measure real accuracy; fix poor-quality OCR; keep the pipeline independent of web frameworks so it can be tested.
