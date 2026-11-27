-- Gold Analytics Mart: Compares Naive SCD Type 1 vs. Point-in-Time SCD Type 2 on H1 2025 Historical Revenue
with scd1 as (
    select
        c.customer_tier,
        round(sum(o.realized_revenue)::numeric, 2) as scd1_naive_latest_tier_rev
    from {{ ref('fct_orders_dbt') }} o
    join {{ ref('dim_customers_dbt') }} c
      on o.customer_id = c.customer_id
     and c.is_current = true
    where o.order_date <= date '2025-06-30'
    group by c.customer_tier
),
scd2 as (
    select
        c.customer_tier,
        round(sum(o.realized_revenue)::numeric, 2) as scd2_point_in_time_rev
    from {{ ref('fct_orders_dbt') }} o
    join {{ ref('dim_customers_dbt') }} c
      on o.customer_id = c.customer_id
     and o.order_date >= c.dbt_valid_from
     and (o.order_date < c.dbt_valid_to or c.dbt_valid_to is null)
    where o.order_date <= date '2025-06-30'
    group by c.customer_tier
)
select
    coalesce(s1.customer_tier, s2.customer_tier) as customer_tier,
    coalesce(s1.scd1_naive_latest_tier_rev, 0)::double precision as scd1_naive_latest_tier_rev,
    coalesce(s2.scd2_point_in_time_rev, 0)::double precision as scd2_point_in_time_rev,
    (coalesce(s1.scd1_naive_latest_tier_rev, 0) - coalesce(s2.scd2_point_in_time_rev, 0))::double precision as historical_distortion
from scd1 s1
full outer join scd2 s2
  on s1.customer_tier = s2.customer_tier
order by customer_tier
