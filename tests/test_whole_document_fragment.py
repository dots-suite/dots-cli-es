from dots_es.cli import whole_document_fields


def test_whole_document_fragment_takes_its_resource_fields():
    resource = {
        "title": "Marguilliers laïques des églises de Paris",
        "dublincore": {"creator": "Dominique-Claude Boisserand", "created": "1849"},
        "extensions": {"dateCreated": "1849"},
    }
    temporal = {"temporal.dublincore.created_start": 1849, "temporal.dublincore.created_end": 1849}

    fields = whole_document_fields(resource, temporal)

    assert fields["citeType"] == "text"
    assert fields["level"] == 1
    assert fields["title"] == "Marguilliers laïques des églises de Paris"
    assert fields["fragment_metadata"]["dublincore"]["creator"] == "Dominique-Claude Boisserand"
    assert fields["fragment_metadata"]["extensions"]["dateCreated"] == "1849"
    assert fields["fragment_temporal"] == temporal


def test_whole_document_fragment_without_metadata():
    fields = whole_document_fields({}, {})

    assert fields["title"] is None
    assert fields["fragment_metadata"]["dublincore"] == {}
    assert fields["fragment_metadata"]["extensions"] == {}
