"""UI — dashboard Streamlit + queries DB."""

import dashboard
import dashboard_queries

from dashboard_queries import (
    load_posts,
    load_hook_variants,
    load_latest_analytics,
    load_formula_stats,
    load_format_distribution,
    load_post_metrics_summary,
    post_dir_for,
    render_pdf_pages,
    read_text_file,
)

__all__ = [
    "dashboard",
    "dashboard_queries",
    "load_posts",
    "load_hook_variants",
    "load_latest_analytics",
    "load_formula_stats",
    "load_format_distribution",
    "load_post_metrics_summary",
    "post_dir_for",
    "render_pdf_pages",
    "read_text_file",
]
