"""Tests for signature normalization & severity classification."""

from tracer.signatures import extract_signature, extract_severity


def test_uuids_collapse():
    a = "ERROR Failed to process aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    b = "ERROR Failed to process 11111111-2222-3333-4444-555555555555"
    assert extract_signature(a) == extract_signature(b)


def test_patterned_ids_collapse():
    a = "INFO Request received customer_id=cust_843211"
    b = "INFO Request received customer_id=cust_120554"
    assert extract_signature(a) == extract_signature(b)


def test_numbers_collapse():
    a = "WARN Slow query took 1200ms"
    b = "WARN Slow query took 800ms"
    assert extract_signature(a) == extract_signature(b)


def test_distinct_messages_stay_distinct():
    a = "ERROR NullPointerException at OfferEligibilityService.evaluate:142"
    b = "ERROR Timeout calling segmentation-service after 5000ms"
    assert extract_signature(a) != extract_signature(b)


def test_severity_classification():
    assert extract_severity("ERROR something bad") == "ERROR"
    assert extract_severity("WARN heads up") == "WARN"
    assert extract_severity("INFO routine") == "INFO"
    assert extract_severity("DEBUG verbose") == "DEBUG"


def test_severity_from_exception_hint():
    # No level prefix but obvious exception
    assert extract_severity(
        "java.lang.NullPointerException: customerSegment is null"
    ) == "ERROR"
