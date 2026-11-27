select
    cast(order_id as integer) as order_id,
    cast(customer_id as integer) as customer_id,
    cast(order_date as date) as order_date,
    trim(status) as status,
    cast(order_total as double precision) as order_total,
    cast(cost_total as double precision) as cost_total
from {{ source('jaffle_raw', 'raw_orders') }}
