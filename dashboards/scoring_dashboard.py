"""
dashboards/scoring_dashboard.py — AI Scoring with the client's own AI (Module B).

No API key required. The client asks their OWN AI (ChatGPT, Claude, Gemini,
whatever they use) how to score the companies they're prospecting, pastes that
scoring guide here, and the app:

  1. takes the pasted guide + the crawled company data,
  2. hands back a ready-to-paste bundle (a prompt + a CSV) to run in their AI,
  3. re-imports the scores their AI produced, matched by domain,
  4. shows the ranked result — with an explicit note that AI scores should get a
     second validation pass before they're trusted.

This keeps scoring as an AI *judgment* step (never keyword counting) while
costing us zero API spend — the judgement runs in the client's own AI.

Convention: exposes a single render(user: dict) called by app.py's router.
"""

import os
import io
import tempfile

import pandas as pd
import streamlit as st

from services import scoring_service as ss

ALLOWED_ROLES = {"super_admin", "org_admin", "manager", "research_manager", "researcher"}

_DOMAIN_COLS = ["domain", "company_domain", "company_website", "website", "url"]


def _to_companies(df: pd.DataFrame) -> list:
    """Turn an uploaded dataframe into the list-of-dicts the service expects."""
    df = df.fillna("")
    return df.to_dict(orient="records")


def render(user: dict):
    role = user.get("role", "researcher")
    org_id = user.get("org_id", 1)

    if role not in ALLOWED_ROLES:
        st.error("You don't have permission to access this page.")
        return

    from core import icons
    st.markdown(icons.header("scoring", "AI Scoring"), unsafe_allow_html=True)
    st.markdown(
        "Score any crawled company list against your own qualification criteria "
        "using the AI model of your choice. Define the criteria, generate a scoring "
        "batch, run it through your preferred model, and import the ranked results — "
        "no API keys stored and no per-scoring cost."
    )

    # ── Step 1: paste the scoring guide ───────────────────────────────────────
    st.markdown("### 1. Paste your scoring guide")
    st.caption(
        "Define how companies should be qualified. You can draft this with your "
        "preferred AI model using a prompt like:\n\n"
        "> *“I'm prospecting [type of company] for [purpose]. Write a scoring "
        "guide with: a hard gate (must-haves to qualify at all), what should raise "
        "the score, and disqualifiers that should drop it to near zero.”*\n\n"
        "The guide is applied verbatim, so you retain full control over how "
        "companies are judged."
    )
    guide = st.text_area(
        "Your scoring guide",
        height=200,
        key="scoring_guide",
        placeholder="e.g. GATE: must do real microbiome sequencing/wet-lab work.\n"
                    "RAISE: clinical reports, reimbursement, explicit software/analytics need.\n"
                    "DISQUALIFY: consumer supplements, cosmetics, already has heavy in-house bioinformatics.",
    )

    # ── Step 2: provide the crawled data ──────────────────────────────────────
    st.markdown("### 2. Upload the crawled company data")
    st.caption(
        "Upload the CSV from the crawler. It needs `company_name`, a domain/website "
        "column, and a `crawled_text` column (the gathered site text the scoring is "
        "judged on). `industry` is optional and helps flag contradictions."
    )
    crawled = st.file_uploader("Crawled companies CSV", type=["csv"], key="scoring_crawled")

    if not crawled:
        st.info("Paste your guide above and upload a crawled CSV to continue.")
        return

    try:
        df = pd.read_csv(crawled)
    except Exception as e:
        st.error(f"Could not read CSV: {e}")
        return

    if "company_name" not in {c.lower() for c in df.columns} and "name" not in {c.lower() for c in df.columns}:
        st.error("CSV needs a `company_name` (or `name`) column.")
        return
    has_text = any(c.lower() in ("crawled_text", "text_sample", "description") for c in df.columns)
    if not has_text:
        st.warning(
            "No `crawled_text` column found — scores would be based only on names/labels, "
            "which defeats the purpose. Run the crawler first so each company has gathered "
            "site text."
        )

    companies = _to_companies(df)
    st.success(f"Loaded **{len(companies)}** companies.")

    # ── Step 3: generate the bundle for the external model ────────────────────
    st.markdown("### 3. Generate the scoring batch")
    if not guide.strip():
        st.info("Paste your scoring guide in step 1 to enable this.")
    else:
        if st.button("Generate scoring bundle", type="primary"):
            out_dir = tempfile.mkdtemp(prefix="dashin_scoring_")
            csv_path, prompt_path = ss.export_from_guide(guide, companies, out_dir, label="batch")
            with open(prompt_path, "r", encoding="utf-8") as f:
                st.session_state["scoring_prompt_txt"] = f.read()
            with open(csv_path, "rb") as f:
                st.session_state["scoring_csv_bytes"] = f.read()

        if st.session_state.get("scoring_prompt_txt"):
            st.markdown(
                "**How to run it:**\n"
                "1. Download both files below.\n"
                "2. Open your AI model (ChatGPT, Claude, Gemini, …), paste the **prompt**, "
                "and attach the **data CSV**.\n"
                "3. The model returns a CSV with `domain, score, tier, rationale, "
                "best_contact, contradiction`. Save it.\n"
                "4. Import that scored CSV in step 4."
            )
            c1, c2 = st.columns(2)
            with c1:
                st.download_button("Prompt (paste into the model)",
                                   st.session_state["scoring_prompt_txt"],
                                   file_name="scoring_prompt.txt", mime="text/plain")
            with c2:
                st.download_button("Data CSV (attach to the model)",
                                   st.session_state["scoring_csv_bytes"],
                                   file_name="scoring_input.csv", mime="text/csv")

    # ── Step 4: re-import the scores ──────────────────────────────────────────
    st.markdown("### 4. Import the scored results")
    scored = st.file_uploader("Scored CSV (from your model)", type=["csv"], key="scoring_scored")
    if scored:
        tmp = os.path.join(tempfile.gettempdir(), "dashin_scored_upload.csv")
        with open(tmp, "wb") as f:
            f.write(scored.getbuffer())
        try:
            results = ss.import_scored_csv(tmp, companies=companies)
        except Exception as e:
            st.error(f"Could not read the scored CSV: {e}")
            return

        if not results:
            st.warning("No rows matched. Make sure the scored CSV kept the `domain` column.")
            return

        res_df = pd.DataFrame(results).sort_values("score", ascending=False)

        # The validation disclaimer the client asked for — front and centre.
        st.warning(
            "**These scores were produced by an AI and may contain mistakes.** "
            "Treat this as a first-pass ranking, not a final verdict — give the top "
            "tiers (and anything flagged **not_site_verified** or **contradiction**) "
            "a second validation pass before acting on them."
        )

        unverified = int(res_df["not_site_verified"].sum()) if "not_site_verified" in res_df else 0
        contradictions = int(res_df["contradiction"].sum()) if "contradiction" in res_df else 0
        a, b, c = st.columns(3)
        a.metric("Scored", len(res_df))
        b.metric("Not site-verified", unverified)
        c.metric("Contradictions", contradictions)

        show_cols = [col for col in
                     ["company_name", "domain", "score", "tier", "rationale",
                      "best_contact", "contradiction", "not_site_verified"]
                     if col in res_df.columns]
        st.dataframe(res_df[show_cols], use_container_width=True, hide_index=True)

        st.download_button(
            "Download scored + ranked CSV",
            res_df[show_cols].to_csv(index=False).encode("utf-8-sig"),
            file_name="scored_ranked.csv", mime="text/csv")
