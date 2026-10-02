# Configuration

All runtime settings live in three YAML files, kept in the `config/` directory of the deployment —
outside the installed package, so they can be edited without reinstalling anything:

```
config/
├── local.yml
├── staging.yml
└── prod.yml
```

One of them is selected by the global `--config` option of the CLI
(`--config [local|staging|prod]`, **default `staging`**), and by `--config` / the `SERVER_ENV_CONFIG`
environment variable for the API. The file loaded is always `config/{alias}.yml`, and the directory
is fixed: one deployment, one configuration directory.

## Keys

### `source:` — where the corpus comes from

| Key | Controls |
|---|---|
| `DTS_URL` | The DoTS/DTS endpoint. Passed to `ThunderDots(endpoint_dts=…)`, used to resolve the root collection, and used by the API to build the `dts_url` of each hit. |
| `TARGET_COLLECTION` | Identifier of the collection to crawl. **Case-sensitive** — it must match the DTS identifier exactly (`ENCPOS`, not `encpos`). **Empty** means "start from the DTS root collection", which is resolved at runtime. |
| `CUSTOM_SETTINGS_PATH` | Directory of front-end `*.conf.json` settings files. Every `excludeCollectionIds` entry found there is added to the exclusion set. Environment-interpolated. |
| `ADDITIONAL_EXCLUDED_COLLECTIONS` | List of collection ids to skip, merged with the ones derived from `CUSTOM_SETTINGS_PATH`. **Case-insensitive**, unlike `TARGET_COLLECTION`: both the list and the candidate identifier are lowercased before comparison, so `ENCPOS` and `encpos` are equivalent here. |
| `FRAGMENT_TEMPORAL_XPATH` | XPath passed to ThunderDots as `fragment_params.temporal_xpath`, evaluated relative to each fragment in the TEI (e.g. `.//tei:docDate//tei:date`). Matching dates are read from `@when`, `@notBefore`/`@notAfter`, `@from`/`@to` or the text, and stored under `fragment_metadata.tei.date` (first match; all matches under `tei.dates` when there are several). The key is `tei.date` whatever the XPath, so the `tei.date` range facet is stable, but its meaning (act date, any date…) is set by this XPath, once per deployment. **Empty** (default) means fragment dates come from the DTS navigation metadata only. |

### `config:` — Elasticsearch and the API

| Key | Controls |
|---|---|
| `ELASTICSEARCH_URL` | ES endpoint used by both the CLI and the API. |
| `DOCUMENT_INDEX` | Index holding the passages. Default `dots_document`. |
| `RESOURCE_INDEX` | Index holding the resources, one document each. Default `dots_resources`, also when the key is missing. |
| `COLLECTION_INDEX` | Index holding collections. Default `dots_collection`. |
| `SEARCH_RESULT_PER_PAGE` | Default `page[size]` of the search API. Default `200`. |

### Differences between the three files

| | `local` | `staging` | `prod` |
|---|---|---|---|
| `DTS_URL` | `http://localhost:8080/api/dts` — DoTS installed locally, on its default port<br>or any reachable DTS endpoint, e.g. `https://dev.chartes.psl.eu/dots/api/dts` | any reachable DTS endpoint, e.g. `https://dev.chartes.psl.eu/dots/api/dts` | any reachable DTS endpoint, e.g. `https://dots.chartes.psl.eu/demo/api/dts` |
| `ELASTICSEARCH_URL` | `http://localhost:9200` — Elasticsearch installed locally, on its default port<br>or any reachable Elasticsearch endpoint | any reachable Elasticsearch endpoint, e.g. `http://127.0.0.1:9200` | idem staging |

Set your `TARGET_COLLECTION` and your `ADDITIONAL_EXCLUDED_COLLECTIONS` as needed for your respective
environments.

## Environment variables

| Variable | Used by | Effect |
|---|---|---|
| `ES_PASSWORD` | CLI + API | Password used to authenticate against Elasticsearch. Read directly by the clients, **never written into `ELASTICSEARCH_URL`**. Required whenever the node has security enabled. Set `ES_USER` too if the account is not `elastic`. |
| `CUSTOM_SETTINGS_PATH` | CLI | Directory scanned for `*.conf.json` front-end settings. If unset or not a directory, no error: the exclusion set is simply empty. |
| `SERVER_ENV_CONFIG` | API only | Overrides the `--config` argument. Intended for server environments. |

!!! danger "No trailing slash in `DTS_URL`"
    The code appends the route itself — `{DTS_URL}/collection`, `{DTS_URL}/document`. A trailing
    slash therefore produces a doubled separator, which the endpoint rejects outright:

    ```
    …/api/dts/collection?id=theater     → 200
    …/api/dts//collection?id=theater    → 400   (no redirect to fall back on)
    ```

    Write `https://dots.chartes.psl.eu/demo/api/dts`, never `…/api/dts/`.

!!! warning "Identifiers are case-sensitive on the DoTS side"
    `TARGET_COLLECTION` — like `--collections` — is sent to the endpoint verbatim, and DoTS matches
    identifiers exactly: `ENCPOS` resolves, `encpos` does not. A wrong case produces an **empty
    crawl, not an error**. Check the identifier against the endpoint first:

    ```bash
    curl "https://dots.chartes.psl.eu/demo/api/dts/collection?id=ENCPOS"
    ```

    The exclusion list is the exception: it is compared in lowercase on both sides, so its case does
    not matter.

Typical invocation with security enabled:

```bash
ES_PASSWORD=your_password dots-es-cli --config=prod index
```

## How the files are loaded

`load_config(alias)` reads `config/{alias}.yml` at runtime, directly from the filesystem, relative to
the directory the command is run from. It then:

1. replaces every `None` with an empty string;
2. expands environment variables in **every** string value;
3. **flattens** `source:` and `config:` into a single dictionary — `app.config["DTS_URL"]` and
   `app.config["DOCUMENT_INDEX"]` sit side by side;
4. coerces `ADDITIONAL_EXCLUDED_COLLECTIONS` into a lowercase set.

!!! warning "Operational caveats"
    - **The configuration is read at runtime.** Editing a file under `config/` takes effect on the
      next invocation, with no reinstall — a plain `pip install .` is enough.
    - **Run the command from the deployment directory.** The path is resolved against the working
      directory, so a service or a cron job must set it to the directory holding `config/`.
      Otherwise the run stops at once: `Config file not found: /somewhere/config/prod.yml`.
    - **An unset variable is left as literal text.** `${ES_PASSWORD}` stays `${ES_PASSWORD}` in the
      URL rather than becoming empty, which surfaces as a confusing connection error. Check that the
      variable is exported before blaming Elasticsearch.
    - Because the two blocks are flattened into one dictionary, a key present in both `source:` and
      `config:` would be silently resolved in favour of `config:`.


