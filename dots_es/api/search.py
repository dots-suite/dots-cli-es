import json
import pprint
import time
from typing import Callable
from flask import Response, request, current_app

from dots_es.api.temporal import (
    TEMPORAL_ROOTS,
    get_temporal_fields,
    get_iso_temporal_fields,
    temporal_key,
    build_range_clause,
    build_temporal_aggs,
    extract_temporal_facets,
    build_open_range,
    unflatten_dict
)

from dots_es.api.search_fields import (
    build_searchfield_aggs,
    extract_searchfield_facets,
    get_facet_es_field,
    resolve_sort_field
)

# Hard ceiling on page[size], whatever the client asks or the configuration says.
MAX_PAGE_SIZE = 200

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

        # Level at which dates are filtered and faceted:
        # - resource (default): the resource dates, repeated on each passage;
        # - fragment: the fragment's own dates (fragment_temporal). Results
        #   are then always resources grouped with their matching fragments,
        #   and a date range excludes the fragments that have no date.
        scope = request.args.get("scope") or "resource"

        if scope not in TEMPORAL_ROOTS:
            return Response(
                f"Invalid scope {scope!r}: expected one of {', '.join(TEMPORAL_ROOTS)}",
                status=400
            )

        temporal_root = TEMPORAL_ROOTS[scope]

        temporal_fields = get_temporal_fields(
            current_app.elasticsearch,
            index,
            temporal_root
        )

        iso_fields = get_iso_temporal_fields(
            current_app.elasticsearch,
            index,
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
                    "missing": "_last"
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
                sort_criteriae.append({
                    resolve_sort_field(criteria): {
                        "order": sort_order,
                        # Missing metadata pushed to the end of sorted results
                        "missing": "_last"
                    }
                })

        r = {}

        collection_filters = []
        other_filters = []

        try:

            # === CAS 1 : Recherche simple sur ressources filtrée par collection ===
            if no_highlight and scope == "resource":
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

                search_result = current_app.elasticsearch.search(index=index, body=body_query)
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

            # === CAS 2 : Full-text search grouped by resource using composite + top_hits ===
            # Also serves every fragment-level search, notice mode included:
            # the query then targets the fragment's description, not its text.
            else:
                print('\nHIGHLIGHTS SEARCH', scope)

                query_type = "fragment_notice" if no_highlight else "fulltext"

                fragment_source = [
                    "passage_id",
                    "title",
                    "level",
                    "ancestors",
                    "citeType"
                ]

                fragment_sort = [{"_score": "desc"}]

                if scope == "fragment":
                    fragment_source += ["fragment_metadata", "fragment_temporal"]
                    # Equal scores (no query, filters only): keep the
                    # fragments in document order.
                    fragment_sort.append({"passage_id": "asc"})

                scope_filter = {"term": {"resource_metadata.path_ids.keyword": collection_id}}

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

                highlight_config = {
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

                body_query = {
                    "query": {
                        "bool": {
                            "must": [{"term": {"type.keyword": "fragment"}}],
                            "filter": [scope_filter]
                        }
                    },
                    # 4 fields of top-hit fragment are used for writing buckets :
                    "_source": [
                        "resource_id",
                        "resource_metadata",
                        "temporal",
                        "collections"
                    ],
                    "collapse": {
                        "field": "resource_id",
                        "inner_hits": {
                            "name": "fragments",
                            "size": 100,
                            "sort": fragment_sort,
                            # 5 keys are used for fragments (highlight not linked to _source),
                            # plus the fragment's own metadata and dates at fragment scope
                            "_source": fragment_source,
                            "highlight": highlight_config
                        }
                    },
                    # tri par défaut = score ; sinon on préfixe avec les critères demandés
                    "sort": sort_criteriae if sort_criteriae else default_sort,
                    "from": (num_page - 1) * page_size,
                    "size": page_size,
                    "track_total_hits": True,
                    "track_scores": True,

                    "aggregations": {
                        "resource_count": {
                            "cardinality": {
                                "field": "resource_id",
                                "precision_threshold": 1
                            }
                        },
                    }
                }

                body_query["aggregations"].update(
                    build_searchfield_aggs(excluded_facets)
                )

                if query_param:
                    body_query["query"]["bool"]["must"].extend(parse_query_param(query_param, query_type))
                else:
                    body_query["query"]["bool"]["must"].append({"match_all": {}})

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
                    "collections": {
                        "global": {},
                        "aggs": {
                            "filtered": {
                                "filter": {
                                    "bool": {
                                        "must": [
                                            m for m in body_query["query"]["bool"]["must"]
                                        ],
                                        "filter": [scope_filter] + other_filters
                                    }
                                },
                                "aggs": {
                                    "values": {
                                        "terms": {
                                            "field": "collection_facets",
                                            "size": 1000
                                        },
                                        "aggs": {
                                            "resource_count": {
                                                "cardinality": {
                                                    "field": "resource_id",
                                                    "precision_threshold": 1
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                print("coll_agg filter:", coll_agg["collections"]["aggs"]["filtered"]["filter"]["bool"]["filter"])

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

                # Ajouter les ranges. At fragment scope a fragment without a
                # date does not pass a date filter: most of them are prefaces,
                # tables or notices, not undated acts.
                if ranges:
                    build_range = (
                        build_range_clause
                        if scope == "fragment"
                        else build_open_range
                    )
                    body_query["query"]["bool"]["must"].extend(
                        [build_range(r) for r in ranges]
                    )

                body_query["aggregations"]["filtered_resource_count"] = {
                    "filter": {
                        "bool": {
                            "must": body_query["query"]["bool"]["must"],
                            "filter": (
                                    other_filters +
                                    collection_filters
                            )
                        }
                    },
                    "aggs": {
                        "count": {
                            "cardinality": {
                                "field": "resource_id",
                                "precision_threshold": 15000
                            }
                        }
                    }
                }

                print('\nbody : ', body_query)
                search_result = current_app.elasticsearch.search(index=index, body=body_query)


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
                        "count": bucket["resource_count"]["value"],
                        "facet_key": bucket["key"]
                    })

                if collection_facet:
                    collection_facets = [
                        f for f in collection_facets if f["facet_key"] not in collection_facet
                    ]

                # No longer used: fragment separator is now added in the front end
                # (ResourcesList.vue). Kept just in case.

                # def add_ellipsis(fragment):
                #     if not fragment:
                #         return fragment
                #     text = fragment.strip()
                #     if text and text[0].islower():
                #         text = "..." + text
                #     if not text.endswith((".", "…", "!", "?")):
                #         text = text + "..."
                #     return text

                grouped_results = []

                for hit in search_result["hits"]["hits"]:
                    inner_hits_list = hit["inner_hits"]["fragments"]["hits"]["hits"]
                    if not inner_hits_list:
                        continue

                    # le hit top-level EST déjà un fragment représentatif de la ressource
                    rep_source = hit["_source"]
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
                                **(
                                    {
                                        "metadata": h["_source"].get("fragment_metadata", {}),
                                        "temporal": unflatten_dict({
                                            key.removeprefix("temporal."): value
                                            for key, value in h["_source"].get("fragment_temporal", {}).items()
                                        }),
                                    }
                                    if scope == "fragment"
                                    else {}
                                ),
                                "highlight": {
                                    "content": [
                                        # add_ellipsis(frag) - no longer in use, see above
                                        frag
                                        for frag in (h.get("highlight", {}).get("content") or [])
                                    ]
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
                    "bucket_count": search_result["aggregations"]["filtered_resource_count"]["count"]["value"],
                    "total_count": search_result["hits"]["total"]["value"],
                    "page": num_page,
                    "page_size": page_size,
                    "highlight_patterns": patterns,
                    "temporal": temporal_facets
                }

            # Collection index check common to 3 possible responses:
            # None when no collection_id has been provided, which is irrelevant
            r["collection_indexed"] = (
                is_collection_indexed(index, collection_id)
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