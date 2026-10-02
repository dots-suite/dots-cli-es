import logging
import re

from functools import lru_cache

from dots_es.api.search_fields import (
    metadata_key_from_path,
    range_field_by_es_path
)

logger = logging.getLogger(__name__)


# Root of the temporal index searched at each level (`scope` parameter):
# the resource dates live under `temporal` in RESOURCE_INDEX, the
# fragment's own dates under `fragment_temporal` in DOCUMENT_INDEX.
TEMPORAL_ROOTS = {
    "resource": "temporal",
    "fragment": "fragment_temporal",
}


def foreign_range_fields(ranges: list[dict], scope: str) -> list[str]:
    """
    Range fields on the dates of the other level: a resource search never
    looks at fragment dates, and a fragment search never at resource dates.
    """
    foreign_roots = tuple(
        f"{root}."
        for other_scope, root in TEMPORAL_ROOTS.items()
        if other_scope != scope
    )

    return [
        field
        for range_query in ranges
        for field in range_query
        if field.startswith(foreign_roots)
    ]


@lru_cache(maxsize=16)
def get_temporal_mapping(es, index: str, root: str = "temporal") -> dict:
    """
    Mapping of every field under `root` (`temporal` or `fragment_temporal`).
    """
    mapping = es.indices.get_field_mapping(
        index=index,
        fields=f"{root}.*"
    )

    return mapping[index]["mappings"]


def _es_type(definition: dict):
    return next(iter(definition.get("mapping", {}).values()), {}).get("type")


@lru_cache(maxsize=16)
def get_temporal_fields(es, index: str, root: str = "temporal") -> list[str]:
    """
    Découvre automatiquement les champs temporels disposant
    d'un couple _start / _end numérique.

    Retour :
    [
        "temporal.dublincore.coverage",
        "temporal.dublincore.created",
        "temporal.extensions.dateCreated",
        ...
    ]
    """

    fields = get_temporal_mapping(es, index, root)

    temporal_fields = []

    for field, definition in fields.items():

        if not field.endswith("_start"):
            continue

        logical_field = field[:-6]          # retire "_start"
        end_field = logical_field + "_end"

        if end_field not in fields:
            continue

        start_type = definition.get("mapping", {})
        end_type = fields[end_field].get("mapping", {})

        # Récupération du type ES
        start_mapping = next(iter(start_type.values()), {})
        end_mapping = next(iter(end_type.values()), {})

        start_es_type = start_mapping.get("type")
        end_es_type = end_mapping.get("type")

        # On ignore les champs texte/date string
        if start_es_type not in (
            "integer",
            "long",
            "short",
            "byte",
            "double",
            "float"
        ):
            continue

        if end_es_type != start_es_type:
            continue

        temporal_fields.append(logical_field)

    return sorted(temporal_fields)


@lru_cache(maxsize=16)
def get_iso_temporal_fields(es, index: str, root: str = "temporal") -> frozenset[str]:
    """
    Champs temporels dont les bornes ISO (_start_iso / _end_iso) sont
    indexées en `date` : ils acceptent des plages au mois ou au jour.
    """
    fields = get_temporal_mapping(es, index, root)

    return frozenset(
        field
        for field in get_temporal_fields(es, index, root)
        if _es_type(fields.get(f"{field}_start_iso", {})) == "date"
        and _es_type(fields.get(f"{field}_end_iso", {})) == "date"
    )


@lru_cache(maxsize=64)
def temporal_key(field: str) -> str:
    """
    Canonical metadata key of a temporal field, resolved through
    SEARCH_FIELDS rather than derived from the last path segment.

    Truncating collapsed distinct properties -- `temporal.dublincore.created`
    and `temporal.extensions.created` both became "created". The namespaced
    key keeps them apart.
    """
    registry_field = range_field_by_es_path(field)

    if registry_field is not None:
        return registry_field.key

    # Temporal fields are discovered from the mapping, so a field may
    # legitimately have no registry entry yet. Fall back on the same
    # derivation and flag it: it means SEARCH_FIELDS needs an entry.
    key = metadata_key_from_path(field)

    logger.warning(
        "Temporal field %r is not declared in SEARCH_FIELDS; "
        "derived its key as %r.",
        field,
        key
    )

    return key


# A bound of an ISO range, with its precision: "1241", "1241-03",
# "1241-03-17", or the same with a leading minus sign ("-0050").
ISO_BOUND_RE = re.compile(r"-?\d{4}(?:-\d{2}(?:-\d{2})?)?")

ISO_BOUND_FORMAT = "strict_date||strict_year_month||strict_year"

RANGE_OPERATORS = {"gt", "gte", "lt", "lte"}


def iso_bound(value: str) -> str:
    """
    Date math anchor for a partial ISO bound, rounded to its own precision.

    Elasticsearch rounds `gte`/`lt` down and `gt`/`lte` up, so
    `lte: 1241-03||/M` reaches the last millisecond of March 1241 and
    `gte: 1241||/y` starts on 1241-01-01.
    """
    value = value.strip()

    if not ISO_BOUND_RE.fullmatch(value):
        raise ValueError(
            f"Invalid date bound {value!r}: expected YYYY, YYYY-MM or YYYY-MM-DD"
        )

    precision = value.lstrip("-").count("-")

    return f"{value}||/{'yMd'[precision]}"


def build_range_clause(range_query: dict) -> dict:
    """
    Elasticsearch `range` clause for one `range[<field>]` parameter.

    Year bounds (`..._start`, `..._end`) are sent as is. ISO bounds
    (`..._start_iso`, `..._end_iso`) accept a year, a month or a day and are
    rounded to that precision.
    """
    field, condition = next(iter(range_query.items()))

    for op in condition:
        if op not in RANGE_OPERATORS:
            raise ValueError(f"Invalid range operator {op!r} on {field}")

    if not field.endswith("_iso"):
        return {"range": {field: dict(condition)}}

    return {
        "range": {
            field: {
                **{op: iso_bound(value) for op, value in condition.items()},
                "format": ISO_BOUND_FORMAT,
            }
        }
    }


def range_fields_of(field: str) -> set[str]:
    """
    Every bound of a temporal facet: years and ISO dates.
    """
    return {
        f"{field}_start",
        f"{field}_end",
        f"{field}_start_iso",
        f"{field}_end_iso",
    }


def build_temporal_aggs(
    temporal_fields,
    base_must,
    base_filters,
    ranges,
    iso_fields=frozenset()
):
    aggs = {}

    for field in temporal_fields:

        key = field.replace(".", "__")

        start_field = field + "_start"
        end_field = field + "_end"

        # Toutes les ranges SAUF celles qui concernent
        # la facette temporelle courante (années ou dates ISO).
        own_fields = range_fields_of(field)

        facet_ranges = [
            build_range_clause(range_query)
            for range_query in ranges
            if not own_fields & range_query.keys()
        ]

        bounds_aggs = {
            # Enveloppe globale des plages
            "min": {
                "min": {
                    "field": start_field
                }
            },
            "max": {
                "max": {
                    "field": end_field
                }
            },
        }

        if field in iso_fields:
            bounds_aggs["min_iso"] = {"min": {"field": start_field + "_iso"}}
            bounds_aggs["max_iso"] = {"max": {"field": end_field + "_iso"}}

        facet_bool = {
            "must": list(base_must),
            "filter": list(base_filters) + facet_ranges
        }

        aggs[f"{key}_available"] = {
            "global": {},
            "aggs": {
                "filtered": {
                    "filter": {
                        "bool": facet_bool
                    },
                    "aggs": {

                        **bounds_aggs,

                        # # Intersection commune
                        # "intersection_min": {
                        #     "max": {
                        #         "field": start_field
                        #     }
                        # },
                        # "intersection_max": {
                        #     "min": {
                        #         "field": end_field
                        #     }
                        # }
                    }
                }
            }
        }

    return aggs


def unflatten_dict(data):
    result = {}

    for key, value in data.items():
        parts = key.split(".")
        current = result

        for part in parts[:-1]:
            current = current.setdefault(part, {})

        current[parts[-1]] = value

    return result


def extract_temporal_facets(
    aggregations: dict,
    temporal_fields: list[str],
    iso_fields=frozenset()
) -> list[dict]:

    facets = []

    for field in temporal_fields:

        key = field.replace(".", "__")

        available = aggregations.get(
            f"{key}_available"
        )

        if not available:
            continue

        filtered = available.get("filtered")

        if not filtered:
            continue

        min_value = filtered["min"]["value"]
        max_value = filtered["max"]["value"]

        if min_value is None or max_value is None:
            continue

        # intersection_min = filtered["intersection_min"]["value"]
        # intersection_max = filtered["intersection_max"]["value"]
        #
        # intersection = None
        #
        # if (
        #     intersection_min is not None
        #     and intersection_max is not None
        #     and intersection_min <= intersection_max
        # ):
        #     intersection = {
        #         "min": int(intersection_min),
        #         "max": int(intersection_max)
        #     }

        facet = {
            "key": temporal_key(field),
            # Fallback label: the canonical key itself, so a collection with
            # no configured label displays exactly the string an editor has
            # to paste into searchConfig.temporalFacets to customise it.
            # Term facets already fall back the same way, on their key.
            "label": temporal_key(field),
            "field": field,
            "start_field": field + "_start",
            "end_field": field + "_end",
            "min": int(min_value),
            "max": int(max_value),
        }#"intersection": intersection

        # Day-precision bounds, when the facet has ISO dates indexed.
        # value_as_string is in the strict_date format of the mapping.
        min_iso = filtered.get("min_iso", {}).get("value_as_string")
        max_iso = filtered.get("max_iso", {}).get("value_as_string")

        if field in iso_fields and min_iso and max_iso:
            facet.update({
                "start_field_iso": field + "_start_iso",
                "end_field_iso": field + "_end_iso",
                "min_iso": min_iso,
                "max_iso": max_iso,
            })

        facets.append(facet)

    return facets


def build_open_range(range_query):
    """
    Range that also lets through documents without the field: a resource
    with no date for this property is not excluded by a date filter.
    """
    field = next(iter(range_query))

    return {
        "bool": {
            "should": [
                build_range_clause(range_query),
                {
                    "bool": {
                        "must_not": [
                            {
                                "exists": {
                                    "field": field
                                }
                            }
                        ]
                    }
                }
            ],
            "minimum_should_match": 1
        }
    }



