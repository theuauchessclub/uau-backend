import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { createClient } from "npm:@supabase/supabase-js@2";
import { getPayPalAccessToken, verifyPayPalWebhook } from "../_shared/paypal.ts";

serve(async (req) => {
  if (req.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }

  try {
    const raw = await req.text();
    const body = JSON.parse(raw);

    const accessToken = await getPayPalAccessToken();
    const verification = await verifyPayPalWebhook(req.headers, body, accessToken);

    if (verification.verification_status !== "SUCCESS") {
      return new Response("Invalid webhook signature", { status: 400 });
    }

    const supabase = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    );

    const eventType = body.event_type as string | undefined;

    if (eventType === "PAYMENT.CAPTURE.COMPLETED") {
      const customId =
        body?.resource?.supplementary_data?.related_ids?.order_id
          ? null
          : null;

      const orderId =
        body?.resource?.supplementary_data?.related_ids?.order_id ??
        body?.resource?.links?.find?.((l: { rel: string }) => l.rel === "up")?.href?.split("/")?.pop?.() ??
        null;

      const captureId = body?.resource?.id ?? null;

      if (!orderId) {
        return new Response("No order id found", { status: 200 });
      }

      const { data: rows, error: findErr } = await supabase
        .from("bookings")
        .select("booking_group_id, student_name, parent_name, parent_email, email, booking_date, booking_time, session_type, total_cost")
        .eq("paypal_order_id", orderId);

      if (findErr) throw findErr;
      if (!rows || rows.length === 0) {
        return new Response("No bookings found for order", { status: 200 });
      }

      const bookingGroupId = rows[0].booking_group_id;

      await supabase
        .from("bookings")
        .update({
          payment_status: "confirmed",
          paypal_capture_id: captureId,
          paid_at: new Date().toISOString(),
          status: "confirmed",
        })
        .eq("booking_group_id", bookingGroupId);

      const appsScriptUrl = Deno.env.get("GOOGLE_APPS_SCRIPT_WEBHOOK_URL");
      if (appsScriptUrl) {
        const payload = {
          source: "paypal-webhook",
          booking_group_id: bookingGroupId,
          paypal_order_id: orderId,
          paypal_capture_id: captureId,
          parent_name: rows[0].parent_name,
          student_name: rows[0].student_name,
          email: rows[0].parent_email ?? rows[0].email,
          sessions: rows.map((r) => ({
            booking_date: r.booking_date,
            booking_time: r.booking_time,
            session_type: r.session_type,
            total_cost: r.total_cost,
          })),
        };

        const automationResp = await fetch(appsScriptUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });

        const automationText = await automationResp.text();

        await supabase
          .from("bookings")
          .update({
            invoice_draft_status: automationResp.ok ? "requested" : `failed:${automationText.slice(0, 120)}`,
            calendar_event_status: automationResp.ok ? "requested" : `failed:${automationText.slice(0, 120)}`,
          })
          .eq("booking_group_id", bookingGroupId);
      }
    }

    return new Response("ok", { status: 200 });
  } catch (err) {
    return new Response(
      err instanceof Error ? err.message : String(err),
      { status: 500 },
    );
  }
});
