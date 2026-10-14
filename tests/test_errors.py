from j5_macro.protocol import ErrorCode, ErrorInfo, Response, decode, encode

# The contract's error table. Codes are wire-stable: changing this list is a protocol change.
CONTRACT_CODES = {
    "parse_error", "validation_failed", "unknown_step_type", "script_not_found",
    "not_running", "invalid_state", "busy", "conflict", "template_missing",
    "image_not_found", "window_not_found", "still_visible", "expression_error",
    "backend_unavailable", "capability_missing", "exec_failed", "input_denied",
    "internal_error",
}


def test_enum_matches_contract():
    assert {code.value for code in ErrorCode} == CONTRACT_CODES


def test_code_serializes_as_plain_string():
    response = Response(type="run", id="c1", ok=False,
                        error=ErrorInfo(code=ErrorCode.BUSY, message="another run is active"))
    line = encode(response)
    assert '"code":"busy"' in line
    decoded = decode(line)
    assert isinstance(decoded, Response)
    assert decoded.error is not None
    assert decoded.error.code == ErrorCode.BUSY
