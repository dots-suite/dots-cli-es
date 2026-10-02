# Indexing

```bash
dots-es-cli [--config local|staging|prod] index [--collections id1,id2]
```

## What a run does

1. **Build the ThunderDots graph.** `ThunderDots` is instantiated with the configured `DTS_URL` and
   `TARGET_COLLECTION`, then `fetch()` walks the whole tree in one pass. Its `results()` give
   `collection_results` and `resource_results`; `to_elastic_documents()` additionally yields the
   `temporal` block of each resource.
2. **Crawl from memory.** The crawler resolves each collection and resource from the dictionaries
   built at step 1 instead of issuing an HTTP request per node. Records are normalised and written as
   JSONL under `out/`.
3. **Ensure the indexes exist.** For each of `DOCUMENT_INDEX`, `RESOURCE_INDEX` and `COLLECTION_INDEX`: if missing, it
   is created from the [mapping files](elasticsearch.md); if present, a
   [mapping-drift warning](elasticsearch.md#mapping-drift) is printed when the live `dynamic` setting
   differs from the conf.
4. **Bulk-index passages** into `DOCUMENT_INDEX`, with `_id = "{resource_id}::{passage_id}"`.
5. **Index resource documents** into `RESOURCE_INDEX`, one by one.
6. **Index collections** into `COLLECTION_INDEX`.
7. **Print a summary** and write the [CSV reports](reporting.md).

!!! info "Fragments come from ThunderDots, not from TEI parsing here"
    Passages are built **only** from the pre-normalised `fragments` returned by ThunderDots
    (`fragment_mode="navigation"`): no TEI parsing and no navigation index are performed in this
    project. A resource with no fragments falls back to a single `__fulltext__` passage built from its
    plain text.

    A fragment carries `id`, `level`, `head`, `content`, `citeType`, `parent`, its own `metadata`
    (`dublincore` and `extensions` from the DTS navigation member, `tei` from
    [`FRAGMENT_TEMPORAL_XPATH`](configuration.md)) and its own `temporal` index.

## Resource and fragment metadata

Resource and fragment metadata are stored in two indexes:

| Field | Index | Source |
|---|---|---|
| `resource_metadata`, `temporal`, `collections` | `RESOURCE_INDEX` | The resource, stored once. A passage only carries its `resource_id`, plus `path_ids` and `collection_facets` for scoping. |
| `fragment_metadata`, `fragment_temporal` | `DOCUMENT_INDEX` | The fragment itself. Empty when the DTS server describes nothing at that level. |

ThunderDots never copies resource metadata or dates into fragments, and neither does this project:
a cartulaire act dated March 1241 has `fragment_temporal` 1241, while its `temporal` holds the
coverage of the whole cartulaire (1204–1715) and the date of the edition.

`fragment_metadata.dublincore` and `fragment_metadata.extensions` follow the same
[SEARCH_FIELDS contract](search-fields.md) as the resource metadata; `fragment_temporal` goes through
the same `build_filtered_temporal_metadata()`.

## Options

| Option | Default | Effect |
|---|---|---|
| `--collections` / `-c` | *none* | Comma-separated collection ids, **case-sensitive** (`ENCPOS`, not `encpos`). Restricts the crawl; without it, the whole tree under `TARGET_COLLECTION` is walked. |

## Examples

```bash
# full local run
dots-es-cli --config=local index

# production, with ES security
ES_PASSWORD=your_password dots-es-cli --config=prod index

# only two collections
dots-es-cli --config=staging index --collections=theater,ENCPOS
```

## Excluded collections

Two sources are merged into a single case-insensitive exclusion set:

- `ADDITIONAL_EXCLUDED_COLLECTIONS` from the [YAML file](configuration.md);
- every `excludeCollectionIds` entry found in the `*.conf.json` files under `CUSTOM_SETTINGS_PATH`.

The end-of-crawl summary reports how many collections were skipped this way.

## Residual direct DTS calls

ThunderDots covers the corpus walk, but a few HTTP calls to the DTS endpoint remain:

- resolving the root collection and its title at the start of the run;
- rebuilding the parent chain when `--collections` is used.

They are the reason `DTS_URL` must stay reachable even though the graph is prefetched.

## After the run

Refresh and count:

```bash
curl -X POST "http://localhost:9200/dots_document/_refresh?pretty"
curl "http://localhost:9200/_cat/indices?v"
```

Then read the [indexing reports](reporting.md) — a run that prints no fatal error can still have
dropped documents, and only the CSV files will tell you.
