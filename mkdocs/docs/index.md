# DoTS Search API

**DoTS Search API** (package `dots_es`, repository `dots-cli-es`) indexes TEI resources published through a
[DoTS](https://github.com/dots-suite/dots) endpoint into [Elasticsearch](https://www.elastic.co/), via
[ThunderDots](https://github.com/dots-suite/ThunderDots), and exposes a search API on top of the ES index.

It ships two console scripts:

- **`dots-es-cli`** — the indexing CLI: crawls a DTS collection tree and populates the Elasticsearch indexes;
- **`dots-api`** — a Flask application exposing `GET /api/1.0/search`, consumed by the
  [dots-vue](https://github.com/dots-suite/dots-vue) front-end.

## How the pieces fit together

```
       TEI corpus
            │
            ▼
    ┌───────────────┐
    │     DoTS      │   DTS API: collections, resources, navigation
    └───────────────┘
            │  DTS over HTTP
            ▼
    ┌───────────────┐
    │  ThunderDots  │   walks the tree, fetches TEI,
    └───────────────┘   extracts fragments, normalises metadata
            │  in-process (Python objects)
            ▼
    ┌───────────────┐
    │  dots-es-cli  │   ← this project
    └───────────────┘   builds Elasticsearch documents
            │
            ▼
    ┌───────────────┐
    │ Elasticsearch │   dots_document · dots_resources · dots_collection
    └───────────────┘
            │
            ▼
    ┌───────────────┐
    │    dots-api   │   GET /api/1.0/search  →  dots-vue
    └───────────────┘
```

!!! info "ThunderDots is used as a library, not as a file producer"
    The indexing pipeline imports ThunderDots in-process and consumes its results directly as Python
    objects — it does **not** read files that ThunderDots wrote earlier. The whole corpus graph is built
    once, then the crawler resolves collections and resources from in-memory lookup tables instead of
    issuing one HTTP request per node.

    ```python
    from thunderdots import ThunderDots

    td = ThunderDots(
        endpoint_dts=app.config["DTS_URL"],
        collection_params={"collection_id": app.config["TARGET_COLLECTION"], ...},
        resource_params={"fragment_mode": "navigation", ...},
    )
    td.fetch()
    td_results = td.results()          # collection_results + resource_results
    elastic_docs = td.to_elastic_documents(include_fragments=False)
    ```

    See [Indexing](indexing.md) for the full pipeline, and the
    [ThunderDots documentation](https://dots-suite.github.io/ThunderDots/) for the client itself.

## What gets indexed

Three indexes, all configured from JSON files shipped with the package (see
[Elasticsearch mappings](elasticsearch.md)):

| Index | Contents |
|---|---|
| `dots_document` | one document per **passage** (a TEI fragment, `_id = "{resource_id}::{passage_id}"`), carrying its own metadata and only the `resource_id` of its resource |
| `dots_resources` | one document per **resource**, `_id = resource_id`, holding its metadata, dates and collections |
| `dots_collection` | one document per **collection** of the DTS tree |

Which metadata survives indexing is not arbitrary: it is declared once in a registry of
[search fields](search-fields.md), which drives filtering at index time *and* facets, sorting and
query aliases at search time.

## Where to start

<div class="grid cards" markdown>

- **[Installation](installation.md)** — Elasticsearch with the ICU plugin, then the Python package.
- **[Quick start](quickstart.md)** — index a small collection locally and query it.
- **[Configuration](configuration.md)** — the `local` / `staging` / `prod` YAML files and their environment variables.
- **[Indexing](indexing.md)** — running a crawl, what each phase does.
- **[Indexing reports](reporting.md)** — the CSV files to read after a run, and what a non-zero count means.
- **[CLI reference](cli-reference.md)** — every command and option.

</div>

## Related projects

| Project | Repository | Documentation |
|---|---|---|
| **DoTS** — DTS-compliant publication API | [dots-suite/dots](https://github.com/dots-suite/dots) | [dots_documentation](https://dots-suite.github.io/dots_documentation/) |
| **ThunderDots** — Python DTS client | [dots-suite/ThunderDots](https://github.com/dots-suite/ThunderDots) | [ThunderDots docs](https://dots-suite.github.io/ThunderDots/) |
| **dots-vue** — search front-end | [dots-suite/dots-vue](https://github.com/dots-suite/dots-vue) | — |

## Requirements

- Python ≥ 3.12
- Elasticsearch 8.12+ **with the `analysis-icu` plugin** (the `folding` analyzer depends on it)
- A reachable DoTS/DTS endpoint
