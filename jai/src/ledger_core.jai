#import "Basic";
#import "String";

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

render_json_string_field :: (name: string, value: string) -> string {
    // Milestone 1 assumes prevalidated simple ASCII field values. The Python
    // reference still owns full JSON escaping and Unicode normalization.
    return tprint("\"%\":\"%\"", name, value);
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
        render_json_raw_field("payload_json", input.payload_json),
        render_json_string_field("worker_id", input.worker_id),
    );
}

