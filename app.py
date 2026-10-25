"""Streamlit UI for the Agentic RFP Evaluation backend."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
import os
from pathlib import Path
import sqlite3

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from scripts.init_db import initialize_database
from src.config import Settings
from src.documents import DocumentTool
from src.evaluator import EvaluationAgent, EvaluationError, OpenAIJsonLLM
from src.exporter import export_run_json
from src.models import SupplierSubmission, decimal_value
from src.orchestrator import RunOrchestrator
from src.repository import EvaluationRepository
from src.validation import ScorecardValidationError, ValidationTool


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=False)

st.set_page_config(
    page_title="RFP Evaluator | Procurement Workspace",
    page_icon="▦",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    :root {
        --ink: #202d2a;
        --muted: #687671;
        --line: #dce4df;
        --canvas: #f6f8f6;
        --paper: #ffffff;
        --forest: #165b4a;
        --forest-soft: #e6f1ec;
        --amber: #a65c20;
        --amber-soft: #fff1df;
        --red: #a43f36;
    }
    .stApp { background: var(--canvas); color: var(--ink); }
    [data-testid="stHeader"] { background: transparent; }
    [data-testid="stMainBlockContainer"] { padding-top: 1.35rem; max-width: 1440px; }
    h1, h2, h3 { color: var(--ink); letter-spacing: 0; }
    h1 { font-family: Georgia, "Times New Roman", serif; font-size: 2.15rem; font-weight: 500; }
    h2 { font-family: Georgia, "Times New Roman", serif; font-size: 1.45rem; font-weight: 500; }
    h3 { font-size: 1.05rem; font-weight: 650; }
    [data-testid="stMetric"] {
        background: var(--paper); border: 1px solid var(--line); border-radius: 6px;
        padding: 0.8rem 1rem;
    }
    [data-testid="stMetricLabel"] { color: var(--muted); }
    [data-testid="stMetricValue"] { color: var(--forest); }
    [data-testid="stTabs"] [role="tablist"] { border-bottom: 1px solid var(--line); gap: 0.35rem; }
    [data-testid="stTabs"] button[role="tab"] { color: var(--muted); }
    [data-testid="stTabs"] button[aria-selected="true"] {
        color: var(--forest); border-bottom-color: var(--forest); font-weight: 650;
    }
    [data-testid="stFileUploader"] section { background: var(--paper); border-color: var(--line); }
    [data-testid="stForm"] { border: 1px solid var(--line); border-radius: 6px; background: var(--paper); }
    div.stButton > button[kind="primaryFormSubmit"], div.stButton > button[kind="primary"] {
        background: var(--forest); border-color: var(--forest); color: white; border-radius: 5px;
    }
    div.stButton > button[kind="primaryFormSubmit"]:hover, div.stButton > button[kind="primary"]:hover {
        background: #104838; border-color: #104838; color: white;
    }
    .eyebrow { color: var(--forest); font-size: 0.75rem; font-weight: 750; text-transform: uppercase; letter-spacing: 0.08em; }
    .subtle { color: var(--muted); font-size: 0.92rem; }
    .run-band { border-left: 3px solid var(--forest); padding: 0.55rem 0.85rem; background: var(--forest-soft); border-radius: 0 4px 4px 0; }
    </style>
    """,
    unsafe_allow_html=True,
)


def get_llm_api_key() -> str | None:
    """Read a provider key without rendering or logging its value."""
    try:
        secret_value = st.secrets.get("OPENAI_API_KEY")
    except Exception:
        secret_value = None
    return secret_value or os.getenv("OPENAI_API_KEY")


def get_llm_model(default_model: str) -> str:
    try:
        return str(st.secrets.get("RFP_LLM_MODEL", default_model))
    except Exception:
        return default_model

def build_services(settings: Settings, api_key: str | None) -> tuple[EvaluationRepository, RunOrchestrator]:
    repository = EvaluationRepository(settings.database_path)
    llm = OpenAIJsonLLM(
        model=settings.llm_model,
        api_key=api_key,
        max_attempts=settings.llm_max_attempts,
    )
    orchestrator = RunOrchestrator(
        repository=repository,
        document_tool=DocumentTool(settings.max_pdf_bytes, settings.max_document_characters),
        evaluation_agent=EvaluationAgent(llm),
        validation_tool=ValidationTool(),
        settings=settings,
    )
    return repository, orchestrator


def load_active_criteria_for_display(database_path: Path):
    """Show configured criteria even when their weight total needs correction."""
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            """SELECT criterion_id, name, description, weight, max_score
               FROM evaluation_criteria WHERE is_active = 1 ORDER BY criterion_id"""
        ).fetchall()
    finally:
        connection.close()


def render_criteria(criteria_rows) -> None:
    st.markdown("### Active evaluation criteria")
    if not criteria_rows:
        st.error("No active evaluation criteria are configured in SQLite.")
        return
    total = sum((decimal_value(row["weight"], "criterion weight") for row in criteria_rows), Decimal("0"))
    criteria_frame = pd.DataFrame(
        [
            {
                "ID": row["criterion_id"],
                "Criterion": row["name"],
                "What is assessed": row["description"],
                "Weight": f"{row['weight']}%",
                "Maximum": row["max_score"],
            }
            for row in criteria_rows
        ]
    )
    with st.container(border=True):
        st.dataframe(criteria_frame, hide_index=True, width="stretch")
        st.caption("Weight distribution")
        for row in criteria_rows:
            weight = decimal_value(row["weight"], "criterion weight")
            label_column, bar_column, value_column = st.columns([2, 7, 1])
            label_column.caption(row["name"])
            bar_column.progress(min(max(float(weight) / 100, 0.0), 1.0))
            value_column.caption(f"{weight}%")
    if total == Decimal("100"):
        st.success("Active weights total 100%.")
    else:
        st.error(f"Active criterion weights total {total}%; they must total 100% before evaluation.")


def submissions_from_form(uploaded_files) -> list[SupplierSubmission]:
    submissions: list[SupplierSubmission] = []
    errors: list[str] = []
    names: set[str] = set()
    for index, proposal in enumerate(uploaded_files):
        supplier_name = st.session_state.get(f"supplier_name_{index}", proposal["default_name"]).strip()
        submission_date = st.session_state.get(f"submission_date_{index}")
        experience_rating = st.session_state.get(f"experience_{index}")
        source_filename = proposal["filename"]
        if not supplier_name:
            errors.append(f"{source_filename}: enter a supplier name.")
            continue
        normalized_name = supplier_name.casefold()
        if normalized_name in names:
            errors.append(f"Supplier name '{supplier_name}' is duplicated.")
            continue
        names.add(normalized_name)
        try:
            rating = decimal_value(experience_rating, "experience rating")
            submissions.append(
                SupplierSubmission(
                    supplier_name=supplier_name,
                    submission_date=submission_date.isoformat(),
                    experience_rating=rating,
                    pdf_bytes=proposal["pdf_bytes"],
                    source_filename=Path(source_filename).name,
                )
            )
        except (ValueError, AttributeError) as error:
            errors.append(f"{source_filename}: {error}")
    if errors:
        raise ValueError("\n".join(errors))
    return submissions


def uploaded_proposal_specs(uploaded_files) -> list[dict[str, object]]:
    return [
        {
            "filename": uploaded.name,
            "pdf_bytes": uploaded.getvalue(),
            "default_name": Path(uploaded.name).stem.replace("_", " ").title(),
            "default_date": date.today(),
            "default_experience": 5.0,
            "widget_key": f"upload_{index}_{Path(uploaded.name).stem}",
        }
        for index, uploaded in enumerate(uploaded_files)
    ]


def sample_proposal_specs(selected_files: list[str]) -> list[dict[str, object]]:
    profiles = {
        "apex_systems_proposal.pdf": ("Apex Systems", date(2026, 10, 1), 4.8),
        "brightpath_tech_proposal.pdf": ("BrightPath Tech", date(2026, 10, 3), 3.9),
        "nexaworks_proposal.pdf": ("NexaWorks", date(2026, 9, 28), 4.6),
        "orbit_digital_proposal.pdf": ("Orbit Digital", date(2026, 10, 2), 4.7),
    }
    sample_directory = ROOT / "samples" / "synthetic_rfp_proposals_all"
    specifications = []
    for filename in selected_files:
        path = sample_directory / filename
        name, submitted, experience = profiles[filename]
        specifications.append(
            {
                "filename": filename,
                "pdf_bytes": path.read_bytes(),
                "default_name": name,
                "default_date": submitted,
                "default_experience": experience,
                "widget_key": f"sample_{Path(filename).stem}",
            }
        )
    return specifications


def profiles_label(filename: str) -> str:
    return {
        "apex_systems_proposal.pdf": "Apex Systems · technical and security strengths",
        "brightpath_tech_proposal.pdf": "BrightPath Tech · lowest cost and fast timeline",
        "nexaworks_proposal.pdf": "NexaWorks · balanced plan and support",
        "orbit_digital_proposal.pdf": "Orbit Digital · experience and references",
    }.get(filename, filename)


def render_leaderboard(run: dict[str, object]) -> None:
    suppliers = run.get("suppliers", [])
    if not suppliers:
        st.info("This run has no completed supplier results.")
        return
    leaderboard = pd.DataFrame(
        [
            {
                "Rank": item["final_rank"],
                "Supplier": item["supplier_name"],
                "Absolute score": f"{Decimal(str(item['absolute_score'])):.2f}",
                "PPI": f"{Decimal(str(item['ppi'])):.2f}%",
                "Submitted": item["submission_date"],
                "Experience / 10": item["experience_rating"],
            }
            for item in suppliers
        ]
    )
    st.dataframe(leaderboard, hide_index=True, width="stretch")


def render_scorecard(run: dict[str, object]) -> None:
    suppliers = run.get("suppliers", [])
    if not suppliers:
        st.info("No scorecards are available for this run.")
        return
    names = [item["supplier_name"] for item in suppliers]
    selected_name = st.segmented_control(
        "Supplier scorecard",
        names,
        default=names[0],
        key="scorecard_supplier",
        width="stretch",
    )
    if selected_name is None:
        return
    selected = next(item for item in suppliers if item["supplier_name"] == selected_name)
    st.markdown(
        f"**Rank {selected['final_rank']} · {selected['supplier_name']}**  "
        f"Absolute score **{Decimal(str(selected['absolute_score'])):.2f}** · "
        f"PPI **{Decimal(str(selected['ppi'])):.2f}%**"
    )
    criteria = selected.get("criteria", [])
    score_rows = []
    for criterion in criteria:
        score_rows.append(
            {
                "Criterion": criterion.get("criterion_name", criterion.get("name", "")),
                "Score": f"{criterion['score']} / {criterion['max_score']}",
                "Weight": f"{criterion['weight']}%",
                "Peer benchmark": criterion["benchmark"],
                "Gap": criterion["gap"],
                "Relative performance": f"{Decimal(str(criterion['relative_performance_pct'])):.2f}%",
            }
        )
    st.dataframe(pd.DataFrame(score_rows), hide_index=True, width="stretch")
    for criterion in criteria:
        title = criterion.get("criterion_name", f"Criterion {criterion['criterion_id']}")
        with st.expander(title):
            st.markdown(f"**Justification**\n\n{criterion.get('justification') or 'No justification supplied.'}")
            st.markdown(f"**Evidence**\n\n{criterion.get('evidence') or 'No evidence supplied.'}")
    if selected.get("overall_summary"):
        st.markdown("**Summary**")
        st.write(selected["overall_summary"])
    if selected.get("risks"):
        st.markdown("**Risks**")
        for risk in selected["risks"]:
            st.warning(risk)
    if selected.get("warnings"):
        st.markdown("**Validation warnings**")
        for warning in selected["warnings"]:
            st.warning(warning)
    if selected.get("ranking_explanation"):
        st.caption(f"Ranking: {selected['ranking_explanation']}")


def render_run_details(repository: EvaluationRepository, run: dict[str, object]) -> None:
    st.markdown(
        f"<div class='run-band'><b>RFP_RUN_ID</b> &nbsp; {run['rfp_run_id']}"
        f" &nbsp; · &nbsp; <b>Status</b> &nbsp; {run['status']}</div>",
        unsafe_allow_html=True,
    )
    st.caption(f"Created at {run['created_at']}")
    if run.get("error_message"):
        st.error(run["error_message"])
    for warning in run.get("warnings", []):
        st.warning(warning)
    with st.expander("Criteria snapshot and run configuration"):
        st.json({"criteria_snapshot": run["criteria_snapshot"], "config": run["config"]})
    if run["status"] == "completed":
        st.download_button(
            "Download complete result JSON",
            data=export_run_json(repository, str(run["rfp_run_id"])),
            file_name=f"{run['rfp_run_id']}.json",
            mime="application/json",
            key=f"download_{run['rfp_run_id']}",
            width="content",
        )


def main() -> None:
    settings = Settings.from_environment()
    settings = replace(settings, llm_model=get_llm_model(settings.llm_model))
    initialize_database(settings.database_path)
    repository, orchestrator = build_services(settings, get_llm_api_key())

    st.markdown("<div class='eyebrow'>Procurement intelligence · Evaluation workspace</div>", unsafe_allow_html=True)
    st.title("Supplier proposal evaluation")
    st.markdown(
        "<div class='subtle'>Compare evidence-backed proposal assessments against a shared criteria set.</div>",
        unsafe_allow_html=True,
    )

    criteria_rows = load_active_criteria_for_display(settings.database_path)
    try:
        active_criteria = repository.load_active_criteria()
        criteria_error = None
    except (ValueError, sqlite3.Error) as error:
        active_criteria = []
        criteria_error = str(error)

    run_id = st.session_state.get("current_run_id")
    loaded_run = None
    if run_id:
        try:
            loaded_run = repository.load_run(str(run_id))
        except (KeyError, sqlite3.Error) as error:
            st.session_state.evaluation_error = f"Saved run could not be loaded: {error}"

    if loaded_run and loaded_run.get("status") == "completed":
        suppliers = loaded_run.get("suppliers", [])
        leader = suppliers[0] if suppliers else None
        metric_a, metric_b, metric_c = st.columns([1, 1, 2])
        metric_a.metric("Current batch", str(loaded_run["rfp_run_id"])[:8])
        metric_b.metric("Suppliers ranked", len(suppliers))
        metric_c.metric(
            "Current leader",
            leader["supplier_name"] if leader else "No results",
            f"PPI {Decimal(str(leader['ppi'])):.2f}%" if leader else None,
        )
        st.progress(1.0, text="Batch complete · all submitted suppliers evaluated")
    else:
        st.progress(0.0, text="Ready · upload proposals to begin a batch")

    criteria_tab, input_tab, leaderboard_tab, scorecard_tab, details_tab = st.tabs(
        ["Criteria", "Supplier input", "Leaderboard", "Detailed scorecard", "Run details"]
    )
    with criteria_tab:
        render_criteria(criteria_rows)
        if criteria_error:
            st.error(criteria_error)

    with input_tab:
        st.markdown("### Submit proposals")
        st.caption("Experience rating uses a 0–10 scale. All proposals in a batch share one criteria snapshot.")
        source_mode = st.segmented_control(
            "Proposal source",
            ["Upload PDFs", "Bundled sample proposals"],
            default="Upload PDFs",
            key="proposal_source_mode",
            width="content",
        )
        if source_mode == "Bundled sample proposals":
            sample_directory = ROOT / "samples" / "synthetic_rfp_proposals_all"
            available_samples = sorted(path.name for path in sample_directory.glob("*.pdf"))
            selected_samples = st.multiselect(
                "Select sample proposals",
                available_samples,
                default=available_samples,
                format_func=lambda filename: profiles_label(filename),
                key="selected_sample_proposals",
            )
            proposal_specs = sample_proposal_specs(selected_samples)
        else:
            uploads = st.file_uploader(
                "Supplier proposal PDFs",
                type=["pdf"],
                accept_multiple_files=True,
                help="Text-based PDF proposals are supported. Image-only PDFs are not OCR processed.",
                key="proposal_uploads",
            )
            proposal_specs = uploaded_proposal_specs(uploads or [])

        if not proposal_specs:
            st.info("Choose sample proposals or upload PDFs to enter supplier metadata.")
        else:
            with st.form("supplier_evaluation_form", clear_on_submit=False):
                st.markdown("#### Supplier metadata")
                for index, proposal in enumerate(proposal_specs):
                    widget_key = proposal["widget_key"]
                    with st.container(border=True):
                        st.markdown(f"**{proposal['default_name']}** · {proposal['filename']}")
                        left, middle, right = st.columns([1.4, 1, 1])
                        with left:
                            st.text_input(
                                "Supplier name",
                                value=proposal["default_name"],
                                key=f"supplier_name_{widget_key}",
                            )
                        with middle:
                            st.date_input(
                                "Submission date",
                                value=proposal["default_date"],
                                key=f"submission_date_{widget_key}",
                            )
                        with right:
                            st.number_input(
                                "Experience / 10",
                                min_value=0.0,
                                max_value=10.0,
                                value=proposal["default_experience"],
                                step=0.1,
                                key=f"experience_{widget_key}",
                            )
                api_key_available = bool(get_llm_api_key())
                if not api_key_available:
                    st.warning("LLM is not configured. Add OPENAI_API_KEY to Streamlit secrets or the server environment before evaluating.")
                button_disabled = not active_criteria or not api_key_available or bool(st.session_state.get("evaluation_in_progress"))
                submitted = st.form_submit_button(
                    "Evaluate supplier batch",
                    type="primary",
                    disabled=button_disabled,
                    width="stretch",
                )
            if submitted and not st.session_state.get("evaluation_in_progress"):
                st.session_state.evaluation_in_progress = True
                try:
                    submissions = []
                    errors = []
                    names: set[str] = set()
                    for index, proposal in enumerate(proposal_specs):
                        widget_key = proposal["widget_key"]
                        supplier_name = st.session_state.get(
                            f"supplier_name_{widget_key}", proposal["default_name"]
                        ).strip()
                        normalized_name = supplier_name.casefold()
                        if normalized_name in names:
                            errors.append(f"Supplier name '{supplier_name}' is duplicated.")
                            continue
                        names.add(normalized_name)
                        submissions.append(
                            SupplierSubmission(
                                supplier_name=supplier_name,
                                submission_date=st.session_state[
                                    f"submission_date_{widget_key}"
                                ].isoformat(),
                                experience_rating=decimal_value(
                                    st.session_state[f"experience_{widget_key}"],
                                    "experience rating",
                                ),
                                pdf_bytes=proposal["pdf_bytes"],
                                source_filename=proposal["filename"],
                            )
                        )
                    if errors:
                        raise ValueError("\n".join(errors))
                    with st.spinner("Extracting proposals and evaluating suppliers..."):
                        run = orchestrator.run(submissions)
                    st.session_state.current_run_id = run.rfp_run_id
                    st.session_state.evaluation_error = None
                    st.success(f"Evaluation complete · Run {run.rfp_run_id}")
                except (ValueError, RuntimeError, EvaluationError, ScorecardValidationError) as error:
                    st.session_state.evaluation_error = str(error)
                    st.error(str(error))
                finally:
                    st.session_state.evaluation_in_progress = False

    with leaderboard_tab:
        st.markdown("### Batch leaderboard")
        if loaded_run and loaded_run["status"] == "completed":
            render_leaderboard(loaded_run)
        else:
            st.info("Run a batch to view its supplier leaderboard.")
    with scorecard_tab:
        st.markdown("### Supplier comparison")
        if loaded_run and loaded_run["status"] == "completed":
            render_scorecard(loaded_run)
        else:
            st.info("Detailed scorecards will appear after a successful evaluation.")
    with details_tab:
        st.markdown("### Run details and export")
        if loaded_run:
            render_run_details(repository, loaded_run)
        else:
            st.info("No run is selected yet.")
        with st.form("load_saved_run_form"):
            requested_run_id = st.text_input("Open a saved run by ID", key="requested_run_id")
            load_run_clicked = st.form_submit_button("Open run")
        if load_run_clicked:
            try:
                repository.load_run(requested_run_id.strip())
                st.session_state.current_run_id = requested_run_id.strip()
                st.rerun()
            except (KeyError, sqlite3.Error, ValueError) as error:
                st.error(f"Could not open run: {error}")


if __name__ == "__main__":
    main()
