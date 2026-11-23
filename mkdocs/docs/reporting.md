# Indexing reports

Every run writes CSV files to `indexation_reporting/`. The directory is gitignored except its README.

## File naming

All files of a single run share one UTC timestamp prefix with minute resolution, computed once when
the CLI starts:

```
2026_09_09_09_57_dots_fragments_indexation_exceptions.csv
2026_09_09_09_57_indexed_passages_report.csv
…
```

!!! warning "Two runs in the same minute share their files"
    The prefix has minute resolution and files are opened in append mode, so two runs started within
    the same minute write into the same files.

## The reports

| File | Columns | Written when |
|---|---|---|
| `{ts}_indexed_passages_report.csv` | `timestamp, resource_id, passage_id` | One row per passage successfully built. This is your reference count. |
| `{ts}_{FRAGMENT_INDEX}_indexation_exceptions.csv` | `timestamp, resource_id, passage_id, error_type, error_message, context` | A passage could not be built. **Also reused** for resource-level indexing failures, which write a shorter 5-column row — so this file can mix two row shapes. |
| `{ts}_{FRAGMENT_INDEX}_no_text.csv` | `timestamp, resource_id, passage_id, citeType, level, reason, context` | A fragment has neither `content` nor `head`, and was **skipped**. `reason` is always `NoIndexableText`. |
| `{ts}_collection_exceptions.csv` | `timestamp, collection_id, error_type, error_message, context` | DTS collection fetch errors. |
| `{ts}_passage_exceptions.csv` | `timestamp, resource_id, passage_id, error_type, error_message, context` | JSON decode failures, bulk call failures, and **per-item Elasticsearch bulk rejections**. |
| `{ts}_metadata_dts_sanitization.csv` | `timestamp, collection_id, json_path, error_type, value` | Empty JSON keys found while sanitising a raw DTS response. |
| `{ts}_dots_indexation_timing.csv` | `timestamp, level, id, parent_id, duration_sec, duration_hms` | One row per indexing phase at the end of the run: `fragments_indexation`, `documents_indexation` and their `total_indexation` parent. |

Every report carries its header row: the writer creates the file with its header the first time a row
is appended, so a file is never produced headerless — even the ones that are not pre-created at the
start of the run.

## What to check after a run

The CLI prints two summaries.

The **crawl summary** (`Résumé du crawler`) comes first: projects, sub-collections, resources and
passages crawled, excluded collections, then the passages in error, the passages without text and the
collections in error, read back from the CSV files.

The **indexation summary** (`Résumé de l’indexation`) closes the run:

- the passages and resources indexed out of those sent (`Passages indexés : 14290 / 14308`);
- when Elasticsearch refused documents, their count and the three most frequent causes, each with its
  number of documents (`18 × mapper_parsing_exception: …`);
- when a cause is a field missing from a `strict` mapping, the field names and the fix:
  `update-conf --rebuild` then a reindex, or adding the field to the conf first;
- the paths of the reports holding the details (`passage_exceptions` and the document exceptions file);
- the per-phase durations;
- a verdict: `✅ Indexation réussie`, `⚠️ INDEXATION PARTIELLE` or `❌ ÉCHEC : aucun document indexé`.
  After a `--collections` run with refusals, a last line warns that the purged collections lost
  the refused documents from the search.

A practical checklist:

1. **Passages en erreur ≠ 0** → open the document exceptions file and group by `error_type` and
   `context`.
2. **Passages sans texte ≠ 0** → open the `no_text` file. These fragments are silently *not* indexed.
   A large count usually means a mismatch between DTS navigation and the TEI.
3. **A verdict other than `✅` in the indexation summary** → open `{ts}_passage_exceptions.csv`.
   This is where Elasticsearch *rejections* land, and they do not stop the run. A real example from
   this project:

    ```
    failed to parse field [fragment_metadata.dublincore.date] of type [date] … '1154-12-16–1157'
    ```

    Rows here mean **data is missing from the index even though the crawl reported success** —
    typically mapping drift, where a dynamic `date` type was inferred from an earlier document.

4. **Collection exceptions** point at DTS availability problems rather than at your mappings.
5. **Cross-check the counts**: the passages indexed in the indexation summary versus
   `GET /dots_fragments/_count`.
6. **Watch stdout** for the `⚠️ Index … mapping 'dynamic=…' differs from conf` warning: it means the
   conf was never applied to that index.

