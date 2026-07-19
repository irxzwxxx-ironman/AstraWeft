# AstraWeft Custom REST Provider

Declaratively maps AstraWeft/ComfyUI operations to arbitrary public HTTPS JSON APIs. API keys
remain in AstraWeft's credential store; configuration contains only `${secret.<field>}` references.

The Provider supports multiple models and multiple request flows per model, including synchronous
responses and asynchronous submit/poll/cancel APIs. See `schemas.py` for the editable starter
definition and `docs/provider-adapters/Custom_REST_Provider_Design.md` for the format.
