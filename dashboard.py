import streamlit as st
import pandas as pd
import plotly.express as px

from app import db, recovery_engine

st.set_page_config(page_title="AI Revenue Recovery Agent", layout="wide")
db.init_db()

st.title("💰 AI Revenue Recovery Agent")
st.caption("Diagnoses failed payments, decides a bounded intervention, executes it, "
           "and logs everything to an audit trail.")

col_a, col_b = st.columns([1, 3])
with col_a:
    n = st.number_input("Batch size", min_value=10, max_value=300, value=75, step=5)
    if st.button("▶ Run new batch", type="primary", use_container_width=True):
        with st.spinner("Generating synthetic failures and running the agent..."):
            from app import synthetic_data
            db.reset_db()
            batch = synthetic_data.generate_batch(n)
            recovery_engine.process_batch(batch)
        st.success(f"Processed {n} cases.")
        st.rerun()

cases = db.get_all_cases()

if not cases:
    st.info("No cases yet — click **Run new batch** to generate synthetic failed payments "
            "and run the agent end to end.")
    st.stop()

metrics = recovery_engine.compute_metrics()

# --- HEADLINE METRICS ---
m1, m2, m3, m4 = st.columns(4)
m1.metric("Total at risk", f"₹{metrics['total_amount_at_risk_inr']:,.0f}")
m2.metric("Recovered", f"₹{metrics['total_amount_recovered_inr']:,.0f}")
m3.metric("Recovery rate", f"{metrics['recovery_rate_pct']}%")
m4.metric("Escalated (unresolved)", metrics["n_escalated_unresolved"])

st.divider()

col1, col2 = st.columns(2)

with col1:
    st.subheader("Outcome breakdown")
    df_cases = pd.DataFrame(cases)
    status_counts = df_cases["execution_status"].value_counts().reset_index()
    status_counts.columns = ["status", "count"]
    fig = px.pie(status_counts, names="status", values="count", hole=0.4)
    st.plotly_chart(fig, use_container_width=True)

with col2:
    st.subheader("Recovery rate by root cause")
    rows = []
    for cause, d in metrics["by_root_cause"].items():
        rate = round(100 * d["recovered"] / d["count"], 1) if d["count"] else 0
        rows.append({"root_cause": cause, "cases": d["count"], "recovered": d["recovered"], "rate_%": rate})
    df_cause = pd.DataFrame(rows).sort_values("cases", ascending=False)
    fig2 = px.bar(df_cause, x="root_cause", y="rate_%", text="cases")
    fig2.update_traces(texttemplate="%{text} cases", textposition="outside")
    st.plotly_chart(fig2, use_container_width=True)

st.divider()

# --- HONEST EXCEPTION LIST: cases the agent could NOT resolve ---
st.subheader("⚠️ Unresolved / escalated cases (honest exception list)")
df_unresolved = df_cases[df_cases["execution_status"].isin(["escalated", "failed_retry"])][
    ["case_id", "customer_name", "amount_inr", "root_cause", "intervention",
     "stopping_reason", "retry_count", "execution_status"]
]
st.dataframe(df_unresolved, use_container_width=True, hide_index=True)

st.divider()

# --- FULL AUDIT TRAIL ---
st.subheader("📋 Full case audit trail")
selected_case = st.selectbox("Inspect a case", options=df_cases["case_id"].tolist())
if selected_case:
    case = db.get_case(selected_case)
    log = db.get_audit_log(selected_case)
    c1, c2 = st.columns([1, 2])
    with c1:
        st.json({
            "customer": case["customer_name"],
            "amount": case["amount_inr"],
            "raw_failure": case["raw_failure_description"],
            "root_cause": case["root_cause"],
            "diagnosis_method": case["diagnosis_method"],
            "intervention": case["intervention"],
            "stopping_reason": case["stopping_reason"],
            "final_status": case["execution_status"],
        })
    with c2:
        st.write("**Message sent to customer:**")
        st.info(case["message_sent"] or "(no customer-facing message for this action)")
        st.write("**Step-by-step audit log:**")
        for entry in log:
            st.text(f"[{entry['timestamp']}] {entry['stage'].upper()}: {entry['detail']}")

st.divider()
with st.expander("📖 All cases (raw table)"):
    st.dataframe(df_cases, use_container_width=True, hide_index=True)
