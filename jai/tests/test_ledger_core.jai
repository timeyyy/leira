#import "Basic";
#import,file "../src/ledger_core.jai";
#import,file "../src/sha256.jai";

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
