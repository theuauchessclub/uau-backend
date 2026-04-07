-- Backend checkout migration for The Up And Up Chess Club

create extension if not exists pgcrypto;

alter table public.bookings
  add column if not exists booking_group_id uuid,
  add column if not exists coupon_code text,
  add column if not exists coupon_discount numeric default 0,
  add column if not exists subtotal numeric,
  add column if not exists paypal_order_id text,
  add column if not exists paypal_capture_id text,
  add column if not exists payment_status text default 'pending',
  add column if not exists paid_at timestamptz,
  add column if not exists invoice_draft_status text,
  add column if not exists calendar_event_status text;

create index if not exists idx_bookings_booking_group_id on public.bookings (booking_group_id);
create index if not exists idx_bookings_payment_status on public.bookings (payment_status);
create index if not exists idx_bookings_paypal_order_id on public.bookings (paypal_order_id);

create table if not exists public.coupons (
  id bigint generated always as identity primary key,
  code text unique not null,
  discount_type text not null check (discount_type in ('percent', 'fixed')),
  discount_value numeric not null default 0,
  active boolean not null default true,
  starts_at timestamptz,
  ends_at timestamptz,
  max_redemptions integer,
  redemptions integer not null default 0,
  description text,
  created_at timestamptz not null default now()
);

insert into public.coupons (code, discount_type, discount_value, active, description)
values ('FAMILY', 'percent', 100, true, 'Testing coupon')
on conflict (code) do update
set discount_type = excluded.discount_type,
    discount_value = excluded.discount_value,
    active = excluded.active,
    description = excluded.description;

alter table public.coupons enable row level security;

drop policy if exists "Public can read active coupons" on public.coupons;
create policy "Public can read active coupons"
on public.coupons
for select
to anon
using (
  active = true
  and (starts_at is null or starts_at <= now())
  and (ends_at is null or ends_at >= now())
);

drop policy if exists "Service role manages coupons" on public.coupons;
create policy "Service role manages coupons"
on public.coupons
for all
to service_role
using (true)
with check (true);

-- Optional helper view
create or replace view public.booking_groups as
select
  booking_group_id,
  min(created_at) as created_at,
  min(parent_name) as parent_name,
  min(student_name) as student_name,
  min(coalesce(parent_email, email)) as email,
  min(grade_level) as grade_level,
  min(payment_status) as payment_status,
  min(paypal_order_id) as paypal_order_id,
  sum(coalesce(cost, 0)) as subtotal,
  sum(coalesce(total_cost, 0)) as total,
  array_agg(jsonb_build_object(
    'booking_date', booking_date,
    'booking_hour', booking_hour,
    'booking_time', booking_time,
    'session_type', session_type,
    'cost', cost,
    'total_cost', total_cost
  ) order by booking_date, booking_hour) as items
from public.bookings
where booking_group_id is not null
group by booking_group_id;
