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

Memory_Ledger :: struct {
    events: [..] Ledger_Event;
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

head_hash :: (ledger: *Memory_Ledger) -> string {
    if ledger.events.count == 0 return GENESIS_PARENT_HASH;
    return ledger.events[ledger.events.count - 1].event_hash;
}

append_event_canonical :: (
    ledger: *Memory_Ledger,
    event_id: string,
    event_type: string,
    worker_id: string,
    payload_json: string,
    created_at: string,
    artifact_hash: string = "",
    has_artifact_hash: bool = false,
    operation_id: string = "",
) -> Append_Result {
    if event_type.count == 0 {
        return Append_Result.{
            success = false,
            error_type = "INVALID_EVENT_TYPE",
            message = "event_type must be a non-empty string",
        };
    }

    if worker_id.count == 0 {
        return Append_Result.{
            success = false,
            error_type = "INVALID_WORKER_ID",
            message = "worker_id must be a non-empty string",
        };
    }

    parent := head_hash(ledger);
    hash_input := Event_Hash_Input.{
        parent_event_hash = parent,
        event_type = event_type,
        worker_id = worker_id,
        artifact_hash = artifact_hash,
        has_artifact_hash = has_artifact_hash,
        payload_json = payload_json,
        created_at = created_at,
    };
    event_hash := compute_event_hash(hash_input);

    event := Ledger_Event.{
        id = event_id,
        operation_id = operation_id,
        parent_event_hash = parent,
        event_type = event_type,
        worker_id = worker_id,
        payload_json = payload_json,
        artifact_hash = artifact_hash,
        has_artifact_hash = has_artifact_hash,
        event_hash = event_hash,
        created_at = created_at,
    };
    array_add(*ledger.events, event);

    return Append_Result.{
        success = true,
        event_id = event_id,
        event_hash = event_hash,
        created_at = created_at,
    };
}

validate_chain :: (ledger: *Memory_Ledger) -> Validate_Chain_Result {
    expected_parent_hash := GENESIS_PARENT_HASH;

    for event: ledger.events {
        if event.parent_event_hash != expected_parent_hash {
            return Validate_Chain_Result.{
                success = false,
                events_checked = it_index,
                failed_event_id = event.id,
                error_type = "CHAIN_BROKEN",
                message = "event parent_event_hash did not match expected previous hash",
            };
        }

        hash_input := Event_Hash_Input.{
            parent_event_hash = event.parent_event_hash,
            event_type = event.event_type,
            worker_id = event.worker_id,
            artifact_hash = event.artifact_hash,
            has_artifact_hash = event.has_artifact_hash,
            payload_json = event.payload_json,
            created_at = event.created_at,
        };
        recomputed := compute_event_hash(hash_input);
        hash_matches := recomputed == event.event_hash;
        free(recomputed);

        if !hash_matches {
            return Validate_Chain_Result.{
                success = false,
                events_checked = it_index,
                failed_event_id = event.id,
                error_type = "HASH_MISMATCH",
                message = "stored event_hash did not match recomputed hash",
            };
        }

        expected_parent_hash = event.event_hash;
    }

    return Validate_Chain_Result.{
        success = true,
        events_checked = ledger.events.count,
    };
}
