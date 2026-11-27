select
    order_id,
    customer_id,
    order_date,
    to_char(order_date, 'YYYY-MM') as order_month,
    status,
    (status = 'Completed') as is_completed,
    order_total,
    case when status = 'Completed' then order_total else 0.0 end as realized_revenue,
    cost_total,
    case when status = 'Completed' then (order_total - cost_total) else 0.0 end as gross_profit
from {{ ref('stg_orders') }}
