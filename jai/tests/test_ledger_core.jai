#import "Basic";
#import,file "../src/ledger_core.jai";
#import,file "../src/sha256.jai";

make_reference_ledger :: () -> Memory_Ledger {
    ledger: Memory_Ledger;
    append_event_canonical(
        *ledger,
        "event-1",
        "task_started",
        "worker-1",
        "{\"task\":\"build\"}",
        "2026-01-01T00:00:00+00:00",
    );
    append_event_canonical(
        *ledger,
        "event-2",
        "task_finished",
        "worker-1",
        "{\"n\":2}",
        "2026-01-01T00:00:01+00:00",
    );
    append_event_canonical(
        *ledger,
        "event-3",
        "artifact_written",
        "worker-1",
        "{\"path\":\"output.txt\"}",
        "2026-01-01T00:00:02+00:00",
        artifact_hash = "artifact-sha-123",
        has_artifact_hash = true,
    );
    return ledger;
}

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
    expected := "{\"artifact_hash\":null,\"created_at\":\"2026-01-01T00:00:00+00:00\",\"event_type\":\"task_started\",\"parent_event_hash\":\"0000000000000000000000000000000000000000000000000000000000000000\",\"payload_json\":\"{\\\"task\\\":\\\"build\\\"}\",\"worker_id\":\"worker-1\"}";

    assert(rendered == expected);

    event_hash := compute_event_hash(input);
    assert(event_hash == "50df03cc9575ddde71e06e6d054768b6eb7ad81b0cc0ddb00ff27406ea9414b7");
    free(event_hash);

    input.has_artifact_hash = true;
    input.artifact_hash = "abc123";
    rendered_with_artifact := render_event_hash_input(input);
    expected_with_artifact := "{\"artifact_hash\":\"abc123\",\"created_at\":\"2026-01-01T00:00:00+00:00\",\"event_type\":\"task_started\",\"parent_event_hash\":\"0000000000000000000000000000000000000000000000000000000000000000\",\"payload_json\":\"{\\\"task\\\":\\\"build\\\"}\",\"worker_id\":\"worker-1\"}";

    assert(rendered_with_artifact == expected_with_artifact);

    event_hash_with_artifact := compute_event_hash(input);
    assert(event_hash_with_artifact == "f7ba93aa999146112b06e39e3427b4701301c7bf17cbb40d82bc22bebe00359a");
    free(event_hash_with_artifact);

    second_input := Event_Hash_Input.{
        parent_event_hash = "1111111111111111111111111111111111111111111111111111111111111111",
        event_type = "step",
        worker_id = "w",
        artifact_hash = "",
        has_artifact_hash = false,
        payload_json = "{\"i\":1,\"label\":\"step-1\"}",
        created_at = "2026-01-02T03:04:05+00:00",
    };
    second_hash := compute_event_hash(second_input);
    assert(second_hash == "cdb7f440152453b38247ef2bccdac306580bbd7f74f28baf02a02f01176c394c");
    free(second_hash);

    ledger: Memory_Ledger;
    first_append := append_event_canonical(
        *ledger,
        "event-1",
        "task_started",
        "worker-1",
        "{\"task\":\"build\"}",
        "2026-01-01T00:00:00+00:00",
    );
    assert(first_append.success);
    assert(first_append.event_id == "event-1");
    assert(first_append.created_at == "2026-01-01T00:00:00+00:00");
    assert(first_append.event_hash == "50df03cc9575ddde71e06e6d054768b6eb7ad81b0cc0ddb00ff27406ea9414b7");
    assert(ledger.events.count == 1);
    assert(ledger.events[0].parent_event_hash == GENESIS_PARENT_HASH);
    assert(ledger.events[0].event_hash == first_append.event_hash);
    assert(!ledger.events[0].has_artifact_hash);

    second_append := append_event_canonical(
        *ledger,
        "event-2",
        "task_finished",
        "worker-1",
        "{\"n\":2}",
        "2026-01-01T00:00:01+00:00",
    );
    assert(second_append.success);
    assert(second_append.event_hash == "f3fca179ff6764700554af35e193c6e015af7c490a8d80c1b39ce714d828b564");
    assert(ledger.events.count == 2);
    assert(ledger.events[0].id == "event-1");
    assert(ledger.events[1].id == "event-2");
    assert(ledger.events[1].parent_event_hash == first_append.event_hash);
    assert(ledger.events[1].parent_event_hash == ledger.events[0].event_hash);

    third_append := append_event_canonical(
        *ledger,
        "event-3",
        "artifact_written",
        "worker-1",
        "{\"path\":\"output.txt\"}",
        "2026-01-01T00:00:02+00:00",
        artifact_hash = "artifact-sha-123",
        has_artifact_hash = true,
    );
    assert(third_append.success);
    assert(third_append.event_hash == "aea0024b8899fd96c8d495c275896848798381e13798a06a916017e9adb14a41");
    assert(ledger.events.count == 3);
    assert(ledger.events[2].parent_event_hash == second_append.event_hash);
    assert(ledger.events[2].has_artifact_hash);
    assert(ledger.events[2].artifact_hash == "artifact-sha-123");

    empty_ledger: Memory_Ledger;
    empty_validation := validate_chain(*empty_ledger);
    assert(empty_validation.success);
    assert(empty_validation.events_checked == 0);

    valid_validation := validate_chain(*ledger);
    assert(valid_validation.success);
    assert(valid_validation.events_checked == 3);

    wrong_genesis := make_reference_ledger();
    wrong_genesis.events[0].parent_event_hash = "bad-parent";
    wrong_genesis_validation := validate_chain(*wrong_genesis);
    assert(!wrong_genesis_validation.success);
    assert(wrong_genesis_validation.error_type == "CHAIN_BROKEN");
    assert(wrong_genesis_validation.failed_event_id == "event-1");
    assert(wrong_genesis_validation.events_checked == 0);

    broken_second_parent := make_reference_ledger();
    broken_second_parent.events[1].parent_event_hash = GENESIS_PARENT_HASH;
    broken_second_parent_validation := validate_chain(*broken_second_parent);
    assert(!broken_second_parent_validation.success);
    assert(broken_second_parent_validation.error_type == "CHAIN_BROKEN");
    assert(broken_second_parent_validation.failed_event_id == "event-2");
    assert(broken_second_parent_validation.events_checked == 1);

    tampered_payload := make_reference_ledger();
    tampered_payload.events[0].payload_json = "{\"task\":\"tampered\"}";
    tampered_payload_validation := validate_chain(*tampered_payload);
    assert(!tampered_payload_validation.success);
    assert(tampered_payload_validation.error_type == "HASH_MISMATCH");
    assert(tampered_payload_validation.failed_event_id == "event-1");
    assert(tampered_payload_validation.events_checked == 0);

    tampered_artifact := make_reference_ledger();
    tampered_artifact.events[2].artifact_hash = "artifact-sha-tampered";
    tampered_artifact_validation := validate_chain(*tampered_artifact);
    assert(!tampered_artifact_validation.success);
    assert(tampered_artifact_validation.error_type == "HASH_MISMATCH");
    assert(tampered_artifact_validation.failed_event_id == "event-3");
    assert(tampered_artifact_validation.events_checked == 2);

    tampered_event_hash := make_reference_ledger();
    tampered_event_hash.events[1].event_hash = "bad-hash";
    tampered_event_hash_validation := validate_chain(*tampered_event_hash);
    assert(!tampered_event_hash_validation.success);
    assert(tampered_event_hash_validation.error_type == "HASH_MISMATCH");
    assert(tampered_event_hash_validation.failed_event_id == "event-2");
    assert(tampered_event_hash_validation.events_checked == 1);

    reordered := make_reference_ledger();
    first_event := reordered.events[0];
    reordered.events[0] = reordered.events[1];
    reordered.events[1] = first_event;
    reordered_validation := validate_chain(*reordered);
    assert(!reordered_validation.success);
    assert(reordered_validation.error_type == "CHAIN_BROKEN");
    assert(reordered_validation.failed_event_id == "event-2");
    assert(reordered_validation.events_checked == 0);

    invalid_event := append_event_canonical(
        *ledger,
        "event-4",
        "",
        "worker-1",
        "{\"task\":\"build\"}",
        "2026-01-01T00:00:03+00:00",
    );
    assert(!invalid_event.success);
    assert(invalid_event.error_type == "INVALID_EVENT_TYPE");
    assert(ledger.events.count == 3);

    invalid_worker := append_event_canonical(
        *ledger,
        "event-4",
        "task_started",
        "",
        "{\"task\":\"build\"}",
        "2026-01-01T00:00:03+00:00",
    );
    assert(!invalid_worker.success);
    assert(invalid_worker.error_type == "INVALID_WORKER_ID");
    assert(ledger.events.count == 3);

    empty_hash := sha256_hex("");
    assert(empty_hash == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    assert(empty_hash.count == SHA256_HEX_LENGTH);
    free(empty_hash);

    hello_hash := sha256_hex("hello");
    assert(hello_hash == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824");
    free(hello_hash);

    abc_hash := sha256_hex("abc");
    assert(abc_hash == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    free(abc_hash);

    long_hash := sha256_hex("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
    assert(long_hash == "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb");
    free(long_hash);

    print("test_ledger_core passed!\n");
}
