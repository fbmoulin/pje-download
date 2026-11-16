"""MTOM attachments must arrive byte for byte, and the declared hash is checked.

zeep 4.3.3 `Attachment.content` strips CR/LF from both ends of a part sent with
`Content-Transfer-Encoding: binary` (TJES/CXF does). Files ending in CR/LF lost 1-2
bytes. `install_exact_binary_attachments` returns the exact bytes; `_save_document`
refuses a document whose MD5 differs from the `hash` the MNI declared. Every payload
here is synthetic and built at runtime.
"""

import base64
import hashlib

import pytest
from lxml import etree
from requests_toolbelt.multipart.decoder import MultipartDecoder
from zeep.wsdl.attachments import Attachment, MessagePack
from zeep.wsdl.messages.xop import process_xop
from zeep.xsd import Schema

import mni_client
from mni_client import MNIClient, MNIDocumento, strip_documento_xs_any
from document_validation import decode_document_base64
from tests.pdf_fixtures import minimal_pdf

NS = "urn:test:mni"
BOUNDARY = "uuid:synthetic-boundary"
CONTENT_TYPE = (
    f'multipart/related; boundary="{BOUNDARY}"; type="application/xop+xml"; '
    'start="<root.message@cxf.apache.org>"; start-info="text/xml"'
)

# Synthetic payloads: the edges the zeep strip used to eat.
PAYLOADS = {
    "crlf_end": b"%PDF-1.4 body\r\n%%EOF\r\n",
    "lf_end": b"%PDF-1.4 body\n%%EOF\n",
    "crlf_start": b"\r\nleading CRLF then text",
    "binary_0a_end": b"PK\x03\x04" + bytes(range(16)) + b"\x0a",
    "plain": b"no CR/LF at either end",
}

XSD = f"""<?xml version="1.0"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:tns="{NS}"
           targetNamespace="{NS}">
  <xs:complexType name="tipoDocumento">
    <xs:sequence>
      <xs:element minOccurs="0" name="conteudo" type="xs:base64Binary"/>
      <xs:any namespace="##other" processContents="lax"/>
      <xs:element maxOccurs="unbounded" minOccurs="0" name="documentoVinculado"
                  nillable="true" type="tns:tipoDocumento"/>
    </xs:sequence>
    <xs:attribute name="idDocumento" type="xs:string"/>
    <xs:attribute name="tipoDocumento" type="xs:string"/>
    <xs:attribute name="mimetype" type="xs:string"/>
    <xs:attribute name="hash" type="xs:string"/>
    <xs:attribute name="descricao" type="xs:string"/>
  </xs:complexType>
  <xs:element name="resp">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="documento" maxOccurs="unbounded"
                    type="tns:tipoDocumento"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>
"""


def _md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def _multipart(parts: dict[str, bytes]) -> bytes:
    """A CXF-like MTOM reply: XML root plus one binary part per payload."""
    includes = "".join(
        f'<documento idDocumento="{cid}" tipoDocumento="1" '
        f'mimetype="application/octet-stream" hash="{_md5(data)}">'
        f"<conteudo><xop:Include "
        f'xmlns:xop="http://www.w3.org/2004/08/xop/include" href="cid:{cid}"/>'
        f"</conteudo></documento>"
        for cid, data in parts.items()
    )
    root = f'<tns:resp xmlns:tns="{NS}">{includes}</tns:resp>'.encode()
    out = b"--" + BOUNDARY.encode() + b"\r\n"
    out += b"Content-Type: application/xop+xml; charset=UTF-8\r\n"
    out += b"Content-Transfer-Encoding: binary\r\n"
    out += b"Content-ID: <root.message@cxf.apache.org>\r\n\r\n" + root
    for cid, data in parts.items():
        out += b"\r\n--" + BOUNDARY.encode() + b"\r\n"
        out += b"Content-Type: application/octet-stream\r\n"
        out += b"Content-Transfer-Encoding: binary\r\n"
        out += f"Content-ID: <{cid}>\r\n\r\n".encode() + data
    return out + b"\r\n--" + BOUNDARY.encode() + b"--\r\n"


def _decode_like_zeep(parts: dict[str, bytes]):
    """Mirror zeep's SoapBinding.process_reply: decoder, MessagePack, process_xop."""
    decoder = MultipartDecoder(_multipart(parts), CONTENT_TYPE, "utf-8")
    pack = MessagePack(parts=decoder.parts[1:])
    doc = etree.fromstring(decoder.parts[0].content)
    assert process_xop(doc, pack)
    return doc, pack


def _parsed_documents(parts: dict[str, bytes]):
    doc, _ = _decode_like_zeep(parts)
    xsd = strip_documento_xs_any(XSD.encode()).decode()
    schema = Schema(etree.fromstring(xsd.encode()))
    return schema.get_element(f"{{{NS}}}resp").parse(doc, schema).documento


def _client() -> MNIClient:
    client = MNIClient.__new__(MNIClient)
    client.tribunal = "TJES"
    return client


class TestExactBytesThroughRealZeep:
    @pytest.mark.parametrize("name", sorted(PAYLOADS))
    def test_attachment_content_is_the_exact_part(self, name):
        _, pack = _decode_like_zeep({"c1": PAYLOADS[name]})
        assert pack.get_by_content_id("<c1>").content == PAYLOADS[name]

    def test_every_payload_survives_one_message(self):
        docs = _parsed_documents(PAYLOADS)
        got = {d.idDocumento: d.conteudo for d in docs}
        assert got == PAYLOADS

    def test_base64_branch_is_unchanged(self):
        part = type(
            "P",
            (),
            {
                "encoding": None,
                "headers": {b"Content-Transfer-Encoding": b"base64"},
                "content": base64.b64encode(b"\r\nabc\r\n"),
            },
        )()
        assert Attachment(part).content == b"\r\nabc\r\n"

    def test_other_encodings_are_returned_as_is(self):
        part = type(
            "P",
            (),
            {
                "encoding": None,
                "headers": {b"Content-Transfer-Encoding": b"8bit"},
                "content": b"\r\nabc\r\n",
            },
        )()
        assert Attachment(part).content == b"\r\nabc\r\n"


class TestParsedDocuments:
    @pytest.mark.parametrize("name", sorted(PAYLOADS))
    def test_decoded_bytes_and_md5_match_the_original(self, name):
        (raw,) = _parsed_documents({"c1": PAYLOADS[name]})
        parsed = _client()._parse_documento(raw)
        assert parsed.hash_declarado == _md5(PAYLOADS[name])
        decoded = decode_document_base64(parsed.conteudo_base64)
        assert decoded == PAYLOADS[name]
        assert _md5(decoded) == parsed.hash_declarado

    def test_linked_documents_carry_their_hash(self):
        child = b"child\r\n"
        parent_xml = (
            f'<tns:resp xmlns:tns="{NS}"><documento idDocumento="1" '
            f'tipoDocumento="1" hash="{_md5(b"p")}">'
            f'<documentoVinculado idDocumento="2" tipoDocumento="1" '
            f'hash="{_md5(child)}"/>'
            f'<documentoVinculado idDocumento="3" tipoDocumento="1"/>'
            f"</documento></tns:resp>"
        )
        xsd = strip_documento_xs_any(XSD.encode()).decode()
        schema = Schema(etree.fromstring(xsd.encode()))
        resp = schema.get_element(f"{{{NS}}}resp").parse(
            etree.fromstring(parent_xml), schema
        )
        parsed = _client()._parse_documento(resp.documento[0])
        assert parsed.hash_declarado == _md5(b"p")
        assert [v.hash_declarado for v in parsed.vinculados] == [_md5(child), None]

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_absent_or_blank_hash_is_none(self, value):
        from types import SimpleNamespace

        doc = SimpleNamespace(idDocumento="1", hash=value)
        assert _client()._parse_documento(doc).hash_declarado is None

    def test_non_string_hash_is_none(self):
        from unittest.mock import MagicMock

        doc = MagicMock()
        doc.idDocumento = "1"
        doc.documentoVinculado = []
        assert _client()._parse_documento(doc).hash_declarado is None


def _doc(content: bytes, declared: str | None, mimetype="application/pdf"):
    return MNIDocumento(
        id="7",
        nome="Doc",
        tipo="t",
        mimetype=mimetype,
        conteudo_base64=base64.b64encode(content).decode("ascii"),
        tamanho_bytes=len(content),
        hash_declarado=declared,
    )


class TestSaveDocumentHash:
    def test_saves_when_hash_matches(self, tmp_path):
        data = minimal_pdf(b"exact") + b"\r\n"
        outcome: dict = {}
        saved = _client()._save_document(
            _doc(data, _md5(data).upper()), tmp_path, set(), "p", outcome
        )
        assert saved is not None and outcome["status"] == "saved"
        assert (tmp_path / saved["nome"]).read_bytes() == data

    def test_refuses_and_does_not_write_when_hash_differs(self, tmp_path):
        data = minimal_pdf(b"exact") + b"\r\n"
        truncated = data[:-2]
        seen: set[str] = set()
        outcome: dict = {}
        saved = _client()._save_document(
            _doc(truncated, _md5(data)), tmp_path, seen, "p", outcome
        )
        assert saved is None
        assert outcome["status"] == "failed"
        assert list(tmp_path.iterdir()) == []
        assert seen == set()

    def test_mismatch_is_audited_as_error_and_logged_without_names(
        self, tmp_path, monkeypatch
    ):
        entries = []
        monkeypatch.setattr("audit.log_access", entries.append)
        events = []
        monkeypatch.setattr(
            mni_client.log,
            "warning",
            lambda event, **kw: events.append((event, kw)),
        )
        data = minimal_pdf(b"exact")
        _client()._save_document(_doc(data, "0" * 32), tmp_path, set(), "p")
        assert [e.status for e in entries] == ["error"]
        assert entries[0].erro == "hash_mismatch"
        ((name, kw),) = [e for e in events if e[0] == "mni.download.hash_mismatch"]
        assert set(kw) == {"doc_id", "tamanho"}

    @pytest.mark.parametrize("declared", [None, "", "abc", "sha256:" + "a" * 64])
    def test_absent_or_other_format_still_saves(self, tmp_path, declared):
        data = minimal_pdf(b"x")
        outcome: dict = {}
        saved = _client()._save_document(
            _doc(data, declared), tmp_path, set(), "p", outcome
        )
        assert saved is not None and outcome["status"] == "saved"

    def test_checksum_duplicate_is_still_not_a_failure(self, tmp_path):
        data = minimal_pdf(b"dup")
        seen = {hashlib.sha256(data).hexdigest()}
        outcome: dict = {}
        _client()._save_document(_doc(data, _md5(data)), tmp_path, seen, "p", outcome)
        assert outcome["status"] == "duplicate"


class TestInstaller:
    def test_installed_on_import(self):
        func = Attachment.__dict__["content"].func
        assert getattr(func, "_exact_binary", False) is True

    def test_install_twice_keeps_the_same_descriptor(self):
        before = Attachment.__dict__["content"]
        mni_client.install_exact_binary_attachments()
        mni_client.install_exact_binary_attachments()
        assert Attachment.__dict__["content"] is before
