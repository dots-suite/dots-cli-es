# Elasticsearch mappings

Three JSON files shipped with the package define how the indexes are built:

```
dots_es/elasticsearch/
├── _global.conf.json          # settings, applied to both indexes
├── dots_collection.conf.json  # mappings for the collections index
└── dots_document.conf.json    # mappings for the resources + passages index
```

!!! warning "File name must equal index name"
    `update-conf` looks for `{index_name}.conf.json`. If you rename `DOCUMENT_INDEX` in the YAML
    without renaming the JSON file accordingly, the command prints *"conf not found"* and moves on —
    the index is created with no mapping at all.

## `_global.conf.json` — shared settings

| Element | Definition |
|---|---|
| `index.number_of_replicas` | `0` |
| filter `french_elision` | Elision, case-insensitive, 13 articles (`l`, `m`, `t`, `qu`, `n`, `s`, `j`, `d`, `c`, `jusqu`, `quoiqu`, `lorsqu`, `puisqu`). |
| char filter `html_stripper` | `html_strip`. |
| char filter `strip_leading_punctuation` | `pattern_replace` removing `^[^\p{L}\p{N}]+`, so leading punctuation does not break sorting. |
| normalizer `sortable` | `strip_leading_punctuation` + `lowercase` + `asciifolding`. |
| analyzer `folding` | Standard tokenizer, French stopwords, `french_elision` + **`icu_folding`**, `html_stripper`. |
| analyzer `keyword` | Keyword tokenizer, French stopwords, `french_elision` + `icu_folding`. |

!!! danger "ICU plugin required"
    `folding` depends on `icu_folding`. Without the `analysis-icu` plugin installed in Elasticsearch,
    creating the indexes fails. See [Installation](installation.md).

The declared `keyword` *analyzer* is not referenced by either mapping file — `dots_collection` uses
the built-in `french` analyzer instead.

## `dots_collection.conf.json`

`"dynamic": "strict"`, with explicit properties only:

- `id`, `type`, `path`, `path_ids`, `parent_id`, `dtsVersion` — `keyword`;
- `title` — `text` / `french`, with a `raw` keyword sub-field; `description` — `text` / `french`;
- `level`, `totalItems`, `totalChildren`, `totalParents` — `integer`;
- `download`, `dublincore`, `extensions` — `object`, `dynamic: true`;
- `members` — `object` with **`"enabled": false`**.

!!! info "Why `members` is disabled"
    Members are stored but not indexed. This is deliberate: indexing them would create one field per
    member id and blow through the field-count limit.

## `dots_document.conf.json`

`"dynamic": "strict"` plus ten **dynamic templates**, which is what lets new metadata fields appear
without a mapping change:

| Template | Matches | Mapped as |
|---|---|---|
| `temporal_dates` | `temporal.*_iso` | `date`, format `strict_date` (`YYYY-MM-DD` only) |
| `temporal_years` | `temporal.*_start` | `integer` |
| `temporal_years_end` | `temporal.*_end` | `integer` |
| `temporal_strings` | `temporal.*` (string) | `keyword` |
| `fragment_temporal_dates`, `fragment_temporal_years`, `fragment_temporal_years_end`, `fragment_temporal_strings` | `fragment_temporal.*`, same suffixes | same as the `temporal_*` templates |
| `resource_metadata_strings` | `resource_metadata.*` (string) | `text` / `folding`, `term_vector: with_positions_offsets`, sub-fields `keyword` and `sort` |
| `fragment_metadata_strings` | `fragment_metadata.*` (string) | same as above |

Explicit properties include `resource_id`, `passage_id`, `citeType`, `path`, `path_ids`,
`collection_facets` (keyword), `level` (integer), `title` and `content` (`text` / `folding`), the dynamic
objects `resource_metadata`, `fragment_metadata`, `temporal` and `fragment_temporal`, plus two
**nested** objects: `ancestors` and `collections`.

!!! warning "New top-level fields need a mapping change"
    Because the root is `strict`, a document carrying a top-level field that the conf does not
    declare is rejected. `fragment_temporal` was added this way: an index created before it must be
    rebuilt (`update-conf --rebuild`) and reindexed.

!!! important "`term_vector` is not optional"
    `content` is mapped with `term_vector: with_positions_offsets` because the search API uses the
    **`fvh`** highlighter, which requires it. Removing it silently breaks highlighting.

## Applying the configuration

```bash
# both indexes
dots-es-cli --config=local update-conf --rebuild

# a single index
ES_PASSWORD=xxx dots-es-cli --config=prod update-conf --rebuild --indexes=dots_document
```

What the command does, per index:

1. with `--rebuild`, `DELETE` the index;
2. load `_global.conf.json` and `{index_name}.conf.json`;
3. inject the global `settings` into the payload;
4. `PUT` the index with `{settings, mappings}`.

Without `--rebuild` on an index that already exists, Elasticsearch answers
`resource_already_exists_exception` and the CLI tells you explicitly to rerun with `--rebuild`.

!!! danger "`--rebuild` always means data loss"
    The index is dropped, so a full reindex is required afterwards. There is no in-place mapping
    update path.

## Mapping drift

When `index` runs against an existing index, it compares the live `dynamic` setting with the one in
the conf file and prints a warning such as:

```
⚠️ Index dots_document mapping 'dynamic=true' differs from conf
```

That warning means the conf file was **never applied** to this index — run `update-conf --rebuild`
followed by a full reindex.
