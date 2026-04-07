# Up And Up Chess Club backend scaffold

This package gives you a cleaner backend architecture for:

- creating PayPal checkout orders from your site
- verifying PayPal webhooks on the backend
- marking bookings paid in Supabase
- triggering post-payment automation after verified payment
- applying coupon codes from Supabase
- creating a Gmail draft and Google Calendar event through Google Apps Script

## Recommended flow

1. Frontend saves the booking rows in Supabase with `payment_status = 'pending'`.
2. Frontend calls the `paypal-create-order` Edge Function with a `booking_group_id`.
3. Edge Function creates a PayPal order with Orders v2 and returns the approve link. PayPal Orders v2 uses `POST /v2/checkout/orders`. citeturn596453search1
4. User pays on PayPal.
5. PayPal sends a webhook to `paypal-webhook`.
6. `paypal-webhook` verifies the signature using PayPal's webhook verification endpoint, updates Supabase using the service role key, and sends the payload to your Apps Script webhook. PayPal provides a verify-webhook-signature endpoint for this workflow. citeturn596453search1
7. Apps Script creates:
   - a Gmail draft in your inbox
   - a Google Calendar event on your calendar

## Files

- `supabase/migrations/20260406_backend_checkout.sql`
- `supabase/functions/paypal-create-order/index.ts`
- `supabase/functions/paypal-webhook/index.ts`
- `supabase/functions/_shared/paypal.ts`
- `google-apps-script/booking_automation.gs`

## Supabase secrets to set

Edge Functions can read secrets through environment variables, including `SUPABASE_SERVICE_ROLE_KEY`, which Supabase documents for server-side use. citeturn596453search0turn596453search2

Set these in Supabase:
- `PAYPAL_CLIENT_ID`
- `PAYPAL_CLIENT_SECRET`
- `PAYPAL_BASE_URL` (`https://api-m.sandbox.paypal.com` for sandbox, `https://api-m.paypal.com` for live)
- `PAYPAL_WEBHOOK_ID`
- `GOOGLE_APPS_SCRIPT_WEBHOOK_URL`

Supabase already exposes:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

## Deploy steps

1. Run the SQL migration in Supabase SQL Editor.
2. Create both Edge Functions:
   - `paypal-create-order`
   - `paypal-webhook`
3. Add the secrets above.
4. Deploy functions with Supabase CLI:
   - `supabase functions deploy paypal-create-order`
   - `supabase functions deploy paypal-webhook`
5. Paste the Apps Script file into script.google.com, deploy as a web app, and copy its URL into `GOOGLE_APPS_SCRIPT_WEBHOOK_URL`.
6. In PayPal developer dashboard:
   - create a REST app
   - add webhook URL pointing to your deployed `paypal-webhook`
   - subscribe to at least:
     - `PAYMENT.CAPTURE.COMPLETED`
     - `CHECKOUT.ORDER.APPROVED`
7. Update the frontend so the PayPal button calls `paypal-create-order` instead of opening a static PayPal URL.

## Frontend integration notes

After you save booking rows in Supabase:
- create a `booking_group_id` (UUID)
- store it on every row in the cart
- call the Edge Function with:
  - `booking_group_id`
  - optional `return_url`
  - optional `cancel_url`

The function will return:
- `orderId`
- `approveUrl`

Then redirect the browser to `approveUrl`.

## Coupon testing

The migration creates a `coupons` table and inserts:
- `FAMILY` = 100% off

That lets you test a no-payment path safely.

## Important note about Gmail + Calendar

This scaffold uses **Google Apps Script** because it is the most practical way to create a Gmail draft in *your own inbox* and a calendar event on *your own calendar* without building a full Google OAuth app first.
