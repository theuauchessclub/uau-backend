export async function getPayPalAccessToken() {
  const clientId = Deno.env.get("PAYPAL_CLIENT_ID");
  const clientSecret = Deno.env.get("PAYPAL_CLIENT_SECRET");
  const baseUrl = Deno.env.get("PAYPAL_BASE_URL");

  if (!clientId || !clientSecret || !baseUrl) {
    throw new Error("Missing PayPal secrets");
  }

  const auth = btoa(`${clientId}:${clientSecret}`);
  const resp = await fetch(`${baseUrl}/v1/oauth2/token`, {
    method: "POST",
    headers: {
      "Authorization": `Basic ${auth}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: "grant_type=client_credentials",
  });

  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`PayPal token error: ${resp.status} ${text}`);
  }

  const json = await resp.json();
  return json.access_token as string;
}

export async function createPayPalOrder(accessToken: string, amount: string, customId: string, returnUrl?: string, cancelUrl?: string) {
  const baseUrl = Deno.env.get("PAYPAL_BASE_URL")!;
  const resp = await fetch(`${baseUrl}/v2/checkout/orders`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${accessToken}`,
      "Content-Type": "application/json",
      "PayPal-Request-Id": crypto.randomUUID(),
    },
    body: JSON.stringify({
      intent: "CAPTURE",
      purchase_units: [
        {
          custom_id: customId,
          reference_id: customId,
          amount: {
            currency_code: "USD",
            value: amount,
          },
        },
      ],
      application_context: {
        return_url: returnUrl,
        cancel_url: cancelUrl,
        user_action: "PAY_NOW",
      },
    }),
  });

  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`PayPal order create error: ${resp.status} ${text}`);
  }

  return await resp.json();
}

export async function verifyPayPalWebhook(headers: Headers, body: unknown, accessToken: string) {
  const baseUrl = Deno.env.get("PAYPAL_BASE_URL")!;
  const webhookId = Deno.env.get("PAYPAL_WEBHOOK_ID");

  if (!webhookId) throw new Error("Missing PAYPAL_WEBHOOK_ID");

  const resp = await fetch(`${baseUrl}/v1/notifications/verify-webhook-signature`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      auth_algo: headers.get("paypal-auth-algo"),
      cert_url: headers.get("paypal-cert-url"),
      transmission_id: headers.get("paypal-transmission-id"),
      transmission_sig: headers.get("paypal-transmission-sig"),
      transmission_time: headers.get("paypal-transmission-time"),
      webhook_id: webhookId,
      webhook_event: body,
    }),
  });

  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`PayPal webhook verification error: ${resp.status} ${text}`);
  }

  return await resp.json();
}
