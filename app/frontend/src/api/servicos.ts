/** Funcoes tipadas da API. Os tipos vem de `tipos.ts`, gerado do OpenAPI. */
import { api } from "./client";
import type { components } from "./tipos";

type S = components["schemas"];
export type UsuarioOut = S["UsuarioOut"];
export type TokenOut = S["TokenOut"];
export type LeituraOut = S["LeituraOut"];
export type LeituraResumoOut = S["LeituraResumoOut"];
export type PaginaLeituras = S["Pagina_LeituraResumoOut_"];
export type PaginaQuarentena = S["Pagina_QuarentenaOut_"];
export type QuarentenaOut = S["QuarentenaOut"];
export type RevisaoOut = S["RevisaoOut"];
export type LoteOut = S["LoteOut"];
export type DecisaoIn = S["DecisaoIn"];
export type MotivoReprovacao = S["MotivoReprovacao"];
export type Papel = S["Papel"];
export type UsuarioIn = S["UsuarioIn"];
export type UsuarioPatch = S["UsuarioPatch"];
export type FotoRotulagemOut = S["FotoRotulagemOut"];
export type RotuloIn = S["RotuloIn"];
export type RotuloOut = S["RotuloOut"];
export type ConsolidacaoOut = S["ConsolidacaoOut"];
export type Cena = S["Cena"];
export type Defeito = S["Defeito"];
export type RodadaTela = RotuloIn["rodada"];
export type RodadaRotulagem = S["RodadaRotulagem"];

export const ORDEM_PAPEL: Record<Papel, number> = { auditor: 1, supervisor: 2, admin: 3 };

export const MOTIVOS: { valor: MotivoReprovacao; rotulo: string; tecla: string }[] = [
  { valor: "fora_de_foco", rotulo: "Fora de foco", tecla: "1" },
  { valor: "glare", rotulo: "Reflexo (glare)", tecla: "2" },
  { valor: "tampa_suja", rotulo: "Tampa suja ou embaçada", tecla: "3" },
  { valor: "enquadramento_incorreto", rotulo: "Enquadramento incorreto", tecla: "4" },
  { valor: "display_apagado", rotulo: "Display apagado", tecla: "5" },
  { valor: "outro", rotulo: "Outro (descrever)", tecla: "6" },
];

// ------------------------------------------------------------------ auth
export const auth = {
  login: (email: string, senha: string) =>
    api.post<TokenOut>("/auth/login", { email, senha }).then((r) => r.data),
  logout: () => api.post("/auth/logout"),
  me: () => api.get<UsuarioOut>("/auth/me").then((r) => r.data),
  trocarSenha: (senha_atual: string, senha_nova: string) =>
    api.post("/auth/senha", { senha_atual, senha_nova }),
};

// ------------------------------------------------------------------ fila
export const fila = {
  listar: (params: { lote?: string; nota?: string; pagina?: number; por_pagina?: number } = {}) =>
    api.get<PaginaLeituras>("/fila-humana", { params }).then((r) => r.data),
  proxima: (excluir: number[] = [], lote?: string) =>
    api
      .post<LeituraOut>("/fila-humana/proxima", null, {
        params: { excluir, lote },
        paramsSerializer: { indexes: null }, // excluir=1&excluir=2
      })
      .then((r) => r.data),
  liberar: (id: number) => api.post(`/fila-humana/${id}/liberar`),
  decidir: (id: number, dados: DecisaoIn) =>
    api.post<RevisaoOut>(`/fila-humana/${id}/decisao`, dados).then((r) => r.data),
};

// ------------------------------------------------------------------ operacao
export const operacao = {
  lotes: () => api.get<LoteOut[]>("/lotes").then((r) => r.data),
  leitura: (id: number) => api.get<LeituraOut>(`/leituras/${id}`).then((r) => r.data),
  /** A rota exige token, entao <img src> nao serve: baixa o binario e cria um object URL. */
  fotoBlob: (url: string) =>
    api
      .get<Blob>(url.replace(/^\/api/, ""), { responseType: "blob" })
      .then((r) => URL.createObjectURL(r.data)),
};

// ------------------------------------------------------------------ quarentena
export const quarentena = {
  listar: (params: {
    tipo?: string;
    severidade?: string;
    resolvido?: boolean;
    pagina?: number;
    por_pagina?: number;
  }) => api.get<PaginaQuarentena>("/quarentena", { params }).then((r) => r.data),
  resumo: () =>
    api
      .get<Record<string, { pendentes: number; resolvidos: number }>>("/quarentena/resumo")
      .then((r) => r.data),
  resolver: (id: number, observacao?: string) =>
    api.post<QuarentenaOut>(`/quarentena/${id}/resolver`, { observacao }).then((r) => r.data),
};

// ------------------------------------------------------------------ metricas
export type MetricasQualidade = S["QualidadeOut"];
export type MetricasDrift = S["DriftOut"];
export type LoteDrift = S["LoteDriftOut"];
export type SinalDrift = S["SinalOut"];
export type AutoAprovacao = S["AutoAprovacaoOut"];
export type ConcordanciaAuditoria = S["ConcordanciaAuditoriaOut"];
export type ItemAuditoria = S["ItemAuditoriaOut"];
export type AuditoriaDecisao = S["AuditoriaDecisaoOut"];

export const metricas = {
  qualidade: () => api.get<MetricasQualidade>("/metricas/qualidade").then((r) => r.data),
  drift: () => api.get<MetricasDrift>("/metricas/drift").then((r) => r.data),
  autoAprovacao: () => api.get<AutoAprovacao>("/metricas/auto-aprovacao").then((r) => r.data),
  auditoria: () => api.get<ConcordanciaAuditoria>("/metricas/auditoria").then((r) => r.data),
};

// ------------------------------------------------------------------ auditoria amostral
export const auditoria = {
  proxima: () => api.post<ItemAuditoria>("/auditoria/proxima").then((r) => r.data),
  liberar: (id: number) => api.post(`/auditoria/${id}/liberar`),
  decidir: (id: number, dados: DecisaoIn) =>
    api.post<AuditoriaDecisao>(`/auditoria/${id}/decisao`, dados).then((r) => r.data),
};

// ------------------------------------------------------------------ rotulagem
export const rotulagem = {
  proxima: (rodada: RodadaTela) =>
    api
      .post<FotoRotulagemOut>("/rotulagem/proxima", null, { params: { rodada } })
      .then((r) => r.data),
  gravar: (fotoId: number, dados: RotuloIn) =>
    api.post<RotuloOut>(`/rotulagem/${fotoId}`, dados).then((r) => r.data),
  consolidacao: (rodadas: RodadaRotulagem[]) =>
    api
      .get<ConsolidacaoOut>("/rotulagem/consolidacao", {
        params: { rodada: rodadas },
        paramsSerializer: { indexes: null }, // rodada=a&rodada=b
      })
      .then((r) => r.data),
};

// ------------------------------------------------------------------ usuarios
export const usuarios = {
  listar: () => api.get<UsuarioOut[]>("/usuarios").then((r) => r.data),
  criar: (dados: UsuarioIn) => api.post<UsuarioOut>("/usuarios", dados).then((r) => r.data),
  atualizar: (id: number, dados: UsuarioPatch) =>
    api.patch<UsuarioOut>(`/usuarios/${id}`, dados).then((r) => r.data),
};
