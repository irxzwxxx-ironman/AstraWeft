# Provider plugins

Built-in Provider plugins live as separately testable packages in this directory and depend only on
the public Provider SDK.

Implementation order:

1. Mock Provider covering synchronous, asynchronous, failure, rate-limit, cancel, and recovery paths.
2. One real provider with a simple completion path.
3. One real asynchronous video provider.
4. Additional providers after the contract has proven generic.

The Mock Provider, OpenAI Responses Provider, and Runway asynchronous video Provider are implemented
as independent packages. Together they cover synchronous completion, accepted tasks, polling,
cancellation, restart recovery, model discovery, authentication, rate limits, unavailable/timeout/
protocol failures, usage mapping, and Core-owned ephemeral URL materialization. Core and presentation
code do not branch on their plugin IDs.
