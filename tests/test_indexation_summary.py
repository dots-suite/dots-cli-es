from elasticsearch import BadRequestError

from dots_es.cli import (
    error_reason,
    format_indexation_summary,
    has_failures,
    new_indexation_counts,
    record_failure,
)

DURATIONS = {"documents": 0.07, "fragments": 2.6, "total": 2.7}
STRICT = {
    "type": "strict_dynamic_mapping_exception",
    "reason": "[1:59] mapping set to strict, dynamic introduction of [corpus_order] within [_doc] is not allowed",
}


class FakeMeta:
    status = 400


def test_error_reason_adds_up_refusals_differing_by_position():
    other = dict(STRICT, reason=STRICT["reason"].replace("[1:59]", "[1:73]"))

    assert error_reason(STRICT) == error_reason(other)
    assert error_reason(STRICT).startswith("strict_dynamic_mapping_exception: mapping set to strict")


def test_error_reason_reads_the_body_of_an_api_error():
    error = BadRequestError("strict", FakeMeta(), {"error": STRICT})

    assert error_reason(error) == error_reason(STRICT)


def test_error_reason_of_any_exception():
    assert error_reason(ValueError("boom")) == "ValueError: boom"


def test_a_run_where_everything_is_refused_is_a_failure():
    # The local run of 2026-11-16: cartulaires purged, then every document refused
    counts = new_indexation_counts()
    for _ in range(14308):
        record_failure(counts, "passages", error_reason(STRICT))
    for _ in range(41):
        record_failure(counts, "resources", error_reason(STRICT))

    summary = format_indexation_summary(counts, DURATIONS, [], ["cartulaires"])

    assert has_failures(counts)
    assert "Passages indexés                  : 0 / 14308" in summary
    assert "14349 × strict_dynamic_mapping_exception" in summary
    assert "Champ(s) absent(s) du mapping de l’index : corpus_order." in summary
    assert "update-conf --rebuild" in summary
    assert "❌  ÉCHEC : aucun document indexé" in summary
    assert "cartulaires purgée(s) avant réindexation" in summary
    assert "✅" not in summary


def test_a_partial_run_says_so():
    counts = new_indexation_counts()
    counts["passages"]["indexed"] = 10
    record_failure(counts, "passages", "mapper_parsing_exception: bad date")

    summary = format_indexation_summary(counts, DURATIONS, [], [])

    assert "⚠️  INDEXATION PARTIELLE : 1 documents refusés" in summary
    assert "update-conf" not in summary
    assert "purgée" not in summary


def test_a_clean_run_succeeds():
    counts = new_indexation_counts()
    counts["passages"]["indexed"] = 10
    counts["resources"]["indexed"] = 2

    summary = format_indexation_summary(counts, DURATIONS, [], ["cartulaires"])

    assert not has_failures(counts)
    assert "✅  Indexation réussie" in summary
    assert "refusés" not in summary
    assert "purgée" not in summary
