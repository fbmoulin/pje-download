"""The first attachment of every document must not vanish into zeep's `xs:any`.

TJES's WSDL declares `tipoDocumento` as conteudo, assinatura*, outroParametro*,
`<any ##other lax/>`, documentoVinculado*. With that wildcard zeep binds the first
`documentoVinculado` of each document to `_value_1` (a raw lxml element), so the
parser saw one attachment fewer per document. `strip_documento_xs_any` removes the
wildcard from `tipoDocumento` only. The schema below mirrors the real one.
"""

from lxml import etree
from zeep.xsd import Schema

from mni_client import strip_documento_xs_any

NS = "urn:test:mni"
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
  </xs:complexType>
  <xs:complexType name="outro">
    <xs:sequence>
      <xs:element name="a" type="xs:string"/>
      <xs:any namespace="##other" processContents="lax"/>
    </xs:sequence>
  </xs:complexType>
  <xs:element name="resp">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="documento" type="tns:tipoDocumento"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>
"""
XML = f"""<tns:resp xmlns:tns="{NS}">
  <documento idDocumento="1">
    <documentoVinculado idDocumento="2"/>
    <documentoVinculado idDocumento="3"/>
  </documento>
</tns:resp>"""


def _attachment_ids(xsd: str) -> list[str]:
    schema = Schema(etree.fromstring(xsd.encode()))
    resp = schema.get_element(f"{{{NS}}}resp").parse(etree.fromstring(XML), schema)
    return [v.idDocumento for v in (resp.documento.documentoVinculado or [])]


def test_without_the_fix_the_first_attachment_is_swallowed():
    assert _attachment_ids(XSD) == ["3"]


def test_with_the_fix_every_attachment_is_bound():
    fixed = strip_documento_xs_any(XSD.encode()).decode()
    assert _attachment_ids(fixed) == ["2", "3"]


def test_only_tipo_documento_loses_its_wildcard():
    fixed = strip_documento_xs_any(XSD.encode()).decode()
    assert fixed.count("<xs:any") == 1
    assert fixed.index("<xs:any") > fixed.index('name="outro"')


def test_wsdl_without_the_wildcard_is_unchanged():
    clean = strip_documento_xs_any(XSD.encode())
    assert strip_documento_xs_any(clean) == clean
