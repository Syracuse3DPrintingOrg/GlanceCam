from app.passwords import hash_secret, looks_hashed, verify_secret


def test_hash_roundtrip():
    h = hash_secret("hunter2")
    assert looks_hashed(h)
    assert h.startswith("pbkdf2$")
    assert verify_secret("hunter2", h)
    assert not verify_secret("wrong", h)


def test_empty_password_stays_empty():
    assert hash_secret("") == ""
    assert not looks_hashed("")


def test_empty_inputs_never_verify():
    assert not verify_secret("", hash_secret("x"))
    assert not verify_secret("x", "")


def test_salt_makes_hashes_unique():
    assert hash_secret("same") != hash_secret("same")


def test_verify_rejects_malformed_stored():
    assert not verify_secret("x", "pbkdf2$notanumber$zz$zz")
    assert not verify_secret("x", "pbkdf2$1$1$1$1")  # too many fields


def test_verify_handles_legacy_plaintext():
    # A value that is not our hash format is compared directly (resilience).
    assert verify_secret("plain", "plain")
    assert not verify_secret("plain", "other")
