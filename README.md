# Agentic RFP Evaluator

Streamlit application for evaluating supplier proposal PDFs against configurable SQLite criteria. The LLM evaluates one proposal at a time and returns evidence-backed criterion scores; Python validates the response and computes all weighted scores, peer benchmarks, PPI values, and ranks.

## Requirements

- Python 3.10 or newer
- An OpenAI API key for live evaluations

## Local setup

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `OPENAI_API_KEY` in `.env`. Keep that file private; it is ignored by Git. Initialize the dedicated database and start the app:

```powershell
python scripts/init_db.py
streamlit run app.py
```

The app defaults to `data/rfp_evaluation.sqlite3`. Set `RFP_DATABASE_PATH` to use another location.

## Evaluation workflow

1. Select uploaded PDFs or the bundled fictional proposals.
2. Review the supplier names, submission dates, and experience ratings.
3. Start an evaluation batch. Each proposal is extracted separately and evaluated against one active-criteria snapshot.
4. Review the leaderboard, criterion scorecards, evidence, warnings, and ranking explanations.
5. Download the complete run as JSON.

The sample PDFs are fictional. The bundled sample mode is intended for demonstrations and will make real LLM requests when evaluated.

## Scoring

- Absolute score: sum of `(criterion score / maximum score) * weight`.
- Criterion benchmark: highest validated score among suppliers in the run.
- Criterion gap: supplier score minus the criterion benchmark.
- Relative performance: `100 * supplier score / benchmark`; defined as zero when the benchmark is zero.
- PPI: weighted average of criterion relative-performance percentages.
- Tie-break: higher PPI, earlier submission date, higher experience rating, then supplier name ascending.

Calculations use decimal arithmetic and unrounded values for ranking. The LLM does not calculate the final totals or rank.

## Configuration and security

The **Model setup** screen lets each app user choose OpenAI or an OpenAI-compatible provider, enter a model and endpoint, and provide a password-masked API key. A key entered in the screen remains in that active Streamlit session only; it is not written to `.env`, SQLite, logs, or run exports. OpenAI can alternatively read `OPENAI_API_KEY` from the server environment, local `.env`, or Streamlit Community Cloud secrets. Compatible providers can use a session key or the optional `RFP_COMPATIBLE_API_KEY` server variable. Never commit `.env` or a populated `secrets.toml`.

SQLite on a hosted ephemeral filesystem may not survive runtime replacement. Download run JSON when persistence beyond the current deployment instance is required.

## Tests

```powershell
python -m unittest discover -s tests -v
```

See [architecture.md](architecture.md) for component responsibilities, data flow, persistence rules, validation behavior, and acceptance criteria.