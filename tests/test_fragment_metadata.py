from types import SimpleNamespace

from dots_es.api.search_fields import build_filtered_temporal_metadata
from dots_es.cli import build_fragment_params, extract_fragment_metadata


# Shape of a cartulaire act as returned by ThunderDots 0.1.7 (MAUB-AB-01_0001).
CARTULAIRE_FRAGMENT = {
    "id": "MAUB-AB-01_0001",
    "head": "1 (Mars 1241)",
    "metadata": {
        "dublincore": {
            "bibliographicCitation": "Cartulaire de l’abbaye de Maubuisson, p. 1-2.",
            "date": "1241-03",
            "language": "lat",
            "title": "1 (Mars 1241)",
            "type": "act",
        },
        "extensions": {
            "@context": {"schema": "https://schema.org/", "dateCreated": "schema:dateCreated"},
            "creditText": ["Cartulaire de l’abbaye de Maubuisson, p. 1-2."],
            "dateCreated": "1241-03",
            "inLanguage": "lat",
            "name": "1 (Mars 1241)",
        },
    },
    "temporal": {
        "dublincore.date": "1241-03",
        "dublincore.date_start": 1241,
        "dublincore.date_start_iso": "1241-03-01",
        "dublincore.date_end": 1241,
        "dublincore.date_end_iso": "1241-03-31",
        "extensions.@context.dateCreated": "schema:dateCreated",
        "extensions.dateCreated": "1241-03",
        "extensions.dateCreated_start": 1241,
        "extensions.dateCreated_start_iso": "1241-03-01",
        "extensions.dateCreated_end": 1241,
        "extensions.dateCreated_end_iso": "1241-03-31",
    },
}


def test_fragment_metadata_follows_search_fields_contract():
    metadata = extract_fragment_metadata(CARTULAIRE_FRAGMENT)

    assert metadata == {
        "dublincore": {"date": "1241-03", "language": "lat", "title": "1 (Mars 1241)"},
        "extensions": {"dateCreated": "1241-03", "inLanguage": "lat", "name": "1 (Mars 1241)"},
    }


def test_fragment_metadata_keeps_tei_dates():
    fragment = {"metadata": {"dublincore": {}, "tei": {"date": "1173-05-02"}}}

    assert extract_fragment_metadata(fragment)["tei"] == {"date": "1173-05-02"}


def test_fragment_without_metadata():
    assert extract_fragment_metadata({"id": "x"}) == {"dublincore": {}, "extensions": {}}


def test_fragment_temporal_keeps_declared_bounds_only():
    assert build_filtered_temporal_metadata(CARTULAIRE_FRAGMENT["temporal"]) == {
        "temporal.dublincore.date_start": 1241,
        "temporal.dublincore.date_start_iso": "1241-03-01",
        "temporal.dublincore.date_end": 1241,
        "temporal.dublincore.date_end_iso": "1241-03-31",
        "temporal.extensions.dateCreated_start": 1241,
        "temporal.extensions.dateCreated_start_iso": "1241-03-01",
        "temporal.extensions.dateCreated_end": 1241,
        "temporal.extensions.dateCreated_end_iso": "1241-03-31",
    }


def test_fragment_temporal_keeps_tei_date():
    # ThunderDots always stores the first TEI match under "tei.date",
    # whatever FRAGMENT_TEMPORAL_XPATH selects; "tei.dates" is not a range.
    temporal = {
        "tei.date": "1173-05-02",
        "tei.date_start": 1173,
        "tei.date_start_iso": "1173-05-02",
        "tei.date_end": 1173,
        "tei.date_end_iso": "1173-05-02",
    }

    assert build_filtered_temporal_metadata(temporal) == {
        "temporal.tei.date_start": 1173,
        "temporal.tei.date_start_iso": "1173-05-02",
        "temporal.tei.date_end": 1173,
        "temporal.tei.date_end_iso": "1173-05-02",
    }


def test_year_zero_is_not_indexed():
    # Obituary entry "0000-11-21": a day of the year, not a dated act.
    obituary = {
        "extensions.dateCreated": "0000-11-21",
        "extensions.dateCreated_start": 0,
        "extensions.dateCreated_start_iso": "0",
        "extensions.dateCreated_end": 0,
        "extensions.dateCreated_end_iso": "0",
    }

    assert build_filtered_temporal_metadata(obituary) == {}


def test_fragment_params_without_temporal_xpath():
    app = SimpleNamespace(config={"FRAGMENT_TEMPORAL_XPATH": ""})

    assert build_fragment_params(app) == {
        "metadata_dublincore": None,
        "metadata_extensions": None,
    }


def test_fragment_params_with_temporal_xpath():
    app = SimpleNamespace(config={"FRAGMENT_TEMPORAL_XPATH": ".//tei:docDate//tei:date"})

    assert build_fragment_params(app)["temporal_xpath"] == ".//tei:docDate//tei:date"


def test_early_years_keep_their_iso_bounds():
    temporal = {
        "dublincore.date_start": 795,
        "dublincore.date_start_iso": "0795-01-01",
        "dublincore.date_end": 795,
        "dublincore.date_end_iso": "0795-12-31",
    }

    assert build_filtered_temporal_metadata(temporal) == {
        "temporal.dublincore.date_start": 795,
        "temporal.dublincore.date_start_iso": "0795-01-01",
        "temporal.dublincore.date_end": 795,
        "temporal.dublincore.date_end_iso": "0795-12-31",
    }


def test_non_iso_bounds_are_not_indexed_as_dates():
    # For years <= 0 ThunderDots returns the bare year: a date field would
    # read "-50" as epoch milliseconds, strict_date would reject the passage.
    temporal = {
        "dublincore.coverage_start": -50,
        "dublincore.coverage_start_iso": "-50",
        "dublincore.coverage_end": 120,
        "dublincore.coverage_end_iso": "0120-12-31",
    }

    assert build_filtered_temporal_metadata(temporal) == {
        "temporal.dublincore.coverage_start": -50,
        "temporal.dublincore.coverage_end": 120,
        "temporal.dublincore.coverage_end_iso": "0120-12-31",
    }


def test_negative_years_keep_their_iso_bounds():
    # ThunderDots EDTF parser, "-0500/0499" (ENCPOS_1850_09).
    temporal = {
        "dublincore.coverage_start": -500,
        "dublincore.coverage_start_iso": "-0500-01-01",
        "dublincore.coverage_end": 499,
        "dublincore.coverage_end_iso": "0499-12-31",
    }

    assert build_filtered_temporal_metadata(temporal) == {
        "temporal.dublincore.coverage_start": -500,
        "temporal.dublincore.coverage_start_iso": "-0500-01-01",
        "temporal.dublincore.coverage_end": 499,
        "temporal.dublincore.coverage_end_iso": "0499-12-31",
    }


def test_long_years_are_not_indexed_as_dates():
    temporal = {
        "dublincore.coverage_start": 170000002,
        "dublincore.coverage_start_iso": "+170000002-01-01",
    }

    assert build_filtered_temporal_metadata(temporal) == {
        "temporal.dublincore.coverage_start": 170000002,
    }
