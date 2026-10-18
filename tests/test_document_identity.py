"""One physical blob can represent several distinct PJe document IDs."""

from copy import deepcopy
import base64
from unittest.mock import patch

import pytest

from file_utils import merge_file_lists, total_bytes


def test_checksum_merge_preserves_each_logical_document_without_double_bytes():
    first = {
        "nome": "a.pdf",
        "checksum": "shared",
        "tamanhoBytes": 100,
        "documentos": [{"id": "first", "parentId": None}],
    }
    second = {
        "nome": "b.pdf",
        "checksum": "shared",
        "tamanhoBytes": 100,
        "documentos": [{"id": "second", "parentId": "parent"}],
    }
    originals = deepcopy([first, second])
    merged = merge_file_lists([first], [second])
    assert len(merged) == 1
    assert total_bytes(merged) == 100
    assert merged[0]["documentos"] == [
        {"id": "first", "parentId": None},
        {"id": "second", "parentId": "parent"},
    ]
    assert [first, second] == originals


def test_repeated_same_document_reference_is_not_duplicated():
    reference = {"id": "same", "parentId": None}
    first = {"checksum": "shared", "documentos": [reference]}
    second = {"checksum": "shared", "documentos": [dict(reference)]}
    assert merge_file_lists([first], [second])[0]["documentos"] == [reference]


def test_legacy_first_blob_keeps_new_reference_without_mutating_input():
    legacy = {"checksum": "shared", "nome": "legacy.pdf"}
    new = {"checksum": "shared", "documentos": [{"id": "new"}]}
    assert merge_file_lists([legacy], [new])[0]["documentos"] == [{"id": "new"}]
    assert "documentos" not in legacy


def test_merged_document_metadata_does_not_alias_original_references():
    first = {"checksum": "shared", "documentos": [{"id": "first"}]}
    result = merge_file_lists([first])
    result[0]["documentos"][0]["id"] = "changed"
    assert first["documentos"][0]["id"] == "first"


@pytest.mark.asyncio
async def test_mni_duplicate_documents_keep_both_ids_on_one_blob(tmp_path):
    from mni_client import MNIClient, MNIDocumento, MNIProcesso

    # Format-only fixture, not a claim of full PDF parsing.
    content = b"%PDF-1.4\nsynthetic fixture\n%%EOF\n"
    encoded = base64.b64encode(content).decode("ascii")
    docs = [
        MNIDocumento(
            id=identifier, nome=identifier, tipo="doc", conteudo_base64=encoded
        )
        for identifier in ("first", "second")
    ]
    client = MNIClient(username="synthetic", password="synthetic", tribunal="TJES")
    with patch("audit.log_document_saved"):
        files = await client.download_documentos(
            MNIProcesso(numero="synthetic", documentos=docs), tmp_path
        )
    assert len(files) == 1
    assert total_bytes(files) == len(content)
    assert files.failed_ids == []
    assert [ref["id"] for ref in files[0]["documentos"]] == ["first", "second"]
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.asyncio
async def test_mni_phase_two_keeps_all_logical_ids_for_repeated_content(tmp_path):
    from mni_client import MNIClient, MNIDocumento, MNIProcesso, MNIResult

    content = b"%PDF-1.4\nsynthetic phase two\n%%EOF\n"
    metadata = [MNIDocumento(id=i, nome=i, tipo="doc") for i in ("first", "second")]
    fetched = [
        MNIDocumento(
            id=i,
            nome=i,
            tipo="doc",
            conteudo_base64=base64.b64encode(content).decode("ascii"),
        )
        for i in ("first", "second")
    ]
    client = MNIClient(username="synthetic", password="synthetic", tribunal="TJES")
    answer = MNIResult(
        success=True, processo=MNIProcesso(numero="synthetic", documentos=fetched)
    )
    with (
        patch.object(client, "consultar_processo", return_value=answer),
        patch("audit.log_document_saved"),
    ):
        files = await client.download_documentos(
            MNIProcesso(numero="synthetic", documentos=metadata), tmp_path
        )
    assert len(files) == 1
    assert files.failed_ids == []
    assert [ref["id"] for ref in files[0]["documentos"]] == ["first", "second"]
