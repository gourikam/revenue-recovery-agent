import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import requests

st.set_page_config(page_title="AI Revenue Recovery Agent", layout="wide")

# --- BACKEND CONNECTION ---
# The dashboard is a pure client of the FastAPI backend's API -- it never
# touches the database directly. This means it can point at a locally running
# backend (uvicorn app.main:app on your machine) OR the live deployed one on
# Render, and see the SAME data either way -- including real webhook cases
# that only exist on whichever backend actually received them.
st.sidebar.header("⚙️ Backend connection")
default_url = st.session_state.get("backend_url", "http://localhost:8000")
backend_url = st.sidebar.text_input(
    "Backend URL", value=default_url,
    help="Point this at your local FastAPI server (http://localhost:8000) or "
         "your deployed Render URL to see real webhook-sourced cases."
).rstrip("/")
st.session_state["backend_url"] = backend_url


def api_get(path: str, timeout=15):
    try:
        r = requests.get(f"{backend_url}{path}", timeout=timeout)
        r.raise_for_status()
        return r.json(), None
    except Exception as e:
        return None, str(e)


def api_post(path: str, params: dict = None, timeout=60):
    try:
        r = requests.post(f"{backend_url}{path}", params=params or {}, timeout=timeout)
        r.raise_for_status()
        return r.json(), None
    except Exception as e:
        return None, str(e)


st.title("💰 AI Revenue Recovery Agent")
st.caption("Diagnoses failed payments, decides a bounded intervention, executes it, "
           "and logs everything to an audit trail.")

status, err = api_get("/status")
if err:
    st.error(
        f"⚠️ Can't reach backend at `{backend_url}` — {err}\n\n"
        f"If this is your Render URL, the free tier sleeps after ~15 min idle and "
        f"can take 30-60s to wake up on the first request. Try refreshing in a moment. "
        f"If it's localhost, make sure `uvicorn app.main:app --port 8000` is running."
    )
    st.stop()

if status.get("razorpay_configured"):
    st.success("🟢 LIVE MODE — this backend is connected to the real Razorpay test-mode API. "
               "Retry/reminder actions create real payment links.")
else:
    st.info("⚪ SIMULATED MODE — this backend has no Razorpay keys configured. Outcomes are "
            "modeled with realistic probabilities.")

with st.expander("📡 Real-time webhook endpoint (advanced — receives live Razorpay events)"):
    st.write(
        f"This backend exposes `POST {backend_url}/webhook/razorpay`. Point a Razorpay "
        f"webhook at it (subscribed to `payment.failed`) and real cases will appear below "
        f"automatically — no batch button needed. If testing locally, Razorpay can't reach "
        f"`localhost`, so use a tunnel (e.g. `zrok`, since `ngrok.io` URLs are blacklisted "
        f"by Razorpay) or point the webhook at your deployed Render URL instead."
    )

col_a, col_b, col_c = st.columns([1, 1, 2])
with col_a:
    n = st.number_input("Batch size", min_value=10, max_value=300, value=75, step=5)
    if st.button("▶ Run new batch", type="primary", use_container_width=True):
        with st.spinner("Generating synthetic failures and running the agent... "
                        "(may take a moment if the backend was asleep)"):
            result, err = api_post("/run-batch", params={"n": int(n), "reset": True})
        if err:
            st.error(f"Failed to run batch: {err}")
        else:
            st.success(f"Processed {result['processed']} cases.")
            st.rerun()
with col_b:
    st.write("")
    st.write("")
    if st.button("🔄 Check real payment links", use_container_width=True,
                 disabled=not status.get("razorpay_configured")):
        with st.spinner("Polling Razorpay for updated payment link statuses..."):
            result, err = api_post("/check-pending-links")
        if err:
            st.error(f"Failed to check pending links: {err}")
        else:
            st.success(f"Checked {result['checked']} pending links, {result['updated']} updated.")
            st.rerun()

cases, err = api_get("/cases")
if err:
    st.error(f"Failed to load cases: {err}")
    st.stop()

if not cases:
    st.info("No cases yet — click **Run new batch** to generate synthetic failed payments, "
            "or trigger a real webhook event, and run the agent end to end.")
    st.stop()

metrics, err = api_get("/metrics")
if err:
    st.error(f"Failed to load metrics: {err}")
    st.stop()

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
df_cases = pd.DataFrame(cases)

with col1:
    st.subheader("Outcome breakdown")
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
    detail, err = api_get(f"/cases/{selected_case}/audit")
    if err:
        st.error(f"Failed to load case detail: {err}")
    else:
        case = detail["case"]
        log = detail["audit_log"]
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
                "source": case.get("source", "batch"),
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