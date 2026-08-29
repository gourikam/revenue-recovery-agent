import streamlit as st
import pandas as pd
import plotly.express as px

from app import db, recovery_engine, razorpay_client

st.set_page_config(page_title="AI Revenue Recovery Agent", layout="wide")
db.init_db()

st.title("💰 AI Revenue Recovery Agent")
st.caption("Diagnoses failed payments, decides a bounded intervention, executes it, "
           "and logs everything to an audit trail.")

if razorpay_client.is_configured():
    st.success("🟢 LIVE MODE — connected to Razorpay test-mode API. Real payment links "
               "will be created for retry/reminder actions.")
else:
    st.info("⚪ SIMULATED MODE — no Razorpay keys detected in .env. Outcomes are "
            "modeled with realistic probabilities. Add RAZORPAY_KEY_ID/SECRET to go live.")

with st.expander("📡 Real-time webhook endpoint (advanced — receives live Razorpay events)"):
    st.write(
        "This app also exposes `POST /webhook/razorpay` on the FastAPI backend "
        "(`uvicorn app.main:app`). Point a Razorpay webhook at it (subscribed to "
        "`payment.failed`) and cases will appear below automatically as real "
        "payments fail — no batch button needed. Razorpay can't reach `localhost`, "
        "so expose the port with a tunnel (e.g. `ngrok http 8000`) during local dev, "
        "and set `RAZORPAY_WEBHOOK_SECRET` in `.env` — the endpoint rejects any "
        "payload that isn't cryptographically verified."
    )

col_a, col_b, col_c = st.columns([1, 1, 2])
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
with col_b:
    st.write("")
    st.write("")
    if st.button("🔄 Check real payment links", use_container_width=True,
                 disabled=not razorpay_client.is_configured()):
        with st.spinner("Polling Razorpay for updated payment link statuses..."):
            result = recovery_engine.check_pending_links()
        st.success(f"Checked {result['checked']} pending links, {result['updated']} updated.")
        st.rerun()

cases = db.get_all_cases()

if not cases:
    st.info("No cases yet — click **Run new batch** to generate synthetic failed payments "
            "and run the agent end to end.")
    st.stop()

metrics = recovery_engine.compute_metrics()
n_webhook_cases = len([c for c in cases if c.get("source") == "webhook"])

# --- HEADLINE METRICS ---
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Total at risk", f"₹{metrics['total_amount_at_risk_inr']:,.0f}")
m2.metric("Recovered", f"₹{metrics['total_amount_recovered_inr']:,.0f}")
m3.metric("Recovery rate", f"{metrics['recovery_rate_pct']}%")
m4.metric("Escalated (unresolved)", metrics["n_escalated_unresolved"])
m5.metric("🔴 Live webhook cases", n_webhook_cases,
          help="Cases that arrived via a real Razorpay payment.failed webhook, not the demo batch button.")

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
    df_cause = pd.DataFrame(rows).sort_values("cases", ascending=False).reset_index(drop=True)

    is_hard_stop = df_cause["root_cause"] == "CARD_BLOCKED_FRAUD"
    labels = [
        f"{c} cases · {r}%" + (" (hard stop)" if hs else "")
        for c, r, hs in zip(df_cause["cases"], df_cause["rate_%"], is_hard_stop)
    ]
    colors = ["#ef4444" if hs else "#60a5fa" for hs in is_hard_stop]

    # Built with graph_objects (single trace) rather than px.bar(color=...), which
    # splits data into one trace per color group and can misalign per-bar text
    # arrays across traces -- this keeps each bar's label/color tied to itself.
    import plotly.graph_objects as go
    fig2 = go.Figure(go.Bar(
        x=df_cause["root_cause"], y=df_cause["rate_%"],
        text=labels, textposition="outside",
        marker_color=colors, cliponaxis=False,
    ))
    fig2.update_layout(
        yaxis=dict(title="rate_%", range=[0, max(df_cause["rate_%"].max() * 1.25, 15)]),
        xaxis=dict(title="root_cause"),
        showlegend=False,
        margin=dict(t=40),
    )

    st.plotly_chart(fig2, use_container_width=True)
    st.caption("🔴 Red bar = a hard stopping rule (never auto-recover a fraud-flagged card), "
               "not a data gap.")

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
        if case.get("payment_link_url"):
            st.write("**Real Razorpay payment link:**")
            st.markdown(f"[{case['payment_link_url']}]({case['payment_link_url']})")
        st.write("**Step-by-step audit log:**")
        for entry in log:
            st.text(f"[{entry['timestamp']}] {entry['stage'].upper()}: {entry['detail']}")

st.divider()
with st.expander("📖 All cases (raw table)"):
    st.dataframe(df_cases, use_container_width=True, hide_index=True)