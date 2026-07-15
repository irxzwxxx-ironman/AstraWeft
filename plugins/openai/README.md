# AstraWeft OpenAI Provider

Independent AstraWeft Provider for synchronous text generation through the OpenAI Responses API.
Core owns the HTTP connection pool, host allowlist, timeout, response-size limit, request logging, and
secret storage. This package imports only `astraweft-provider-sdk` and does not use the OpenAI SDK.

The first Phase 4 slice supports model discovery and `text.generate`. It deliberately disables remote
response storage (`store: false`), does not claim idempotency, and does not calculate cost from a baked-in
price table. Tests use recorded-shaped synthetic responses and never make live or paid API calls.

OpenAI is a trademark of OpenAI, L.L.C. This community integration is not an endorsement claim.
