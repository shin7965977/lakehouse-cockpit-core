-- Published Gold Fact Table: promoted ONLY after candidate_fct_orders passes all dbt tests
select
    order_id,
    customer_id,
    order_date,
    order_month,
    status,
    is_completed,
    order_total,
    realized_revenue,
    cost_total,
    gross_profit
from {{ ref('candidate_fct_orders') }}
