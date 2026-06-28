#import "Basic";
#import "String";
#import,file "sha256.jai";

GENESIS_PARENT_HASH :: "0000000000000000000000000000000000000000000000000000000000000000";

Append_Result :: struct {
    success: bool;
    event_id: string;
    event_hash: string;
    created_at: string;
    error_type: string;
    message: string;
}

Validate_Chain_Result :: struct {
    success: bool;
    events_checked: int;
    failed_event_id: string;
    error_type: string;
    message: string;
}

Ledger_Event :: struct {
    id: string;
    operation_id: string;
    parent_event_hash: string;
    event_type: string;
    worker_id: string;
    payload_json: string;
    artifact_hash: string;
    has_artifact_hash: bool;
    event_hash: string;
    created_at: string;
}

Event_Hash_Input :: struct {
    parent_event_hash: string;
    event_type: string;
    worker_id: string;
    artifact_hash: string;
    has_artifact_hash: bool;
    payload_json: string;
    created_at: string;
}

append_ascii_byte :: (builder: *String_Builder, c: u8) {
    bytes: [1] u8;
    bytes[0] = c;
    s: string;
    s.data = bytes.data;
    s.count = 1;
    append(builder, s);
}

escape_json_ascii_string :: (value: string) -> string {
    // This slice is intentionally limited to already-canonical simple ASCII
    // inputs. It only escapes the characters needed by the Python reference
    // vectors in this milestone: quote and backslash.
    builder: String_Builder;
    for 0..value.count-1 {
        c := value[it];
        if c == #char "\"" {
            append(*builder, "\\\"");
        } else if c == #char "\\" {
            append(*builder, "\\\\");
        } else {
            append_ascii_byte(*builder, c);
        }
    }
    return builder_to_string(*builder);
}

render_json_string_field :: (name: string, value: string) -> string {
    escaped := escape_json_ascii_string(value);
    defer free(escaped);
    return tprint("\"%\":\"%\"", name, escaped);
}

render_json_raw_field :: (name: string, value: string) -> string {
    return tprint("\"%\":%", name, value);
}

render_event_hash_input :: (input: Event_Hash_Input) -> string {
    artifact_field: string;
    if input.has_artifact_hash {
        artifact_field = render_json_string_field("artifact_hash", input.artifact_hash);
    } else {
        artifact_field = render_json_raw_field("artifact_hash", "null");
    }

    // Field order intentionally matches Python compute_event_hash(), whose
    // json.dumps(sort_keys=True) output sorts these object keys lexically.
    return tprint(
        "{%,%,%,%,%,%}",
        artifact_field,
        render_json_string_field("created_at", input.created_at),
        render_json_string_field("event_type", input.event_type),
        render_json_string_field("parent_event_hash", input.parent_event_hash),
        render_json_string_field("payload_json", input.payload_json),
        render_json_string_field("worker_id", input.worker_id),
    );
}

compute_event_hash :: (input: Event_Hash_Input) -> string {
    preimage := render_event_hash_input(input);
    return sha256_hex(preimage);
}
