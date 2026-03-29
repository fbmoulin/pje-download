"""
MNI Client — Cliente SOAP para o Modelo Nacional de Interoperabilidade do PJe.
==============================================================================

Permite consultar processos e baixar documentos via WSDL,
sem necessidade de browser automation.

Endpoints conhecidos:
  - TJES: https://sistemas.tjes.jus.br/pje/intercomunicacao?wsdl
  - TJBA: https://pje.tjba.jus.br/pje/intercomunicacao?wsdl

Operações MNI:
  - consultarProcesso          → dados do processo (partes, movimentações, documentos)
  - consultarAvisosPendentes   → comunicações pendentes
  - consultarTeorComunicacao   → conteúdo das comunicações
  - entregarManifestacaoProcessual → protocolar documentos (não usado aqui)

Referências:
  - https://docs.pje.jus.br/servicos-auxiliares/servico-mni-client/
  - https://docs.pje.jus.br/manuais-basicos/padroes-de-api-do-pje/
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

log: structlog.BoundLogger = structlog.get_logger("kratos.mni-client")

# ─────────────────────────────────────────────
# CONFIGURAÇÃO DE TRIBUNAIS
# ─────────────────────────────────────────────

TRIBUNAL_ENDPOINTS: dict[str, str] = {
    "TJES": "https://pje.tjes.jus.br/pje/intercomunicacao?wsdl",
    "TJES_2G": "https://pje.tjes.jus.br/pje2g/intercomunicacao?wsdl",
    "TJBA": "https://pje.tjba.jus.br/pje/intercomunicacao?wsdl",
    "TJBA_2G": "https://pje.tjba.jus.br/pje2g/intercomunicacao?wsdl",
    "TJCE": "https://pje.tjce.jus.br/pje1grau/intercomunicacao?wsdl",
    "TRT17": "https://pje.trt17.jus.br/pje/intercomunicacao?wsdl",
}

from config import MNI_USERNAME, MNI_PASSWORD, MNI_TRIBUNAL, MNI_TIMEOUT


# ─────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────


@dataclass
class MNIDocumento:
    """Documento retornado pela consulta MNI."""

    id: str
    nome: str
    tipo: str
    mimetype: str = "application/pdf"
    conteudo_base64: str | None = None
    tamanho_bytes: int = 0
    vinculados: list["MNIDocumento"] = field(default_factory=list)
    id_pai: str | None = None  # Se é anexo, referência ao doc pai

    @property
    def has_content(self) -> bool:
        return self.conteudo_base64 is not None and len(self.conteudo_base64) > 0

    @property
    def is_anexo(self) -> bool:
        return self.id_pai is not None


@dataclass
class MNIProcesso:
    """Processo retornado pela consulta MNI."""

    numero: str
    classe: str = ""
    assuntos: list[str] = field(default_factory=list)
    polo_ativo: list[str] = field(default_factory=list)
    polo_passivo: list[str] = field(default_factory=list)
    documentos: list[MNIDocumento] = field(default_factory=list)
    movimentacoes: list[dict] = field(default_factory=list)
    raw_xml: str | None = None


@dataclass
class MNIResult:
    """Resultado genérico de operação MNI."""

    success: bool
    processo: MNIProcesso | None = None
    error: str | None = None
    raw_response: Any = None


# ─────────────────────────────────────────────
# CLIENTE MNI
# ─────────────────────────────────────────────


class MNIClient:
    """
    Cliente SOAP para o MNI do PJe.

    Usa zeep para comunicação SOAP/WSDL.
    Suporta múltiplos tribunais via TRIBUNAL_ENDPOINTS.

    Uso:
        client = MNIClient(tribunal="TJES", username="12345678900", password="senha")
        result = await client.consultar_processo("0001234-56.2024.8.08.0020")
        if result.success:
            for doc in result.processo.documentos:
                await client.download_documento(doc, Path("/data/downloads"))
    """

    def __init__(
        self,
        tribunal: str = MNI_TRIBUNAL,
        username: str = MNI_USERNAME,
        password: str = MNI_PASSWORD,
        timeout: int = MNI_TIMEOUT,
    ) -> None:
        self.tribunal = tribunal.upper()
        self.username = username
        self.password = password
        self.timeout = timeout
        self._client: Any | None = None

        endpoint = TRIBUNAL_ENDPOINTS.get(self.tribunal)
        if not endpoint:
            available = ", ".join(sorted(TRIBUNAL_ENDPOINTS.keys()))
            raise ValueError(
                f"Tribunal '{self.tribunal}' não suportado. Disponíveis: {available}"
            )
        self.wsdl_url = endpoint

    def _get_client(self):
        """Inicializa o cliente zeep (lazy)."""
        if self._client is None:
            from zeep import Client
            from zeep.transports import Transport
            from requests import Session

            session = Session()
            session.timeout = self.timeout
            transport = Transport(session=session, timeout=self.timeout)

            log.info(
                "mni.client.init",
                tribunal=self.tribunal,
                wsdl=self.wsdl_url,
            )

            self._client = Client(
                wsdl=self.wsdl_url,
                transport=transport,
            )
        return self._client

    # ──────────────────────
    # CONSULTAR PROCESSO
    # ──────────────────────

    async def consultar_processo(
        self,
        numero_processo: str,
        incluir_documentos: bool = True,
        incluir_cabecalho: bool = True,
        incluir_movimentacoes: bool = False,
        documento_ids: list[str] | None = None,
    ) -> MNIResult:
        """
        Consulta um processo via MNI SOAP.

        O MNI retorna metadados dos documentos por padrão. Para obter o
        conteúdo binário, passe os IDs específicos em documento_ids.

        Args:
            numero_processo: Número CNJ do processo (ex: "0001234-56.2024.8.08.0020")
            incluir_documentos: Se True, retorna metadados/conteúdo dos documentos
            incluir_cabecalho: Se True, retorna dados das partes e assuntos
            incluir_movimentacoes: Se True, retorna movimentações processuais
            documento_ids: Lista de IDs para obter conteúdo binário (fase 2)

        Returns:
            MNIResult com dados do processo ou mensagem de erro
        """
        import asyncio

        try:
            log.info(
                "mni.consultar_processo.start",
                processo=numero_processo,
                tribunal=self.tribunal,
            )

            client = self._get_client()

            # Executa chamada SOAP em thread separada (zeep é síncrono)
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self._call_consultar_processo,
                    client,
                    numero_processo,
                    incluir_documentos,
                    incluir_cabecalho,
                    incluir_movimentacoes,
                    documento_ids,
                ),
                timeout=self.timeout,
            )

            # Verificar resposta MNI (sucesso/mensagem/processo)
            sucesso = getattr(result, "sucesso", True)
            mensagem = getattr(result, "mensagem", "")

            if not sucesso:
                log.warning(
                    "mni.consultar_processo.mni_error",
                    processo=numero_processo,
                    mensagem=mensagem,
                )
                return MNIResult(success=False, error=mensagem, raw_response=result)

            processo = self._parse_processo(result, numero_processo)

            log.info(
                "mni.consultar_processo.success",
                processo=numero_processo,
                documentos=len(processo.documentos),
            )

            return MNIResult(success=True, processo=processo, raw_response=result)

        except asyncio.TimeoutError:
            log.error(
                "mni.consultar_processo.timeout",
                processo=numero_processo,
                timeout_s=self.timeout,
            )
            return MNIResult(
                success=False, error=f"SOAP timeout ({self.timeout}s)"
            )

        except Exception as exc:
            error_msg = str(exc)

            # Detectar erros comuns do MNI
            if "Processo não encontrado" in error_msg:
                log.warning(
                    "mni.consultar_processo.not_found", processo=numero_processo
                )
            elif "Acesso negado" in error_msg or "Unauthorized" in error_msg:
                log.error(
                    "mni.consultar_processo.auth_failed", processo=numero_processo
                )
            else:
                log.error(
                    "mni.consultar_processo.failed",
                    processo=numero_processo,
                    error=error_msg,
                )

            return MNIResult(success=False, error=error_msg)

    def _call_consultar_processo(
        self,
        client,
        numero_processo: str,
        incluir_documentos: bool,
        incluir_cabecalho: bool,
        incluir_movimentacoes: bool,
        documento_ids: list[str] | None = None,
    ):
        """Chamada síncrona ao SOAP — executada via asyncio.to_thread.

        IMPORTANTE: O MNI do TJES (e provavelmente outros) só retorna o
        conteúdo binário dos documentos quando IDs específicos são passados
        no parâmetro `documento`. Sem isso, retorna apenas metadados.

        Estratégia de 2 fases:
          Fase 1: incluirDocumentos=True, sem documento_ids → lista metadados
          Fase 2: incluirDocumentos=True, com documento_ids → baixa conteúdo
        """
        params = {
            "idConsultante": self.username,
            "senhaConsultante": self.password,
            "numeroProcesso": numero_processo,
            "movimentos": incluir_movimentacoes,
            "incluirCabecalho": incluir_cabecalho,
            "incluirDocumentos": incluir_documentos,
        }

        if documento_ids:
            params["documento"] = documento_ids

        return client.service.consultarProcesso(**params)

    # ──────────────────────
    # PARSER DE RESPOSTA
    # ──────────────────────

    def _parse_processo(self, raw_response, numero_processo: str) -> MNIProcesso:
        """
        Parseia resposta SOAP do MNI para MNIProcesso.

        Estrutura real do TJES (MNI 2.2.2):
          resultado.processo.dadosBasicos.classeProcessual → int (código)
          resultado.processo.dadosBasicos.polo[] → sem attr 'polo', indexado por posição
          resultado.processo.dadosBasicos.polo[].parte[].pessoa.nome → str
          resultado.processo.dadosBasicos.assunto[].codigoNacional → int
          resultado.processo.documento[].conteudo → bytes | None
        """
        processo = MNIProcesso(numero=numero_processo)

        try:
            proc_data = getattr(raw_response, "processo", raw_response)

            # Dados do cabeçalho
            dados_basicos = getattr(proc_data, "dadosBasicos", None)
            if dados_basicos:
                # classeProcessual pode ser int (código) ou objeto com descricao
                classe_raw = getattr(dados_basicos, "classeProcessual", "")
                if hasattr(classe_raw, "descricao"):
                    processo.classe = str(classe_raw.descricao)
                else:
                    processo.classe = str(classe_raw)

                # Assuntos — no TJES vem como dict com codigoNacional
                assuntos = getattr(dados_basicos, "assunto", [])
                if not isinstance(assuntos, list):
                    assuntos = [assuntos]
                for assunto in assuntos:
                    if hasattr(assunto, "assuntoLocal") and assunto.assuntoLocal:
                        desc = getattr(assunto.assuntoLocal, "descricao", "")
                    elif hasattr(assunto, "codigoNacional"):
                        desc = f"cod:{assunto.codigoNacional}"
                        if getattr(assunto, "principal", False):
                            desc += " (principal)"
                    else:
                        desc = str(assunto)
                    if desc:
                        processo.assuntos.append(desc)

                # Polos (partes) — TJES indexa AT=0, PA=1, FL=2
                polos = getattr(dados_basicos, "polo", [])
                if not isinstance(polos, list):
                    polos = [polos]
                for i, polo in enumerate(polos):
                    # Tentar attr 'polo' (string AT/PA) — se não existir, usar índice
                    tipo_polo = getattr(polo, "polo", None)
                    if tipo_polo is None:
                        tipo_polo = ["AT", "PA", "FL"][i] if i < 3 else f"POLO_{i}"

                    partes = getattr(polo, "parte", [])
                    if not isinstance(partes, list):
                        partes = [partes]
                    for parte in partes:
                        pessoa = getattr(parte, "pessoa", None)
                        nome = getattr(pessoa, "nome", "") if pessoa else ""
                        if nome:
                            if tipo_polo == "AT":
                                processo.polo_ativo.append(nome)
                            elif tipo_polo == "PA":
                                processo.polo_passivo.append(nome)

            # Documentos
            documentos = getattr(proc_data, "documento", [])
            if not isinstance(documentos, list):
                documentos = [documentos]
            for doc in documentos:
                doc_id = str(getattr(doc, "idDocumento", getattr(doc, "id", "")))
                nome = getattr(doc, "descricao", getattr(doc, "nome", f"doc_{doc_id}"))
                # tipoDocumento no TJES é um código numérico, não objeto
                tipo_raw = getattr(doc, "tipoDocumento", "documento")
                if hasattr(tipo_raw, "descricao"):
                    tipo = str(tipo_raw.descricao)
                else:
                    tipo = str(tipo_raw)
                mimetype = getattr(doc, "mimetype", "application/pdf")

                # Conteúdo binário (presente quando documento_ids é passado)
                # No TJES, vem como bytes direto (não base64)
                conteudo_raw = getattr(doc, "conteudo", None)
                conteudo_b64 = None
                tamanho = 0
                if conteudo_raw is not None:
                    if isinstance(conteudo_raw, bytes) and len(conteudo_raw) > 0:
                        conteudo_b64 = base64.b64encode(conteudo_raw).decode("ascii")
                        tamanho = len(conteudo_raw)
                    elif isinstance(conteudo_raw, str) and len(conteudo_raw) > 0:
                        conteudo_b64 = conteudo_raw
                        tamanho = len(base64.b64decode(conteudo_raw))

                doc_obj = MNIDocumento(
                    id=doc_id,
                    nome=nome,
                    tipo=tipo,
                    mimetype=mimetype,
                    conteudo_base64=conteudo_b64,
                    tamanho_bytes=tamanho,
                )

                # Documentos vinculados (anexos: procuração, docs pessoais, etc.)
                vinculados_raw = getattr(doc, "documentoVinculado", [])
                if not isinstance(vinculados_raw, list):
                    vinculados_raw = [vinculados_raw]
                for vdoc in vinculados_raw:
                    vid = str(getattr(vdoc, "idDocumento", ""))
                    vnome = getattr(vdoc, "descricao", f"anexo_{vid}")
                    vtipo_raw = getattr(vdoc, "tipoDocumento", "anexo")
                    vtipo = (
                        str(vtipo_raw.descricao)
                        if hasattr(vtipo_raw, "descricao")
                        else str(vtipo_raw)
                    )
                    vmime = getattr(vdoc, "mimetype", "application/pdf")
                    vconteudo_raw = getattr(vdoc, "conteudo", None)
                    vb64 = None
                    vtam = 0
                    if vconteudo_raw is not None:
                        if isinstance(vconteudo_raw, bytes) and len(vconteudo_raw) > 0:
                            vb64 = base64.b64encode(vconteudo_raw).decode("ascii")
                            vtam = len(vconteudo_raw)
                        elif isinstance(vconteudo_raw, str) and len(vconteudo_raw) > 0:
                            vb64 = vconteudo_raw
                            vtam = len(base64.b64decode(vconteudo_raw))
                    doc_obj.vinculados.append(
                        MNIDocumento(
                            id=vid,
                            nome=vnome,
                            tipo=vtipo,
                            mimetype=vmime,
                            conteudo_base64=vb64,
                            tamanho_bytes=vtam,
                            id_pai=doc_id,
                        )
                    )

                processo.documentos.append(doc_obj)

            # Movimentações
            movimentos = getattr(proc_data, "movimento", [])
            if not isinstance(movimentos, list):
                movimentos = [movimentos]
            for mov in movimentos:
                processo.movimentacoes.append(
                    {
                        "data": str(getattr(mov, "dataHora", "")),
                        "descricao": getattr(mov, "descricao", ""),
                        "complemento": getattr(mov, "complemento", ""),
                    }
                )

        except Exception as exc:
            log.warning(
                "mni.parse.partial_failure",
                processo=numero_processo,
                error=str(exc),
            )

        return processo

    # ──────────────────────
    # DOWNLOAD DE DOCUMENTOS
    # ──────────────────────

    async def download_documentos(
        self,
        processo: MNIProcesso,
        output_dir: Path,
        tipos_filtro: list[str] | None = None,
        batch_size: int = 5,
        incluir_anexos: bool = True,
    ) -> list[dict]:
        """
        Salva documentos do processo em disco usando estratégia de 2 fases.

        Fase 1 (já feita): consultar_processo retornou metadados dos docs.
        Fase 2 (este método): para cada doc sem conteúdo, faz nova consulta
        passando o ID específico no parâmetro 'documento' para obter o binário.

        Args:
            processo: MNIProcesso com documentos (metadados)
            output_dir: Diretório de destino
            tipos_filtro: Se fornecido, filtra por tipo de documento
            batch_size: Quantos docs baixar por chamada SOAP
            incluir_anexos: Se True (padrão), baixa também documentos vinculados
                           (procuração, docs pessoais, atos constitutivos, etc.)

        Returns:
            Lista de dicts com informações dos arquivos salvos
        """
        import asyncio

        output_dir.mkdir(parents=True, exist_ok=True)
        saved_files: list[dict] = []

        # Montar lista de docs principais (MNI só retorna conteúdo destes).
        # Documentos vinculados (anexos) NÃO são acessíveis via parâmetro
        # 'documento' do MNI — ficam registrados para download via Playwright.
        all_docs: list[MNIDocumento] = []
        vinculados_pendentes: list[MNIDocumento] = []

        for doc in processo.documentos:
            if tipos_filtro and doc.tipo.lower() not in [
                t.lower() for t in tipos_filtro
            ]:
                continue
            all_docs.append(doc)
            if incluir_anexos and doc.vinculados:
                vinculados_pendentes.extend(doc.vinculados)

        if vinculados_pendentes:
            log.info(
                "mni.download.vinculados_skipped",
                count=len(vinculados_pendentes),
                note="MNI não retorna conteúdo de documentos vinculados; usar Playwright",
            )

        # Separar docs que já têm conteúdo dos que precisam de fase 2
        docs_with_content: list[MNIDocumento] = []
        docs_need_fetch: list[MNIDocumento] = []

        for doc in all_docs:
            if doc.has_content:
                docs_with_content.append(doc)
            else:
                docs_need_fetch.append(doc)

        total_vinculados = sum(len(d.vinculados) for d in processo.documentos)
        log.info(
            "mni.download.plan",
            total_principais=len(processo.documentos),
            total_vinculados=total_vinculados,
            total_download=len(all_docs),
            with_content=len(docs_with_content),
            need_fetch=len(docs_need_fetch),
            incluir_anexos=incluir_anexos,
        )

        # ── Salvar docs que já têm conteúdo (raro na fase 1, mas possível) ──
        for doc in docs_with_content:
            saved = self._save_document(doc, output_dir)
            if saved:
                saved_files.append(saved)

        # ── Fase 2: buscar conteúdo em batches via SOAP ──
        if docs_need_fetch:
            log.info(
                "mni.download.phase2_start",
                docs_to_fetch=len(docs_need_fetch),
                batch_size=batch_size,
            )

            # Criar batches de IDs
            batches = [
                docs_need_fetch[i : i + batch_size]
                for i in range(0, len(docs_need_fetch), batch_size)
            ]

            for batch_idx, batch in enumerate(batches):
                batch_ids = [doc.id for doc in batch]
                log.info(
                    "mni.download.batch",
                    batch=batch_idx + 1,
                    total_batches=len(batches),
                    doc_ids=batch_ids,
                )

                try:
                    # Chamar consultarProcesso com IDs específicos
                    result = await self.consultar_processo(
                        processo.numero,
                        incluir_documentos=True,
                        incluir_cabecalho=True,
                        documento_ids=batch_ids,
                    )

                    if not result.success:
                        log.warning(
                            "mni.download.batch_failed",
                            batch=batch_idx + 1,
                            error=result.error,
                        )
                        continue

                    # Mapear docs retornados por ID para salvar
                    fetched_docs = {doc.id: doc for doc in result.processo.documentos}

                    for doc_id in batch_ids:
                        fetched = fetched_docs.get(doc_id)
                        if fetched and fetched.has_content:
                            saved = self._save_document(fetched, output_dir)
                            if saved:
                                saved_files.append(saved)
                        else:
                            log.warning(
                                "mni.download.doc_no_content_after_fetch",
                                doc_id=doc_id,
                            )

                    # Pausa entre batches para não sobrecarregar o servidor
                    if batch_idx < len(batches) - 1:
                        await asyncio.sleep(1.0)

                except Exception as exc:
                    log.error(
                        "mni.download.batch_error",
                        batch=batch_idx + 1,
                        error=str(exc),
                    )
                    continue

        log.info(
            "mni.download.complete",
            processo=processo.numero,
            total_saved=len(saved_files),
            total_download=len(all_docs),
        )
        return saved_files

    _seen_checksums: set[str] = set()

    def _save_document(self, doc: MNIDocumento, output_dir: Path) -> dict | None:
        """Salva um documento com conteúdo em disco. Skips duplicates by checksum."""
        try:
            ext = _mimetype_to_ext(doc.mimetype)
            safe_name = _sanitize_filename(doc.nome)
            filename = f"{safe_name}_{doc.id}{ext}"
            dest = output_dir / filename

            content_bytes = base64.b64decode(doc.conteudo_base64)
            checksum = hashlib.sha256(content_bytes).hexdigest()

            if checksum in self._seen_checksums:
                log.info(
                    "mni.download.duplicate_skipped",
                    doc_id=doc.id,
                    checksum=checksum[:12],
                )
                return None
            self._seen_checksums.add(checksum)

            dest.write_bytes(content_bytes)

            log.info(
                "mni.download.saved",
                filename=filename,
                size=len(content_bytes),
                doc_id=doc.id,
            )

            return {
                "nome": filename,
                "tipo": doc.tipo,
                "tamanhoBytes": len(content_bytes),
                "localPath": str(dest),
                "checksum": checksum,
                "fonte": "mni_soap",
            }
        except Exception as exc:
            log.warning(
                "mni.download.save_failed",
                doc_id=doc.id,
                nome=doc.nome,
                error=str(exc),
            )
            return None

    # ──────────────────────
    # VERIFICAÇÃO DE SAÚDE
    # ──────────────────────

    async def health_check(self) -> dict:
        """
        Verifica se o endpoint MNI do tribunal está acessível.
        Retorna dict com status e detalhes.
        """
        import asyncio
        import time

        try:
            start = time.monotonic()
            client = await asyncio.to_thread(self._get_client)
            elapsed_ms = (time.monotonic() - start) * 1000

            # Verificar se o serviço tem as operações esperadas
            service_ops = []
            for service in client.wsdl.services.values():
                for port in service.ports.values():
                    for op_name in port.binding._operations:
                        service_ops.append(op_name)

            return {
                "status": "healthy",
                "tribunal": self.tribunal,
                "wsdl": self.wsdl_url,
                "operations": service_ops,
                "latency_ms": round(elapsed_ms, 1),
            }
        except Exception as exc:
            return {
                "status": "unhealthy",
                "tribunal": self.tribunal,
                "wsdl": self.wsdl_url,
                "error": str(exc),
            }


# ─────────────────────────────────────────────
# UTILITÁRIOS
# ─────────────────────────────────────────────


def _mimetype_to_ext(mimetype: str) -> str:
    """Converte mimetype para extensão de arquivo."""
    mapping = {
        "application/pdf": ".pdf",
        "text/html": ".html",
        "text/plain": ".txt",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    }
    return mapping.get(mimetype, ".bin")


def _sanitize_filename(name: str) -> str:
    """Remove caracteres inválidos de nomes de arquivo."""
    import re

    # Substituir caracteres problemáticos
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    # Limitar tamanho
    return sanitized[:100].strip(". ")
