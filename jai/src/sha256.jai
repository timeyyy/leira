#import "Basic";
#import "String";

SHA256_HEX_LENGTH :: 64;

sha256_k := u32.[
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
    0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
    0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
    0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

rotr32 :: (x: u32, n: u32) -> u32 {
    return (x >> n) | (x << (32 - n));
}

ch32 :: (x: u32, y: u32, z: u32) -> u32 {
    return (x & y) ^ (~x & z);
}

maj32 :: (x: u32, y: u32, z: u32) -> u32 {
    return (x & y) ^ (x & z) ^ (y & z);
}

big_sigma0 :: (x: u32) -> u32 {
    return rotr32(x, 2) ^ rotr32(x, 13) ^ rotr32(x, 22);
}

big_sigma1 :: (x: u32) -> u32 {
    return rotr32(x, 6) ^ rotr32(x, 11) ^ rotr32(x, 25);
}

small_sigma0 :: (x: u32) -> u32 {
    return rotr32(x, 7) ^ rotr32(x, 18) ^ (x >> 3);
}

small_sigma1 :: (x: u32) -> u32 {
    return rotr32(x, 17) ^ rotr32(x, 19) ^ (x >> 10);
}

read_be_u32 :: (bytes: [] u8, offset: int) -> u32 {
    b0 := cast(u32) bytes[offset];
    b1 := cast(u32) bytes[offset + 1];
    b2 := cast(u32) bytes[offset + 2];
    b3 := cast(u32) bytes[offset + 3];
    return (b0 << 24) | (b1 << 16) | (b2 << 8) | b3;
}

append_hex_u32 :: (builder: *String_Builder, value: u32) {
    print_to_builder(builder, "%", formatInt(value, base=16, minimum_digits=8));
}

sha256_hex_bytes :: (input: [] u8) -> string {
    h0: u32 = 0x6a09e667;
    h1: u32 = 0xbb67ae85;
    h2: u32 = 0x3c6ef372;
    h3: u32 = 0xa54ff53a;
    h4: u32 = 0x510e527f;
    h5: u32 = 0x9b05688c;
    h6: u32 = 0x1f83d9ab;
    h7: u32 = 0x5be0cd19;

    original_bit_count := cast(u64) input.count * 8;
    padded_count := input.count + 1 + 8;
    if padded_count % 64 != 0 {
        padded_count += 64 - (padded_count % 64);
    }

    padded := NewArray(padded_count, u8);
    defer array_free(padded);

    for 0..input.count-1 {
        padded[it] = input[it];
    }
    padded[input.count] = 0x80;

    len_offset := padded_count - 8;
    for 0..7 {
        shift := cast(u64) (7 - it) * 8;
        padded[len_offset + it] = cast(u8) ((original_bit_count >> shift) & 0xff);
    }

    chunk_offset := 0;
    while chunk_offset < padded_count {
        w: [64] u32;
        for 0..15 {
            w[it] = read_be_u32(padded, chunk_offset + it * 4);
        }
        for 16..63 {
            w[it] = small_sigma1(w[it - 2]) + w[it - 7] + small_sigma0(w[it - 15]) + w[it - 16];
        }

        a := h0;
        b := h1;
        c := h2;
        d := h3;
        e := h4;
        f := h5;
        g := h6;
        h := h7;

        for 0..63 {
            t1 := h + big_sigma1(e) + ch32(e, f, g) + sha256_k[it] + w[it];
            t2 := big_sigma0(a) + maj32(a, b, c);
            h = g;
            g = f;
            f = e;
            e = d + t1;
            d = c;
            c = b;
            b = a;
            a = t1 + t2;
        }

        h0 += a;
        h1 += b;
        h2 += c;
        h3 += d;
        h4 += e;
        h5 += f;
        h6 += g;
        h7 += h;

        chunk_offset += 64;
    }

    builder: String_Builder;
    append_hex_u32(*builder, h0);
    append_hex_u32(*builder, h1);
    append_hex_u32(*builder, h2);
    append_hex_u32(*builder, h3);
    append_hex_u32(*builder, h4);
    append_hex_u32(*builder, h5);
    append_hex_u32(*builder, h6);
    append_hex_u32(*builder, h7);
    return builder_to_string(*builder);
}

sha256_hex :: (input: string) -> string {
    bytes: [] u8;
    bytes.data = input.data;
    bytes.count = input.count;
    return sha256_hex_bytes(bytes);
}
