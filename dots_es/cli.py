import json
import pprint
import re
import os
import csv
from datetime import datetime, timezone
import time
from typing import Any, Optional
from importlib import resources

import click
import httpx
import asyncio
from elasticsearch import Elasticsearch
from lxml import etree

from dots_es.config_loader import load_config, es_basic_auth

from dots_es.api.search_fields import SEARCH_FIELDS, SearchField, get_value, SearchFieldFamily, build_filtered_temporal_metadata


# ============================================================
# SIMPLE APP OBJECT
# ============================================================

class App:
    def __init__(self, config_dict: dict):
        """Initialize the application with configuration and Elasticsearch client.

        :param config_dict: Flattened configuration dictionary
        :type config_dict: dict
        """
        self.config = config_dict
        # Initialize Elasticsearch client if URL is provided
        self.elasticsearch = Elasticsearch(
            [self.config["ELASTICSEARCH_URL"]],
            basic_auth=es_basic_auth()
        ) if self.config.get("ELASTICSEARCH_URL") else None

        # Combined indexes string for ES
        self.all_indexes = f"{self.config['DOCUMENT_INDEX']},{self.config['COLLECTION_INDEX']}"

        # Store excluded collections as lowercase for case-insensitive comparison
        self.excluded_collections = {c.lower() for c in self.config.get("ADDITIONAL_EXCLUDED_COLLECTIONS", [])}

        # Placeholder for indexing statistics
        self.index_stats = {}

# ============================================================
# CLI CONTEXT OBJECT
# ============================================================

class CLIContext:
    """
    Object shared across CLI commands.
    Holds config and lazily builds App when needed.
    """

    def __init__(self, config_dict: dict):
        self.config = config_dict
        self._app: Optional[App] = None

    @property
    def app(self) -> App:
        """Lazy instantiation of App."""
        if self._app is None:
            self._app = App(self.config)
        return self._app

# ============================================================
# FUNCTIONS
# ============================================================

clean_tags = re.compile('<.*?>')
body_tag = re.compile('<body(?:(?:.|\n)*?)>((?:.|\n)*?)</body>')

app = None

XML_NS = {"tei": "http://www.tei-c.org/ns/1.0"}

ts = datetime.now(timezone.utc).strftime("%Y_%m_%d_%H_%M")

def count_csv_rows(csv_path: str) -> int:
    """Compte le nombre de lignes (hors header) dans un CSV."""
    if not os.path.isfile(csv_path):
        return 0

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)

    # 0 ou 1 ligne → uniquement le header
    return max(0, len(rows) - 1)

def format_duration(seconds: float) -> str:
    """Formate une durée en HH:MM:SS."""
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def report_timing(csv_path: str, row: dict):
    header = [
        "timestamp",
        "level",
        "id",
        "parent_id",
        "duration_sec",
        "duration_hms",
    ]
    ensure_csv_file(csv_path, header)

    with open(csv_path, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writerow(row)

def ensure_csv_file(csv_path: str, header: list):
    """Crée le fichier CSV avec header si n'existe pas."""
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    if not os.path.isfile(csv_path):
        with open(csv_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()

def get_indexation_csv_paths(app):
    """
    Retourne les chemins des fichiers CSV horodatés pour CE run
    """
    base_dir = "indexation_reporting"
    os.makedirs(base_dir, exist_ok=True)

    return {
        "dts_sanitization": os.path.join(
            base_dir,
            f"{ts}_metadata_dts_sanitization.csv"
        ),
        "document_exceptions": os.path.join(
            base_dir,
            f"{ts}_{app.config['DOCUMENT_INDEX']}_indexation_exceptions.csv"
        ),
        "document_no_text": os.path.join(
            base_dir,
            f"{ts}_{app.config['DOCUMENT_INDEX']}_no_text.csv"
        ),
        "collection_exceptions": os.path.join(
            base_dir,
            f"{ts}_{app.config['COLLECTION_INDEX']}_indexation_exceptions.csv"
        ),
        "timing": os.path.join(
            base_dir,
            f"{ts}_dots_indexation_timing.csv"
        ),
        "indexed_passages_report": os.path.join(
            base_dir,
            f"{ts}_indexed_passages_report.csv"
        ),
        "passage_exceptions": os.path.join(
            base_dir,
            f"{ts}_passage_exceptions.csv"
        )
    }

def ensure_out_directory_exists():
    """Assure que le dossier /out existe."""
    out_dir = "out"
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)
        print(f"Dossier '{out_dir}' créé.")
    else:
        print(f"Dossier '{out_dir}' déjà existant.")

def sanitize_dts_json(data: dict, app, collection_id: str):
    """
    Nettoie récursivement le JSON DTS :
    - supprime les clés vides
    - nettoie dict ET list
    - reporte les anomalies
    """

    anomalies = []

    def clean(value, path="root"):
        # Cas dictionnaire
        if isinstance(value, dict):
            cleaned = {}
            for k, v in value.items():
                current_path = f"{path}.{k}"

                # Clé vide
                if not k or k.strip() == "":
                    anomalies.append({
                        "collection_id": collection_id,
                        "path": current_path,
                        "error": "empty_key",
                        "value": str(v)
                    })
                    continue

                cleaned_value = clean(v, current_path)
                cleaned[k] = cleaned_value

            return cleaned

        # Cas liste
        elif isinstance(value, list):
            cleaned_list = []
            for idx, item in enumerate(value):
                cleaned_item = clean(item, f"{path}[{idx}]")
                cleaned_list.append(cleaned_item)
            return cleaned_list

        # Valeur simple
        else:
            return value

    cleaned_data = clean(data)

    if anomalies:
        report_dts_sanitization(app, collection_id, anomalies)
    #print('cleaned data ', cleaned_data)
    return cleaned_data

def report_indexation_event(csv_path: str, row: dict, header: list):
    # ensure_csv_file crée le dossier ET écrit l'en-tête si le fichier n'existe
    # pas encore : sans cela, les rapports qui ne sont pas pré-créés au début de
    # la commande index (passage_exceptions, metadata_dts_sanitization) étaient
    # produits sans ligne d'en-tête.
    ensure_csv_file(csv_path, header)

    with open(csv_path, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writerow(row)

def report_passages_indexation(app, resource_id: str, passage_id: str):
    _csv_path = get_indexation_csv_paths(app)["indexed_passages_report"]
    report_indexation_event(
        csv_path=_csv_path,
        header=[
            "timestamp",
            "resource_id",
            "passage_id"
        ],
        row={
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "resource_id": resource_id,
            "passage_id": passage_id
        }
    )

def report_indexation_exception(app, resource_id: str, passage_id: str, error: Exception, context: str):
    _csv_path = get_indexation_csv_paths(app)["document_exceptions"]
    report_indexation_event(
        csv_path=_csv_path,
        header=[
            "timestamp",
            "resource_id",
            "passage_id",
            "error_type",
            "error_message",
            "context"
        ],
        row={
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "resource_id": resource_id,
            "passage_id": passage_id,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "context": context
        }
    )

def report_no_text_passage(app, resource_id: str, passage_id: str, nav: dict):
    _csv_path = get_indexation_csv_paths(app)["document_no_text"]
    report_indexation_event(
        csv_path=_csv_path,
        header=[
            "timestamp",
            "resource_id",
            "passage_id",
            "citeType",
            "level",
            "reason",
            "context"
        ],
        row={
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "resource_id": resource_id,
            "passage_id": passage_id,
            "citeType": nav.get("citeType"),
            "level": nav.get("level"),
            "reason": "NoIndexableText",
            "context": "index_resource_passages"
        }
    )

def report_collection_exception(app, collection_id: str, error: Exception, context: str):
    _csv_path = get_indexation_csv_paths(app)["collection_exceptions"]
    report_indexation_event(
        csv_path=_csv_path,
        header=["timestamp", "collection_id", "error_type", "error_message", "context"],
        row={
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "collection_id": collection_id,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "context": context
        }
    )

def report_collection_indexation_errors(app, collection_id: str, error: Exception):
    _csv_path = get_indexation_csv_paths(app)["collection_exceptions"]
    report_indexation_event(
        csv_path=_csv_path,
        header=[
            "timestamp",
            "collection_id",
            "error_type",
            "error_message",
            "context"
        ],
        row={
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "collection_id": collection_id,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "context": "collection_indexation"
        }
    )

def report_passage_indexation_errors(app, resource_id: str, passage_id: str, error: Exception):
    _csv_path = get_indexation_csv_paths(app)["passage_exceptions"]
    report_indexation_event(
        csv_path=_csv_path,
        header=[
            "timestamp",
            "resource_id",
            "passage_id",
            "error_type",
            "error_message",
            "context"
        ],
        row={
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "resource_id": resource_id,
            "passage_id": passage_id,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "context": "passage_indexation"
        }
    )

def report_resource_indexation_errors(app, resource_id: str, error: Exception):
    _csv_path = get_indexation_csv_paths(app)["document_exceptions"]
    report_indexation_event(
        csv_path=_csv_path,
        header=[
            "timestamp",
            "resource_id",
            "error_type",
            "error_message",
            "context"
        ],
        row={
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "resource_id": resource_id,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "context": "document_indexation"
        }
    )


def report_dts_sanitization(app, collection_id: str, anomalies: list[dict]):
    _csv_path = get_indexation_csv_paths(app)["dts_sanitization"]

    header = [
        "timestamp",
        "collection_id",
        "json_path",
        "error_type",
        "value"
    ]

    for anomaly in anomalies:
        report_indexation_event(
            csv_path=_csv_path,
            header=header,
            row={
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "collection_id": collection_id,
                "json_path": anomaly.get("path"),
                "error_type": anomaly.get("error"),
                "value": anomaly.get("value"),
            }
        )

def load_excluded_collections_from_settings(settings_path: str) -> set:
    """
    Parcourt les *.conf.json d'un dossier et extrait excludeCollectionIds
    """
    excluded = set()

    if not settings_path or not os.path.isdir(settings_path):
        return excluded

    for filename in os.listdir(settings_path):
        if not filename.endswith(".conf.json"):
            continue

        filepath = os.path.join(settings_path, filename)

        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)

            for coll_id in data.get("excludeCollectionIds", []):
                if coll_id:
                    excluded.add(coll_id.lower())

        except Exception as e:
            print(f"⚠️ Impossible de lire {filepath}: {e}")

    return excluded

def extract_passage_text(element, nav_index=None) -> str:
    """
    Extrait le texte d'un élément TEI dans l'ordre naturel de lecture.
    - Exclut uniquement les descendants avec xml:id dans nav_index.
    - Inclut tout texte direct et variantes (<sic>, <corr>, <lem>, <rdg>, etc.).
    """
    if nav_index is None:
        nav_index = {}

    def _recurse(el):
        texts = []

        xmlid = el.get("{http://www.w3.org/XML/1998/namespace}id")
        # ignorer uniquement descendants dans nav_index
        if el != element and xmlid in nav_index:
            return texts

        # texte avant enfants
        if el.text:
            texts.append(el.text)

        # parcourir enfants
        for child in el:
            texts.extend(_recurse(child))
            # texte après enfant (tail)
            if child.tail:
                texts.append(child.tail)

        return texts

    all_texts = _recurse(element)
    # nettoyage : retirer espaces, tabs, newlines inutiles
    cleaned_texts = [t.strip() for t in all_texts if t.strip()]
    return " ".join(cleaned_texts)


def remove_html_tags(text):
    return re.sub(clean_tags, ' ', text)

def normalize_text(text: str) -> str:
    """
    Nettoie le texte TEI :
    - supprime espaces / retours parasites
    - normalise les espaces
    """
    if not text:
        return ""

    # remplace tous les blancs (espaces, \n, \t) par un espace
    text = re.sub(r"\s+", " ", text)

    return text.strip()

def extract_body(text):
    match = re.search(body_tag, text)
    if match:
        return match.group(1)
    return text


def load_elastic_conf(app, index_name, rebuild=False):
    url = '/'.join([app.config['ELASTICSEARCH_URL'], index_name])
    res = None
    try:
        if rebuild:
            print(f"Deleting {index_name} index.")
            with httpx.Client(auth=es_basic_auth()) as client:
                res = client.delete(url)
        with resources.files("dots_es").joinpath("elasticsearch", "_global.conf.json").open('r') as _global:
            global_settings = json.load(_global)

            with resources.files("dots_es").joinpath("elasticsearch", f"{index_name}.conf.json").open('r') as f:
                payload = json.load(f)
                payload["settings"] = global_settings
                print("UPDATE INDEX CONFIGURATION:", index_name)
                with httpx.Client(auth=es_basic_auth()) as client:
                    res = client.put(url, json=payload)
                    if not str(res.status_code).startswith("20"):
                        try:
                            error_type = res.json().get("error", {}).get("type", "")
                        except Exception:
                            error_type = ""

                        if error_type == "resource_already_exists_exception":
                            raise RuntimeError(
                                f"❌ La configuration de {index_name} n'a PAS été appliquée : "
                                f"l'index existe déjà. "
                                f"Relancer avec --rebuild pour le supprimer puis le recréer "
                                f"(⚠️ toutes les données de {index_name} seront perdues et à réindexer)."
                            )

                        raise RuntimeError(
                            f"❌ La configuration de {index_name} n'a PAS été appliquée "
                            f"(HTTP {res.status_code}) : {res.text}"
                        )

    except FileNotFoundError as e:
        print(str(e))
        print("conf not found", flush=True, end=" ")
    except Exception as e:
        # Les RuntimeError levées ci-dessus portent déjà le corps de la réponse
        if res is not None and not isinstance(e, RuntimeError):
            print(res.text, flush=True, end=" ")
        print(str(e), flush=True, end=" ")
        raise e

def update_conf_internal(cli_ctx: CLIContext, indexes=None, rebuild=False):
    app = cli_ctx.app
    if indexes is None:
        indexes = app.all_indexes
    if isinstance(indexes, list):
        indexes = ",".join(indexes)
    elif not isinstance(indexes, str):
        indexes = str(indexes)

    for name in indexes.split(','):
        name = name.strip()
        if name:
            load_elastic_conf(app, name, rebuild=rebuild)

def warn_on_mapping_drift(app, es, index_name):
    """
    Signale qu'un index existant ne porte pas le mapping de son fichier de conf.

    Un index créé implicitement par un premier es.index() hérite du mapping
    dynamique par défaut : chaque nouvelle clé d'objet (ex. members.<id>) devient
    un champ, et l'index finit par heurter index.mapping.total_fields.limit (1000).
    """
    try:
        conf = resources.files("dots_es").joinpath(
            "elasticsearch", f"{index_name}.conf.json"
        )
        with conf.open('r') as f:
            expected = json.load(f).get("mappings", {}).get("dynamic")
    except FileNotFoundError:
        return

    if expected is None:
        return

    try:
        live = es.indices.get_mapping(index=index_name)[index_name]["mappings"].get("dynamic")
    except Exception as e:
        print(f"⚠️ Impossible de lire le mapping de {index_name}: {e}")
        return

    if live != expected:
        print(
            f"⚠️ Index {index_name} : mapping 'dynamic={live or 'true (défaut ES)'}' en base alors que "
            f"dots_es/elasticsearch/{index_name}.conf.json attend 'dynamic={expected}'. "
            f"La conf n'a jamais été appliquée à cet index."
            f"`update-conf --indexes {index_name} --rebuild` (⚠️ réindexation nécessaire)."
        )

def normalize_extension_key(key: str) -> str:
    """
    Normalize DTS extension keys for Elasticsearch:
    - replace ':' by '_'
    """
    return key.replace(":", "_")

def normalize_metadata_value(value):
    """
    Normalise une valeur DTS (dc / extensions) pour ES :
    - dict → @id / label
    - list → liste de valeurs normalisées
    - scalar → string
    """
    if value is None:
        return ""

    if isinstance(value, dict):
        return (
            value.get("@id")
            or value.get("id")
            or value.get("label")
            or str(value)
        )

    if isinstance(value, list):
        values = []
        for v in value:
            nv = normalize_metadata_value(v)
            if nv is not None:
                values.append(nv)
        return values or [""]

    # int, float, str, bool…
    return str(value)

def build_ancestor_cache(nav_index: dict) -> dict:
    cache = {}

    def compute(pid):
        if pid in cache:
            return cache[pid]

        node = nav_index.get(pid)
        if not node or not node.get("parent"):
            cache[pid] = []
            return []

        parent_id = node["parent"]
        parent = nav_index.get(parent_id)

        if not parent:
            cache[pid] = []
            return []

        ancestors = compute(parent_id) + [{
            "id": parent["id"],
            "level": parent.get("level"),
            "citeType": parent.get("citeType"),
            "title": parent.get("title")
        }]

        cache[pid] = ancestors
        return ancestors

    for pid in nav_index:
        compute(pid)

    return cache

def get_ancestors(passage_id: str, nav_index: dict) -> list:
    ancestors = []
    current = nav_index.get(passage_id)

    while current and current.get("parent"):
        parent_id = current["parent"]
        parent = nav_index.get(parent_id)
        if not parent:
            break
        ancestors.insert(0, {
            "id": parent["id"],
            "level": parent.get("level"),
            "citeType": parent.get("citeType"),
            "title": parent.get("title")
        })
        current = parent

    return ancestors

def build_ancestor_cache_from_fragments(fragments: list[dict]) -> dict:
    cache = {}

    fragment_by_id = {
        f.get("id"): f
        for f in fragments
        if f.get("id")
    }

    def compute(pid):
        if pid in cache:
            return cache[pid]

        node = fragment_by_id.get(pid)
        if not node:
            cache[pid] = []
            return []

        parent_id = node.get("parent")
        if not parent_id:
            cache[pid] = []
            return []

        parent = fragment_by_id.get(parent_id)
        if not parent:
            cache[pid] = []
            return []

        ancestors = compute(parent_id) + [{
            "id": parent.get("id"),
            "level": parent.get("level"),
            "citeType": parent.get("citeType"),
            "title": parent.get("head") or parent.get("title")
        }]

        cache[pid] = ancestors
        return ancestors

    for pid in fragment_by_id:
        compute(pid)

    return cache

async def build_navigation_index(app, dts_url: str, resource_id: str, client: httpx.AsyncClient = None) -> dict[Any, Any] | None:
    data = None
    own_client = False
    if client is None:
        client = httpx.AsyncClient()
        own_client = True

    for attempt in range(3):
        try:
            response = await client.get(
                f"{dts_url}/navigation",
                params={"resource": resource_id, "down": -1}
            )
            response.raise_for_status()
            data = response.json()
            #print('build_navigation_index' , dts_url, resource_id, response)
            break

        except httpx.HTTPStatusError as e:
            if attempt == 2:
                # log proprement l'erreur HTTP
                report_indexation_event(
                    csv_path=get_indexation_csv_paths(app)["document_exceptions"],
                    header=[
                        "timestamp",
                        "resource_id",
                        "passage_id",
                        "error_type",
                        "error_message",
                        "context"
                    ],
                    row={
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "resource_id": resource_id,
                        "passage_id": '',
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                        "context": "dts_navigation"
                    }
                )
                return None  # skip
            await asyncio.sleep(0.5)

        except Exception as e:
            # log toute autre erreur réseau / JSON
            report_indexation_event(
                csv_path=get_indexation_csv_paths(app)["document_exceptions"],
                header=[
                    "timestamp",
                    "resource_id",
                    "passage_id",
                    "error_type",
                    "error_message",
                    "context"
                ],
                row={
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "resource_id": resource_id,
                    "passage_id": '',
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "context": "dts_navigation_unexpected"
                }
            )
            return None

    if own_client:
        await client.aclose()

    if data is None:
        return None

    nav = {}
    for item in data.get("member", []):
        # attention utilisez 'identifier' au lieu de 'id'
        passage_id = item.get("identifier")
        if not passage_id:
            continue  # ignore les items sans identifiant

        nav[passage_id] = {
            "id": passage_id,
            "citeType": item.get("citeType", None),
            "level": item.get("level", None),
            "title": item.get("dublinCore", {}).get("title", None),
            "parent": item.get("parent", None)
        }
    #print('build_navigation_index result : ', nav)
    return nav


def extract_resource_metadata(resource_meta_response: dict) -> dict:
    """
    Extrait les métadonnées DTS d'une Resource depuis /collection?id=RESOURCE_ID
    """
    metadata = {}

    # ─────────────────────────────
    # Standard DTS fields
    # ─────────────────────────────

    if "description" in resource_meta_response:
        metadata["description"] = resource_meta_response.get("description")

    if "@id" in resource_meta_response:
        metadata["id"] = resource_meta_response.get("@id")

    # ─────────────────────────────
    # Dublin Core (flat structure)
    # ─────────────────────────────

    dc = resource_meta_response.get("dublincore", {})
    if isinstance(dc, dict):
        for key, value in dc.items():
            if value is None:
                continue
            metadata[key] = value

    # ─────────────────────────────
    # Extensions DTS (normalised)
    # ─────────────────────────────

    ext = resource_meta_response.get("extensions", {})
    if isinstance(ext, dict):
        normalized_extensions = {}
        for key, value in ext.items():
            if value is None:
                continue
            normalized_key = normalize_extension_key(key)
            normalized_extensions[normalized_key] = value

        if normalized_extensions:
            metadata["extensions"] = normalized_extensions

    return metadata


def allowed_metadata_paths():
    """
    Retourne les chemins DTS autorisés dans resource_metadata.

    Les chemins viennent exclusivement du contrat SearchField.
    """

    return {
        field.path
        for field in SEARCH_FIELDS
        if field.index
    }

def filter_resource_metadata(
    resource_metadata: dict
) -> dict:
    """
    Filtre resource_metadata selon SearchField.

    Une métadonnée DTS absente de SEARCH_FIELDS
    est supprimée.

    Exemple :

    conservé :
        dublincore.creator

    supprimé :
        extensions.creditText
        extensions.@context
    """

    allowed = allowed_metadata_paths()

    filtered = {}

    for field_path in allowed:

        value = get_value(
            resource_metadata,
            SearchField(
                path=field_path
            )
        )

        if value is None:
            continue

        target = filtered

        parts = field_path.split(".")

        for part in parts[:-1]:
            target = target.setdefault(
                part,
                {}
            )

        target[parts[-1]] = value

    return filtered


def extract_metadata(
    response,
    parent_id=None,
    parent_path=None,
    parent_path_ids=None
):
    """
    Extrait les métadonnées d'une ressource DTS.

    Le résultat est le bloc resource_metadata.
    Il contient uniquement les métadonnées sources autorisées
    par SEARCH_FIELDS.
    """

    obj_id = response.get("@id") or response.get("id")

    title = response.get("title") or obj_id

    path = (
        title
        if not parent_path
        else f"{parent_path} > {title}"
    )

    path_ids = (
        [obj_id]
        if not parent_path_ids
        else parent_path_ids + [obj_id]
    )


    metadata = {
        "id": obj_id,
        "type": response.get("@type"),

        "title": title,
        "description": response.get("description"),

        "parent_id": parent_id,

        "path": path,
        "path_ids": path_ids,
        "level": len(path_ids) - 1,

        "dtsVersion": response.get("dtsVersion"),
        "totalItems": response.get("totalItems"),
        "totalChildren": response.get("totalChildren"),
        "totalParents": response.get("totalParents"),

        "download": response.get("download"),
    }


    # ==========================================================
    # Allowed metadata fields from SearchField contract
    # ==========================================================

    allowed_dct_fields = {
        field.path.removeprefix("dublincore.")
        for field in SEARCH_FIELDS
        if field.family == SearchFieldFamily.DCT
        and field.path.startswith("dublincore.")
    }


    allowed_schema_fields = {
        field.path.removeprefix("extensions.")
        for field in SEARCH_FIELDS
        if field.family == SearchFieldFamily.SCHEMA
        and field.path.startswith("extensions.")
    }


    # ==========================================================
    # Dublin Core
    # ==========================================================

    dublincore = {}

    dc = (
        response
        .get("metadata", {})
        .get("dublincore", {})
    )

    if isinstance(dc, dict):

        for key, value in dc.items():

            if key not in allowed_dct_fields:
                continue

            normalized = normalize_metadata_value(
                value
            )

            if normalized is None:
                continue

            dublincore[key] = normalized


    metadata["dublincore"] = dublincore


    # ==========================================================
    # Schema extensions
    # ==========================================================

    extensions = {}

    ext = (
        response
        .get("metadata", {})
        .get("extensions", {})
    )

    if isinstance(ext, dict):

        for key, value in ext.items():

            if key not in allowed_schema_fields:
                continue

            normalized = normalize_metadata_value(
                value
            )

            if normalized is None:
                continue

            extensions[key] = normalized


    metadata["extensions"] = extensions


    # ==========================================================
    # Members
    # ==========================================================

    members = {}

    raw_members = response.get("member", [])

    if isinstance(raw_members, list):

        for member in raw_members:

            if not isinstance(member, dict):
                continue

            member_id = (
                member.get("@id")
                or member.get("id")
            )

            member_title = (
                member.get("title")
                or member_id
            )

            if member_id:
                members[member_id] = member_title


    metadata["members"] = members


    # ==========================================================
    # Fragments
    # ==========================================================

    metadata["fragments"] = response.get(
        "fragments",
        []
    )


    return metadata

#10juillet def extract_metadata(response, parent_id=None, parent_path=None, parent_path_ids=None):
#     obj_id = response.get("@id") or response.get("id")
#     title = response.get("title") or obj_id
#     #print('extract metadata \n')
#     #pprint.pprint(response)
#     path = title if not parent_path else f"{parent_path} > {title}"
#     path_ids = [obj_id] if not parent_path_ids else parent_path_ids + [obj_id]
#
#     metadata = {
#         "id": obj_id,
#         "type": response.get("@type"),
#         "title": title,
#         "description": response.get("description"),
#
#         "parent_id": parent_id,
#         "path": path,
#         "path_ids": path_ids,
#         "level": len(path_ids) - 1,
#
#         "dtsVersion": response.get("dtsVersion"),
#         "totalItems": response.get("totalItems"),
#         "totalChildren": response.get("totalChildren"),
#         "totalParents": response.get("totalParents"),
#
#         "download": response.get("download")
#     }
#
#     # Add Dublin Core:
#     dublincore = {}
#     dc = response.get("metadata", {}).get("dublincore", {})
#
#     if isinstance(dc, dict):
#         for key, value in dc.items():
#             normalized = normalize_metadata_value(value)
#             if normalized is None:
#                 continue
#             dublincore[key] = normalized
#
#     metadata["dublincore"] = dublincore
#
#     # Add extensions:
#     extensions = {}
#
#     ext = response.get("metadata", {}).get("extensions", {})
#     if isinstance(ext, dict):
#         for key, value in ext.items():
#             normalized = normalize_metadata_value(value)
#             if normalized is None:
#                 continue
#             extensions[key] = normalized
#
#     metadata["extensions"] = extensions
#
#     # Add members:
#     members = {}
#     mbers = response.get("member", [])
#     if isinstance(mbers, dict):
#         for key, value in dc.items():
#             if value is None:
#                 continue
#
#             # transform to a string if this is a dict or a list
#             if isinstance(value, dict):
#                 # for example, only use label/id if they exist
#                 value = value.get("label") or value.get("@id") or str(value)
#             elif isinstance(value, list):
#                 normalized = []
#                 for v in value:
#                     if isinstance(v, dict):
#                         normalized.append(
#                             v.get("label") or v.get("@id") or str(v)
#                         )
#                     else:
#                         normalized.append(str(v))
#                 value = ", ".join(normalized)
#             elif not isinstance(value, str):
#                 value = str(value)
#             members[key] = value
#     metadata["members"] = members
#     metadata["fragments"] = response.get("fragments", [])
#
#     return metadata

def sanitize_resource_metadata(
    resource_metadata: dict
):
    """
    Supprime uniquement les données lourdes
    non destinées au document ES.
    """

    cleaned = dict(resource_metadata)

    cleaned.pop(
        "fragments",
        None
    )

    cleaned.pop(
        "members",
        None
    )

    return cleaned

def sanitize_collection_metadata(
    collection_metadata: dict
):
    """
    Removes keys not expected for ES collections.
    """

    cleaned = dict(collection_metadata)

    cleaned.pop(
        "fragments",
        None
    )

    return cleaned

#10juillet def sanitize_resource_metadata(resource_metadata: dict) -> dict:
#     """
#     Return a clean copy of resource_metadata without heavy/internal fields.
#     """
#     cleaned = dict(resource_metadata)
#     cleaned.pop("fragments", None)  # 👈 suppression clé
#     return cleaned

def build_collection_facets(collection_metadata):
    path_ids = collection_metadata.get("path_ids") or []
    path = collection_metadata.get("path") or ""

    labels = [p.strip() for p in path.split(" > ") if p.strip()]

    return [
        f"{cid}###{label}"
        for cid, label in zip(path_ids, labels)
    ]


def collect_resource_collection_scopes(out_dir: str) -> dict:
    """
    Union, per resource, of the collections it belongs to: a DTS resource may
    have several parents, and the crawl writes one JSONL line per branch.
    """
    scopes = {}

    for name in sorted(os.listdir(out_dir)):
        if not name.endswith("_documents.jsonl"):
            continue

        with open(os.path.join(out_dir, name), "r", encoding="utf-8") as f:
            for line in f:
                try:
                    doc = json.loads(line)
                except json.JSONDecodeError:
                    continue

                resource_id = doc.get("resource_id")
                if not resource_id:
                    continue

                scope = scopes.setdefault(
                    resource_id,
                    {"collections": [], "collection_facets": []}
                )

                known = {
                    coll.get("collection_id")
                    for coll in scope["collections"]
                }
                for coll in doc.get("collections") or []:
                    if coll.get("collection_id") not in known:
                        scope["collections"].append(coll)
                        known.add(coll.get("collection_id"))

                for facet in doc.get("collection_facets") or []:
                    if facet not in scope["collection_facets"]:
                        scope["collection_facets"].append(facet)

    return scopes


def apply_collection_scope(doc: dict, scopes: dict) -> dict:
    """
    Replace the single-branch collections of a document by their union.
    Passages inherit the scope of their resource, so one table serves both.
    """
    scope = scopes.get(doc.get("resource_id"))
    if not scope:
        return doc

    doc["collections"] = scope["collections"]
    doc["collection_facets"] = scope["collection_facets"]

    return doc


async def index_resource_passages_async(
    app,
    resource_id: str,
    collections: list,
    resource_metadata: dict,
    client: httpx.AsyncClient = None
):
    """
    Index passages of a DTS resource using pre-normalized fragments only.
    No TEI parsing, no navigation index: everything comes from resource_metadata["fragments"].
    """

    collection_metadata = collections[0]
    collection_id = collection_metadata["id"]

    print("index_resource_passages (fragments mode)", resource_id)
    print("index used for resource indexation", app.config["DOCUMENT_INDEX"])

    fragments = resource_metadata.get("fragments") or []

    #10juillet temporal = getattr(app, "thunderdots_temporal", {}).get(resource_id, {})

    resource_temporal = getattr(app, "thunderdots_temporal", {}).get(resource_id, {})
    temporal = build_filtered_temporal_metadata(resource_temporal)

    async_client_provided = client is not None
    client = client or httpx.AsyncClient()

    passages = []

    # ---------------------------------------------------------------------
    # Ancestor cache (NEW LOGIC)
    # ---------------------------------------------------------------------
    ancestor_cache = build_ancestor_cache_from_fragments(fragments)

    # ---------------------------------------------------------------------
    # FALLBACK: no fragments → single fulltext passage
    # ---------------------------------------------------------------------
    if not fragments:
        try:
            document_passage = {
                "resource_id": resource_id,
                "passage_id": "__fulltext__",
                "type": "fragment",
                "citeType": "text",
                "level": 1,
                "content": normalize_text(
                    resource_metadata.get("metadata", {}).get("text", "")
                ),
                "path": resource_metadata.get("path"),
                "path_ids": resource_metadata.get("path_ids"),
                "ancestors": [],
                "collections": [{
                    "collection_id": collection_metadata["id"],
                    "collection_title": collection_metadata["title"],
                    "path": collection_metadata["path"],
                    "path_ids": collection_metadata["path_ids"],
                    "level": collection_metadata["level"],
                    "dublincore": collection_metadata.get("dublincore", {}),
                }],
                "collection_facets": build_collection_facets(collection_metadata),
                "resource_metadata": sanitize_resource_metadata(
                    resource_metadata
                ),
                "temporal": temporal
                #10juillet "resource_metadata": sanitize_resource_metadata(resource_metadata),
            }

            passages.append(document_passage)
            app.index_stats["passages"] += 1

            report_passages_indexation(app, resource_id, "__fulltext__")

        except Exception as e:
            report_indexation_exception(
                app=app,
                resource_id=resource_id,
                passage_id="__fulltext__",
                error=e,
                context="index_resource_passages_fallback_no_fragments"
            )

    # ---------------------------------------------------------------------
    # MAIN LOOP: fragments → passages
    # ---------------------------------------------------------------------
    for fragment in fragments:
        try:
            passage_id = fragment.get("id")
            if not passage_id:
                continue

            text = normalize_text(fragment.get("content") or fragment.get("head") or "")
            if not text:
                report_no_text_passage(
                    app=app,
                    resource_id=resource_id,
                    passage_id=passage_id,
                    nav=fragment
                )
                continue

            ancestors = ancestor_cache.get(passage_id, [])

            document_passage = {
                "resource_id": resource_id,
                "passage_id": passage_id,
                "type": "fragment",
                "citeType": fragment.get("citeType"),
                "level": fragment.get("level"),
                "title": fragment.get("head"),
                "content": text,
                "fragment_metadata": {
                    "dublincore": fragment.get("metadata_dublincore", {}),
                    "extensions": fragment.get("metadata_extensions", {})
                },
                "path": resource_metadata.get("path"),
                "path_ids": resource_metadata.get("path_ids"),
                "ancestors": ancestors,
                "collections": [{
                    "collection_id": collection_metadata["id"],
                    "collection_title": collection_metadata["title"],
                    "path": collection_metadata["path"],
                    "path_ids": collection_metadata["path_ids"],
                    "level": collection_metadata["level"],
                    "dublincore": collection_metadata.get("dublincore", {}),
                }],
                "collection_facets": build_collection_facets(collection_metadata),
                "resource_metadata": sanitize_resource_metadata(
                    resource_metadata
                ),
                #10juillet "resource_metadata": sanitize_resource_metadata(resource_metadata),
                "temporal": temporal
            }

            passages.append(document_passage)
            app.index_stats["passages"] += 1

            report_passages_indexation(app, resource_id, passage_id)

        except Exception as e:
            report_indexation_exception(
                app=app,
                resource_id=resource_id,
                passage_id=fragment.get("id"),
                error=e,
                context="index_resource_passages_fragment_loop"
            )

    # ---------------------------------------------------------------------
    # WRITE PASSAGES JSONL
    # ---------------------------------------------------------------------
    if passages:
        collection_passages_jsonl_path = f"out/{collection_id}_passages.jsonl"
        with open(collection_passages_jsonl_path, "a", encoding="utf-8") as f:
            for passage in passages:
                f.write(json.dumps(passage, ensure_ascii=False) + "\n")

    # ---------------------------------------------------------------------
    # RESOURCE DOCUMENT JSONL
    # ---------------------------------------------------------------------
    document = {
        "resource_id": resource_id,
        "type": "Resource",
        "level": 0,
        "resource_metadata": sanitize_resource_metadata(
                    resource_metadata
                ),
                #10juillet "resource_metadata": sanitize_resource_metadata(resource_metadata),
        "temporal": temporal,
        "collections": [{
            "collection_id": collection_metadata["id"],
            "collection_title": collection_metadata["title"],
            "path": collection_metadata["path"],
            "path_ids": collection_metadata["path_ids"],
            "level": collection_metadata["level"],
            "dublincore": collection_metadata.get("dublincore", {}),
        }],
        "collection_facets": build_collection_facets(collection_metadata),
    }

    collection_documents_jsonl_path = f"out/{collection_id}_documents.jsonl"
    with open(collection_documents_jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(document, ensure_ascii=False) + "\n")

    app.index_stats["resources"] += 1
    print(f"Document de la ressource écrit dans {collection_documents_jsonl_path}")

    if not async_client_provided:
        await client.aclose()

# async def index_resource_passages_async(
#     app,
#     resource_id: str,
#     collections: list,
#     resource_metadata: dict,
#     client: httpx.AsyncClient = None
# ):
#     """
#     Index passages of a DTS resource asynchronously using httpx.AsyncClient.
#     All original logic and comments are preserved.
#     """
#     collection_metadata = collections[0]
#
#     collection_id = collection_metadata["id"]
#     dts_url = app.config["DTS_URL"]
#     print('index_resource_passages', resource_id)
#     print('index used for resource indexation', app.config["DOCUMENT_INDEX"])
#
#     # Async fetch navigation index
#     nav_index = await build_navigation_index(app, dts_url, resource_id, client=client)
#
#     if nav_index is None:
#         return  # there was an error getting the navigation : we skip the document and go to the next one
#
#     ancestor_cache = build_ancestor_cache(nav_index)
#
#
#     async_client_provided = client is not None
#     client = client or httpx.AsyncClient()  # Shared async client for connection pooling
#
#     # Fetch TEI document asynchronously
#     try:
#         xml_response = await client.get(
#             f"{dts_url}/document",
#             params={"resource": resource_id}
#         )
#         xml_response.raise_for_status()
#     except Exception as e:
#         report_indexation_exception(
#             app=app,
#             resource_id=resource_id,
#             passage_id="__fulltext__",
#             error=e,
#             context="index_resource_passages_document_fetch"
#         )
#         if not async_client_provided:
#             await client.aclose()
#         return
#
#     parser = etree.XMLParser(collect_ids=False)
#     start = time.perf_counter()
#     root = etree.XML(xml_response.content, parser)  # type: ignore
#     XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
#
#     wanted_ids = set(nav_index.keys())
#
#     xmlid_index = {}
#
#     for elem in root.iter():
#         xml_id = elem.get(XML_ID)
#
#         if xml_id in wanted_ids:
#             xmlid_index[xml_id] = elem
#
#             if len(xmlid_index) == len(wanted_ids):
#                 break
#     print("XML parse took", time.perf_counter() - start)
#     # Initialise list to store passages to add to JSONL file
#     passages = []
#     document = None
#
#     # FALLBACK : no DTS fragments for the resource → index all <text>
#     if not nav_index:
#         try:
#             text_nodes = root.xpath("//tei:text", namespaces=XML_NS)
#             if not text_nodes:
#                 raise ValueError("No <tei:text> found in TEI document")
#
#             full_text = normalize_text(
#                 extract_passage_text(text_nodes[0], nav_index={})
#             )
#
#             if not full_text:
#                 raise ValueError("Empty fulltext extracted")
#
#             document_passage = {
#                 "resource_id": resource_id,
#                 "passage_id": "__fulltext__",
#                 "citeType": "text",
#                 "level": 1,
#                 "content": full_text,
#                 "path": resource_metadata["path"],
#                 "path_ids": resource_metadata["path_ids"],
#                 "ancestors": [],
#                 "collections": [{
#                     "collection_id": collection_metadata["id"],
#                     "collection_title": collection_metadata["title"],
#                     "path": collection_metadata["path"],
#                     "path_ids": collection_metadata["path_ids"],
#                     "level": collection_metadata["level"],
#                     "dublincore": collection_metadata.get("dublincore", {}),
#                 }],
#                 "collection_facets": [
#                     f'{collection_metadata["id"]}###{collection_metadata["title"]}'
#                 ],
#                 "resource_metadata": resource_metadata
#             }
#
#             passages.append(document_passage)
#
#             # Passages counter
#             app.index_stats["passages"] += 1
#
#             report_passages_indexation(app, resource_id, '__fulltext__')
#
#         except Exception as e:
#             report_indexation_exception(
#                 app=app,
#                 resource_id=resource_id,
#                 passage_id="__fulltext__",
#                 error=e,
#                 context="index_resource_passages_fulltext_fallback"
#             )
#
#         # Skip main loop if no navigation index
#         nav_index = {}
#
#     # Verifying if nav_index actually exist in TEI
#     for passage_id in nav_index:
#         try:
#             el = xmlid_index.get(passage_id)
#
#             if el is None:
#                 raise IndexError("xml:id not found in TEI document")
#
#         except Exception as e:
#             report_indexation_exception(
#                 app=app,
#                 resource_id=resource_id,
#                 passage_id=passage_id,
#                 error=e,
#                 context="index_resource_passages"
#             )
#             print(
#                 f"⚠️ Passage {passage_id} absent du TEI "
#                 f"(resource {resource_id}) → loggé"
#             )
#             continue
#
#         if not isinstance(el, etree._Element):
#             print("⚠️ Skipping non-element", el)
#             continue
#
#         text = normalize_text(extract_passage_text(el, nav_index=nav_index))
#
#         if not text:
#             report_no_text_passage(
#                 app=app,
#                 resource_id=resource_id,
#                 passage_id=passage_id,
#                 nav=nav_index.get(passage_id, {})
#             )
#             continue
#
#         nav = nav_index.get(passage_id, {})
#
#         ancestors = ancestor_cache.get(passage_id, [])
#
#         document_passage = {
#             "resource_id": resource_id,
#             "passage_id": passage_id,
#             "citeType": nav.get("citeType"),
#             "level": nav.get("level"),
#             "title": nav.get("title"),
#             "content": text,
#             "path": resource_metadata["path"],
#             "path_ids": resource_metadata["path_ids"],
#             "ancestors": ancestors,
#             "collections": [{
#                 "collection_id": collection_metadata["id"],
#                 "collection_title": collection_metadata["title"],
#                 "path": collection_metadata["path"],
#                 "path_ids": collection_metadata["path_ids"],
#                 "level": collection_metadata["level"],
#                 "dublincore": collection_metadata.get("dublincore", {}),
#             }],
#             "collection_facets": [
#                 f'{collection_metadata["id"]}###{collection_metadata["title"]}'
#             ],
#             "resource_metadata": resource_metadata
#         }
#
#         passages.append(document_passage)
#         app.index_stats["passages"] += 1
#         report_passages_indexation(app, resource_id, passage_id)
#
#     # Add all passages by collection to a JSONL file
#     if passages:
#         collection_passages_jsonl_path = f"out/{collection_id}_passages.jsonl"
#         with open(collection_passages_jsonl_path, 'a', encoding='utf-8') as f:
#             for passage in passages:
#                 f.write(json.dumps(passage) + '\n')
#
#     # Create Resource document object
#     document = {
#         "type": "Resource",
#         "level": 0,
#         "resource_metadata": resource_metadata,
#         "collections": [{
#                 "collection_id": collection_metadata["id"],
#                 "collection_title": collection_metadata["title"],
#                 "path": collection_metadata["path"],
#                 "path_ids": collection_metadata["path_ids"],
#                 "level": collection_metadata["level"],
#                 "dublincore": collection_metadata.get("dublincore", {}),
#             }],
#         "collection_facets": [
#             f'{collection_metadata["id"]}###{collection_metadata["title"]}'
#         ]
#     }
#
#     # Add all Resources by collection to a JSONL file
#     collection_documents_jsonl_path = f"out/{collection_id}_documents.jsonl"
#     with open(collection_documents_jsonl_path, 'a', encoding='utf-8') as f:
#         f.write(json.dumps(document) + '\n')
#
#     app.index_stats['resources'] += 1
#     print(f"Document de la ressource écrit dans {collection_documents_jsonl_path}")
#
#     if not async_client_provided:
#         await client.aclose()

async def index_collection(app, collection_metadata):
    """
    Async helper to write collection metadata into a JSONL file and update index stats.
    """
    ensure_out_directory_exists()  # Make sure the 'out' folder exists

    collection_id = collection_metadata["id"]
    collection_jsonl_path = f"out/collections.jsonl"

    # Write collection metadata to JSONL
    try:
        with open(collection_jsonl_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(sanitize_collection_metadata(collection_metadata)) + '\n')
        print(f"Collection indexed and added to {collection_jsonl_path}")

        # Update the stats for indexed collections

        if collection_metadata.get("id", "").lower() != app.config.get("TARGET_COLLECTION", "").lower():
            if collection_metadata.get("parent_id", "").lower() == app.config.get("TARGET_COLLECTION", "").lower():
                app.index_stats["projects"] += 1
            else:
                app.index_stats["collections"] += 1

    except Exception as e:
        print(f"⚠️ Error indexing collection {collection_id}: {e}")
        report_collection_exception(app, collection_metadata["id"], e, context="index_collection_exception")

import httpx

async def fetch_collection(app, collection_id: str):
    """
    Fetch collection details from the DTS asynchronously.
    """
    dts_url = app.config["DTS_URL"]
    # Si on récupère la collection racine (root_collection_id), ne pas inclure `id`
    if len(collection_id) == 0:
        params = {}  # Pas de paramètres, on va chercher la collection DTS racine sans `id`
    else:
        params = {"id": collection_id}  # Utiliser l'ID pour d'autres collections

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(
                f"{dts_url}/collection",
                params=params
            )
            response.raise_for_status()
            return sanitize_dts_json(response.json(), app, collection_id)  # Returns the collection metadata
        except httpx.HTTPStatusError as e:
            print(f"⚠️ HTTP Error when fetching collection {collection_id}: {e}")
            report_collection_exception(app, collection_id, e, context="fetch_collection_http_error")
        except Exception as e:
            print(f"⚠️ General error when fetching collection {collection_id}: {e}")
            report_collection_exception(app, collection_id, e, context="fetch_collection_exception")
    return None


async def get_parent_collection(app, current_id, stop_at, stop_at_name):
    """
    Async helper to fetch the parent collection ID from the DTS API based on the current collection ID.
    This function sends a request to the DTS API to retrieve metadata for the given collection ID.
    If the parent collection exists, it returns the parent ID.

    :param app: The application context, containing configuration (DTS URL).
    :param current_id: The current collection ID for which the parent ID is to be fetched.
    :return: Parent collection ID if exists, otherwise None.
    """
    dts_url = app.config["DTS_URL"]

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Fetch metadata for the current collection
            response = await client.get(f"{dts_url}/collection", params={"id": current_id, "nav": 'parents'})
            response.raise_for_status()  # Check for errors in the response

            # Parse response
            data = response.json()
            # Extract the parent ID from the metadata
            if data.get("member"):
                parent_id = data["member"][0]["@id"]
                parent_label = data["member"][0]['title']
            else:
                parent_id = stop_at
                parent_label = stop_at_name

            if parent_id:
                return [parent_id, parent_label, data.get("title")]
            else:
                return None  # No parent collection found

    except Exception as e:
        print(f"⚠️ Error fetching parent collection ID for {current_id}: {e}")
        return None  # In case of an error, return None


async def build_parent_chain(app, collection_id: str, stop_at: str | None = None, stop_at_name: str | None = None) -> list[str]:
    """
    Build the parent breadcrumb chain for a target collection.
    Stops if stop_at is reached (optional).
    Returns a list of collection_ids from root -> target.
    """
    chain = []
    chain_label = []
    current_id = collection_id

    while current_id:
        if current_id in chain:
            # Prevent infinite loops
            break

        chain.insert(0, current_id)  # prepend to have root -> target
        print('await result', stop_at, stop_at_name)
        result = await get_parent_collection(app, current_id, stop_at, stop_at_name) # async helper fetching DTS metadata
        print('result', result)
        current_name = result[2]

        if current_id != stop_at:
            chain_label.insert(0, current_name)
        parent_id = result[0]
        parent_label = result[1]
        print('build_parent parent_id / parent_label', parent_id, parent_label)
        if stop_at and parent_id == stop_at:
            chain.insert(0, parent_id)
            chain_label.insert(0, parent_label)
            break
        current_id = parent_id

    return [chain, chain_label]


async def resource_worker(
    queue: asyncio.Queue,
    app,
    client: httpx.AsyncClient,
):
    while True:

        item = await queue.get()

        if item is None:
            queue.task_done()
            break

        try:
            await index_resource_passages_async(
                app=app,
                resource_id=item["resource_id"],
                collections=item["collections"],
                resource_metadata=item["resource_metadata"],
                client=client
            )

        except Exception as e:
            print(
                f"⚠️ Worker error on resource {item['resource_id']}: {e}"
            )

        finally:
            queue.task_done()


# async def crawl_branch(app, chain: list[str], chain_label: list[str], collection_index: str, target_collections: set | None, visited: set, semaphore: asyncio.Semaphore, resource_queue: asyncio.Queue | None = None, parent_id=None, parent_path=None, parent_path_ids=None,
#     in_target_branch=False):
#     """
#     Crawl a single branch of collections from root -> target.
#     Only indexes collections in target_collections or non-excluded.
#     """
#     print('crawl_branch debug chain', chain)
#     print('crawl_branch debug chain_label', chain_label)
#     for collection_id in chain:
#         collection_id_lc = collection_id.lower()
#
#         is_target = (
#                 target_collections is not None
#                 and collection_id_lc in {c.lower() for c in target_collections}
#         )
#
#         in_target_branch = in_target_branch or is_target
#         print('crawl_branch debug target_collections, is_target, in_target_branch', collection_id, collection_id_lc, target_collections,
#               is_target, in_target_branch)
#         async with semaphore:
#             # Skip already visited collections
#             if collection_id in visited:
#                 continue
#             visited.add(collection_id)
#
#             # Fetch collection details
#             try:
#                 data = await fetch_collection(app, collection_id)  # async helper
#             except Exception as e:
#                 print(f"⚠️ Impossible de récupérer la collection {collection_id}: {e}")
#                 report_collection_exception(app, collection_id, e, context="crawl_branch_fetch")
#                 continue
#             print("data for collection_id", collection_id + '\n')
#
#             if not data or data.get("@type") != "Collection":
#                 continue
#
#             # Decide whether to index this collection
#             index_current = (
#                 collection_id_lc not in app.excluded_collections
#                 and (target_collections is None or in_target_branch)
#             )
#             print('index_current test ', collection_id_lc not in app.excluded_collections)
#             print('index_current test 2 ', target_collections is None)
#             print('index_current test 3 ', collection_id in chain)
#             print('index_current test 4 ', target_collections is None or in_target_branch)
#             print('index_current test for collection_id_lc ', collection_id_lc, index_current)
#
#             # Extract collection metadata and path_ids
#             collection_metadata = extract_metadata(
#             data,
#             parent_id=parent_id,
#             parent_path=parent_path,
#             parent_path_ids=parent_path_ids
#             )
#
#             collection_es_id = collection_metadata.get("id") or f"collection_{collection_id}"
#
#             if index_current:
#                 print('indexing collection index_current', index_current)
#                 await index_collection(app, collection_metadata)  # async helper: writes JSONL + stats
#
#             # Crawl members recursively
#             for member in data.get("member", []):
#                 member_type = member.get("@type")
#                 member_id = member.get("@id")
#                 member_chain_label = member.get("title")
#                 if not member_id:
#                     continue
#                 member_chain = [member_id]
#                 member_chain_label = [member_chain_label]
#                 print('recursive crawling member ', member_id)
#                 if member_type == "Collection":
#                     # Always (?) explore the branch for target collections
#
#                     print("looping throught member_id", member_id + '\n')
#
#                     # ── Exclusion explicite : coupe la branche sans requête HTTP ──
#                     if member_id.lower() in app.excluded_collections:
#                         print(f"Skipping excluded collection member: {member_id}")
#                         app.index_stats["excluded_collections"] = app.index_stats.get("excluded_collections", 0) + 1
#                         continue
#
#                     print('crawled member : ', member)
#                     await crawl_branch(
#                         app=app,
#                         chain=member_chain,
#                         chain_label= member_chain_label,
#                         collection_index=collection_index,
#                         target_collections=target_collections,
#                         visited=visited,
#                         semaphore=semaphore,
#                         resource_queue=resource_queue,
#                         parent_id=collection_es_id,
#                         parent_path=collection_metadata.get("path"),
#                         parent_path_ids=collection_metadata.get("path_ids"),
#                         in_target_branch = in_target_branch
#                     )
#
#                 elif member_type == "Resource":
#                     # Only index resources if parent or descendant is targeted
#                     if index_current:
#                         resource_metadata = extract_metadata(
#                             member,
#                             parent_id=parent_id,
#                             parent_path=collection_metadata.get("path"),
#                             parent_path_ids=collection_metadata.get("path_ids")
#                         )
#
#                         if resource_queue is not None:
#
#                             await resource_queue.put(
#                                 {
#                                     "resource_id": member_id,
#                                     "collections": [collection_metadata],
#                                     "resource_metadata": resource_metadata
#                                 }
#                             )
#
#                         else:
#
#                             await index_resource_passages_async(
#                                 app=app,
#                                 resource_id=member_id,
#                                 collections=[collection_metadata],
#                                 resource_metadata=resource_metadata
#                             )
#
# async def crawl_collection(app, collection_id: str, collection_index: str, target_collections: set | None = None, parent_id=None, parent_path=None, parent_path_ids=None):
#     """
#     Entry point to crawl collections.
#     Handles full tree or only branches leading to target_collections.
#     """
#     semaphore = asyncio.Semaphore(app.config.get("MAX_CONCURRENT_REQUESTS", 5))
#     visited = set()
#
#     resource_queue = asyncio.Queue(maxsize=200)
#
#     client = httpx.AsyncClient(
#         timeout=httpx.Timeout(
#             connect=10.0,
#             read=120.0,
#             write=30.0,
#             pool=30.0
#         ),
#         limits=httpx.Limits(
#             max_connections=10,
#             max_keepalive_connections=10
#         )
#     )
#
#     RESOURCE_WORKERS = app.config.get(
#         "RESOURCE_WORKERS",
#         5
#     )
#
#     workers = [
#         asyncio.create_task(
#             resource_worker(
#                 queue=resource_queue,
#                 app=app,
#                 client=client
#             )
#         )
#         for _ in range(RESOURCE_WORKERS)
#     ]
#
#     root_collection_id = app.config['TARGET_COLLECTION']
#     root_collection_name = app.config['TARGET_COLLECTION_NAME']
#
#     if target_collections:
#         tasks = []
#         for coll in target_collections:
#             # Build breadcrumb from root to target
#             print('crawl debug', root_collection_name)
#             result = await build_parent_chain(app, coll, stop_at=root_collection_id, stop_at_name=root_collection_name)
#             chain = result[0]
#             chain_label = result[1]
#             print('crawl_collection debug chain', chain)
#             print('crawl_collection debug chain_label', chain_label)
#             parent_id = chain[-2]
#             print('crawl_collection debug parent_id', parent_id)
#             tasks.append(crawl_branch(app, chain, chain_label, collection_index, target_collections, visited, semaphore, resource_queue, parent_id, parent_path, parent_path_ids))
#         await asyncio.gather(*tasks)
#
#         await resource_queue.join()
#
#         for _ in workers:
#             await resource_queue.put(None)
#
#         await asyncio.gather(*workers)
#
#         await client.aclose()
#
#     else:
#         # Full tree crawl
#         await crawl_branch(
#             app=app,
#             chain=[root_collection_id],
#             chain_label=[],
#             collection_index=collection_index,
#             target_collections=None,
#             visited=visited,
#             semaphore=semaphore,
#             resource_queue=resource_queue,
#             parent_id=root_collection_id,
#             parent_path=None,
#             parent_path_ids=None
#         )

async def crawl_branch(app, chain: list[str], chain_label: list[str], collection_index: str, target_collections: set | None, visited: set, semaphore: asyncio.Semaphore, resource_queue: asyncio.Queue | None = None, parent_id=None, parent_path=None, parent_path_ids=None,
    in_target_branch=False):
    """
    Crawl a single branch of collections from root -> target.
    Only indexes collections in target_collections or non-excluded.
    """

    print('crawl_branch debug chain', chain)
    print('crawl_branch debug chain_label', chain_label)

    # ─────────────────────────────────────────────
    # Thunderdots lookup tables (ONE TIME BUILD)
    # ─────────────────────────────────────────────
    collection_results = {
        c.get("@id") or c.get("id"): c
        for c in app.thunderdots_results.get("collection_results", [])
        if c.get("@id") or c.get("id")
    }

    resource_results = {
        r.get("@id") or r.get("id"): {
            **r,
            "path": r.get("path") or [],
            "path_ids": r.get("path_ids") or []
        }
        for r in app.thunderdots_results.get("resource_results", [])
    }

    for collection_id in chain:
        collection_id_lc = collection_id.lower()

        is_target = (
                target_collections is not None
                and collection_id_lc in {c.lower() for c in target_collections}
        )

        in_target_branch = in_target_branch or is_target

        print(
            'crawl_branch debug target_collections, is_target, in_target_branch',
            collection_id,
            collection_id_lc,
            target_collections,
            is_target,
            in_target_branch
        )

        async with semaphore:

            if collection_id in visited:
                continue
            visited.add(collection_id)

            # ─────────────────────────────
            # REPLACEMENT FETCH_COLLECTION
            # ─────────────────────────────
            data = collection_results.get(collection_id)

            if not data or data.get("@type") != "Collection":
                continue

            print("data for collection_id", collection_id + '\n')

            index_current = (
                collection_id_lc not in app.excluded_collections
                and (target_collections is None or in_target_branch)
            )

            print('index_current test ', collection_id_lc not in app.excluded_collections)
            print('index_current test 2 ', target_collections is None)
            print('index_current test 3 ', collection_id in chain)
            print('index_current test 4 ', target_collections is None or in_target_branch)
            print('index_current test for collection_id_lc ', collection_id_lc, index_current)

            collection_metadata = extract_metadata(
                data,
                parent_id=parent_id,
                parent_path=parent_path,
                parent_path_ids=parent_path_ids
            )

            collection_es_id = collection_metadata.get("id") or f"collection_{collection_id}"

            if index_current:
                print('indexing collection index_current', index_current)
                await index_collection(app, collection_metadata)

            # ─────────────────────────────
            # MEMBERS LOOP (UNCHANGED LOGIC)
            # ─────────────────────────────
            for member in data.get("member", []):

                member_type = member.get("@type")
                member_id = member.get("@id")
                member_chain_label = member.get("title")

                if not member_id:
                    continue

                member_chain = [member_id]
                member_chain_label = [member_chain_label]

                print('recursive crawling member ', member_id)

                if member_type == "Collection":

                    print("looping throught member_id", member_id + '\n')

                    if member_id.lower() in app.excluded_collections:
                        print(f"Skipping excluded collection member: {member_id}")
                        app.index_stats["excluded_collections"] = app.index_stats.get("excluded_collections", 0) + 1
                        continue

                    print('crawled member : ', member)

                    await crawl_branch(
                        app=app,
                        chain=member_chain,
                        chain_label=member_chain_label,
                        collection_index=collection_index,
                        target_collections=target_collections,
                        visited=visited,
                        semaphore=semaphore,
                        resource_queue=resource_queue,
                        parent_id=collection_es_id,
                        parent_path=collection_metadata.get("path"),
                        parent_path_ids=collection_metadata.get("path_ids"),
                        in_target_branch=in_target_branch
                    )

                elif member_type == "Resource":

                    if index_current:

                        # ─────────────────────────────
                        # REPLACEMENT extract_metadata RESOURCE
                        # ─────────────────────────────
                        data = resource_results.get(member_id)


                        if data:
                            # injection du contexte crawl (inchangé fonctionnellement)

                            resource_metadata = extract_metadata(
                                data,
                                parent_id=parent_id,
                                parent_path=collection_metadata.get("path"),
                                parent_path_ids=collection_metadata.get("path_ids")
                            )

                            #10juillet resource_metadata = extract_metadata(
                            #     data,
                            #     parent_id=parent_id,
                            #     parent_path=collection_metadata.get("path"),
                            #     parent_path_ids=collection_metadata.get("path_ids")
                            # )

                            # resource_metadata = {
                            #     **data,
                            #     "parent_id": parent_id,
                            #     "parent_path": collection_metadata.get("path"),
                            #     "parent_path_ids": collection_metadata.get("path_ids")
                            # }

                        if resource_queue is not None:

                            await resource_queue.put(
                                {
                                    "resource_id": member_id,
                                    "collections": [collection_metadata],
                                    "resource_metadata": resource_metadata
                                }
                            )

                        else:

                            await index_resource_passages_async(
                                app=app,
                                resource_id=member_id,
                                collections=[collection_metadata],
                                resource_metadata=resource_metadata
                            )


async def crawl_collection(app, collection_id: str, collection_index: str, target_collections: set | None = None, parent_id=None, parent_path=None, parent_path_ids=None):
    """
    Entry point to crawl collections.
    Handles full tree or only branches leading to target_collections.
    """
    semaphore = asyncio.Semaphore(app.config["MAX_CONCURRENT_REQUESTS"])
    visited = set()

    resource_queue = asyncio.Queue(maxsize=200)

    client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=10.0,
            read=120.0,
            write=30.0,
            pool=30.0
        ),
        limits=httpx.Limits(
            max_connections=10,
            max_keepalive_connections=10
        )
    )

    RESOURCE_WORKERS = app.config["RESOURCE_WORKERS"]

    workers = [
        asyncio.create_task(
            resource_worker(
                queue=resource_queue,
                app=app,
                client=client
            )
        )
        for _ in range(RESOURCE_WORKERS)
    ]

    root_collection_id = app.config['TARGET_COLLECTION']
    root_collection_name = app.config['TARGET_COLLECTION_NAME']

    if target_collections:
        tasks = []
        for coll in target_collections:
            # Build breadcrumb from root to target
            print('crawl debug', root_collection_name)
            result = await build_parent_chain(app, coll, stop_at=root_collection_id, stop_at_name=root_collection_name)
            chain = result[0]
            chain_label = result[1]
            print('crawl_collection debug chain', chain)
            print('crawl_collection debug chain_label', chain_label)
            parent_id = chain[-2]
            print('crawl_collection debug parent_id', parent_id)
            tasks.append(crawl_branch(app, chain, chain_label, collection_index, target_collections, visited, semaphore, resource_queue, parent_id, parent_path, parent_path_ids))
        await asyncio.gather(*tasks)

    else:
        # Full tree crawl
        await crawl_branch(
            app=app,
            chain=[root_collection_id],
            chain_label=[],
            collection_index=collection_index,
            target_collections=None,
            visited=visited,
            semaphore=semaphore,
            resource_queue=resource_queue,
            parent_id=root_collection_id,
            parent_path=None,
            parent_path_ids=None
        )

    await resource_queue.join()

    for _ in workers:
        await resource_queue.put(None)

    await asyncio.gather(*workers)

    await client.aclose()


async def dotsplorer(app, collections, _index_name):
    """
        Commande principale d'indexation :
        - Purge /out
        - Crawl des collections et resources
        - Écriture des fichiers JSONL
        - Indexation Elasticsearch via index_jsonl()
        - Optionally, limit indexing to specific collections with --collections.
    """
    # Purge previous DTS fetch result (/out folder)
    out_dir = os.path.join(os.getcwd(), "out")
    if not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    for filename in os.listdir(out_dir):
        file_path = os.path.join(out_dir, filename)
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"⚠️ Impossible to purge {file_path}: {e}")
    print("Folder /out purged prior fetching/crawl DTS")

    # Crawl DTS collections to extract collection/resource/fragment data for indexing
    try:
        # collection racine DTS
        root_collection_id = app.config['TARGET_COLLECTION'] or ""
        print('dosplorer TARGET_COLLECTION', app.config['TARGET_COLLECTION'], root_collection_id, len(root_collection_id))
        if len(root_collection_id) == 0:
            dts_root_collection = await fetch_collection(app, "")
            print('dosplorer dts_root_collection response', dts_root_collection)
            app.config['TARGET_COLLECTION'] = dts_root_collection["@id"]
            app.config['TARGET_COLLECTION_NAME'] = dts_root_collection["title"]
        else:
            root_collection = await fetch_collection(app, root_collection_id)
            app.config['TARGET_COLLECTION_NAME'] = root_collection["title"]
        root_collection_id = app.config['TARGET_COLLECTION']
        print('dosplorer root_collection_id', root_collection_id, app.config['TARGET_COLLECTION_NAME'])
        print("Crawling DTS collections and resources from root_collection_id: ", root_collection_id, app.config['TARGET_COLLECTION_NAME'])

        # collection exclusion
        settings_excluded = load_excluded_collections_from_settings(
            app.config.get("CUSTOM_SETTINGS_PATH")
        )
        print("A custom setting folder was defined: ", app.config.get("CUSTOM_SETTINGS_PATH"))
        print("These settings will exclude the following collections ids: ", settings_excluded)

        manual_excluded = app.config.get("ADDITIONAL_EXCLUDED_COLLECTIONS")
        print("You have manually also excluded the following collections ids:", manual_excluded)

        excluded_collections = settings_excluded | manual_excluded

        app.excluded_collections = excluded_collections

        # statistiques partagées du run d'indexation
        app.index_stats = {
            "projects": 0,  # collections sans parent
            "collections": 0,  # collections avec parent
            "excluded_collections": 0, # collections exclues
            "resources": 0,
            "passages": 0,
            "bulk_es_errors": 0
        }

        start_time = time.perf_counter()

        print("Crawling DTS collections and resources from root_collection_id …", root_collection_id)
        # fichiers horodatés (calculés après que app soit créé)
        csv_paths = get_indexation_csv_paths(app)

        # création des fichiers de reporting vides avec header
        ensure_csv_file(csv_paths["document_exceptions"], [
            "timestamp", "resource_id", "passage_id", "error_type", "error_message", "context"
        ])
        ensure_csv_file(csv_paths["document_no_text"], [
            "timestamp", "resource_id", "passage_id", "citeType", "level", "reason", "context"
        ])
        ensure_csv_file(csv_paths["collection_exceptions"], [
            "timestamp", "collection_id", "error_type", "error_message", "context"
        ])
        ensure_csv_file(csv_paths["indexed_passages_report"], [
            "timestamp", "resource_id", "passage_id"
        ])

        from thunderdots import ThunderDots
        print("Building Thunderdots global graph...")

        td = ThunderDots(
            endpoint_dts=app.config['DTS_URL'],
            collection_params={
                "collection_id": app.config['TARGET_COLLECTION'],
                "metadata_dublincore": None,
                "metadata_extensions": None,
            },
            resource_params={
                "fragment_mode": "navigation",
                "metadata_dublincore": None,
                "metadata_extensions": None,
                "add_head_to_content": False,
            },
            fragment_params={
                "metadata_dublincore": None
            },
        )

        td.fetch()
        td_results = td.results()

        elastic_docs = td.to_elastic_documents(
            include_fragments=False
        )

        # on le garde globalement accessible pour les facettes / autocomplete
        app.thunderdots_results = td_results

        app.thunderdots_temporal = {
            doc["id"]: doc.get("temporal", {})
            for doc in elastic_docs
        }

        print("Thunderdots graph built")

        # --- traitement des collections ciblées ---
        if collections:
            # split et suppression des espaces autour
            target_collections = {c.strip() for c in collections.split(",") if c.strip()}
            print(f"Collections ciblées pour l'indexation: {target_collections}")
        else:
            target_collections = None  # tout indexer

        await crawl_collection(
            app=app,
            collection_id=root_collection_id,
            collection_index=_index_name,
            target_collections=target_collections
        )
        # ─────────────────────────────
        # Résumé de fin d’indexation
        # ─────────────────────────────

        doc_errors = count_csv_rows(csv_paths["document_exceptions"])
        doc_no_text = count_csv_rows(csv_paths["document_no_text"])
        collection_errors = count_csv_rows(csv_paths["collection_exceptions"])

        end_time = time.perf_counter()
        duration = format_duration(end_time - start_time)

        stats = app.index_stats

        target = app.config.get("TARGET_COLLECTION")
        print('dosplorer target', target)

        if target:
            title = f"📊  Résumé du crawler de la collection cible {target}"
        else:
            title = "📊  Résumé du crawler"

        print("\n" + "=" * 60)
        print(title)
        print("=" * 60)
        print(f"🗂️  Projets (collections racine) : {stats['projects']}")
        print(f"📁  Sous-collections             : {stats['collections']}")
        print(f"📄  Resources crawlées           : {stats['resources']}")
        print(f"🧩  Passages crawlées            : {stats['passages']}")
        print(f"📁  Collections exclues          : {stats['excluded_collections']}")
        print("────────────────────────")
        print(f"📄  Passages en erreur           : {doc_errors}")
        print(f"📄  Passages sans texte          : {doc_no_text}")
        print(f"🗂️  Collections en erreur        : {collection_errors}")
        print("────────────────────────")
        print(f"🕒  Timestamp du crawler             : {ts}")
        print(f"⏱️  Durée totale du crawler          : {duration}")
        print("=" * 60 + "\n")
        #print("DTS collections and documents indexed successfully.")

    except Exception as e:
        print('Indexation error (collections): ', str(e))

def make_cli():
    """ Creates a Command Line Interface for everydays tasks

    :return: Click groum
    """

    @click.group()
    @click.option('--config', default="staging", type=click.Choice(["local", "staging", "prod"]), help="select appropriate .yml file to use", show_default=True)
    @click.pass_context
    def cli(ctx, config):
        config_dict = load_config(config)
        ctx.obj = CLIContext(config_dict)

    @click.command("search")
    @click.argument('query')
    @click.option('--indexes', required=False, default=None, help="index names separated by a comma")
    @click.option('-t', '--term', is_flag=True, help="use a term instead of a whole query")
    @click.pass_obj
    def search(cli_ctx: CLIContext, query, indexes, term):
        """
        Perform a search using the provided query. Use --term or -t to simply search a term.
        """
        app = cli_ctx.app
        indexes = indexes or app.all_indexes

        if term:
            es_query = {
                "query_string": {
                    "query": query
                }
            }
        else:
            es_query = {
                "match": {
                    "content": query
                }
            }

        result = app.elasticsearch.search(
            index=indexes,
            query=es_query
        )

        print("\n", "=" * 12, " RESULT ", "=" * 12)
        pprint.pprint(result)

    @click.command("update-conf")
    @click.option('--indexes', default=None, help="index names separated by a comma")
    @click.option('--rebuild', is_flag=True, help="truncate the index before updating its configuration")
    @click.pass_obj
    def update_conf(cli_ctx: CLIContext, indexes, rebuild):
        """
        Update the index configuration and mappings
        """
        update_conf_internal(cli_ctx, indexes=indexes, rebuild=rebuild)

    @click.command("delete")
    @click.option('--indexes', required=True, help="index names separated by a comma")
    @click.pass_obj
    def delete_indexes(cli_ctx: CLIContext, indexes):
        """
        Delete the indexes
        """
        app = cli_ctx.app
        indexes = indexes or app.all_indexes

        for name in indexes.split(','):
            url = '/'.join([app.config['ELASTICSEARCH_URL'], name])
            res = None
            try:
                print(f"Deleting {name} index.")
                with httpx.Client(auth=es_basic_auth()) as client:
                    res = client.delete(url)
            except Exception as e:
                print(res.text, str(e), flush=True, end=" ")
                raise e

    @click.command("index")
    @click.option("--collections", "-c", default=None,
                  help="Comma separated collection ids to index, ex: coll1, coll2,coll3")
    @click.pass_obj
    def index(cli_ctx: CLIContext, collections):
        """
            Commande principale d'indexation :
            - Purge /out
            - Crawl des collections et resources
            - Écriture des fichiers JSONL
            - Indexation Elasticsearch via index_jsonl()
                - passages (DOCUMENT_INDEX)
                - documents (DOCUMENT_INDEX)
                - collections (COLLECTION_INDEX)
            - Optionally, limit indexing to specific collections with --collections.
        """

       # crawler and extract DTS data
        app = cli_ctx.app
        es = app.elasticsearch
        doc_index = app.config["DOCUMENT_INDEX"]
        coll_index = app.config["COLLECTION_INDEX"]

        MERGE_COLLECTIONS_SCRIPT = """
        if (ctx._source.collections == null) {
          ctx._source.collections = params.collections;
        } else {
          for (c in params.collections) {
            boolean exists = false;
            for (e in ctx._source.collections) {
              if (e.collection_id == c.collection_id) {
                exists = true;
                break;
              }
            }
            if (!exists) {
              ctx._source.collections.add(c);
            }
          }
        }
        """

        print('index collections:', collections)
        print('index _index_name:', app.config['COLLECTION_INDEX'])
        print('index _index_name:', app.config['DOCUMENT_INDEX'])
        try:
            _index_name = app.config['COLLECTION_INDEX']
            asyncio.run(dotsplorer(
                app=app,
                collections=collections,
                _index_name=_index_name
            ))

        except Exception as e:
            print('dotsplorer error (collections): ', str(e))


        # Indexation ES
        # try:
        for index_name in (doc_index, coll_index):
            if not es.indices.exists(index=index_name):
                print(f"⚠️ Index {index_name} not found → creating with correct mapping")
                # Appel positionnel correct pour update_conf
                update_conf_internal(cli_ctx, indexes=index_name, rebuild=True)
            else:
                print(f"✅ Index {index_name} exists, nothing to do for mapping")
                warn_on_mapping_drift(app, es, index_name)
        # except Exception as e:
        #     print(f"❌ Erreur lors de la vérification ou création de l’index {doc_index}: {e}")
        #     return

        out_dir = os.path.join(os.getcwd(), "out")
        if not os.path.exists(out_dir):
            print("⚠️ Aucun dossier /out trouvé.")
            return

        # Lister tous les fichiers JSONL dans /out
        all_files = [f for f in os.listdir(out_dir) if f.endswith(".jsonl")]

        # Union the collections of every resource before indexing: otherwise
        # the last branch written overwrites the branches seen before it.
        collection_scopes = collect_resource_collection_scopes(out_dir)
        multi_parent = sum(
            1 for scope in collection_scopes.values()
            if len(scope["collections"]) > 1
        )
        print(
            f"Portée des collections : {len(collection_scopes)} ressources, "
            f"dont {multi_parent} rattachées à plusieurs collections"
        )

        # -----------------------------
        # 1 Indexer les passages
        # -----------------------------
        start_fragments_indexation = time.perf_counter()

        passage_files = [f for f in all_files if f.endswith("_passages.jsonl")]
        for passage_file in passage_files:
            bulk_actions = []
            path = os.path.join(out_dir, passage_file)
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        doc = json.loads(line)
                        apply_collection_scope(doc, collection_scopes)

                        bulk_actions.append({"index": {"_index": app.config["DOCUMENT_INDEX"],
                                                       "_id": f'{doc["resource_id"]}::{doc["passage_id"]}'}})
                        bulk_actions.append(doc)

                        # bulk_actions.append({
                        #     "update": {
                        #         "_index": app.config["DOCUMENT_INDEX"],
                        #         "_id": f'{doc["resource_id"]}::{doc["passage_id"]}'
                        #     }
                        # })
                        #
                        # bulk_actions.append({
                        #     "scripted_upsert": True,
                        #     "script": {
                        #         "lang": "painless",
                        #         "source": MERGE_COLLECTIONS_SCRIPT,
                        #         "params": {
                        #             "collections": doc.get("collections", [])
                        #         }
                        #     },
                        #     "upsert": doc
                        # })
                    except Exception as e:
                        report_passage_indexation_errors(
                            app,
                            resource_id=doc.get("resource_id", "*"),
                            passage_id=doc.get("passage_id", "*"),
                            error=e
                        )
            if bulk_actions:
                try:
                    response = app.elasticsearch.bulk(body=bulk_actions, refresh=False)
                except Exception as e:
                    report_passage_indexation_errors(app, resource_id="*", passage_id="*", error=e)
                else:
                    if response.get("errors"):
                        for item in response.get("items", []):
                            action = item.get("index", {})
                            if "error" in action:
                                es_id = action.get("_id", "")
                                passage_id = es_id.split("::", 1)[1] if "::" in es_id else es_id
                                resource_id = es_id.split("::", 1)[0] if "::" in es_id else "*"
                                report_passage_indexation_errors(
                                    app,
                                    resource_id=resource_id,
                                    passage_id=passage_id,
                                    error=Exception(action["error"].get("reason", "ES bulk error"))
                                )
                        app.index_stats["bulk_es_errors"] += 1
            app.index_stats["passages_indexed"] = app.index_stats.get("passages_indexed", 0) + len(bulk_actions) // 2

        end_fragments_indexation = time.perf_counter()

        # -----------------------------
        # 2 Indexer les documents
        # -----------------------------
        start_documents_indexation = time.perf_counter()

        document_files = [f for f in all_files if f.endswith("_documents.jsonl")]
        # A multi-parent resource appears in several files: index it once,
        # carrying the union of its collections.
        indexed_resources = set()
        for document_file in document_files:
            path = os.path.join(out_dir, document_file)
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        doc = json.loads(line)

                        if doc.get("resource_id") in indexed_resources:
                            continue

                        apply_collection_scope(doc, collection_scopes)
                        app.elasticsearch.index(
                            index=app.config["DOCUMENT_INDEX"],
                            id=doc["resource_metadata"]["id"],
                            body=doc
                        )
                        indexed_resources.add(doc.get("resource_id"))
                        app.index_stats["resources_indexed"] = app.index_stats.get("resources_indexed", 0) + 1
                    except Exception as e:
                        report_resource_indexation_errors(app, resource_id=doc.get("resource_id", "*"), error=e)

        end_documents_indexation = time.perf_counter()

        # -----------------------------
        # 3 Indexer les collections
        # -----------------------------
        start_collections_indexation = time.perf_counter()

        collection_file = os.path.join(out_dir, "collections.jsonl")
        if os.path.exists(collection_file):
            with open(collection_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        collection = json.loads(line)
                        collection_es_id = collection.get("id") or f"collection_{collection.get('@id', 'unknown')}"
                        app.elasticsearch.index(
                            index=app.config["COLLECTION_INDEX"],
                            id=collection_es_id,
                            body=sanitize_collection_metadata(collection)
                        )
                        app.index_stats["collections_indexed"] = app.index_stats.get("collections_indexed", 0) + 1
                    except Exception as e:
                        report_collection_indexation_errors(app, collection_id=collection.get("id", "*"), error=e)

        end_collections_indexation = time.perf_counter()

        timer_collections_indexation = end_collections_indexation - start_collections_indexation
        timer_documents_indexation = end_documents_indexation - start_documents_indexation
        timer_fragments_indexation = end_fragments_indexation - start_fragments_indexation
        timer_total_indexation = timer_collections_indexation + timer_documents_indexation + timer_fragments_indexation

        # -----------------------------
        # Reporting des durées par phase
        # -----------------------------
        timing_csv = get_indexation_csv_paths(app)["timing"]
        _now = datetime.now(timezone.utc).isoformat()

        for _phase_id, _duration in (
            ("fragments_indexation", timer_fragments_indexation),
            ("documents_indexation", timer_documents_indexation),
            ("collections_indexation", timer_collections_indexation),
            ("total_indexation", timer_total_indexation),
        ):
            report_timing(timing_csv, {
                "timestamp": _now,
                "level": "phase",
                "id": _phase_id,
                "parent_id": "total_indexation" if _phase_id != "total_indexation" else "",
                "duration_sec": round(_duration, 3),
                "duration_hms": format_duration(_duration),
            })

        print("\n" + "=" * 60)
        print("📊  Résumé de l’indexation")
        print("=" * 60)
        # print(f"❌  Erreurs ES (bulk)                    : ")#{ app.index_stats["bulk_es_errors"] }
        print(f"🗂️  Durée indexation collection         : { format_duration(timer_collections_indexation) }")
        print(f"📁  Durée indexation documents          : { timer_documents_indexation }")
        print(f"📄  Durée indexation fragments          : { timer_fragments_indexation }")
        print(f"🧩  Durée totale d'indexation           : { timer_total_indexation }")



    cli.add_command(delete_indexes)
    cli.add_command(update_conf)
    cli.add_command(index)
    cli.add_command(search)
    return cli


def main():
    """Console entry point for the DoTS indexing CLI."""
    make_cli()()


if __name__ == "__main__":
    main()
