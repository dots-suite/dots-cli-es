# Search fields

`dots_es/api/search_fields.py` holds a single registry, `SEARCH_FIELDS`, that declares every metadata
field the application knows about. It is the **one place** to edit when you want a new field indexed,
facetable or sortable: the same declaration drives filtering at index time *and* aggregations, sorting
and query aliases at search time.

## The `SearchField` dataclass

| Attribute | Default | Meaning |
|---|---|---|
| `id` | — | Internal identifier, e.g. `dct:creator`. Also the name of the Elasticsearch aggregation. |
| `path` | — | Source and index path, e.g. `dublincore.creator`. |
| `family` | — | One of `CLI`, `DTS`, `DCT`, `SCHEMA`, `DOTS`, `THUNDERDOTS`. Drives ES prefixing and sortability. |
| `type` | — | One of `KEYWORD`, `TEXT`, `TEMPORAL`, `URL`, `INTEGER`. Drives `.keyword` suffixing, aggregation eligibility and sort strategy. |
| `index` | `True` | The field is kept in `resource_metadata` by the indexer. |
| `facet` | `False` | Build a `terms` aggregation for this field. |
| `autocomplete` | `False` | Not operational yet — see the note below. |
| `fulltext` | `False` | Only `content` declares it. Not operational yet. |
| `multiple` | `False` | Not operational yet. |
| `range_start` / `range_end` | `None` | ES paths of the numeric bounds of a temporal range facet. |

Two derived properties matter:

- **`key`** — the *canonical* metadata key exposed to clients. `dublincore.created` and
  `temporal.dublincore.created` both surface as `dublinCore.created`. Facets are published under this
  key, not under `id`.
- **`is_range_facet`** — true when `facet` is set together with `range_start` and `range_end`.

!!! note "Flags that are not operational yet"
    `autocomplete`, `fulltext` and `multiple` can be declared on a field, but they are **not
    operational at the moment**: setting them changes neither indexing nor querying.

## Families

`CLI`, `DTS`, `DCT` (Dublin Core), `SCHEMA` (schema.org), `DOTS` (DoTS extensions) and `THUNDERDOTS`.

Two groupings drive the Elasticsearch paths:

- `METADATA_FAMILIES = (DCT, SCHEMA, DOTS)` — these are prefixed with `resource_metadata.` when
  queried;
- `SORTABLE_FAMILIES = METADATA_FAMILIES + (DTS,)`.

The registry is organised in blocks: CLI navigation fields (`parent_id`, `path`, `path_ids`,
`ancestors`), DTS fields (`id`, `type`, `title`, `description`, `download`, `content`), Dublin Core,
schema.org, DoTS extensions, and finally temporal range facets generated from ThunderDots output.

## What the registry does at index time

`extract_metadata()` filters incoming DTS metadata against the registry:

- allowed Dublin Core keys are the `path` of every `DCT` field, minus the `dublincore.` prefix;
- allowed extension keys are the `path` of every `SCHEMA` field;
- **anything not declared is dropped.**

Fragment metadata goes through the same filter (`extract_fragment_metadata()`), so the contract is
identical at both levels. The optional `tei` block of a fragment is kept as is.

Temporal metadata goes through `build_filtered_temporal_metadata()`, which keeps only the bounds of
declared range facets and discards raw values. It is applied to the resource `temporal` and to each
`fragment_temporal`. Each bound is kept twice:

| Bound | Type | Example for `1241-03` |
|---|---|---|
| `range_start` / `range_end` (`…_start`, `…_end`) | `integer`, year | `1241` / `1241` |
| `range_start_iso` / `range_end_iso` (`…_start_iso`, `…_end_iso`) | `date`, `strict_date` | `1241-03-01` / `1241-03-31` |

The ISO bounds keep the precision of the source value: a year covers the whole year, a month the
whole month, a day that single day.

ISO bounds that are not `[-]YYYY-MM-DD` dates are dropped, and the year bound is kept. Before
ThunderDots 0.1.8, the ISO bound of a year ≤ 0 was the bare year (`-50`), which Elasticsearch would
read as epoch milliseconds (1969-12-31) under the default date format, and reject with the whole
passage under `strict_date`. Years beyond four digits (`+170000002-01-01`) are dropped for the same
reason.

### Dates are ISO 8601 / EDTF

Source dates are taken as ISO 8601 / EDTF, **without interpretation**. Years are therefore
astronomical, in the index, the API and the facets alike:

| Source value | Meaning | Indexed years | Indexed ISO bounds |
|---|---|---|---|
| `-0500/0499` | 501 BC → 499 | `-500` / `499` | `-0500-01-01` / `0499-12-31` |
| `-0499` | 500 BC | `-499` / `-499` | `-0499-01-01` / `-0499-12-31` |
| `0000` | 1 BC | `0` / `0` | `0000-01-01` / `0000-12-31` |
| `XXXX-11-21` | a day, year unknown | none | none |

The indexer cannot tell an editor who wrote `-0500` for 500 BC from one who meant 501 BC, so it does
not try: providing the right EDTF value is up to the editors, upstream of ThunderDots. A day without a
year must be written `XXXX-MM-DD`; `0000-MM-DD` is a day of 1 BC.

!!! note "`DOTS` fields are declared but deliberately not indexed"
    The extension whitelist collects only fields whose family is `SCHEMA`, so the two `DOTS`
    extensions — `dots:shortTitle` and `dots:resourceIIIFManifest` — never reach the index, even
    though they carry the default `index=True`.

    **This is a deliberate choice for the time being**: these two fields are not meant to be indexed
    yet. Adding `SearchFieldFamily.DOTS` to the whitelist in `extract_metadata` is all it would take
    to start writing them into every resource document — so do not treat the current behaviour as a
    bug to be fixed.

## What the registry does at query time

| Helper | Role |
|---|---|
| `get_es_path` | Prefixes `resource_metadata.` for `DCT`/`SCHEMA`/`DOTS`; other families use `path` verbatim. |
| `get_es_field` | Appends `.keyword` for `KEYWORD` fields. |
| `build_searchfield_aggs` | One `terms` aggregation per non-range `KEYWORD` facet, named after `field.id`, with a nested `cardinality` sub-aggregation on `resource_id` so counts are **per resource**, not per fragment. |
| `extract_searchfield_facets` | Reads buckets by `field.id` and republishes them under `field.key`. |
| `get_es_sort_field` / `resolve_sort_field` | `TEMPORAL` sorts on `temporal.{range_start}`; text, keyword and URL fields sort on the `.sort` sub-field (normalizer `sortable`), never on `.keyword`. |

## Adding a field

Add one entry to the relevant block. For a facetable Dublin Core subject:

```python title="dots_es/api/search_fields.py"
SearchField(
    "dct:subject",
    "dublincore.subject",
    SearchFieldFamily.DCT,
    SearchFieldType.KEYWORD,
    facet=True,
),
```

With no other code change, this single declaration:

1. lets `dublincore.subject` survive the `extract_metadata` filter;
2. indexes it at `resource_metadata.dublincore.subject`, typed by the `resource_metadata.*` dynamic
   template;
3. adds an aggregation named `dct:subject` on `resource_metadata.dublincore.subject.keyword`;
4. publishes the facet to clients as `dublinCore.subject`;
5. makes `sort=dublinCore.subject` resolve to `resource_metadata.dublincore.subject.sort`.

!!! tip "Reindex, but usually no `--rebuild`"
    Because string metadata is mapped by a **dynamic template** rather than by explicit properties, a
    new string field usually needs no `update-conf --rebuild`. It does need a **reindex**, since
    documents indexed earlier dropped the value.

For a temporal range facet, declare a second entry whose `id` ends in `:range` and which carries
`range_start` / `range_end`, following the pattern of the existing temporal block.

