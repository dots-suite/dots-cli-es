from dots_es.cli import bulk_chunks


def test_bulk_chunks_bound_the_number_of_documents():
    chunks = list(bulk_chunks([f"{i}\n" for i in range(2500)], max_docs=1000))

    assert [len(c) for c in chunks] == [1000, 1000, 500]


def test_bulk_chunks_bound_the_request_size():
    # Ducange: 90,388 passages, 335 MB in one request, refused with HTTP 413
    lines = ["x" * 99 + "\n"] * 25

    chunks = list(bulk_chunks(lines, max_bytes=1000))

    assert all(sum(map(len, c)) <= 1000 for c in chunks)
    assert sum(len(c) for c in chunks) == 25


def test_bulk_chunks_keep_an_oversized_line_alone():
    chunks = list(bulk_chunks(["a\n", "b" * 50 + "\n", "c\n"], max_bytes=10))

    assert chunks == [["a\n"], ["b" * 50 + "\n"], ["c\n"]]
