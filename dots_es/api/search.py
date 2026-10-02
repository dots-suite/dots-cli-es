import json
import pprint
import time
from typing import Callable
from flask import Response, request, current_app

from dots_es.api.temporal import (
    TEMPORAL_ROOTS,
    foreign_range_fields,
    get_temporal_fields,
    get_iso_temporal_fields,
    temporal_key,
    build_temporal_aggs,
    extract_temporal_facets,
    build_open_range,
    unflatten_dict
)

from dots_es.api.search_fields import (
    build_searchfield_aggs,
    extract_searchfield_facets,
    get_facet_es_field,
    resolve_sort_field,
    resolve_fragment_sort_field
)

# Hard ceiling on page[size], whatever the client asks or the configuration says.
MAX_PAGE_SIZE = 200

# Old unified config kept for reference:
# "sentence" scanner starts at sentence start, often
# leaving <mark> at the edge (median: 4 chars before
# <mark> vs 20 with fvh).

# highlight_config = {
#     "type": "unified",
#     "require_field_match": True,
#     "pre_tags": ["<mark>"],
#     "post_tags": ["</mark>"],
#     "fields": {
#         "content": {
#             "fragment_size": 80,
#             "number_of_fragments": 100,
#             "boundary_scanner": "sentence",
#             "no_match_size": 50
#         }
#     }
# }

# fvh: more balanced context around the term.
# Requires "with_positions_offsets" term_vector on
# content (set in dots_document.conf.json).
# fragment_offset: context before the highlighted term (fvh only).
# No boundary_scanner: with fvh, it can stretch fragments
# far beyond fragment_size.

HIGHLIGHT_CONFIG = {
    "type": "fvh",
    "require_field_match": True,
    "pre_tags": ["<mark>"],
    "post_tags": ["</mark>"],
    "fields": {
        "content": {
            "fragment_size": 80,
            "number_of_fragments": 100,
            "fragment_offset": 25,
            "no_match_size": 50
        }
    }
}

def build_collection_facet(scope_collection_id):
    return {
        "filter": {
            "bool": {
                "must": [
                    {"term": {"type.keyword": "Resource"}}
                ],
                "filter": [
                    {
                        "term": {
                            "resource_metadata.path_ids.keyword": scope_collection_id
                        }
                    }
                ]
            }
        },
        "aggs": {
            "values": {
                "terms": {
                    "field": "collection_facets",
                    "size": 100000
                }
            }
        }
    }

def parse_query_param(query_param: str, searchType: str = "notice"):
    """
    Parser ES :
    - phrases exactes
    - AND / OR / NOT
    - wildcards (* ?)
    - field:value
    - recherche multi-fields
    """

    FIELD_ALIASES = {
        "notice": {
            "title": "resource_metadata.dublincore.title",
            "creator": "resource_metadata.dublincore.creator",
            "date": "resource_metadata.dublincore.created",
            "description": "resource_metadata.description",
        },
        "fulltext": {
            "content": "content",
            "title": "title",
        },
        # Same field as fulltext without content; required, or aliases fall back to resource-level notice
        "fragment_notice": {
            "title": "title",
        }
    }

    SEARCH_FIELDS = {
        "notice": [
            "resource_metadata.title",
            "resource_metadata.description",
            "resource_metadata.dublincore.title",
            "resource_metadata.dublincore.creator",
            "resource_metadata.dublincore.contributor",
            "resource_metadata.dublincore.subject",
            "resource_metadata.dublincore.publisher"
        ],
        "fulltext": [
            "content",
            "title"
        ],
        # Notice search at fragment level: the fragment's own description,
        # not its text (an act titled "Donation de Louis VII.").
        "fragment_notice": [
            "title",
            "fragment_metadata.dublincore.title",
            "fragment_metadata.extensions.name"
        ]
    }

    if not query_param:
        return []

    query_param = query_param.strip()

    fields_to_search = SEARCH_FIELDS.get(
        searchType,
        SEARCH_FIELDS["notice"]
    )

    field_aliases = FIELD_ALIASES.get(
        searchType,
        FIELD_ALIASES["notice"]
    )

    # ---------------------------------------------------
    # Remplacement des alias :
    # title:"xxx" -> vrai champ ES
    # ---------------------------------------------------
    for alias, es_field in field_aliases.items():
        query_param = re.sub(
            rf'(?<![\w.]){re.escape(alias)}:',
            f'{es_field}:',
            query_param
        )

    # ---------------------------------------------------
    # Cas optimisé :
    # uniquement une ou plusieurs phrases exactes
    # ---------------------------------------------------
    phrases = re.findall(r'"([^"]+)"', query_param)
    remaining = re.sub(r'"([^"]+)"', '', query_param).strip()

    if phrases and not remaining:

        return [
            {
                "bool": {
                    "should": [
                        {
                            "match_phrase": {
                                field: {
                                    "query": phrase
                                }
                            }
                        }
                        for phrase in phrases
                        for field in fields_to_search
                    ],
                    "minimum_should_match": 1
                }
            }
        ]

    # ---------------------------------------------------
    # Tout le reste est géré par Lucene/Elasticsearch
    # ---------------------------------------------------
    return [
        {
            "query_string": {
                "query": query_param,
                "fields": fields_to_search,
                "default_operator": "AND",
                "analyze_wildcard": True
            }
        }
    ]

def parse_filters_param(filters_param: str):
    es_filters = []

    if not filters_param:
        return es_filters

    for part in filters_param.split(","):
        if ":" not in part:
            continue

        field, raw_values = part.split(":", 1)
        field = field.strip()

        values = [v.strip() for v in raw_values.split("|") if v.strip()]
        if not values:
            continue

        # on reconstruit une query string OR
        query = " OR ".join(values)

        es_filters.append({
            "query_string": {
                "query": query,
                "fields": [field],
                "default_operator": "AND",
                "analyze_wildcard": True
            }
        })

    return es_filters

def parse_range_parameter():
    _range = None
    _parsed_ranges = []
    for f in request.args.keys():
        if f.startswith('range[') and f.endswith(']'):
            key, ops = (f[len('range['):-1], [op.split(':') for op in request.args[f].split(",")])
            print(key, ops)
            _range = {key: {}}
            for op, value in ops:
                _range[key][op] = value
            _parsed_ranges.append(_range)
    return _parsed_ranges


import re

def extract_highlight_patterns(query: str):
    if not query:
        return []

    patterns = []

    # 1. phrases exactes "..."
    phrases = re.findall(r'"([^"]+)"', query)
    for p in phrases:
        patterns.append({
            "type": "phrase",
            "value": p
        })

    query_clean = re.sub(r'"([^"]+)"', '', query)

    # 2. tokens
    tokens = re.split(r'\s+', query_clean)

    for token in tokens:
        token = token.strip()
        token = re.sub(r'^[()]+|[()]+$', '', token)
        if not token:
            continue

        upper = token.upper()

        # skip opérateurs
        if upper in {
            "AND", "OR", "NOT",
            "TO"
        }:
            continue

        if token in {"(", ")", "[", "]", "{", "}"}:
            continue

        # field queries
        if ":" in token:
            field, value = token.split(":", 1)
            field = field.strip()
            field = field.lstrip("+-")
            value = value.strip()

            if not value:
                continue

            # wildcard prefix/suffix
            if "*" in value:
                patterns.append({
                    "type": "prefix",
                    "value": value.replace("*", ""),
                    "field": field
                })
            elif "?" in value:
                patterns.append({
                    "type": "wildcard",
                    "value": value,
                    "field": field
                })
            else:
                patterns.append({
                    "type": "word",
                    "value": value,
                    "field": field
                })

            continue

        # wildcards globaux
        if "*" in token:
            patterns.append({
                "type": "prefix",
                "value": token.replace("*", "")
            })
        elif "?" in token:
            patterns.append({
                "type": "wildcard",
                "value": token
            })
        else:
            patterns.append({
                "type": "word",
                "value": token
            })

    return patterns


def build_facet_clause(es_field: str, values: list, match_any: bool = False) -> dict:
    """
    ES facet clause for several values on single facet, combined with AND

    `terms` is an OR logic: on a multivalued field as dublincore.creator,
    it would select docs pertaining to one or the other 'creator'
    We intend to restrict to docs where authors are co-authors :
    one term per value, all mandatory.

    `match_any` switches to that OR logic, for facets whose values are
    alternatives rather than cumulative properties: a resource belongs to a
    single collection (e.g. one annual volume), so selecting several
    collections widens the results instead of emptying them.
    """
    if len(values) == 1:
        return {"term": {es_field: values[0]}}

    if match_any:
        return {"terms": {es_field: values}}

    return {
        "bool": {
            "must": [
                {"term": {es_field: value}}
                for value in values
            ]
        }
    }


# Prefixes of the fields stored on resources (RESOURCE_INDEX), not on fragments
RESOURCE_FIELD_PREFIXES = ("resource_metadata.", "temporal.", "collections.", "collection_facets")

# Page size of the composite aggregation listing the resources hit by fragments
RESOURCE_HITS_PAGE = 10000


def is_resource_filter(clause: dict) -> bool:
    """
    True when a `filters` clause targets a resource field rather than a fragment field.
    """
    fields = clause.get("query_string", {}).get("fields", [])

    return any(field.startswith(RESOURCE_FIELD_PREFIXES) for field in fields)


def collect_resource_hits(index: str, fragment_query: dict) -> dict:
    """
    Resources having fragments that match: {resource_id: (fragment count, best fragment score)}.

    Paged with a composite aggregation, so the number of resources is not capped.
    """
    hits = {}
    composite = {
        "size": RESOURCE_HITS_PAGE,
        "sources": [{"resource_id": {"terms": {"field": "resource_id"}}}]
    }

    while True:
        result = current_app.elasticsearch.search(index=index, body={
            "size": 0,
            "query": fragment_query,
            "aggregations": {
                "resources": {
                    "composite": composite,
                    "aggs": {"score": {"max": {"script": "_score"}}}
                }
            }
        })

        aggregation = result["aggregations"]["resources"]

        for bucket in aggregation["buckets"]:
            hits[bucket["key"]["resource_id"]] = (bucket["doc_count"], bucket["score"]["value"])

        if len(aggregation["buckets"]) < RESOURCE_HITS_PAGE or "after_key" not in aggregation:
            return hits

        composite = {**composite, "after": aggregation["after_key"]}


def get_resource_titles(resource_index: str, resource_ids) -> dict:
    """
    Titles of the given resources, read from RESOURCE_INDEX.
    """
    if not resource_ids:
        return {}

    result = current_app.elasticsearch.mget(
        index=resource_index,
        ids=list(resource_ids),
        source=["resource_metadata.title"]
    )

    return {
        doc["_id"]: doc["_source"].get("resource_metadata", {}).get("title")
        for doc in result["docs"]
        if doc.get("found")
    }


def is_collection_indexed(index: str, collection_id: str) -> bool:
    """
    Check if there is at least one resource indexed for the scope collection
    Identify difference between "has not yet been indexed" vs. "ho results for a search"
    which the frontend can't efficiently resolve
    """
    result = current_app.elasticsearch.count(
        index=index,
        body={
            "query": {
                "term": {
                    "resource_metadata.path_ids.keyword": collection_id
                }
            }
        }
    )

    return result["count"] > 0


def register_search_endpoint(
    app,
    api_version="1.0",
    compose_result_func: Callable[[str], list] = lambda s: [],
    compose_result_grouped_by_resource: Callable[[str], list] = lambda s: []
):
    @app.route(f"/api/{api_version}/search", methods=["GET"])
    def api_search_endpoint():
        start_time: float = time.time()

        index: str = request.args.get("index", None)
        if index is None or len(index) == 0:
            index = current_app.config["DOCUMENT_INDEX"]

        # Resource metadata and dates are only stored in their own index
        resource_index: str = request.args.get("resourceIndex") or current_app.config["RESOURCE_INDEX"]

        # Dates filtered and faceted: resource (temporal) or fragment (fragment_temporal)
        scope = request.args.get("scope") or "resource"

        if scope not in TEMPORAL_ROOTS:
            return Response(
                f"Invalid scope {scope!r}: expected one of {', '.join(TEMPORAL_ROOTS)}",
                status=400
            )

        temporal_root = TEMPORAL_ROOTS[scope]

        # Resource dates are mapped in RESOURCE_INDEX, fragment dates in the fragment index
        temporal_index = index if scope == "fragment" else resource_index

        temporal_fields = get_temporal_fields(
            current_app.elasticsearch,
            temporal_index,
            temporal_root
        )

        iso_fields = get_iso_temporal_fields(
            current_app.elasticsearch,
            temporal_index,
            temporal_root
        )

        # Temporal facets explicitly disabled by the client
        # (searchConfig.temporalFacets, entries with "enabled": false),
        # designated by their canonical key -- `dublinCore.created`.
        # Same declarative semantics as excludeFacets below: a facet that
        # is not declared is still computed and returned, and the front
        # displays it with its default label. Declaring an entry therefore
        # only serves to customise it (label, order) or to exclude it.
        # Filtering happens HERE, after get_temporal_fields, so that its
        # lru_cache -- keyed on (es, index) -- stays effective.
        excluded_temporal_param = request.args.get("excludeTemporalFacets")

        if excluded_temporal_param:
            excluded_temporal = {
                t.strip()
                for t in excluded_temporal_param.split(",")
                if t.strip()
            }

            temporal_fields = [
                f for f in temporal_fields
                if temporal_key(f) not in excluded_temporal
            ]

        # Metadata facets (searchConfig.facets on the front side),
        # designated by their canonical key -- `dublinCore.creator`.
        # This mirrors the exact semantics of the front rendering, which is
        # an explicit EXCLUSION: a facet missing from the config is still
        # displayed. The client therefore sends the disabled facets, not
        # the enabled ones -- otherwise a partial config (e.g. cid.conf,
        # which only declares "collections": false) would make every other
        # facet disappear.
        excluded_param = request.args.get("excludeFacets")

        excluded_facets = set()

        if excluded_param:
            excluded_facets = {
                f.strip()
                for f in excluded_param.split(",")
                if f.strip()
            }

        # The "collections" facet does not come from SEARCH_FIELDS: it has
        # its own aggregation (global + terms), built further down.
        with_collections = "collections" not in excluded_facets

        query_param: str = request.args.get("query", None)
        patterns = extract_highlight_patterns(query_param)

        ranges: list[dict] = parse_range_parameter()

        foreign_fields = foreign_range_fields(ranges, scope)

        if foreign_fields:
            return Response(
                f"Range on {', '.join(foreign_fields)} not allowed at {scope} scope",
                status=400
            )

        filters_param = request.args.get("filters")
        collection_id: str = request.args.get("collectionId")

        collection_facet = []
        collections_param = request.args.get("collections")

        after_key = request.args.get("after")

        if collections_param:
            collection_facet = [
                c for c in collections_param.strip("[]").split(",")
                if c
            ]

        facets_param = request.args.get("facets")

        selected_facets = {}

        if facets_param:
            try:
                selected_facets = json.loads(facets_param)
            except json.JSONDecodeError:
                selected_facets = {}


        no_highlight = isinstance(request.args.get("no-highlight", False), str)

        # Pagination
        default_page_size = current_app.config["SEARCH_RESULT_PER_PAGE"]
        num_page = max(int(request.args.get('page[number]', 1)), 1)
        page_size = min(max(int(request.args.get('page[size]', default_page_size)), 1), MAX_PAGE_SIZE)

        # Tri

        default_sort = [
            {
                "temporal.temporal.dublincore.created_start": {
                    "order": "asc",
                    "missing": "_last",
                    # No document with dc:created in the index: sort on score instead of failing
                    "unmapped_type": "integer"
                }
            },
            {"_score": "desc"}
        ]


        sort_criteriae: list[dict] = []
        if "sort" in request.args:
            for criteria in request.args["sort"].split(','):
                sort_order = "asc"
                criteria = criteria.strip()
                if criteria.startswith('-'):
                    sort_order = "desc"
                    criteria = criteria[1:]
                if not criteria:
                    continue
                # Sort criteria mapped to sortable ES field
                # ES `.sort` order accented chars with their based letters
                # Dates are sorted against normalized temporal (start) bound, not the raw value
                try:
                    es_sort_field = (
                        resolve_fragment_sort_field(criteria)
                        if scope == "fragment"
                        else resolve_sort_field(criteria)
                    )
                except ValueError as e:
                    return Response(str(e), status=400)

                sort_criteriae.append({
                    es_sort_field: {
                        "order": sort_order,
                        # Missing metadata pushed to the end of sorted results
                        "missing": "_last"
                    }
                })

        r = {}

        collection_filters = []
        other_filters = []

        try:

            # === CAS 3 : Fragment search, notice or full-text, one hit per fragment ===
            # Only the fragment's own metadata and dates: no resource metadata or date
            if scope == "fragment":
                print('\nFRAGMENT SEARCH')

                # A fragment carries the path of its resource
                scope_filter = {"term": {"path_ids": collection_id}}

                body_query = {
                    "query": {
                        "bool": {
                            "must": [{"term": {"type.keyword": "fragment"}}],
                            "filter": [scope_filter]
                        }
                    },
                    "_source": [
                        "resource_id",
                        "passage_id",
                        "title",
                        "level",
                        "citeType",
                        "ancestors",
                        "path",
                        "fragment_metadata",
                        "fragment_temporal"
                    ],
                    # Notice mode: no match on content, no_match_size gives a preview
                    "highlight": HIGHLIGHT_CONFIG,
                    # Ties (no query, equal scores) keep the document order
                    "sort": (sort_criteriae or [{"_score": "desc"}]) + [
                        {"resource_id": "asc"},
                        {"passage_id": "asc"}
                    ],
                    "from": (num_page - 1) * page_size,
                    "size": page_size,
                    "track_total_hits": True,
                    "track_scores": True,
                    "aggregations": build_searchfield_aggs(excluded_facets, scope)
                }

                query_type = "fragment_notice" if no_highlight else "fulltext"

                if query_param:
                    body_query["query"]["bool"]["must"].extend(parse_query_param(query_param, query_type))
                else:
                    body_query["query"]["bool"]["must"].append({"match_all": {}})

                if filters_param:
                    es_filters = parse_filters_param(filters_param)
                    if es_filters:
                        body_query["query"]["bool"]["filter"].extend(es_filters)

                for facet_field, values in selected_facets.items():
                    if not values:
                        continue

                    clause = build_facet_clause(
                        get_facet_es_field(facet_field, scope),
                        values,
                        match_any=facet_field == "collections"
                    )

                    if facet_field != "collections":
                        other_filters.append(clause)

                    body_query["query"]["bool"]["filter"].append(clause)

                base_must = body_query["query"]["bool"]["must"]
                base_filters = body_query["query"]["bool"]["filter"]

                if with_collections:
                    # Counts fragments; the selected collections do not narrow their own facet
                    body_query["aggregations"]["collections_fac"] = {
                        "global": {},
                        "aggs": {
                            "filtered": {
                                "filter": {
                                    "bool": {
                                        "must": list(base_must),
                                        "filter": [scope_filter] + other_filters
                                    }
                                },
                                "aggs": {
                                    "values": {
                                        "terms": {
                                            "field": "collection_facets",
                                            "size": 1000
                                        }
                                    }
                                }
                            }
                        }
                    }

                body_query["aggregations"].update(
                    build_temporal_aggs(
                        temporal_fields,
                        base_must,
                        base_filters,
                        ranges,
                        iso_fields
                    )
                )

                # Undated fragments stay in the results, scored below dated ones
                if ranges:
                    body_query["query"]["bool"]["must"].extend(
                        [build_open_range(r) for r in ranges]
                    )

                search_result = current_app.elasticsearch.search(index=index, body=body_query)

                resource_titles = get_resource_titles(
                    resource_index,
                    {hit["_source"].get("resource_id") for hit in search_result["hits"]["hits"]}
                )

                results = []

                for hit in search_result["hits"]["hits"]:
                    source = hit["_source"]

                    results.append({
                        "resource_id": source.get("resource_id"),
                        "resource_title": resource_titles.get(source.get("resource_id")),
                        "path": source.get("path"),
                        "passage_id": source.get("passage_id"),
                        "title": source.get("title"),
                        "level": source.get("level", 1),
                        "citeType": source.get("citeType"),
                        "ancestors": source.get("ancestors", []),
                        "metadata": source.get("fragment_metadata", {}),
                        "temporal": unflatten_dict({
                            key.removeprefix("temporal."): value
                            for key, value in source.get("fragment_temporal", {}).items()
                        }),
                        "highlight": {
                            "content": hit.get("highlight", {}).get("content") or []
                        }
                    })

                facets = extract_searchfield_facets(
                    search_result["aggregations"],
                    excluded_facets,
                    scope
                )

                if with_collections:
                    facets["collections"] = []

                    for bucket in search_result["aggregations"]["collections_fac"]["filtered"]["values"]["buckets"]:
                        coll_id, _, label = bucket["key"].partition("###")

                        facets["collections"].append({
                            "id": coll_id,
                            "label": label or coll_id,
                            "count": bucket["doc_count"],
                            "facet_key": bucket["key"]
                        })

                r = {
                    "data": results,
                    "total_count": search_result["hits"]["total"]["value"],
                    "facets": facets,
                    "page": num_page,
                    "page_size": page_size,
                    "highlight_patterns": patterns,
                    "temporal": extract_temporal_facets(
                        search_result["aggregations"],
                        temporal_fields,
                        iso_fields
                    )
                }

            # === CAS 1 : Recherche simple sur ressources filtrée par collection ===
            elif no_highlight:
                print('\nRESOURCE SEARCH')

                scope_filter = {"term": {"resource_metadata.path_ids.keyword": collection_id}}

                body_query = {
                    "query": {
                        "bool": {
                            "must": [{"term": {"type.keyword": "Resource"}}],
                            "filter": [scope_filter]
                        }
                    },
                    "_source": [
                        "resource_metadata",
                        "temporal"
                    ],
                    "sort": sort_criteriae,
                    "from": (num_page - 1) * page_size,
                    "size": page_size,
                    "track_total_hits": True,
                    "aggregations": {
                    }
                }

                body_query["aggregations"].update(
                    build_searchfield_aggs(excluded_facets)
                )

                # Ajouter la clause terme de la notice
                if query_param:
                    body_query["query"]["bool"]["must"].extend(parse_query_param(query_param, "notice"))
                else:
                    body_query["query"]["bool"]["must"].append({"match_all": {}})

                # Ajouter les filtres
                if filters_param:
                    es_filters = parse_filters_param(filters_param)
                    if es_filters:
                        body_query["query"]["bool"].setdefault("filter", []).extend(es_filters)

                if selected_facets:
                    for facet_field, values in selected_facets.items():
                        if not values:
                            continue

                        es_field = get_facet_es_field(facet_field)
                        clause = build_facet_clause(
                            es_field,
                            values,
                            match_any=facet_field == "collections"
                        )

                        if facet_field == "collections":
                            collection_filters.append(clause)
                            body_query["query"]["bool"].setdefault("filter", []).append(clause)
                        else:
                            other_filters.append(clause)
                            body_query["query"]["bool"].setdefault("filter", []).append(clause)

                coll_agg = {
                    "collections_fac": {
                        "global": {},
                        "aggs": {
                            "filtered": {
                                "filter": {
                                    "bool": {
                                        "must": body_query["query"]["bool"]["must"],
                                        "filter": [scope_filter] + other_filters
                                    }
                                },
                                "aggs": {
                                    "values": {
                                        "terms": {
                                            "field": "collection_facets",
                                            "size": 1000
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                base_must = body_query["query"]["bool"]["must"]
                base_filters = body_query["query"]["bool"]["filter"]

                if with_collections:
                    body_query["aggregations"].update(coll_agg)

                body_query["aggregations"].update(
                    build_temporal_aggs(
                        temporal_fields,
                        base_must,
                        base_filters,
                        ranges,
                        iso_fields
                    )
                )

                if ranges:
                    body_query["query"]["bool"]["must"].extend(
                        [build_open_range(r) for r in ranges]
                    )

                search_result = current_app.elasticsearch.search(index=resource_index, body=body_query)
                print('\nbody_query')
                print(body_query)


                print('\nsearch_result["aggregations"]')
                #print(search_result)
                collection_facets = []

                collections_buckets = []

                if with_collections:
                    collections_buckets = (
                        search_result["aggregations"]["collections_fac"]
                        ["filtered"]["values"]["buckets"]
                    )

                for bucket in collections_buckets:
                    try:
                        coll_id, label = bucket["key"].split("###", 1)
                    except ValueError:
                        coll_id = bucket["key"]
                        label = bucket["key"]

                    collection_facets.append({
                        "id": coll_id,
                        "label": label,
                        "count": bucket["doc_count"],
                        "facet_key": bucket["key"]
                    })

                temporal_facets = extract_temporal_facets(
                    search_result["aggregations"],
                    temporal_fields,
                    iso_fields
                )


                results = [
                    {
                        "resource_id": hit["_id"],
                        **hit["_source"].get("resource_metadata", {}),
                        "temporal": unflatten_dict({
                            key.removeprefix("temporal."): value
                            for key, value in hit["_source"].get("temporal", {}).items()
                        }),
                    }
                    for hit in search_result["hits"]["hits"]
                ]

                facets = {
                    **extract_searchfield_facets(
                        search_result["aggregations"],
                        excluded_facets
                    )
                }

                if with_collections:
                    facets["collections"] = collection_facets

                r = {
                    "data": results,
                    "total_count": search_result["hits"]["total"]["value"],
                    "facets": facets,
                    "highlight_patterns": patterns,
                    "temporal": temporal_facets
                }

            # === CAS 2 : Full-text search grouped by resource ===
            # Fragments only carry resource_id: the text query runs on the
            # fragments, facets, filters, sort and pagination on RESOURCE_INDEX
            else:
                print('\nHIGHLIGHTS SEARCH')

                fragment_query = {
                    "bool": {
                        "must": [{"term": {"type.keyword": "fragment"}}] + (
                            parse_query_param(query_param, "fulltext")
                            if query_param
                            else [{"match_all": {}}]
                        ),
                        # The scope is applied to the fragments only: a resource with
                        # several parents keeps the branch its fragments were indexed with
                        "filter": [{"term": {"path_ids": collection_id}}]
                    }
                }

                resource_filters = []

                if filters_param:
                    for clause in parse_filters_param(filters_param):
                        if is_resource_filter(clause):
                            resource_filters.append(clause)
                        else:
                            fragment_query["bool"]["filter"].append(clause)

                for facet_field, values in selected_facets.items():
                    if not values:
                        continue

                    clause = build_facet_clause(
                        get_facet_es_field(facet_field),
                        values,
                        match_any=facet_field == "collections"
                    )

                    if facet_field == "collections":
                        collection_filters.append(clause)
                    else:
                        other_filters.append(clause)

                    resource_filters.append(clause)

                # 1. Resources having matching fragments
                resource_hits = collect_resource_hits(index, fragment_query)
                hit_ids = {"ids": {"values": list(resource_hits)}}

                # 2. Resources: facets, sort and pagination
                body_query = {
                    "query": {
                        # Relevance of a resource = score of its best fragment, as with collapse
                        "script_score": {
                            "query": {
                                "bool": {
                                    "filter": [hit_ids] + resource_filters,
                                    # Undated resources stay in the results, scored below dated ones
                                    "must": [build_open_range(r) for r in ranges]
                                }
                            },
                            "script": {
                                "source": "params.scores[doc['resource_id'].value] + _score",
                                "params": {
                                    "scores": {rid: score for rid, (_, score) in resource_hits.items()}
                                }
                            }
                        }
                    },
                    "_source": [
                        "resource_id",
                        "resource_metadata",
                        "temporal",
                        "collections"
                    ],
                    # tri par défaut = date puis score ; sinon les critères demandés
                    "sort": sort_criteriae if sort_criteriae else default_sort,
                    "from": (num_page - 1) * page_size,
                    "size": page_size,
                    "track_total_hits": True,
                    "track_scores": True,
                    "aggregations": {
                        # Fragments of the resources kept by the filters
                        "fragment_count": {
                            "sum": {
                                "script": {
                                    "source": "params.counts[doc['resource_id'].value]",
                                    "params": {
                                        "counts": {rid: count for rid, (count, _) in resource_hits.items()}
                                    }
                                }
                            }
                        },
                        **build_searchfield_aggs(excluded_facets)
                    }
                }

                if with_collections:
                    # The selected collections do not narrow their own facet
                    body_query["aggregations"]["collections"] = {
                        "global": {},
                        "aggs": {
                            "filtered": {
                                "filter": {
                                    "bool": {
                                        "filter": [hit_ids] + other_filters
                                    }
                                },
                                "aggs": {
                                    "values": {
                                        "terms": {
                                            "field": "collection_facets",
                                            "size": 1000
                                        }
                                    }
                                }
                            }
                        }
                    }

                body_query["aggregations"].update(
                    build_temporal_aggs(
                        temporal_fields,
                        [],
                        [hit_ids] + resource_filters,
                        ranges,
                        iso_fields
                    )
                )

                search_result = current_app.elasticsearch.search(index=resource_index, body=body_query)

                # 3. Highlighted fragments of the page's resources
                page_ids = [hit["_source"]["resource_id"] for hit in search_result["hits"]["hits"]]
                fragments_by_resource = {}

                if page_ids:
                    fragment_result = current_app.elasticsearch.search(index=index, body={
                        "query": {
                            "bool": {
                                "must": fragment_query["bool"]["must"],
                                "filter": fragment_query["bool"]["filter"] + [{"terms": {"resource_id": page_ids}}]
                            }
                        },
                        "_source": ["resource_id"],
                        "collapse": {
                            "field": "resource_id",
                            "inner_hits": {
                                "name": "fragments",
                                "size": 100,
                                "sort": [{"_score": "desc"}],
                                # 5 keys are used for fragments (highlight not linked to _source)
                                "_source": [
                                    "passage_id",
                                    "title",
                                    "level",
                                    "ancestors",
                                    "citeType"
                                ],
                                "highlight": HIGHLIGHT_CONFIG
                            }
                        },
                        "size": len(page_ids)
                    })

                    for hit in fragment_result["hits"]["hits"]:
                        fragments_by_resource[hit["_source"]["resource_id"]] = (
                            hit["inner_hits"]["fragments"]["hits"]["hits"]
                        )

                collection_facets = []

                collections_buckets = []

                if with_collections:
                    collections_buckets = (
                        search_result["aggregations"]["collections"]
                        ["filtered"]["values"]["buckets"]
                    )

                for bucket in collections_buckets:
                    try:
                        coll_id, label = bucket["key"].split("###", 1)
                    except ValueError:
                        coll_id = bucket["key"]
                        label = bucket["key"]
                    collection_facets.append({
                        "id": coll_id,
                        "label": label,
                        "count": bucket["doc_count"],
                        "facet_key": bucket["key"]
                    })

                if collection_facet:
                    collection_facets = [
                        f for f in collection_facets if f["facet_key"] not in collection_facet
                    ]

                grouped_results = []

                for hit in search_result["hits"]["hits"]:
                    rep_source = hit["_source"]
                    inner_hits_list = fragments_by_resource.get(rep_source.get("resource_id"))
                    if not inner_hits_list:
                        continue

                    resource_metadata = rep_source.get("resource_metadata", {})
                    temporal_metadata = rep_source.get("temporal", {})

                    grouped_results.append({
                        "resource_id": rep_source.get("resource_id"),
                        **resource_metadata,
                        "temporal": unflatten_dict({
                            key.removeprefix("temporal."): value
                            for key, value in temporal_metadata.items()
                        }),
                        "collection_ids": list({
                            c.get("collection_id")
                            for c in rep_source.get("collections", [])
                            if c.get("collection_id")
                        }),
                        "hits": [
                            {
                                "passage_id": h["_source"].get("passage_id"),
                                "title": h["_source"].get("title"),
                                "level": h["_source"].get("level", 1),
                                "ancestors": h["_source"].get("ancestors", []),
                                "citeType": h["_source"].get("citeType"),
                                "highlight": {
                                    "content": h.get("highlight", {}).get("content") or []
                                }
                            }
                            for h in inner_hits_list
                        ]
                    })

                temporal_facets = extract_temporal_facets(
                    search_result["aggregations"],
                    temporal_fields,
                    iso_fields
                )

                facets = {
                    **extract_searchfield_facets(
                        search_result["aggregations"],
                        excluded_facets
                    )
                }

                if with_collections:
                    facets["collections"] = collection_facets

                r = {
                    "buckets": grouped_results,
                    "facets": facets,
                    "bucket_count": search_result["hits"]["total"]["value"],
                    "total_count": int(search_result["aggregations"]["fragment_count"]["value"]),
                    "page": num_page,
                    "page_size": page_size,
                    "highlight_patterns": patterns,
                    "temporal": temporal_facets
                }

            # Collection index check common to 3 possible responses:
            # None when no collection_id has been provided, which is irrelevant
            r["collection_indexed"] = (
                is_collection_indexed(resource_index, collection_id)
                if collection_id
                else None
            )

            r["scope"] = scope
            r["duration"] = float('%.4f' % (time.time() - start_time))

        except Exception as e:
            return Response(str(e), status=400)

        return Response(
            json.dumps(r, indent=2, ensure_ascii=False),
            status=200,
            content_type="application/json; charset=utf-8",
            headers={"Access-Control-Allow-Origin": "*"}
        )