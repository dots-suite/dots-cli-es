from elasticsearch import Elasticsearch
from flask import Flask, Blueprint
from dots_es.config_loader import load_config, es_basic_auth

api_bp = Blueprint('api_bp', __name__)

def parse_es_doc_id(es_id: str) -> str:
    """
    Parse an Elasticsearch _id of the form:
    resource_id::passage_id

    Returns: resource_id&ref=passage_id
    """
    if "::" in es_id:
        resource_id, passage_id = es_id.split("::", 1)
        return f"{resource_id}&ref={passage_id}"
    else:
        return es_id

def create_app(config_name: str):
    """Create the Flask application using YAML configuration.

    :param config_name: Configuration alias (e.g., local, staging, prod), selects config/{config_name}.yml
    :type config_name: str
    :return: Flask app instance
    :rtype: Flask
    """
    app = Flask(__name__)

    # Load YAML config
    config_dict = load_config(config_name)
    app.config.update(config_dict)

    # Initialize Elasticsearch client if URL is present
    app.elasticsearch = Elasticsearch(
        [app.config['ELASTICSEARCH_URL']],
        basic_auth=es_basic_auth()
    ) if app.config.get('ELASTICSEARCH_URL') else None

    with app.app_context():
        # Import and register search endpoint
        from dots_es.api.search import register_search_endpoint

        def compose_result(search_result):
            results = []
            for h in search_result['hits']['hits']:
                fields = h.get('_source', {}).copy()
                fields.pop("content", None)  # remove content if exists
                fields['dts_url'] = f"{app.config['DTS_URL']}/document?resource={parse_es_doc_id(h['_id'])}"
                results.append({
                    "id": h['_id'],
                    "score": h.get('_score'),
                    "fields": fields,
                    "highlight": h.get('highlight')
                })
            return results

        register_search_endpoint(api_bp, "1.0", compose_result)
        app.register_blueprint(api_bp)

    # Errors too, so that a browser client can read them
    @app.after_request
    def allow_any_origin(response):
        response.headers.setdefault("Access-Control-Allow-Origin", "*")
        return response

    return app
