"""Useful provider diagnostics without printing raw exceptions or request data."""


def model_error_result(exc: Exception) -> dict:
    status = None
    names = []
    current = exc
    for _ in range(5):
        if current is None:
            break
        names.append(type(current).__name__.lower())
        for field in ("code", "status_code"):
            value = getattr(current, field, None)
            if type(value) is int and 400 <= value <= 599:
                status = value
                break
        if status is not None:
            break
        current = current.__cause__ or current.__context__
    name = " ".join(names)
    if status == 429 or "ratelimit" in name:
        code, hint = "model_rate_limited", "Provider quota or rate limit reached. Retry later or check API quota."
    elif status in (401, 403) or "authentication" in name or "permissiondenied" in name:
        code, hint = "model_access_denied", "Check the configured provider key and model access."
    elif status == 404 or "modelnotfound" in name:
        code, hint = "model_not_found", "Check LLM_MODEL and its availability for your account."
    elif status == 400 or "invalidrequest" in name:
        code, hint = "model_invalid_request", "Provider rejected the request. Check model and tool-message compatibility."
    elif status in (408, 504) or "timeout" in name:
        code, hint = "model_timeout", "Provider request timed out. Retry your query."
    elif status is not None and status >= 500:
        code, hint = "model_unavailable", "Provider returned a server error. Retry your query later."
    elif "connect" in name:
        code, hint = "model_connection_failed", "Could not connect to the provider. Check network access."
    else:
        code, hint = "model_request_failed", "Model request failed. Check provider configuration or retry your query."
    return {"ok": False, "error": code, "http_status": status, "hint": hint}
