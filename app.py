import os
import math
import pandas as pd
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, date
from dateutil import tz
import streamlit as st
import plotly.express as px

# ---------- Page ----------
st.set_page_config(page_title="Context Switching Dashboard", layout="wide")
st.title("🖥️ Jira Context Switching Dashboard (Live)")

# ---------- Secrets / Config ----------
JIRA_DOMAIN = st.secrets["jira"]["domain"].rstrip("/")
JIRA_EMAIL  = st.secrets["jira"]["email"]
JIRA_TOKEN  = st.secrets["jira"]["token"]

PROJECT_KEYS = st.secrets["jira"].get("project_keys", ["YTCS", "DS"])
CATEGORY_SOURCE = st.secrets["jira"].get("category_source", "labels").lower()
CUSTOMFIELD_ID = st.secrets["jira"].get("customfield_id", "")
PRIMARY_CATS = [c.upper() for c in st.secrets["jira"].get("categories", ["VL","CS","POC","ClipFlow"])]

# ---------- Sidebar filters ----------
with st.sidebar:
    st.header("Filters")
    # Week scope (defaults to current week)
    week_mode = st.radio("Timeframe", ["This Week", "Last Week"], index=0)
    if week_mode == "This Week":
        jql_time = "updated >= startOfWeek() AND updated <= endOfWeek()"
    else:
        jql_time = "updated >= startOfWeek(-1) AND updated <= endOfWeek(-1)"

    # Project selector (from secrets, but editable)
    project_text = ", ".join(PROJECT_KEYS) if PROJECT_KEYS else ""
    project_text = st.text_input("Project keys (comma-separated)", project_text).strip()
    selected_projects = [p.strip() for p in project_text.split(",") if p.strip()]
    if not selected_projects:
        st.warning("⚠️ Please enter at least one project key.")
        st.stop()

    # Status filter
    st.subheader("Status Filter")
    st.caption("Select which ticket statuses to include in the analysis")
    
    # Default statuses to include
    default_statuses = ["DEV READY", "QA RELEASE", "DONE"]
    available_statuses = st.multiselect(
        "Ticket Statuses",
        options=default_statuses,
        default=default_statuses,
        help="Only tickets in these statuses will be counted"
    )
    
    if not available_statuses:
        st.warning("⚠️ Please select at least one status to include.")
        st.stop()

    # Source of category classification
    st.caption(f"Category source: **{CATEGORY_SOURCE}**"
               + (f" ({CUSTOMFIELD_ID})" if CATEGORY_SOURCE == "customfield" else ""))

# ---------- Helpers ----------
def map_category(issue_fields):
    """
    Decide the category for an issue based on the configured source.
    """
    if CATEGORY_SOURCE == "labels":
        labels = [l.upper() for l in (issue_fields.get("labels") or [])]
        for c in PRIMARY_CATS:
            if c in labels:
                return c
        return "OTHERS"

    elif CATEGORY_SOURCE == "components":
        comps = [c.get("name", "").upper() for c in (issue_fields.get("components") or [])]
        # allow either short codes (VL/CS/POC) or full names containing them
        for c in PRIMARY_CATS:
            if c in comps or any(c in name for name in comps):
                return c
        return "OTHERS"

    elif CATEGORY_SOURCE == "customfield" and CUSTOMFIELD_ID:
        val = issue_fields.get(CUSTOMFIELD_ID)
        # handle different CF types: option dict, string, list
        if isinstance(val, dict):
            name = (val.get("value") or val.get("name") or "").upper()
        elif isinstance(val, list) and val:
            name = (val[0].get("value") if isinstance(val[0], dict) else str(val[0])).upper()
        else:
            name = (str(val) if val is not None else "").upper()
        return name if name in PRIMARY_CATS else "OTHERS"

    else:
        return "OTHERS"

def fetch_issues(project_keys, jql_time, statuses):
    """
    Pull issues via Jira Search API with pagination.
    We rely on 'updated' for the day's bucket (POC). Later we can switch to worklogs.
    """
    jql_projects = " OR ".join([f'project = "{p}"' for p in project_keys])
    
    # Add status filter to JQL
    if statuses:
        status_filter = " OR ".join([f'status = "{status}"' for status in statuses])
        jql = f"({jql_projects}) AND {jql_time} AND ({status_filter})"
    else:
        jql = f"({jql_projects}) AND {jql_time}"

    url = f"{JIRA_DOMAIN}/rest/api/3/search"
    headers = {"Accept": "application/json"}
    auth = HTTPBasicAuth(JIRA_EMAIL, JIRA_TOKEN)

    start_at = 0
    max_results = 100
    all_issues = []

    fields = ["assignee","labels","updated","components","status"]
    if CATEGORY_SOURCE == "customfield" and CUSTOMFIELD_ID:
        fields.append(CUSTOMFIELD_ID)

    while True:
        params = {
            "jql": jql,
            "fields": ",".join(fields),
            "startAt": start_at,
            "maxResults": max_results,
        }
        r = requests.get(url, headers=headers, params=params, auth=auth, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"Jira API error {r.status_code}: {r.text}")

        payload = r.json()
        issues = payload.get("issues", [])
        all_issues.extend(issues)

        total = payload.get("total", 0)
        start_at += len(issues)
        if start_at >= total or not issues:
            break

    return all_issues

def build_dataframe(issues):
    rows = []
    for it in issues:
        key = it.get("key")
        f = it.get("fields", {})
        assignee = "Unassigned"
        if f.get("assignee"):
            assignee = f["assignee"].get("displayName") or f["assignee"].get("name") or "Unassigned"

        category = map_category(f)
        
        # Get status information
        status = "Unknown"
        if f.get("status"):
            status = f["status"].get("name", "Unknown")

        # Use 'updated' (UTC) -> convert to local date for grouping
        updated_raw = f.get("updated")
        # Jira returns ISO string; parse to date
        dt = pd.to_datetime(updated_raw, utc=True, errors="coerce")
        if pd.isna(dt):
            continue
        local_dt = dt.tz_convert(tz.tzlocal())
        day = local_dt.date()

        rows.append({
            "Assignee": assignee, 
            "Ticket": key, 
            "Category": category, 
            "Status": status,
            "Date": day
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # normalize categories
    df["Category"] = df["Category"].fillna("OTHERS").str.upper()
    return df

def compute_summaries(df):
    # Tickets per user per category (week)
    weekly_summary = (
        df.groupby(["Assignee","Category"])
          .size()
          .reset_index(name="Tickets")
    )

    # Distinct categories per user per day
    per_day = (
        df.groupby(["Assignee","Date"])["Category"]
          .nunique()
          .reset_index(name="DistinctCategories")
    )
    per_day["SwitchFlag"] = (per_day["DistinctCategories"] > 1).astype(int)
    switch_summary = (
        per_day.groupby("Assignee")["SwitchFlag"].sum()
        .reset_index(name="ContextSwitchDays")
    )

    # Pivot for nice table
    pivot = (
        weekly_summary.pivot(index="Assignee", columns="Category", values="Tickets")
        .fillna(0)
        .astype(int)
    )
    for c in ["VL","CS","POC","OTHERS"]:
        if c not in pivot.columns:
            pivot[c] = 0
    pivot = pivot[["VL","CS","POC","OTHERS"]]  # consistent order
    pivot["Total"] = pivot.sum(axis=1)

    # merge with switching
    final = pivot.merge(switch_summary, on="Assignee", how="left").fillna({"ContextSwitchDays":0})
    final["ContextSwitchDays"] = final["ContextSwitchDays"].astype(int)

    return weekly_summary, per_day, final

# ---------- Run ----------
try:
    issues = fetch_issues(selected_projects, jql_time, available_statuses)
except Exception as e:
    st.error(str(e))
    st.stop()

df = build_dataframe(issues)
if df.empty:
    st.info("No issues found for the selected week/projects.")
    st.stop()

weekly_summary, per_day, user_summary = compute_summaries(df)

# ---------- UI: Summary table ----------
st.subheader("📊 User Summary (This Week)")
if not user_summary.empty:
    st.dataframe(user_summary, use_container_width=True)
else:
    st.info("No data to display in summary table.")

# ---------- UI: Status Summary ----------
st.subheader("📋 Status Distribution")
if not df.empty:
    status_counts = df["Status"].value_counts()
    col1, col2 = st.columns(2)
    
    with col1:
        st.write("**Tickets by Status:**")
        st.dataframe(status_counts.reset_index().rename(columns={"index": "Status", "Status": "Count"}))
    
    with col2:
        st.write("**Status Breakdown:**")
        fig_pie = px.pie(
            values=status_counts.values, 
            names=status_counts.index, 
            title="Ticket Status Distribution"
        )
        st.plotly_chart(fig_pie, use_container_width=True)
else:
    st.info("No status data available.")

# ---------- UI: Enhanced Heatmap (Assignee × Day -> # distinct categories) ----------
st.subheader("🔥 Enhanced Context Switching Heatmap")

# Add explanation of what the heatmap shows
st.markdown("""
**What this heatmap shows:** Each cell represents how many different categories of work a person touched on a specific day. 
- 🟢 **Green (0-1)**: Focused work - good for productivity
- 🟡 **Yellow (2)**: Moderate context switching - manageable
- 🔴 **Red (3+)**: High context switching - may impact focus
""")

if not per_day.empty:
    # Create enhanced heatmap data - ensure we're only working with dates
    heatmap_data = per_day.pivot(index="Assignee", columns="Date", values="DistinctCategories").fillna(0)
    
    # Ensure sorted columns (dates) and format them nicely
    heatmap_data = heatmap_data.reindex(sorted(heatmap_data.columns), axis=1)
    
    # Format date columns for better readability - ensure we only show dates, not times
    formatted_columns = []
    for col in heatmap_data.columns:
        if hasattr(col, 'strftime'):
            # Format as "Aug 17" for better readability
            formatted_columns.append(col.strftime('%b %d'))
        else:
            # If it's already a string, use as is
            formatted_columns.append(str(col))
    
    heatmap_data.columns = formatted_columns
    
    # Create enhanced heatmap with better colors and annotations
    fig_heat = px.imshow(
        heatmap_data,
        aspect="auto",
        color_continuous_scale="RdYlGn_r",  # Red-Yellow-Green (reversed for better interpretation)
        labels=dict(
            x="Date", 
            y="Assignee", 
            color="Categories Worked On"
        ),
        title="Daily Context Switching by Assignee",
        text_auto=True,  # Show values on cells
    )
    
    # Customize the layout for better readability
    fig_heat.update_layout(
        title_x=0.5,  # Center the title
        title_font_size=16,
        font=dict(size=10),
        height=500,  # Increase height for better visibility
        margin=dict(l=50, r=50, t=80, b=50)
    )
    
    # Customize the colorbar
    fig_heat.update_coloraxes(
        colorbar_title="Categories",
        colorbar_tickmode='array',
        colorbar_tickvals=[0, 1, 2, 3, 4],
        colorbar_ticktext=['0', '1', '2', '3', '4+'],
        colorbar_len=0.8,
        colorbar_thickness=20
    )
    
    # Customize x and y axes
    fig_heat.update_xaxes(
        title="Date",
        tickangle=45,
        tickfont=dict(size=10)
    )
    
    fig_heat.update_yaxes(
        title="Assignee",
        tickfont=dict(size=10)
    )
    
    # Add hover template for better tooltips
    fig_heat.update_traces(
        hovertemplate="<b>%{y}</b><br>" +
                     "Date: <b>%{x}</b><br>" +
                     "Categories: <b>%{z}</b><br>" +
                     "<extra></extra>"
    )
    
    # Display the enhanced heatmap
    st.plotly_chart(fig_heat, use_container_width=True)
    
else:
    st.info("No heatmap data available.")

# ---------- UI: Stacked bar (tickets per user by category) ----------
st.subheader("📈 Workload Distribution per User")
fig_bar = px.bar(
    weekly_summary, x="Assignee", y="Tickets", color="Category", barmode="stack",
)
st.plotly_chart(fig_bar, use_container_width=True)

# ---------- Notes ----------
with st.expander("ℹ️ How this works"):
    st.markdown("""
- Data source: Jira Search API (`/rest/api/3/search`) with your JQL for the selected week.
- **Status Filter**: Only tickets in the selected statuses (DEV READY, QA RELEASE, DONE) are included in the analysis.
- Category mapping source: **{}**{}.
- Context switch day = a day where a person touched **>1** category.
- This POC uses the **issue's `updated` date** to bucket work by day. For perfect accuracy, we can switch to **worklogs** in the next iteration.
""".format(
        CATEGORY_SOURCE.upper(),
        f" (`{CUSTOMFIELD_ID}`)" if CATEGORY_SOURCE=="customfield" else ""
    ))
