from pydantic import BaseModel, Field, HttpUrl, field_validator


class SourceConfig(BaseModel):
    DTS_URL: HttpUrl
    TARGET_COLLECTION: str = ""
    CUSTOM_SETTINGS_PATH: str = ""
    ADDITIONAL_EXCLUDED_COLLECTIONS: list[str] = Field(default_factory=list)

    @field_validator("ADDITIONAL_EXCLUDED_COLLECTIONS")
    @classmethod
    def lower_excluded(cls, v: list[str]) -> list[str]:
        return [c.lower() for c in v]


class SearchConfig(BaseModel):
    ELASTICSEARCH_URL: HttpUrl
    DOCUMENT_INDEX: str = Field(min_length=1)
    COLLECTION_INDEX: str = Field(min_length=1)
    SEARCH_RESULT_PER_PAGE: int = Field(default=200, ge=25)


class IndexingConfig(BaseModel):
    MAX_CONCURRENT_REQUESTS: int = Field(default=5, ge=1)
    RESOURCE_WORKERS: int = Field(default=5, ge=1)
