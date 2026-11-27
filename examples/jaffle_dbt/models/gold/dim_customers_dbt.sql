select
    dbt_scd_id,
    customer_id,
    customer_name,
    customer_tier,
    region,
    dbt_valid_from,
    dbt_valid_to,
    (dbt_valid_to is null) as is_current
from {{ ref('snap_customers_dbt') }}
