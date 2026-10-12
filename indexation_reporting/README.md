# Indexation reporting

This directory contains **technical reports generated during DTS → Elasticsearch indexing operations**.

## Expected contents

Files located in this directory are **automatically generated** by the application and are **not versioned** (with the exception of this README).

In particular, it may contain:

- `dots_fragments_indexation_exceptions.csv`  
  → Report of errors encountered during DTS passage indexing  
  (e.g. passages present in the `navigation` endpoint but missing from TEI files)

- other CSV files or logs related to:
  - inconsistencies between DTS Navigation and TEI
  - XML parsing errors
  - Elasticsearch mapping errors
  - ignored or partially indexed passages

---

## CSV report format

### `dots_fragments_indexation_exceptions.csv`

This file lists **all DTS passages that could not be indexed correctly**, without interrupting the overall indexing process.

### Columns

| Column | Description |
|-------|-------------|
| `timestamp` | Date and time of the error (ISO 8601) |
| `resource_id` | DTS resource identifier (e.g. `ENCPOS_1972_18`) |
| `passage_id` | DTS passage identifier (`xml:id`) |
| `error_type` | Technical error type (e.g. `MissingTEIPassage`, `XPathError`, `ParsingError`) |
| `error_message` | Python exception message or concise description |
| `context` | Pipeline step where the error occurred (e.g. `index_resource_passages`, `extract_passage_text`) |

### Example

```csv
timestamp,resource_id,passage_id,error_type,error_message,context
2026-01-13T10:42:01,ENCPOS_1972_18,r9999,MissingTEIPassage,xml:id not found in TEI,index_resource_passages
