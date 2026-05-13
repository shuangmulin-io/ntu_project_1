import streamlit as st
import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


# --- PAGE CONFIG ---
st.set_page_config(page_title="SG Job Insights Pro", layout="wide", page_icon="📈")

# --- DATABASE CONNECTION & ENRICHMENT ---
#@st.cache_resource
def get_connection():
    con = duckdb.connect(database=':memory:')
    
    jobs_csv = "temp_sgjobdata_cleaned-20k.csv"
    skills_csv = "temp_job_skills_cleaned-10k.csv"
    
    # 1. Load data with All Varchar to avoid header crashes
    # 2. Enrich data: Outlier filtering + Seniority Classification
    con.execute(f"""
        CREATE OR REPLACE VIEW sg_jobs_raw AS 
        SELECT * FROM read_csv_auto('{jobs_csv}', all_varchar=True);
        
        CREATE OR REPLACE VIEW sg_jobs AS
        SELECT 
            categories,
            title,
            CAST(average_salary AS DOUBLE) as average_salary,
            -- Recommendation: Advanced Seniority Classification
            CASE 
                WHEN lower(title) SIMILAR TO '%(intern|trainee|graduate|fresh|entry|junior)%' 
                     OR lower(positionLevels) LIKE '%fresh%' THEN 'Entry-level'
                WHEN lower(title) SIMILAR TO '%(senior|lead|principal|manager|head|director|architect)%' 
                     OR lower(positionLevels) SIMILAR TO '%(senior|manager)%' THEN 'Senior-level'
                ELSE 'Mid-level'
            END AS seniority
        FROM sg_jobs_raw
        WHERE title != 'title' 
          AND CAST(average_salary AS DOUBLE) BETWEEN 500 AND 30000; -- Recommendation: Outlier filtering
    """)
    
    con.execute(f"CREATE OR REPLACE VIEW job_skills AS SELECT * FROM read_csv_auto('{skills_csv}')")
    con.execute("SET threads TO 1;") 
    return con

con = get_connection()

st.title("🇸🇬 Singapore Career Intelligence Dashboard")
st.markdown("Strategize your next move by identifying high-value, accessible skills.")

# --- DATA LOADING (CATEGORIES) ---
@st.cache_data
def get_unique_categories():
    # Use a subquery to filter out empty/null categories first
    query = """
    WITH cleaned_data AS (
        SELECT categories 
        FROM sg_jobs 
        WHERE categories IS NOT NULL AND categories != ''
    )
    SELECT DISTINCT trim(unnest(regexp_extract_all(categories, 'category:([^},]+)', 1))) as cat
    FROM cleaned_data
    ORDER BY cat
    """
    df = con.execute(query).df()
    return df['cat'].tolist()

selected_cat = st.sidebar.multiselect("Select Industry", options=get_unique_categories())

# --- ANALYTICS ENGINE: EMPLOYABILITY SCORE ---
@st.cache_data
def run_analysis(selected_categories):
    filter_sql = ""
    if selected_categories:
        cats_list = str(selected_categories)
        filter_sql = f"WHERE list_intersect(regexp_extract_all(s.categories, 'category:([^}},]+)', 1), {cats_list}::VARCHAR[]) != []"

    # Join jobs and skills
    raw_df = con.execute(f"""
        SELECT 
            trim(unnest(string_split(js.job_skills, ','))) as skill,
            s.average_salary,
            s.seniority
        FROM sg_jobs s
        INNER JOIN job_skills js 
           ON lower(trim(s.title)) = lower(replace(js.job_keyword, '-', ' '))
        {filter_sql}
    """).df()

    if raw_df.empty: return pd.DataFrame()

    # Recommendation: Calculate Employability Score
    # We group by skill to get stats
    stats = raw_df.groupby('skill').agg(
        postings=('skill', 'count'),
        avg_salary=('average_salary', 'mean'),
        entry_count=('seniority', lambda x: (x == 'Entry-level').sum())
    ).reset_index()

    # Filter out very low frequency skills for better quality
    stats = stats[stats['postings'] > 2]

    # Calculate Entry-level accessibility share
    stats['entry_share'] = stats['entry_count'] / stats['postings']

    # Min-Max Scaling (0 to 1) for the score
    for col in ['postings', 'avg_salary', 'entry_share']:
        stats[f'{col}_norm'] = (stats[col] - stats[col].min()) / (stats[col].max() - stats[col].min())

    # Weighted Score: 50% Demand, 30% Salary, 20% Entry-friendliness
    stats['employability_score'] = (
        stats['postings_norm'] * 0.5 + 
        stats['avg_salary_norm'] * 0.3 + 
        stats['entry_share_norm'] * 0.2
    )

    return stats.sort_values('employability_score', ascending=False), raw_df

# --- RENDER DASHBOARD ---
stats_df, raw_data_df = run_analysis(selected_cat)

if stats_df.empty:
    st.info("No data found for the selected category.")
else:
    # 1. TOP SKILLS BY SCORE
    st.subheader("🔥 Top High-Value Skills (Employability Score)")
    st.caption("Score balances Demand (50%), Salary (30%), and Entry-Level Accessibility (20%)")
    
    top_n = stats_df.head(10)
    fig_score = px.bar(
        top_n, x='employability_score', y='skill', orientation='h',
        color='avg_salary', color_continuous_scale='RdYlGn',
        hover_data=['postings', 'avg_salary', 'entry_share'],
        labels={'employability_score': 'Score', 'avg_salary': 'Avg Salary ($)'}
    )
    # UPDATED HERE: use width="stretch"
    st.plotly_chart(fig_score, width="stretch")

    # 2. CAREER PATHWAY: DEMAND VS SALARY
    st.markdown("---")
    st.subheader("🚀 Career Pathway: Opportunity vs. Reward")
    
    pathway_df = raw_data_df.groupby('seniority').agg(
        postings=('seniority', 'count'),
        median_salary=('average_salary', 'median')
    ).reindex(['Entry-level', 'Mid-level', 'Senior-level']).reset_index()

    # Dual Axis Chart
    fig_pathway = go.Figure()
    fig_pathway.add_trace(go.Bar(
        x=pathway_df['seniority'], y=pathway_df['postings'], 
        name='Number of Postings', marker_color='#2a9d8f', yaxis='y'
    ))
    fig_pathway.add_trace(go.Scatter(
        x=pathway_df['seniority'], y=pathway_df['median_salary'], 
        name='Median Salary', line=dict(color='#e45756', width=4), yaxis='y2'
    ))
    fig_pathway.update_layout(
        yaxis=dict(title="Volume of Jobs"),
        yaxis2=dict(title="Salary (S$)", overlaying='y', side='right'),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    # UPDATED HERE: use width="stretch"
    st.plotly_chart(fig_pathway, width="stretch")