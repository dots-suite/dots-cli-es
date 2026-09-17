from thunderdots import ThunderDots

td = ThunderDots(
        endpoint_dts="https://dev.chartes.psl.eu/dots/api/dts",
        collection_params={"collection_id": "ENCPOS_1992"},
        resource_params={
            "fragment_mode": "navigation",
            "metadata_dublincore": None,
            "metadata_extensions": None,
            "add_head_to_content": False,
        },
        fragment_params={"metadata_dublincore": None}
    )

td.fetch()
results = td.results()
notice = td.notices()[0]

elastic_docs = td.to_elastic_documents(
    include_fragments=True
)

elastic_docs[17].pop("text", None)
for fragment in elastic_docs[17].get("fragments", []):
    content = fragment.get("content", "")
    fragment["content"] = " ".join(content.split()[:2])

print('Elasticsearch results:')
print(elastic_docs[0])
print('/n/n elastic_docs type:')
print(type(elastic_docs))
doc = next(
    (d for d in elastic_docs if d["id"] == "ENCPOS_1992_01"),
    None
)

print(doc)

#collections = results.get("collection_results", [])
resources = results.get("resource_results", [])


#print('Collections results:')
#print(collections)
print('Resources results:')
print(resources)
#print([obj for obj in resources if obj.get("id") == "ENCPOS_1972_02"])
#print('Notice results:')
#print(notice)

#print(notice.temporal_index)

# elastic_actions = td.to_elastic_actions(
#      index="thunderdots_test12__allinc"
# )
# for action in elastic_actions:
#     action.get("_source", {}).pop("text", None)
#
#     for fragment in action["_source"].get("fragments", []):
#         content = fragment.get("content", "")
#         fragment["content"] = " ".join(content.split()[:2])
#
# print('elastic_actions:')
# print(elastic_actions[17])


from pprint import pprint

# fragment_index = {
#     f["id"]: f
#     for f in resources[0]["fragments"]
# }
#
# print(fragment_index["a1-s1"])

#
#
# elastic_actions = td.to_elastic_actions(
#     index="thunderdots_test10__allinc"
# )
#
# # print('elastic_actions : ', elastic_actions)
#
# from elasticsearch import Elasticsearch
# from elasticsearch.helpers import bulk, BulkIndexError
# import json
#
# es = Elasticsearch("http://localhost:9200")
#
# response = None
#
# try:
#     response = bulk(es, elastic_actions)
# except BulkIndexError as e:
#     for i, error in enumerate(e.errors, start=1):
#         print(f"\n--- Erreur {i} ---")
#         print(json.dumps(error, indent=2, ensure_ascii=False))
#
# print('response ', response)
#
# # Force refresh of the index to make the documents searchable immediately
# es.indices.refresh(index="thunderdots_test10__allinc")
#
# # Query the index for documents containing "archives médiévales"
#
# response = es.search(
#         index="thunderdots_test10__allinc",
#         query={
#             "match": {
#                 "text": "archives médiévales",
#             }
#         },
#     )
#
# print("\nSearch results :")
#
# for hit in response["hits"]["hits"]:
#     source = hit["_source"]
#     print(
#             f"- {source.get('id')} | "
#             f"{source.get('title')} | "
#             f"score={hit.get('_score')}"
#         )
#
