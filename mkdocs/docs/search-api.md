# Search API

```bash
dots-api [--config local|staging|prod]
```

The Flask application listens on **port 5003**, on `localhost`, with debug enabled. On servers it is
served through uWSGI as `flask_app:flask_app`, and the `SERVER_ENV_CONFIG` environment variable
overrides `--config`.

Smoke test:

```
http://localhost:5003/api/1.0/search?query=*&index=dots_document
```

## The endpoint

A single route is exposed:

```
GET /api/1.0/search
```

Responses are `application/json; charset=utf-8` with `Access-Control-Allow-Origin: *`. Errors return
**HTTP 400** with the exception text as the body.

## Two modes

The `no-highlight` parameter is a **mode switch**:

=== "Full-text mode (default)"

    `no-highlight` absent. Runs in three requests, since passages only carry the `resource_id` of
    their resource:

    1. the text query on the passages lists the matching resources, with their number of matching
       passages and the score of their best passage (composite aggregation, not capped);
    2. `RESOURCE_INDEX` applies the facets, filters and date ranges to these resources, counts the
       facets, sorts and paginates; a resource scores as its best passage;
    3. the passages of the page's resources are collapsed by `resource_id` with `inner_hits` named
       `fragments`, and `content` is highlighted with the **`fvh`** highlighter (`<mark>` tags,
       `fragment_size: 80`, `number_of_fragments: 100`, `fragment_offset: 25`, `no_match_size: 50`).

    Response: `{buckets, facets, bucket_count, total_count, page, page_size, highlight_patterns, temporal}`.

=== "Notice mode"

    `no-highlight` present. Returns resource records rather than highlighted fragments.

    Response: `{data, total_count, facets, highlight_patterns, temporal}`, where each `data` item is a
    `resource_id` plus flattened `resource_metadata` and unflattened `temporal`.

!!! warning "Presence, not value"
    The switch tests whether the parameter is a string, so **`no-highlight=false` also enables notice
    mode**. Omit the parameter entirely to stay in full-text mode.

Both responses also carry `collection_indexed`, the `scope` used and a `duration` in seconds.

## Resource or fragment scope

Two parameters combine into four searches:

- **`scope`** sets the level: the documents searched, the metadata and dates used to filter, facet
  and sort, and the shape of the results;
- **`no-highlight`** only sets what the query reads: the description (notice mode) or the text
  (full-text mode).

| | `scope=resource` (default) | `scope=fragment` |
|---|---|---|
| **Notice** (`no-highlight`) | Resource records (above). | Fragments, queried on `title`, `fragment_metadata.dublincore.title`, `fragment_metadata.extensions.name`. |
| **Full-text** | Fragments grouped by resource (above). | Fragments, queried on `content` and `title`, highlighted. |
| Metadata and dates | `resource_metadata`, `temporal` | `fragment_metadata`, `fragment_temporal` |
| Results | `data` (notice) or `buckets` (full-text) | `data`: one item per fragment, never grouped |

The two levels never mix. A resource search uses no fragment metadata or date, and a fragment search
no resource metadata or date, whether for filtering, facets or sorting. The only resource data in a
fragment search is the collection perimeter (`collectionId`, `collections` facet) and the resource
title and path returned for display.

At fragment scope:

- **results** are a flat list, so sorting and pagination apply to fragments: `sort=dublinCore.date`
  lists the acts from the oldest to the most recent, and `total_count` counts fragments. Each item
  carries `passage_id`, `title`, `level`, `citeType`, `ancestors`, its `metadata`
  (`fragment_metadata`), its unflattened `temporal`, `highlight.content`, and, for display,
  `resource_id`, `resource_title` and `path`;
- **the default sort** is the score, then the document order (`resource_id`, `passage_id`), which
  also breaks ties after an explicit `sort`;
- **metadata facets** are the same facets as at resource scope, read in `fragment_metadata` and
  counting fragments; `facets={"dublinCore.language": ["lat"]}` filters on the fragment's language.
  The `collections` facet also counts fragments;
- **temporal facets** are discovered under `fragment_temporal.*`, so their `start_field` /
  `end_field` point there and the front-end sends its ranges back on those fields;
- **undated fragments are kept, after the dated ones.** As at resource scope, a date range is open:
  a fragment without the date still matches, but each satisfied range adds to its score. Without a
  text query the order is strict; with one, a very relevant undated fragment can still rank above a
  weakly relevant dated one. An explicit `sort` replaces this order;
- **notice mode** has no match in `content`: `highlight.content` holds its first 50 characters as a
  preview (`no_match_size`).

A `range[...]` on the dates of the other level (`fragment_temporal.*` at resource scope,
`temporal.*` at fragment scope), a `sort` criterion that only exists for resources (`title`,
`resource_metadata.*`) at fragment scope, and an unknown `scope` return **HTTP 400**. The client
resets its date ranges and sort when the scope changes.

## Query parameters

| Parameter | Default | Effect |
|---|---|---|
| `index` | `DOCUMENT_INDEX` | Elasticsearch index of the passages. |
| `resourceIndex` | `RESOURCE_INDEX` | Elasticsearch index of the resources. |
| `query` | `match_all` | Supports exact phrases, `AND`/`OR`/`NOT`, `*` and `?` wildcards, and `field:value` with aliases. Default operator is `AND`, wildcards are analyzed. The available field aliases differ between the two modes. |
| `no-highlight` | absent | Mode switch, see above. |
| `scope` | `resource` | `resource` or `fragment`, see [above](#resource-or-fragment-scope). |
| `collectionId` | none | Scopes the search to a collection subtree; also drives `collection_indexed` in the response. |
| `collections` | none | `[a,b]` list of collection keys to **remove** from the returned collection facets. |
| `facets` | none | JSON object `{canonical_key: [values]}` of selected facet values. `collections` uses OR logic; every other facet uses AND. |
| `excludeFacets` <sup>*</sup> | none | Comma-separated canonical keys not to compute or return. `collections` is a valid value. |
| `excludeTemporalFacets` <sup>*</sup> | none | Same, for temporal range facets. |
| `range[<field>]` | none | Repeated-key syntax, e.g. `range[temporal.temporal.dublincore.created_start]=gte:1200,lte:1300`. Operators: `gt`, `gte`, `lt`, `lte`. See [Date ranges](#date-ranges). |
| `filters` | none | `field:value1\|value2,field2:value3` — one clause per comma, values within a field joined with `OR`. Each clause becomes a `query_string` restricted to that field. |
| `page[number]` | `1` | Offset pagination. |
| `page[size]` | `SEARCH_RESULT_PER_PAGE` (200) | **Minimum 25**, no maximum. |
| `sort` | `dublinCore.created` ascending, then `_score` descending | Comma-separated criteria; a `-` prefix means descending. Missing values sort last. |

<sup>*</sup> These two parameters exist mainly for the
[dots-vue](https://github.com/dots-suite/dots-vue) front-end and the per-collection settings it
reads. You rarely build them by hand: see [Using custom settings](custom-settings.md), and the
example repository [dots-vue-demo-settings](https://github.com/dots-suite/dots-vue-demo-settings).

!!! tip "`filters` versus `facets`"
    Both narrow the result set, but they are not interchangeable. `facets` takes a JSON object keyed
    by **canonical** metadata keys and is what the front-end sends when a user ticks a facet value;
    `filters` is a compact string form resolved directly against **Elasticsearch field names**, which
    makes it handy for hand-written queries and debugging.

    ```
    filters=resource_metadata.dublincore.creator:Molière|Racine
    ```

## Date ranges

Each temporal facet in the `temporal` part of the response gives the fields to filter on:

```json
{
  "key": "dublinCore.date",
  "start_field": "fragment_temporal.temporal.dublincore.date_start",
  "end_field": "fragment_temporal.temporal.dublincore.date_end",
  "min": 528,
  "max": 1715,
  "start_field_iso": "fragment_temporal.temporal.dublincore.date_start_iso",
  "end_field_iso": "fragment_temporal.temporal.dublincore.date_end_iso",
  "min_iso": "0528-01-01",
  "max_iso": "1715-07-22"
}
```

The `*_iso` keys are only present when the index holds ISO bounds for that facet.

- **Year bounds** (`start_field`, `end_field`) take integers.
- **ISO bounds** (`start_field_iso`, `end_field_iso`) take `YYYY`, `YYYY-MM` or `YYYY-MM-DD`, with a
  leading `-` before year 1. Each value is rounded to its own precision, so `lte:1241-03` reaches
  31 March 1241 and `gte:1241` starts on 1 January. Any other value returns **HTTP 400**.

A period is matched by **overlap**: send `lte` on the start field and `gte` on the end field.

```
range[fragment_temporal.temporal.dublincore.date_start_iso]=lte:1241-03
range[fragment_temporal.temporal.dublincore.date_end_iso]=gte:1241-03
```

This matches the acts of March 1241, and also an act dated only "1241", whose year overlaps March.

## Facets

Facet aggregations are generated from the [search field registry](search-fields.md): one `terms`
aggregation per non-range `KEYWORD` field declared with `facet=True`. At resource scope it runs on
`RESOURCE_INDEX`, one document per resource, so its `doc_count` is the **exact number of resources**;
at fragment scope it counts fragments.

Buckets are computed under the field `id` and republished to clients under the canonical `key`
(`dct:creator` → `dublinCore.creator`).

## Sorting

- Temporal fields sort on their `temporal.{range_start}` bound.
- Text, keyword and URL fields sort on the `.sort` sub-field produced by the `sortable` normalizer —
  never on `.keyword`. That normalizer strips leading punctuation, lowercases and folds accents, which
  is what makes `« Tragédie »` sort next to `Tragédie`.
