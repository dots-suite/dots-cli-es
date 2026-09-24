# search_fields.py

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class SearchFieldType(str, Enum):
    KEYWORD = "keyword"
    TEXT = "text"
    TEMPORAL = "temporal"
    URL = "url"
    INTEGER = "integer"


class SearchFieldFamily(str, Enum):
    CLI = "cli"
    DTS = "dts"
    DCT = "dct"
    SCHEMA = "schema"
    DOTS = "dots"
    THUNDERDOTS = "thunderdots"


# Index paths are lowercased; configurations and DTS payloads use the
# camelCase spelling. Only this namespace needs restoring -- `extensions`
# and the root-level properties are already spelled the same way.
NAMESPACE_KEYS = {
    "dublincore": "dublinCore",
}


def metadata_key_from_path(path: str) -> str:
    """
    Turn an index path into its canonical metadata key.

        dublincore.created                    -> dublinCore.created
        temporal.dublincore.created           -> dublinCore.created
        temporal.temporal.dublincore.created  -> dublinCore.created
        extensions.dateCreated                -> extensions.dateCreated
        title                                 -> title

    Leading `temporal.` segments are index plumbing and never belong to the
    key: the same property is one key whether it is read as a value or
    aggregated as a range.
    """
    while path.startswith("temporal."):
        path = path.removeprefix("temporal.")

    head, separator, tail = path.partition(".")

    if not separator:
        return path

    return f"{NAMESPACE_KEYS.get(head, head)}{separator}{tail}"


@dataclass(frozen=True)
class SearchField:

    id: str
    path: str

    family: SearchFieldFamily
    type: SearchFieldType

    index: bool = True

    facet: bool = False
    autocomplete: bool = False
    fulltext: bool = False
    multiple: bool = False

    range_start: Optional[str] = None
    range_end: Optional[str] = None

    @property
    def key(self) -> str:
        """
        Canonical metadata key: the DTS path of the property, as an editor
        writes it in a collection configuration (`columns`, `facets`,
        `temporalFacets`).

        This is the abstraction layer over the index structure. It is
        namespaced, so it never collapses two distinct properties the way a
        bare last segment does -- `dublinCore.created` and
        `extensions.dateCreated` stay distinct.

        Derived from `path`:
            dublincore.created           -> dublinCore.created
            temporal.dublincore.created  -> dublinCore.created
            extensions.dateCreated       -> extensions.dateCreated
            title                        -> title

        A range facet shares the key of the property it covers; the two are
        told apart by `is_range_facet`, not by their key.
        """
        return metadata_key_from_path(self.path)

    @property
    def range_start_iso(self) -> Optional[str]:
        """
        ES path of the ISO start bound (`date`), next to the year bound:
            temporal.dublincore.date_start -> temporal.dublincore.date_start_iso
        """
        return f"{self.range_start}_iso" if self.range_start else None

    @property
    def range_end_iso(self) -> Optional[str]:
        """
        ES path of the ISO end bound (`date`), next to the year bound.
        """
        return f"{self.range_end}_iso" if self.range_end else None

    @property
    def is_range_facet(self) -> bool:
        """
        Indique si le champ représente une facette temporelle range.

        Une facette range repose sur deux champs Elasticsearch :
        - un champ début
        - un champ fin

        Exemple :
            temporal.dublincore.coverage_start
            temporal.dublincore.coverage_end
        """
        return (
            self.facet
            and self.range_start is not None
            and self.range_end is not None
        )




SEARCH_FIELDS = [

    # ==========================================================
    # CLI / Elasticsearch
    # ==========================================================

    SearchField(
        "cli:parent",
        "parent_id",
        SearchFieldFamily.CLI,
        SearchFieldType.KEYWORD,
    ),

    SearchField(
        "cli:path",
        "path",
        SearchFieldFamily.CLI,
        SearchFieldType.KEYWORD,
    ),

    SearchField(
        "cli:pathIds",
        "path_ids",
        SearchFieldFamily.CLI,
        SearchFieldType.KEYWORD,
        multiple=True,
    ),

    SearchField(
        "cli:ancestors",
        "ancestors",
        SearchFieldFamily.CLI,
        SearchFieldType.KEYWORD,
        multiple=True,
    ),

    # ==========================================================
    # DTS
    # ==========================================================

    SearchField(
        "dts:id",
        "id",
        SearchFieldFamily.DTS,
        SearchFieldType.KEYWORD,
    ),

    SearchField(
        "dts:type",
        "type",
        SearchFieldFamily.DTS,
        SearchFieldType.KEYWORD,
    ),

    SearchField(
        "dts:title",
        "title",
        SearchFieldFamily.DTS,
        SearchFieldType.TEXT,
    ),

    SearchField(
        "dts:description",
        "description",
        SearchFieldFamily.DTS,
        SearchFieldType.TEXT,
    ),

    SearchField(
        "dts:download",
        "download",
        SearchFieldFamily.DTS,
        SearchFieldType.URL,
    ),

    SearchField(
        "content",
        "content",
        SearchFieldFamily.THUNDERDOTS,
        SearchFieldType.TEXT,
        fulltext=True,
    ),

    # ==========================================================
    # Dublin Core
    # ==========================================================

    SearchField(
        "dct:title",
        "dublincore.title",
        SearchFieldFamily.DCT,
        SearchFieldType.TEXT,
    ),

    SearchField(
        "dct:creator",
        "dublincore.creator",
        SearchFieldFamily.DCT,
        SearchFieldType.KEYWORD,
        facet=True,
        autocomplete=True,
        multiple=True,
    ),

    SearchField(
        "dct:created",
        "dublincore.created",
        SearchFieldFamily.DCT,
        SearchFieldType.TEMPORAL,
    ),

    SearchField(
        "dct:date",
        "dublincore.date",
        SearchFieldFamily.DCT,
        SearchFieldType.TEMPORAL,
    ),

    SearchField(
        "dct:issued",
        "dublincore.issued",
        SearchFieldFamily.DCT,
        SearchFieldType.TEMPORAL,
    ),

    SearchField(
        "dct:coverage",
        "dublincore.coverage",
        SearchFieldFamily.DCT,
        SearchFieldType.TEMPORAL,
    ),

    SearchField(
        "dct:contributor",
        "dublincore.contributor",
        SearchFieldFamily.DCT,
        SearchFieldType.KEYWORD,
        facet=True,
        autocomplete=True,
        multiple=True,
    ),

    SearchField(
        "dct:publisher",
        "dublincore.publisher",
        SearchFieldFamily.DCT,
        SearchFieldType.KEYWORD,
        facet=True,
        autocomplete=True,
        multiple=True,
    ),

    SearchField(
        "dct:language",
        "dublincore.language",
        SearchFieldFamily.DCT,
        SearchFieldType.KEYWORD,
        facet=True,
        autocomplete=True,
        multiple=True,
    ),

    SearchField(
        "dct:description",
        "dublincore.description",
        SearchFieldFamily.DCT,
        SearchFieldType.TEXT,
    ),

    SearchField(
        "dct:source",
        "dublincore.source",
        SearchFieldFamily.DCT,
        SearchFieldType.TEXT,
    ),

    SearchField(
        "dct:isVersionOf",
        "dublincore.isVersionOf",
        SearchFieldFamily.DCT,
        SearchFieldType.URL,
    ),

    SearchField(
        "dct:rights",
        "dublincore.rights",
        SearchFieldFamily.DCT,
        SearchFieldType.TEXT,
    ),

    SearchField(
        "dct:license",
        "dublincore.license",
        SearchFieldFamily.DCT,
        SearchFieldType.URL,
    ),

    SearchField(
        "dct:relation",
        "dublincore.relation",
        SearchFieldFamily.DCT,
        SearchFieldType.URL,
    ),

    # ==========================================================
    # Schema.org
    # ==========================================================

    SearchField(
        "schema:name",
        "extensions.name",
        SearchFieldFamily.SCHEMA,
        SearchFieldType.TEXT,
    ),

    SearchField(
        "schema:author",
        "extensions.author",
        SearchFieldFamily.SCHEMA,
        SearchFieldType.KEYWORD,
        facet=True,
        autocomplete=True,
        multiple=True,
    ),

    SearchField(
        "schema:editor",
        "extensions.editor",
        SearchFieldFamily.SCHEMA,
        SearchFieldType.KEYWORD,
        facet=True,
        autocomplete=True,
        multiple=True,
    ),

    SearchField(
        "schema:publisher",
        "extensions.publisher",
        SearchFieldFamily.SCHEMA,
        SearchFieldType.KEYWORD,
        facet=True,
        autocomplete=True,
        multiple=True,
    ),
    SearchField(
        "schema:dateCreated",
        "extensions.dateCreated",
        SearchFieldFamily.SCHEMA,
        SearchFieldType.TEMPORAL,
    ),

    SearchField(
        "schema:datePublished",
        "extensions.datePublished",
        SearchFieldFamily.SCHEMA,
        SearchFieldType.TEMPORAL,
    ),

    SearchField(
        "schema:temporalCoverage",
        "extensions.temporalCoverage",
        SearchFieldFamily.SCHEMA,
        SearchFieldType.TEMPORAL,
    ),

    SearchField(
        "schema:description",
        "extensions.description",
        SearchFieldFamily.SCHEMA,
        SearchFieldType.TEXT,
    ),

    SearchField(
        "schema:license",
        "extensions.license",
        SearchFieldFamily.SCHEMA,
        SearchFieldType.URL,
    ),

    SearchField(
        "schema:isBasedOn",
        "extensions.isBasedOn",
        SearchFieldFamily.SCHEMA,
        SearchFieldType.URL,
    ),

    SearchField(
        "schema:exampleOfWork",
        "extensions.exampleOfWork",
        SearchFieldFamily.SCHEMA,
        SearchFieldType.URL,
    ),

    SearchField(
        "schema:inLanguage",
        "extensions.inLanguage",
        SearchFieldFamily.SCHEMA,
        SearchFieldType.KEYWORD,
        facet=True,
        autocomplete=True,
        multiple=True,
    ),

    SearchField(
        "schema:funder",
        "extensions.funder",
        SearchFieldFamily.SCHEMA,
        SearchFieldType.KEYWORD,
        facet=True,
        autocomplete=True,
        multiple=True,
    ),

    SearchField(
        "schema:associatedMedia",
        "extensions.associatedMedia",
        SearchFieldFamily.SCHEMA,
        SearchFieldType.KEYWORD,
        multiple=True,
    ),

    SearchField(
        "schema:subjectOf",
        "extensions.subjectOf",
        SearchFieldFamily.SCHEMA,
        SearchFieldType.KEYWORD,
        multiple=True,
    ),

    SearchField(
        "schema:about",
        "extensions.about",
        SearchFieldFamily.SCHEMA,
        SearchFieldType.KEYWORD,
        multiple=True,
    ),

    SearchField(
        "schema:@type",
        "extensions.@type",
        SearchFieldFamily.SCHEMA,
        SearchFieldType.KEYWORD,
    ),

    # ==========================================================
    # DoTS extensions
    # ==========================================================

    SearchField(
        "dots:shortTitle",
        "extensions.dots:shortTitle",
        SearchFieldFamily.DOTS,
        SearchFieldType.TEXT,
    ),

    SearchField(
        "dots:resourceIIIFManifest",
        "extensions.dots:resourceIIIFManifest",
        SearchFieldFamily.DOTS,
        SearchFieldType.URL,
    ),

    # ==========================================================
    # Temporal range facets (generated by Thunderdots)
    # ==========================================================

    SearchField(
        id="dct:created:range",
        path="temporal.dublincore.created",
        range_start="temporal.dublincore.created_start",
        range_end="temporal.dublincore.created_end",
        family=SearchFieldFamily.DCT,
        type=SearchFieldType.TEMPORAL,
        facet=True,
    ),

    SearchField(
        id="dct:date:range",
        path="temporal.dublincore.date",
        range_start="temporal.dublincore.date_start",
        range_end="temporal.dublincore.date_end",
        family=SearchFieldFamily.DCT,
        type=SearchFieldType.TEMPORAL,
        facet=True,
    ),

    SearchField(
        id="dct:issued:range",
        path="temporal.dublincore.issued",
        range_start="temporal.dublincore.issued_start",
        range_end="temporal.dublincore.issued_end",
        family=SearchFieldFamily.DCT,
        type=SearchFieldType.TEMPORAL,
        facet=True,
    ),

    SearchField(
        id="dct:coverage:range",
        path="temporal.dublincore.coverage",
        range_start="temporal.dublincore.coverage_start",
        range_end="temporal.dublincore.coverage_end",
        family=SearchFieldFamily.DCT,
        type=SearchFieldType.TEMPORAL,
        facet=True,
    ),

    SearchField(
        id="schema:dateCreated:range",
        path="temporal.extensions.dateCreated",
        range_start="temporal.extensions.dateCreated_start",
        range_end="temporal.extensions.dateCreated_end",
        family=SearchFieldFamily.SCHEMA,
        type=SearchFieldType.TEMPORAL,
        facet=True,
    ),

    SearchField(
        id="schema:datePublished:range",
        path="temporal.extensions.datePublished",
        range_start="temporal.extensions.datePublished_start",
        range_end="temporal.extensions.datePublished_end",
        family=SearchFieldFamily.SCHEMA,
        type=SearchFieldType.TEMPORAL,
        facet=True,
    ),

    SearchField(
        id="schema:temporalCoverage:range",
        path="temporal.extensions.temporalCoverage",
        range_start="temporal.extensions.temporalCoverage_start",
        range_end="temporal.extensions.temporalCoverage_end",
        family=SearchFieldFamily.SCHEMA,
        type=SearchFieldType.TEMPORAL,
        facet=True,
    ),

    # Fragment dates read in the TEI by ThunderDots (FRAGMENT_TEMPORAL_XPATH).
    # The key is always "tei.date" whatever the XPath: the XPath decides what
    # the date means (docDate, any date...), not where it is stored.
    SearchField(
        id="tei:date:range",
        path="temporal.tei.date",
        range_start="temporal.tei.date_start",
        range_end="temporal.tei.date_end",
        family=SearchFieldFamily.THUNDERDOTS,
        type=SearchFieldType.TEMPORAL,
        facet=True,
    ),

]


# ----------------------------------------------------------------------
# Registry accessors -- deliberately kept, commented out
#
# The two lookup tables below belong to get_search_field, their only
# consumer, so they are commented out with it: left live they would be
# built on every import for nothing. Uncomment them together.
#
# Read-only views over SEARCH_FIELDS: look a single field up, or list the
# ones that are indexed, facetable, full-text searchable or temporal, or
# group them by family or by type.
#
# Nothing calls them today. They are commented out rather than deleted
# because they are not a stale duplicate of live code -- unlike the
# temporal helpers that used to sit in this module -- but the exact shape a
# future feature would need:
#
#   - an endpoint publishing the available fields, letting a client
#     discover what a collection can be configured with;
#   - a `dots-es-cli fields` command listing the metadata keys an editor may
#     put in a configuration (searchConfig.facets,
#     searchConfig.temporalFacets, homePageSettings.listSection.columns).
#
# Uncomment what you need rather than rewriting it: these already agree
# with the registry and with the canonical `key` vocabulary.
# ----------------------------------------------------------------------

# SEARCH_FIELDS_BY_ID = {
#     field.id: field
#     for field in SEARCH_FIELDS
# }


# SEARCH_FIELDS_BY_PATH = {
#     field.path: field
#     for field in SEARCH_FIELDS
# }


# def get_search_field(id_or_path: str) -> Optional[SearchField]:
#     """
#     Lookup by id first, then by path.
#     """
#     return (
#         SEARCH_FIELDS_BY_ID.get(id_or_path)
#         or SEARCH_FIELDS_BY_PATH.get(id_or_path)
#     )


# # ----------------------------------------------------------------------
# # Field groups
# # ----------------------------------------------------------------------

# def indexed_fields():
#     """
#     Champs réellement indexés dans les métadonnées de recherche.

#     Les facettes temporelles range ne sont pas indexées ici :
#     elles utilisent directement les champs temporal.*_start/end
#     produits par Thunderdots.
#     """
#     return [
#         field
#         for field in SEARCH_FIELDS
#         if field.index
#         and not field.is_range_facet
#     ]


# def facet_fields():
#     """
#     Facettes classiques (keyword, listes, etc.).

#     Exclut les facettes temporelles range.
#     """
#     return [
#         field
#         for field in SEARCH_FIELDS
#         if field.facet
#         and not field.is_range_facet
#     ]


# def fulltext_fields():
#     return [
#         field
#         for field in SEARCH_FIELDS
#         if field.fulltext
#     ]


# def temporal_fields():
#     """
#     Champs temporels métier.

#     Exemple :
#         dct:created
#         schema:datePublished

#     Ce ne sont pas les champs utilisés pour les ranges.
#     """
#     return [
#         field
#         for field in SEARCH_FIELDS
#         if field.type == SearchFieldType.TEMPORAL
#         and not field.is_range_facet
#     ]


# def fields_by_family(
#     family: SearchFieldFamily
# ):
#     return [
#         field
#         for field in SEARCH_FIELDS
#         if field.family == family
#     ]


# def fields_by_type(
#     type_: SearchFieldType
# ):
#     return [
#         field
#         for field in SEARCH_FIELDS
#         if field.type == type_
#     ]


# ----------------------------------------------------------------------
# Metadata extraction
# ----------------------------------------------------------------------

def get_value(
    document: dict,
    field: SearchField
):
    """
    Retourne la valeur correspondant au path d'un SearchField.
    """

    value = document

    for part in field.path.split("."):

        if not isinstance(value, dict):
            return None

        value = value.get(part)

        if value is None:
            return None

    return value

# ----------------------------------------------------------------------
# Metadata facets helpers
# ----------------------------------------------------------------------

def build_searchfield_aggs(exclude_ids: set[str] | None = None, scope: str = "resource"):
    """
    Build the terms aggregations for the metadata facets.

    exclude_ids: facets explicitly disabled by the client
    (searchConfig.facets, entries with "enabled": false). This mirrors the
    front semantics: a facet missing from the config is still built.
    None / set() => historical behaviour.

    scope: "resource" counts resources on resource_metadata; "fragment"
    counts fragments on fragment_metadata.
    """
    aggs = {}

    for field in facet_fields(exclude_ids, scope):

        if field.type != SearchFieldType.KEYWORD:
            continue

        if scope == "fragment":
            aggs[field.id] = {
                "terms": {
                    "field": f"{get_fragment_es_path(field)}.keyword",
                    "size": 15000
                }
            }
            continue

        aggs[field.id] = {
            "terms": {
                "field": get_es_field(field),
                "size": 15000
            },
            "aggs": {
                "resource_count": {
                    "cardinality": {
                        "field": "resource_id",
                        "precision_threshold": 15000
                    }
                }
            }
        }

    return aggs


def facet_fields(exclude_ids: set[str] | None = None, scope: str = "resource"):
    """
    Metadata facets of the scope, excluded ones left out. A fragment only
    carries Dublin Core and schema.org metadata.
    """
    return [
        field
        for field in SEARCH_FIELDS
        if field.facet
        and not field.is_range_facet
        and not matches_field(field, exclude_ids)
        and (scope == "resource" or get_fragment_es_path(field) is not None)
    ]

def range_field_by_es_path(es_path: str):
    """
    Resolve a temporal field discovered in the Elasticsearch mapping.

    The mapping exposes `temporal.temporal.dublincore.created` -- or
    `fragment_temporal.temporal.dublincore.created` for the dates of a
    fragment -- while the registry stores the inner path
    `temporal.dublincore.created`, so each spelling is tried.
    """
    inner_path = es_path.removeprefix("fragment_temporal.")

    candidates = (es_path, inner_path, inner_path.removeprefix("temporal."))

    for candidate in candidates:
        for field in SEARCH_FIELDS:
            if field.is_range_facet and field.path == candidate:
                return field

    return None


def resolve_field(name: str, range_facet: bool | None = None):
    """
    Look a field up by its canonical metadata key.

    `range_facet` disambiguates the two entries that share a key: pass True
    for the temporal range facet, False for the plain property, None when
    either will do.
    """
    candidates = SEARCH_FIELDS

    if range_facet is not None:
        candidates = [
            f for f in SEARCH_FIELDS
            if f.is_range_facet is range_facet
        ]

    for field in candidates:
        if field.key == name:
            return field

    return None


def matches_field(field: SearchField, names) -> bool:
    """
    True when `names` designates `field` by its canonical key.
    """
    return bool(names) and field.key in names


def get_facet_es_field(facet_id, scope: str = "resource"):

    # Facette spéciale collections
    if facet_id == "collections":
        return "collection_facets"

    field = resolve_field(facet_id)

    if field is not None and scope == "resource":
        return get_es_field(field)

    fragment_path = get_fragment_es_path(field) if field is not None else None

    if fragment_path is not None:
        return f"{fragment_path}.keyword" if field.type == SearchFieldType.KEYWORD else fragment_path

    raise ValueError(
        f"Unknown facet field {facet_id} at {scope} scope"
    )

def extract_searchfield_facets(aggregations, exclude_ids: set[str] | None = None, scope: str = "resource"):
    """
    Extract the terms facets from the ES result.

    exclude_ids and scope must mirror the ones passed to
    build_searchfield_aggs, otherwise empty facets would be returned for the
    aggregations that were never requested.
    """
    facets = {}

    for field in facet_fields(exclude_ids, scope):

        buckets = aggregations.get(field.id, {}).get("buckets", [])

        # The aggregation is named after the internal id, but the facet is
        # published under its canonical key: that is the only vocabulary a
        # configuration should ever need to know about.
        facets[field.key] = [
            {
                "value": bucket["key"],
                # Resources at resource scope, fragments at fragment scope
                "count": bucket["resource_count"]["value"] if scope == "resource" else bucket["doc_count"]
            }
            for bucket in buckets
        ]

    return facets


# Property families indexed under `resource_metadata`,
# and therefore covered by the `resource_metadata.*`
# dynamic template from `dots_document.conf.json`.
METADATA_FAMILIES = (
    SearchFieldFamily.DCT,
    SearchFieldFamily.SCHEMA,
    SearchFieldFamily.DOTS,
)


# Sortable property families. Sorting always applies to Resources, whether
# queried directly or rebuilt by collapsing fragments. Their properties,
# including DTS fields, are indexed under `resource_metadata`.
# The root `title` belongs to the fragment, not the Resource: never sorted on
SORTABLE_FAMILIES = METADATA_FAMILIES + (SearchFieldFamily.DTS,)


def get_es_path(field: SearchField) -> str:
    """
    Index field path, without a sub-field.
    """
    if field.family in METADATA_FAMILIES:
        return f"resource_metadata.{field.path}"

    return field.path


def get_fragment_es_path(field: SearchField) -> Optional[str]:
    """
    Path of the field in a fragment's own metadata, or None: a fragment only
    carries Dublin Core and schema.org metadata (`fragment_metadata`).
    """
    if field.family in (SearchFieldFamily.DCT, SearchFieldFamily.SCHEMA):
        return f"fragment_metadata.{field.path}"

    return None


def get_es_field(field: SearchField) -> str:
    path = get_es_path(field)

    if field.type == SearchFieldType.KEYWORD:
        path += ".keyword"

    return path


def get_es_sort_field(field: SearchField) -> Optional[str]:
    """
    ES field to sort this SearchField on, or `None` if it is not sortable.

    Sorting never uses `.keyword`: its terms are raw, so accented values
    are not ordered with their base letter. The `.sort` sub-field uses the
    `sortable` normalizer (lowercase + asciifolding) for consistent ordering.
    It is `index: false`; doc_values are sufficient for sorting.
    """
    if field.type == SearchFieldType.TEMPORAL:
        # Dates are sorted by their numeric start bound, never by the original
        # string (`"1245-1250"`). The bound is stored in the range entry sharing
        # the field's key.

        range_field = (
            field
            if field.is_range_facet
            else resolve_field(field.key, range_facet=True)
        )

        if range_field is None:
            return None

        # The registry stores the internal path; the mapping exposes these fields
        # under `temporal.` — see `range_field_by_es_path`.
        return f"temporal.{range_field.range_start}"

    # URL included: these are strings, indexed like any other by the dynamic
    # templates, and so carry the same `sort` sub-field.
    if field.type in (
        SearchFieldType.TEXT,
        SearchFieldType.KEYWORD,
        SearchFieldType.URL,
    ):
        if field.family in SORTABLE_FAMILIES:
            return f"resource_metadata.{field.path}.sort"

        # content, path_ids, ancestors...: no `sort` sub-field, and no
        # meaning as a sort criterion either.
        return None

    if field.type == SearchFieldType.INTEGER:
        return get_es_path(field)

    return None


def resolve_sort_field(criteria: str) -> str:
    """
    Translate a sort criterion received from the client into an ES field.

    Two vocabularies are accepted:

        dublinCore.title                            (canonical key)
        resource_metadata.dublincore.title.keyword  (raw ES path)

    and both resolve to:

        resource_metadata.dublincore.title.sort

    An unresolved criterion is returned unchanged: the historical
    behaviour, leaving ES to report an unknown field.
    """
    field = resolve_field(criteria)

    if field is not None:
        es_field = get_es_sort_field(field)

        if es_field is not None:
            return es_field

    # Raw ES path. The `.keyword` suffix attests to a string field, hence to
    # the `.sort` sub-field beside it; without it nothing is rewritten, so as
    # never to invent a `.sort` on a numeric field.
    if criteria.endswith(".keyword"):
        base = criteria[: -len(".keyword")]

        if base.startswith(("resource_metadata.", "fragment_metadata.")):
            return f"{base}.sort"

    return criteria


def resolve_fragment_sort_field(criteria: str) -> str:
    """
    Sort criterion of a fragment search, on the fragment's own metadata and
    dates only; a resource-only criterion raises ValueError.

        dublinCore.date   -> fragment_temporal.temporal.dublincore.date_start
        dublinCore.title  -> fragment_metadata.dublincore.title.sort
    """
    field = resolve_field(criteria)

    if field is not None:
        es_field = get_es_sort_field(field)

        if es_field is not None and es_field.startswith("temporal."):
            return f"fragment_{es_field}"

        fragment_path = get_fragment_es_path(field)

        if es_field is not None and fragment_path is not None:
            return f"{fragment_path}.sort"

    # Raw ES paths of the fragment level
    if criteria.startswith("fragment_metadata.") and criteria.endswith(".keyword"):
        return f"{criteria[: -len('.keyword')]}.sort"

    if criteria.startswith(("fragment_metadata.", "fragment_temporal.")) or criteria in ("passage_id", "level"):
        return criteria

    raise ValueError(f"Sort criterion {criteria!r} is not available at fragment scope")


# Signed years before year 0 in astronomical numbering ("-0500-01-01" is
# 501 BC), as ThunderDots writes them from its EDTF parser.
ISO_DATE_RE = re.compile(r"-?\d{4}-\d{2}-\d{2}")


def is_indexable_iso_date(value) -> bool:
    """
    True for a [-]AAAA-MM-JJ date that the `strict_date` format of the
    temporal mapping accepts.
    """
    return (
        isinstance(value, str)
        and ISO_DATE_RE.fullmatch(value) is not None
    )


def build_filtered_temporal_metadata(
    temporal_metadata: dict,
) -> dict:
    """
    Filtre le temporal produit par Thunderdots pour son indexation (CLI).

    Le temporal Thunderdots est sans préfixe "temporal.".
    Le contrat SearchField utilise les chemins ES complets.

    Les dates sources sont prises pour de l'ISO 8601 / EDTF, sans
    interprétation : "-0500" est 501 av. J.-C. et "0000" 1 av. J.-C.
    (numérotation astronomique). Fournir la bonne valeur est à la charge
    des éditeurs, en amont de Thunderdots.

    Garde uniquement :
    - les champs range déclarés dans SEARCH_FIELDS
    - leurs bornes en années (_start / _end, integer)
    - leurs bornes ISO (_start_iso / _end_iso, date), qui gardent la
      précision de la valeur : "1241-03" donne 1241-03-01 / 1241-03-31

    Supprime :
    - les champs temporels bruts
    - les artefacts extensions.@context
    - les bornes ISO qui ne sont pas des dates [-]AAAA-MM-JJ : avant son
      parser EDTF, Thunderdots renvoyait l'année seule pour une année <= 0
      ("-50"), qu'un champ date lirait comme des millisecondes depuis 1970 ;
      les années de plus de 4 chiffres ("+170000002-01-01") que strict_date
      refuse
    """

    allowed = {}

    range_fields = {
        field.path: field
        for field in SEARCH_FIELDS
        if field.is_range_facet
    }

    for logical_path, field in range_fields.items():

        for year_path, iso_path in (
            (field.range_start, field.range_start_iso),
            (field.range_end, field.range_end_iso),
        ):

            if not year_path:
                continue

            # SearchField :
            # temporal.dublincore.created_start
            #
            # Thunderdots :
            # dublincore.created_start
            year = temporal_metadata.get(
                year_path.removeprefix("temporal.")
            )

            if year is not None:
                allowed[year_path] = year

            iso = temporal_metadata.get(
                iso_path.removeprefix("temporal.")
            )

            if is_indexable_iso_date(iso):
                allowed[iso_path] = iso

    return allowed