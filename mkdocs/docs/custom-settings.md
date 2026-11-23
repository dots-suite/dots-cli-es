# Using custom settings

Most callers of this API are not humans: they are instances of the
[dots-vue](https://github.com/dots-suite/dots-vue) front-end, which builds its requests from a JSON
settings file **per collection**. Those files live in a settings repository — for example
[dots-vue-demo-settings](https://github.com/dots-suite/dots-vue-demo-settings) — and two of their keys
concern search directly: `customRoutes` and `searchConfig`.

Nothing here affects indexing. The only settings key the CLI reads is `excludeCollectionIds`; see
[Indexing](indexing.md#excluded-collections).

## `customRoutes` — whether the collection has a search page at all

```json
"customRoutes": [
  {
    "name": "Search",
    "path": "search",
    "compName": "SearchPage"
  }
]
```

This is pure routing: it decides that `/<collectionId>/search` exists and which component answers
there. Without this entry the URL redirects to the collection home, and the collection simply has no
search page — so no request ever reaches this API for it.

`SearchPage` is case-sensitive.

## `searchConfig` — what is searched

Each collection has one search. Three keys of `searchConfig` decide which documents it lists and in
which order, and translate directly into API parameters:

```json
"searchConfig": {
  "target": "fragment",
  "include": { "citeTypes": ["partie", "acte"] },
  "defaultSort": "dublinCore.date"
}
```

| Key | Values | Sent to the API as |
|---|---|---|
| `target` | `"resource"` (default) or `"fragment"` | `scope=fragment` for `"fragment"`, nothing otherwise. See [Resource or fragment scope](search-api.md#resource-or-fragment-scope). |
| `include` | `{"citeTypes": [...]}` or `{"levels": [...]}`, with `target: "fragment"` only | `citeType=partie,acte` or `level=1`. Without it, every fragment is searched. See [Fragment types and levels](search-api.md#fragment-types-and-levels). |
| `defaultSort` | a `sort` value, e.g. `"dublinCore.title"` or `"-dublinCore.date"` | `sort`, only when the search has no term: with a term, results come by relevance. |

dots-vue gives these keys defaults (`target: "resource"`, `defaultSort: "dublinCore.title"`), and the
settings redefine only the ones they need, key by key. When a collection changes `target`, it starts again from
those defaults rather than from its parent's keys.

The other keys of `searchConfig` — `columns` and `countLabel` — only shape the display and never reach
this API; they are described in the
[dots-vue-demo-settings documentation](https://dots-suite.github.io/dots-vue-demo-settings/search/).

## `searchConfig` — which facets are offered

```json
"searchConfig": {
  "facets": [
    {
      "key": "dublinCore.creator",
      "label": "Auteurs",
      "enabled": true,
      "order": 3
    },
    {
      "key": "dublinCore.publisher",
      "enabled": false
    }
  ],
  "temporalFacets": [
    {
      "key": "dublinCore.created",
      "label": "Promotion (période)",
      "enabled": true,
      "order": 1
    },
    {
      "key": "dublinCore.issued",
      "enabled": false
    }
  ]
}
```

| Field | Effect |
|---|---|
| `key` | A **canonical metadata key** — the same vocabulary the API publishes (`dublinCore.creator`, `extensions.author`), plus the facets that do not come from the registry: `collections`, `resources`, and, at fragment scope, `citeType` and `level`. |
| `enabled: false` | The key is collected and sent as **`excludeFacets`** (or **`excludeTemporalFacets`** for the temporal array). The facet is neither aggregated by Elasticsearch nor displayed. |
| `label` | Front-end only: overrides the displayed name. |
| `order` | Front-end only: display order. |
| `sort` | Front-end only: order of the facet's values — `"label"` (default), `"count"` or `"corpus_order"` (the order of the corpus, see [Corpus order](search-api.md#corpus-order)); a `-` prefix reverses it. |

!!! important "The logic is exclusion-based, not inclusion-based"
    A facet **absent** from the configuration is still aggregated and still displayed. Listing a facet
    with `enabled: true` documents an intent but changes nothing by itself; only `enabled: false`
    has an effect. A partial configuration therefore never hides the facets it forgets to mention.

## What the front-end actually sends

Taking the `ENCPOS` settings as an example — `collections` and `dublinCore.creator` enabled, the rest
disabled — the first request of the search page is:

```
GET /api/1.0/search
  ?query=&page[number]=1&page[size]=25
  &collectionId=ENCPOS
  &excludeFacets=dublinCore.contributor,dublinCore.publisher,dublinCore.language,extensions.author,…
  &excludeTemporalFacets=dublinCore.issued,extensions.dateCreated,…
```

The excluded keys appear in the order of the arrays in the file. Because `collections` is not
excluded here, the collections aggregation is built and rendered — under the label the settings give
it.

!!! warning "Arrays are replaced, not merged"
    A collection's settings are merged over the project-wide ones, but **arrays are replaced
    wholesale**. A collection that redefines `facets` supersedes the root `facets` entirely, while
    still inheriting the root `temporalFacets` if it does not redefine them. Copy the whole array
    when you override one.
