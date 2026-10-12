# The [DoTS search](https://dev.chartes.psl.eu/dots/api/dts/collection) API

Elasticsearch API to search the [DoTS collections & resources](https://dev.chartes.psl.eu/dots/api/dts/collection).

![Static Badge](https://img.shields.io/badge/python-3.12-blue?style=for-the-badge&logo=python&label=PYTHON&color=blue)

![Static Badge](https://img.shields.io/badge/Flask-3.1.0-blue?logo=flask)
![Static Badge](https://img.shields.io/badge/elasticsearch-8.12-blue?logo=elasticsearch)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE.md)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://dots-suite.github.io/dots-cli-es/)

## Prerequisite - Install Elasticsearch

### Install Elasticsearch _and_ its ICU plugin

:warning: Use an ES version compatible with [requirements.txt](./requirements.txt)
:information_source: Below commands are run independently/outside virtual environments (`deactivate`)

- Elasticsearch: refer to your organisation instructions or [Elasticsearch guidelines](https://www.elastic.co/guide/en/elasticsearch/reference/current/install-elasticsearch.html#elasticsearch-install-packages)
- [ICU plugin](https://www.elastic.co/guide/en/elasticsearch/plugins/current/analysis-icu.html): check if ICU is installed with `uconv -V`, otherwise:
  <pre><code><b><i>path/to/elasticsearch_folder</i></b>/bin/elasticsearch-plugin install analysis-icu</code></pre>

- With docker (security disabled)
    <pre><code>
      docker run --name <b><i>dots-es</i></b> -d -p 9200:9200 -e "discovery.type=single-node" -e "xpack.security.enabled=false" -e "xpack.security.http.ssl.enabled=false" elasticsearch:8.12.1
      docker exec <b><i>dots-es</i></b> bash -c "bin/elasticsearch-plugin install analysis-icu"
      docker restart <b><i>dots-es</i></b>
    </code></pre>

## Install

- Clone the GitHub repository in your projects' folder:
<pre>
<code>
  cd <b><i>path/to/projects_folder/</i></b>
  git clone https://github.com/dots-suite/dots-cli-es.git
</code>
</pre>
- Ensure you are running Python 3.12, for example with pyenv:

  ```bash
  pyenv shell 3.12
  ```

- Set up the virtual environment:
  <pre><code>
  cd <b><i>path/to/projects_folder</i></b>/dots-cli-es
  python3 -m venv <b><i>your_venv_name</i></b>
  source <b><i>your_venv_name</i></b>/bin/activate
  pip install .
  </code></pre>

  This installs the `dots-es` package plus the `dots-es-cli` and `dots-api` console scripts.
  For development (editable install + dev tools):
  <pre><code>
  pip install -e . -r requirements-dev.txt
  </code></pre>

- The `requirements*.txt` files are **generated**, not edited by hand. Dependencies are
  declared in `pyproject.toml` and compiled with `pip-tools`:
  <pre><code>
  pip-compile pyproject.toml -o requirements.txt
  pip-compile --extra dev pyproject.toml -o requirements-dev.txt
  </code></pre>

### Install thunderdots from a local directory (development)

`thunderdots` is a runtime dependency of the indexing CLI. To develop on it
locally, install it in editable mode from your local repository instead of
PyPI. The local version must satisfy the specifier declared in `pyproject.toml`
(currently `thunderdots>=0.1.dev,<0.2`).

1. Install the package and its dependencies as usual:
   <pre><code>
   pip install -e . -r requirements-dev.txt
   </code></pre>

2. Override thunderdots with your local directory:
   <pre><code>
   pip install -e <b><i>path/to/thunderdots</i></b>
   </code></pre>
   Any change in the local repository is reflected immediately, without reinstalling.
   :warning: The local version must satisfy the `thunderdots` specifier above. A
   strict pin (e.g. `==0.1.6`) would conflict with a development snapshot such as
   `0.1.dev37`.

To check that thunderdots is installed from your local directory:

<pre><code>
pip list
</code></pre>

- For servers requiring uWSGI to run Python apps (remote Nginx servers):
  - check if uWSGI is installed `pip list --local`
  - install it in your virtual _**your_venv_name**_ if it's not: `pip install uwsgi`.
    The WSGI application is located at `flask_app:flask_app`
    _NB : this command may require wheel:_
    - to check whether wheel is installed: `pip show wheel`
    - to install it if required: `pip install wheel`

## Indexing

- Install Elasticsearch and create indices _if they are not available_:

- Follow the ES installation & initial indexing instructions [above](#prerequisite---install-elasticsearch)

> :warning: Below (re)indexing commands are run within the app virtual environment:
> reactivate the virtual environment if needed (<code>source <b><i>your_venv_name</i></b>/bin/activate</code>)
>
> In below commands, options are indicated within brackets <em>(option)</em>. Remove them as required.
>
> With ES security enabled, the<em>ES_PASSWORD</em> option is required in commands below.

### Initial indexing (and reindexing without configuration changes):

<pre><code>
(ES_PASSWORD=<b><i>ELASTIC_PASSWORD</i></b>) dots-es-cli (--config=<b><i>local/staging/prod</i></b>) index (--collections=<b><i>theater,ENCPOS</i></b>)
</code></pre>

When the index doesn't exist it is created according to the project ES [configuration files](./dots_es/elasticsearch/).

### Updating index configuration

This operation will delete the pre-existing index.

<pre><code>
(ES_PASSWORD=<b><i>ELASTIC_PASSWORD</i></b>) dots-es-cli (--config=<b><i>local/staging/prod</i></b>) update-conf --rebuild --indexes=<b><i>dots_collections/dots_resources/dots_fragments</i></b>
</code></pre>

The above command updates the indexes according to the project ES [configuration files](./dots_es/elasticsearch/).

### Check created indexes:

- with ES security disabled:
<pre><code>
curl -X POST "http://localhost:9200/dots_fragments/_refresh?pretty"
curl http://localhost:9200/_cat/indices?v
</code></pre>

- with ES security enabled:
<pre><code>
curl -X POST "http://elastic:<b><i>ELASTIC_PASSWORD</i></b>@localhost:9200/dots_fragments/_refresh?pretty"
curl http://elastic:<b><i>ELASTIC_PASSWORD</i></b>@localhost:9200/_cat/indices?v
</code></pre>

## Launch the app:

> :warning: Below commands are mainly for local launch.
> For servers, apps may be started via processes management tools, refer to the servers documentation

- Reactivate the virtual environment if needed (<code>source <b><i>your_venv_name</i></b>/bin/activate</code>)
- Launch:
  <code>(ES_PASSWORD=<b><i>ELASTIC_PASSWORD</i></b>) dots-api</code> (or <code>python3 flask_app.py</code>)
- Then visit http://localhost:5003/api/1.0/search?query=\*&index=dots_fragments to test it is running

## Launch the front-end:

- [Front-end's Readme](https://github.com/dots-suite/dots-vue)

## Documentation

The documentation site (MkDocs Material) lives in [`mkdocs/`](./mkdocs/) and is published to GitHub
Pages by [`.github/workflows/docs.yml`](./.github/workflows/docs.yml) on every push to `dev`. Pushes
to `master` build the site as a check, without publishing it.

Everyday commands:

```bash
make docs.install    # install the toolchain (once)
make docs.serve      # live-reload server on http://127.0.0.1:8000
make docs.build      # build in strict mode — exactly what the CI runs
make docs.clean      # remove the generated site
make help            # list every target
```

`make docs.build` runs `mkdocs build --strict`, which **fails** on any broken internal link or any
page missing from the nav. Run it before pushing.

### Documentation dependencies

The documentation toolchain is deliberately kept **separate from the application dependencies**:
building the site must not require Elasticsearch, lxml or thunderdots. It is therefore not declared
as an extra in `pyproject.toml`, but in its own files.

As everywhere else in this project, the pinned file is **generated, not edited by hand**. Edit
`mkdocs/requirements.in` to add or change a dependency, then recompile:

```bash
make docs.lock
# equivalent to:
pip-compile mkdocs/requirements.in -o mkdocs/requirements.txt
```

`pip-compile` comes from the `dev` extra (`pip install -e . -r requirements-dev.txt`).

---

Additional details for offline commands:

```bash
dots-es-cli --help

Usage: dots-es-cli [OPTIONS] COMMAND [ARGS]...

Options:
  --config [local|staging|prod]  select appropriate .yml file to use
                                 [default: staging]
  --help                         Show this message and exit.

Commands:
  delete       Delete the indexes
  index        Rebuild the elasticsearch indexes
  search       Perform a search using the provided query.
  update-conf  Update the index configuration and mappings

```

---

## License

dots-cli-es is distributed under the [MIT License](./LICENSE.md).

## Citation

If you use dots-cli-es in academic work, please cite it as:

```
@software{boby_dots_cli_es_2026,
  author       = {Boby, Jean-Victor},
  title        = {dots-cli-es},
  year         = {2026},
  publisher    = {GitHub},
  institution  = {{École nationale des chartes}},
  url          = {https://github.com/dots-suite/dots-cli-es},
  note         = {Elasticsearch indexing CLI and search API for TEI resources published with DoTS}
}
```

You can also use the repository metadata from [CITATION.cff](./CITATION.cff).
