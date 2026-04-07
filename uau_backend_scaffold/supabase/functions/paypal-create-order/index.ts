import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { createClient } from "npm:@supabase/supabase-js@2";
import { createPayPalOrder, getPayPalAccessToken } from "../_shared/paypal.ts";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  try {
    const supabase = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    );

    const { booking_group_id, return_url, cancel_url } = await req.json();

    if (!booking_group_id) {
      return new Response(JSON.stringify({ error: "booking_group_id is required" }), {
        status: 400,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    const { data: bookings, error } = await supabase
      .from("bookings")
      .select("id, booking_group_id, total_cost, payment_status")
      .eq("booking_group_id", booking_group_id);

    if (error) throw error;
    if (!bookings || bookings.length === 0) {
      return new Response(JSON.stringify({ error: "No bookings found for group" }), {
        status: 404,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    const total = bookings.reduce((sum, row) => sum + Number(row.total_cost ?? 0), 0);
    const totalString = total.toFixed(2);

    if (total <= 0) {
      await supabase
        .from("bookings")
        .update({
          payment_status: "confirmed",
          paid_at: new Date().toISOString(),
        })
        .eq("booking_group_id", booking_group_id);

      return new Response(JSON.stringify({
        booking_group_id,
        zeroTotal: true,
        approveUrl: null,
        message: "Booking total is $0. No PayPal order needed.",
      }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    const accessToken = await getPayPalAccessToken();
    const order = await createPayPalOrder(
      accessToken,
      totalString,
      booking_group_id,
      return_url,
      cancel_url,
    );

    const approveUrl = (order.links || []).find((l: { rel: string }) => l.rel === "approve")?.href ?? null;

    await supabase
      .from("bookings")
      .update({
        paypal_order_id: order.id,
        payment_status: "awaiting_payment",
      })
      .eq("booking_group_id", booking_group_id);

    return new Response(JSON.stringify({
      orderId: order.id,
      approveUrl,
      amount: totalString,
      booking_group_id,
    }), {
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  } catch (err) {
    return new Response(JSON.stringify({
      error: err instanceof Error ? err.message : String(err),
    }), {
      status: 500,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }
});
