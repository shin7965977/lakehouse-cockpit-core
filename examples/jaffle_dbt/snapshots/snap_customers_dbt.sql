{% snapshot snap_customers_dbt %}

{{
    config(
      target_schema='dbt_snapshots',
      unique_key='customer_id',
      strategy='check',
      check_cols=['customer_tier', 'region'],
      invalidate_hard_deletes=True
    )
}}

select
    customer_id,
    customer_name,
    customer_tier,
    region,
    updated_at
from {{ ref('stg_customers') }}

{% endsnapshot %}
