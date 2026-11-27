select
    cast(customer_id as integer) as customer_id,
    trim(customer_name) as customer_name,
    trim(customer_tier) as customer_tier,
    trim(region) as region,
    cast(updated_at as date) as updated_at
from {{ source('jaffle_raw', 'raw_customers') }}
