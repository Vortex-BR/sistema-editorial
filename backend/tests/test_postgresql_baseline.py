from pathlib import Path


ROOT = Path(__file__).parents[2]
PG17_IMAGE = "pgvector/pgvector:0.8.5-pg17"
PG16_IMAGE = "pgvector/pgvector:pg16"


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_new_database_images_use_the_postgresql_17_pgvector_baseline():
    compose = read("docker-compose.yml")
    workflow = read(".github/workflows/docker-build.yml")

    assert PG17_IMAGE in compose
    assert PG17_IMAGE in workflow
    assert PG16_IMAGE not in compose
    assert PG16_IMAGE not in workflow
    assert "postgres17_data:/var/lib/postgresql/data" in compose
    assert "- postgres_data:/var/lib/postgresql/data" not in compose


def test_operator_documentation_records_validated_versions_without_false_pin():
    readme = read("README.md")
    easypanel = read("deploy/easypanel/README.md")
    local_environment = read(".env.example")
    production_environment = read(".env.easypanel.example")

    for document in (readme, easypanel, local_environment, production_environment):
        assert PG17_IMAGE in document
    for version in ("17.10", "0.8.5"):
        assert version in readme
        assert version in easypanel
    assert "fixa a extensão" in readme
    assert "fixa a versão da extensão" in easypanel
    assert "volume PG16" in readme
    assert "volume PG16" in easypanel
