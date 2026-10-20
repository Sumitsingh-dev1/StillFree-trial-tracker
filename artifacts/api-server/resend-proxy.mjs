import { ReplitConnectors } from "@replit/connectors-sdk";

// Python passes the message over stdin, never as shell arguments or credentials.
try {
  let input = "";
  for await (const chunk of process.stdin) input += chunk;
  const { payload, idempotencyKey } = JSON.parse(input);
  const response = await new ReplitConnectors().proxy("resend", "/emails", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: payload,
  });
  if (!response.ok) {
    // Do not expose the provider's response body (recipient data may be included).
    process.stdout.write(JSON.stringify({ error: "Resend rejected the email", status: response.status }));
    process.exitCode = 1;
  } else {
    const result = await response.json();
    if (!result.id) throw new Error("No email ID");
    process.stdout.write(JSON.stringify({ id: result.id }));
  }
} catch {
  process.stdout.write(JSON.stringify({ error: "Email connector request failed" }));
  process.exitCode = 1;
}
