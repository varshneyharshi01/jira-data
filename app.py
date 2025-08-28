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
st.title("🖥️ Jira Dashboard")

# ---------- Leave Management UI ----------
st.subheader("📅 Leave Management")
st.caption("Enter leave days for team members this week to adjust efficiency calculations")

# Initialize leave inputs in session state
if 'leave_inputs' not in st.session_state:
    st.session_state.leave_inputs = {}

# Placeholder for leave inputs - will be populated after data loads
st.info("Leave inputs will be populated after data loads. Please wait for the dashboard to refresh.")

# ---------- Secrets / Config ----------
JIRA_DOMAIN = st.secrets["jira"]["domain"].rstrip("/")
JIRA_EMAIL  = st.secrets["jira"]["email"]
JIRA_TOKEN  = st.secrets["jira"]["token"]

PROJECT_KEYS = st.secrets["jira"].get("project_keys", ["YTCS", "DS"])
CATEGORY_SOURCE = st.secrets["jira"].get("category_source", "labels").lower()
CUSTOMFIELD_ID = st.secrets["jira"].get("customfield_id", "")
PRIMARY_CATS = [c.upper() for c in st.secrets["jira"].get("categories", ["VL","CS","POC","ClipFlow"])]

# Project efficiency rules
PROJECT_RULES = {
    "YTCS": {"expected_points": 15},  # 3/day × 5 days
    "DS": {"expected_points": 5},     # 5 points baseline
}

# ---------- Sprint Helper Function ----------
def fetch_active_sprint(project_key):
    """
    Automatically fetch the active sprint for a specific project.
    """
    try:
        # Fetch board information for the project
        url = f"{JIRA_DOMAIN}/rest/agile/1.0/board"
        headers = {"Accept": "application/json"}
        auth = HTTPBasicAuth(JIRA_EMAIL, JIRA_TOKEN)
        
        # Get all boards and filter by project
        r = requests.get(url, headers=headers, auth=auth, timeout=30)
        if r.status_code == 200:
            all_boards = r.json().get("values", [])
            
            # Find boards that contain our project
            project_boards = []
            for board in all_boards:
                board_name = board.get("name", "")
                if project_key in board_name:
                    project_boards.append(board)
            
            # Fetch active sprint from each board
            for board in project_boards:
                board_id = board.get("id")
                if board_id:
                    # Only fetch active sprint
                    sprint_url = f"{JIRA_DOMAIN}/rest/agile/1.0/board/{board_id}/sprint"
                    sprint_params = {"state": "active"}
                    
                    sprint_r = requests.get(sprint_url, headers=headers, params=sprint_params, auth=auth, timeout=30)
                    if sprint_r.status_code == 200:
                        sprint_data = sprint_r.json().get("values", [])
                        if sprint_data:
                            # Return the first active sprint found
                            active_sprint = sprint_data[0]
                            return {
                                "name": active_sprint.get("name", ""),
                                "project": project_key,
                                "id": active_sprint.get("id"),
                                "state": active_sprint.get("state", ""),
                                "startDate": active_sprint.get("startDate", ""),
                                "endDate": active_sprint.get("endDate", "")
                            }
        
        return None
        
    except Exception as e:
        st.warning(f"Could not fetch active sprint for project {project_key}: {str(e)}")
        return None

def fetch_sprints(project_keys):
    """
    Fetch available sprints for the given project keys from Jira.
    """
    sprints = []
    
    for project_key in project_keys:
        try:
            # Fetch board information for the project
            url = f"{JIRA_DOMAIN}/rest/agile/1.0/board"
            headers = {"Accept": "application/json"}
            auth = HTTPBasicAuth(JIRA_EMAIL, JIRA_TOKEN)
            
            # Get all boards and filter by project
            r = requests.get(url, headers=headers, auth=auth, timeout=30)
            if r.status_code == 200:
                all_boards = r.json().get("values", [])
                
                # Find boards that contain our project
                project_boards = []
                for board in all_boards:
                    board_name = board.get("name", "")
                    if project_key in board_name:
                        project_boards.append(board)
                
                # Fetch sprints from each board
                for board in project_boards:
                    board_id = board.get("id")
                    if board_id:
                        sprint_url = f"{JIRA_DOMAIN}/rest/agile/1.0/board/{board_id}/sprint"
                        sprint_params = {"state": "active,closed,future"}
                        
                        sprint_r = requests.get(sprint_url, headers=headers, params=sprint_params, auth=auth, timeout=30)
                        if sprint_r.status_code == 200:
                            sprint_data = sprint_r.json().get("values", [])
                            for sprint in sprint_data:
                                sprint_name = sprint.get("name", "")
                                if sprint_name:
                                    sprints.append({
                                        "name": sprint_name,
                                        "project": project_key,
                                        "id": sprint.get("id")
                                    })
        except Exception as e:
            st.warning(f"Could not fetch sprints for project {project_key}: {str(e)}")
            continue
    
    return sprints

# ---------- Sidebar filters ----------
with st.sidebar:
    st.header("Filters")
    # Sprint-based analysis - no time frame needed
    st.info("🎯 **Sprint-based Analysis**: Data is automatically filtered by current active sprint(s)")

    # Project filter
    st.subheader("Project Filter")
    st.caption("Select which projects to include in the analysis")
    
    # Create project options including "All projects"
    all_project_options = ["All projects"] + PROJECT_KEYS if PROJECT_KEYS else ["All projects"]
    selected_project_filter = st.selectbox(
        "Select Project",
        options=all_project_options,
        index=0,
        help="Choose 'All projects' to include all configured projects, or select a specific project"
    )
    
    # Determine which projects to use based on selection
    if selected_project_filter == "All projects":
        selected_projects = PROJECT_KEYS if PROJECT_KEYS else []
    else:
        selected_projects = [selected_project_filter]
    
    if not selected_projects:
        st.warning("⚠️ No projects available. Please check your configuration.")
        st.stop()
    
    # Show selected projects
    st.info(f"📋 Selected projects: {', '.join(selected_projects)}")

    # Sprint filter
    st.subheader("Sprint Filter")
    st.caption("Automatically detects active sprint for selected project(s)")
    
    # Automatically fetch active sprint for selected project(s)
    if selected_project_filter == "All projects":
        # For all projects, fetch active sprints from both projects
        st.info("📋 Multiple projects selected - fetching data from current active sprints of both projects")
        
        all_active_sprints = []
        for project in PROJECT_KEYS:
            active_sprint = fetch_active_sprint(project)
            if active_sprint:
                all_active_sprints.append(active_sprint)
                st.success(f"🎯 **{project} Active Sprint:** {active_sprint['name']}")
                
                # Show sprint details
                if active_sprint.get('startDate') and active_sprint.get('endDate'):
                    start_date = active_sprint['startDate'][:10] if active_sprint['startDate'] else "N/A"
                    end_date = active_sprint['endDate'][:10] if active_sprint['endDate'] else "N/A"
                    st.info(f"📅 **{project} Sprint Period:** {start_date} to {end_date}")
                
                st.info(f"📋 **{project} Status:** {active_sprint['state'].title()}")
            else:
                st.warning(f"⚠️ No active sprint found for {project}. Make sure the project has an active scrum board.")
        
        # Set selected_sprints to the list of active sprints
        selected_sprints = all_active_sprints if all_active_sprints else None
        
    else:
        # For specific project, automatically fetch active sprint
        active_sprint = fetch_active_sprint(selected_project_filter)
        
        if active_sprint:
            selected_sprints = active_sprint
            st.success(f"🎯 **Active Sprint Detected:** {active_sprint['name']}")
            
            # Show sprint details
            if active_sprint.get('startDate') and active_sprint.get('endDate'):
                start_date = active_sprint['startDate'][:10] if active_sprint['startDate'] else "N/A"
                end_date = active_sprint['endDate'][:10] if active_sprint['endDate'] else "N/A"
                st.info(f"📅 **Sprint Period:** {start_date} to {end_date}")
            
            st.info(f"📋 **Status:** {active_sprint['state'].title()}")
        else:
            st.warning(f"⚠️ No active sprint found for {selected_project_filter}. Make sure the project has an active scrum board.")
            selected_sprints = None

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

def fetch_issues(project_keys, statuses, sprint_filter=None):
    """
    Pull issues via Jira Search API with pagination.
    Data is filtered by sprint, so no time-based filtering is needed.
    """
    jql_projects = " OR ".join([f'project = "{p}"' for p in project_keys])
    
    # Build JQL query - start with projects
    jql_parts = [f"({jql_projects})"]
    
    # Add status filter to JQL
    if statuses:
        status_filter = " OR ".join([f'status = "{status}"' for status in statuses])
        jql_parts.append(f"({status_filter})")
    
    # Add sprint filter to JQL
    if sprint_filter:
        if isinstance(sprint_filter, list):
            # Multiple sprints (All projects view)
            sprint_names = [sprint.get("name") for sprint in sprint_filter if sprint.get("name")]
            if sprint_names:
                sprint_filter_jql = " OR ".join([f'sprint = "{name}"' for name in sprint_names])
                jql_parts.append(f"({sprint_filter_jql})")
        elif isinstance(sprint_filter, dict) and sprint_filter.get("name"):
            # Single sprint (specific project view)
            jql_parts.append(f'sprint = "{sprint_filter["name"]}"')
    
    jql = " AND ".join(jql_parts)

    url = f"{JIRA_DOMAIN}/rest/api/3/search"
    headers = {"Accept": "application/json"}
    auth = HTTPBasicAuth(JIRA_EMAIL, JIRA_TOKEN)

    start_at = 0
    max_results = 100
    all_issues = []

    fields = ["assignee","labels","updated","components","status"]
    if CATEGORY_SOURCE == "customfield" and CUSTOMFIELD_ID:
        fields.append(CUSTOMFIELD_ID)
    
    # Add story points field for efficiency calculation
    fields.append("customfield_10016")

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

        # Get story points and project
        story_points = f.get("customfield_10016", 0) or 0
        
        # Extract project from ticket key with better logic
        if key:
            if key.startswith("YTCS"):
                project_key = "YTCS"
            elif key.startswith("DS"):
                project_key = "DS"
            else:
                project_key = key[:3]  # Fallback for other projects
        else:
            project_key = "Unknown"
        
        rows.append({
            "Assignee": assignee, 
            "Ticket": key, 
            "Category": category, 
            "Status": status,
            "Date": day,
            "Project": project_key,
            "Story Points": story_points
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
    issues = fetch_issues(selected_projects, available_statuses, selected_sprints)
except Exception as e:
    st.error(str(e))
    st.stop()

df = build_dataframe(issues)
if df.empty:
    st.info("No issues found for the selected week/projects.")
    st.stop()

# ---------- Dynamic Leave Management UI ----------
if not df.empty:
    # Get unique assignees from the data
    unique_assignees = sorted(df["Assignee"].unique())
    
    # st.subheader("📅 Leave Management")
    # st.caption("Enter leave days for team members this week to adjust efficiency calculations")
    
    # Create leave input interface without form
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        st.write("**Team Member**")
    with col2:
        st.write("**Leave Days**")
    with col3:
        st.write("**Actions**")
    
    # Create inputs for each assignee
    for assignee in unique_assignees:
        if assignee != "Unassigned":
            col1, col2, col3 = st.columns([2, 1, 1])
            
            with col1:
                st.write(assignee)
            
            with col2:
                # Get current leave value or default to 0
                current_leave = st.session_state.leave_inputs.get(assignee, 0)
                leave_days = st.number_input(
                    f"Leave for {assignee}",
                    min_value=0,
                    max_value=5,
                    value=current_leave,
                    key=f"leave_{assignee}",
                    label_visibility="collapsed"
                )
                st.session_state.leave_inputs[assignee] = leave_days
            
            with col3:
                if st.button("Reset", key=f"reset_{assignee}"):
                    st.session_state.leave_inputs[assignee] = 0
                    st.rerun()
    
    # Add a separator and update button
    st.divider()
    if st.button("🔄 Update Leave Data & Refresh Dashboard"):
        st.success("Leave data updated! Efficiency calculations will reflect the new availability.")
        st.rerun()
    
    # Show current leave summary
    if any(st.session_state.leave_inputs.values()):
        st.info("**Current Leave Summary:** " + ", ".join([
            f"{assignee}: {days} day(s)" 
            for assignee, days in st.session_state.leave_inputs.items() 
            if days > 0
        ]))

weekly_summary, per_day, user_summary = compute_summaries(df)

# ---------- UI: Summary table ----------
st.subheader("📊 User Summary (Current Sprint)")
if not user_summary.empty:
    st.dataframe(user_summary, use_container_width=True)
else:
    st.info("No data to display in summary table.")

# ---------- UI: Efficiency Summary ----------
st.subheader("⚡ Efficiency Summary")
if not df.empty and "Story Points" in df.columns:
    # Aggregate totals per user with project grouping
    summary_df = (
        df.groupby(["Assignee", "Project"])
          .agg({"Story Points": "sum"})
          .reset_index()
    )
    
    # Calculate adjusted efficiency based on leave days
    efficiency_data = []
    
    for _, row in summary_df.iterrows():
        assignee = row["Assignee"]
        project = row["Project"]
        completed_points = row["Story Points"]
        
        # Get leave days for this assignee
        leave_days = st.session_state.leave_inputs.get(assignee, 0)
        
        # Calculate adjusted expected points based on leave and project rules
        if project == "YTCS":
            # YTCS: (5 - leave_days) * 3 points expected
            working_days = 5 - leave_days
            expected_points = working_days * 3
        elif project == "DS":
            # DS: (5 - leave_days) * 1 point expected
            working_days = 5 - leave_days
            expected_points = working_days * 1
        else:
            # Unknown project, skip but log it
            st.warning(f"⚠️ Unknown project '{project}' for {assignee}, skipping efficiency calculation")
            continue
        
        # Calculate efficiency percentage (avoid division by zero)
        if expected_points > 0:
            efficiency = (completed_points / expected_points) * 100
        else:
            efficiency = 0 if completed_points == 0 else 100  # If no work expected but work done
        
        efficiency_data.append({
            "Assignee": assignee,
            "Project": project,
            "Completed Points": completed_points,
            "Expected Points": expected_points,
            "Efficiency %": round(efficiency, 2),
            "Leave Days": leave_days,
            "Working Days": working_days
        })
    
    # Create efficiency dataframe
    efficiency_df = pd.DataFrame(efficiency_data)
    
    if not efficiency_df.empty:
        # Add sprint information to the display
        if selected_sprints:
            if isinstance(selected_sprints, list):
                # Multiple sprints (All projects view)
                sprint_names = [sprint.get('name') for sprint in selected_sprints if sprint.get('name')]
                if sprint_names:
                    sprint_info = f" - Sprints: {', '.join(sprint_names)}"
                    st.success(f"🎯 **Current Sprints:** {', '.join(sprint_names)}")
                else:
                    sprint_info = ""
            elif isinstance(selected_sprints, dict) and selected_sprints.get('name'):
                # Single sprint (specific project view)
                sprint_info = f" - Sprint: {selected_sprints['name']}"
                st.success(f"🎯 **Current Sprint:** {selected_sprints['name']}")
            else:
                sprint_info = ""
        else:
            sprint_info = ""
        
        # Display efficiency table
        st.dataframe(efficiency_df, use_container_width=True)
        
        # Create efficiency bar chart using plotly
        chart_title = f"Efficiency per User & Project (Adjusted for Leave){sprint_info}"
        fig_efficiency = px.bar(
            efficiency_df, 
            x="Assignee", 
            y="Efficiency %",
            color="Project",
            title=chart_title,
            color_discrete_map={"YTCS": "#1f77b4", "DS": "#ff7f0e"}
        )
        
        # Customize the chart
        fig_efficiency.update_layout(
            title_x=0.5,
            title_font_size=16,
            xaxis_title="Assignee",
            yaxis_title="Efficiency %",
            height=500
        )
        
        # Add value labels on bars
        fig_efficiency.update_traces(
            texttemplate='%{y:.1f}%',
            textposition='outside'
        )
        
        st.plotly_chart(fig_efficiency, use_container_width=True)
        
        # Download CSV button with leave information
        csv_filename = f"efficiency_summary_with_leave_{datetime.now().strftime('%Y%m%d')}.csv"
        csv = efficiency_df.to_csv(index=False)
        st.download_button(
            label="📥 Download Efficiency CSV (with Leave Data)",
            data=csv,
            file_name=csv_filename,
            mime="text/csv"
        )
    else:
        st.info("No efficiency data available for the selected projects.")
else:
    st.info("No efficiency data available. Make sure tickets have story points assigned.")

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
- 🟢 **Green (0)**: Worked on 0 categories
- 🟡 **Yellow (1)**: Worked on 1 category
- 🔴 **Red (2+)**: Worked on 2+ categories
- ⚠️ **Note**: Efficiency calculations are adjusted for leave days to show true productivity
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
    
    # Add hover template for better tooltips with leave information
    fig_heat.update_traces(
        hovertemplate="<b>%{y}</b><br>" +
                     "Date: <b>%{x}</b><br>" +
                     "Categories: <b>%{z}</b><br>" +
                     "Leave Days: <b>" + str(st.session_state.leave_inputs.get("%{y}", 0)) + "</b><br>" +
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
    color_discrete_map={
        "VL": "#e91e63",      # Pink/Magenta
        "CS": "#9c27b0",      # Purple
        "POC": "#3f51b5",     # Indigo
        "ClipFlow": "#00bcd4", # Cyan
        "OTHERS": "#8bc34a"   # Light Green
    }
)
st.plotly_chart(fig_bar, use_container_width=True)

# ---------- Notes ----------
with st.expander("ℹ️ How this works"):
    st.markdown("""
- Data source: Jira Search API (`/rest/api/3/search`) with sprint-based filtering.
- **Project Filter**: Choose between "All projects" (default) or a specific project (YTCS, DS).
- **Automatic Sprint Detection**: 
  - **Specific Project**: Automatically detects and fetches the current active sprint from Jira Agile API
  - **All Projects**: Fetches data from current active sprints of both YTCS and DS projects
- **Status Filter**: Only tickets in the selected statuses (DEV READY, QA RELEASE, DONE) are included in the analysis.
- **Efficiency Calculation**: Based on story points (customfield_10016) completed per user per sprint. 
  - **Only DONE tickets** in the selected sprint(s) are considered for efficiency calculation
  - YTCS: Expected = (5 - leave_days) × 3 points per week
  - DS: Expected = (5 - leave_days) × 1 point per week
  - Efficiency = (Completed Points / Expected Points) × 100
- **Sprint-based Data**: Always shows current sprint data without manual selection.
- **Leave Management**: Enter leave days for team members to adjust efficiency calculations based on actual availability.
- **Adjusted Efficiency**: 
  - YTCS: (5 - leave_days) × 3 points expected
  - DS: (5 - leave_days) × 1 point expected
- Category mapping source: **{}**{}.
- Context switch day = a day where a person touched **>1** category.
- This POC uses the **issue's `updated` date** to bucket work by day within the selected sprint. For perfect accuracy, we can switch to **worklogs** in the next iteration.
""".format(
        CATEGORY_SOURCE.upper(),
        f" (`{CUSTOMFIELD_ID}`)" if CATEGORY_SOURCE=="customfield" else ""
    ))
