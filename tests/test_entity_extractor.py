from smos.memory.entity_extractor import extract_entities


def test_extracts_function_and_class_from_python_source():
    content = """
class JWTValidator:
    def validate(self, token):
        return True

def helper():
    pass
"""
    entities = extract_entities(content)
    names = {(e.kind, e.name) for e in entities}
    assert ("class", "JWTValidator") in names
    assert ("function", "validate") in names
    assert ("function", "helper") in names


def test_extracts_import_targets():
    content = "import os\nfrom smos.memory.vector_store import VectorStore\n"
    entities = extract_entities(content)
    names = {(e.kind, e.name) for e in entities}
    assert ("import", "os") in names
    assert ("import", "smos") in names


def test_extracts_backtick_references_from_prose():
    content = "The `embed` function is called by `VectorStore.store`."
    entities = extract_entities(content)
    names = {e.name for e in entities if e.kind == "reference"}
    assert "embed" in names
    assert "VectorStore.store" in names


def test_backtick_regex_does_not_capture_trailing_parens():
    """Scope limit, not a bug: `foo()`-style spans (with parens) aren't
    captured — the char class stops at `(`. Entities are bare
    identifiers/dotted paths, matching this repo's own memory-file style."""
    content = "See `embed()` for details."
    entities = extract_entities(content)
    assert all(e.name != "embed()" for e in entities)


def test_non_python_content_yields_no_ast_entities_but_keeps_backticks():
    content = "This is prose with no code. It mentions `SomeEntity` though."
    entities = extract_entities(content)
    assert all(e.kind not in ("function", "class") for e in entities)
    assert any(e.name == "SomeEntity" for e in entities)


def test_deduplicates_repeated_mentions():
    content = "`JWT` appears here. `JWT` appears again. `JWT` a third time."
    entities = extract_entities(content)
    jwt_count = sum(1 for e in entities if e.name == "JWT")
    assert jwt_count == 1


def test_caps_at_fifty_entities():
    content = " ".join(f"`entity_{i}`" for i in range(100))
    entities = extract_entities(content)
    assert len(entities) <= 50
