# Troubleshooting

## Index creation fails on an unknown analyzer

```
Unknown analyzer type [icu_folding]
```

The `analysis-icu` plugin is missing from Elasticsearch. The `folding` analyzer cannot be built
without it, so both indexes fail to be created. See [Installation](installation.md).

```bash
path/to/elasticsearch/bin/elasticsearch-plugin install analysis-icu
# then restart the node
```

## `resource_already_exists_exception`

`update-conf` was run on an existing index without `--rebuild`. Rerun with the flag, keeping in mind
it **deletes the index**:

```bash
dots-es-cli --config=local update-conf --rebuild --indexes=dots_fragments
```

## Elasticsearch rejects the connection (401)

The node has security enabled and `ES_PASSWORD` is unset or wrong. Credentials are read from the
environment, never from `ELASTICSEARCH_URL`:

```bash
ES_PASSWORD=your_password dots-es-cli --config=prod index
```

Set `ES_USER` as well if the account is not `elastic`.

## "conf not found" during `update-conf`

`update-conf` loads `{index_name}.conf.json`. If `FRAGMENT_INDEX` or `COLLECTION_INDEX` was renamed
in the YAML without renaming the matching JSON file, the command prints *"conf not found"*, continues,
and leaves you with an index that has **no mapping**. Rename the file in `dots_es/elasticsearch/` to
match.

## `⚠️ Index … mapping 'dynamic=…' differs from conf`

The live index was not built from the current conf file. Run `update-conf --rebuild` and reindex.

## The run succeeded but documents are missing

Check `{ts}_passage_exceptions.csv`. Elasticsearch **bulk rejections do not stop the run** and are
only recorded there. The most common cause is mapping drift on a dynamically typed field:

```
failed to parse field [fragment_metadata.dublincore.date] of type [date] … '1154-12-16–1157'
```

Here a `date` type was inferred from an earlier document, and a later value that is a date *range*
cannot be parsed. Rebuild the mapping, or declare the field explicitly.

See [Indexing reports](reporting.md) for the full checklist.

## Many "passages sans texte"

Fragments with neither `content` nor `head` are skipped and logged to `{ts}_…_no_text.csv`. A large
count usually points at a mismatch between the DTS navigation and the TEI structure — the fragments
exist in navigation but carry no indexable text.

## `HTTP 400` from the search API

Every uncaught exception in the endpoint is returned as an **HTTP 400 with the exception text as the
body** — so read the response body, it names the failing parameter. Malformed `facets` JSON and
malformed `range[…]` clauses are the usual causes.

## The API returns resources instead of highlighted fragments

You passed `no-highlight`. The switch tests for *presence*, so even `no-highlight=false` enables
notice mode. Remove the parameter entirely.

