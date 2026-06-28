#import "Basic";
#import,file "../src/ledger_core.jai";

main :: () {
    print("Running test_ledger_core...\n");

    assert(GENESIS_PARENT_HASH == "0000000000000000000000000000000000000000000000000000000000000000");

    input := Event_Hash_Input.{
        parent_event_hash = GENESIS_PARENT_HASH,
        event_type = "task_started",
        worker_id = "worker-1",
        artifact_hash = "",
        has_artifact_hash = false,
        payload_json = "{\"task\":\"build\"}",
        created_at = "2026-01-01T00:00:00+00:00",
    };

    rendered := render_event_hash_input(input);
    expected := "{\"artifact_hash\":null,\"created_at\":\"2026-01-01T00:00:00+00:00\",\"event_type\":\"task_started\",\"parent_event_hash\":\"0000000000000000000000000000000000000000000000000000000000000000\",\"payload_json\":{\"task\":\"build\"},\"worker_id\":\"worker-1\"}";

    assert(rendered == expected);

    input.has_artifact_hash = true;
    input.artifact_hash = "abc123";
    rendered_with_artifact := render_event_hash_input(input);
    expected_with_artifact := "{\"artifact_hash\":\"abc123\",\"created_at\":\"2026-01-01T00:00:00+00:00\",\"event_type\":\"task_started\",\"parent_event_hash\":\"0000000000000000000000000000000000000000000000000000000000000000\",\"payload_json\":{\"task\":\"build\"},\"worker_id\":\"worker-1\"}";

    assert(rendered_with_artifact == expected_with_artifact);

    print("test_ledger_core passed!\n");
}

