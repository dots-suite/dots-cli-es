from dots_es.api import create_app


def test_errors_carry_the_cors_header():
    client = create_app("local").test_client()

    response = client.get("/api/1.0/search?query=*&scope=unknown")

    assert response.status_code == 400
    assert response.headers["Access-Control-Allow-Origin"] == "*"
