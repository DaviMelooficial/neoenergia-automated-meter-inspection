/**
 * Cliente HTTP da API.
 *
 * O access token vive so em memoria (nunca em localStorage). O refresh token e
 * cookie httpOnly, invisivel daqui. Em 401 o interceptor renova o token UMA vez
 * por requisicao, e todas as requisicoes que falharem ao mesmo tempo aguardam a
 * MESMA promise de refresh: o refresh e rotativo, entao N chamadas concorrentes
 * a /auth/refresh invalidariam umas as outras e derrubariam a sessao.
 */
import axios, { AxiosError, type InternalAxiosRequestConfig } from "axios";

import type { components } from "./tipos";

type TokenOut = components["schemas"]["TokenOut"];

let accessToken: string | null = null;
let aoPerderSessao: (() => void) | null = null;
let refreshEmAndamento: Promise<string> | null = null;
// Incrementada a cada logout: um refresh que estava em voo quando o usuario saiu
// pertence a uma geracao antiga e nao pode reinstalar token nenhum.
let geracaoSessao = 0;

export class SessaoExpirada extends Error {
  constructor() {
    super("sessão expirada, entre novamente");
    this.name = "SessaoExpirada";
  }
}

export function definirToken(token: string | null): void {
  accessToken = token;
}

export function obterToken(): string | null {
  return accessToken;
}

/** Logout: descarta o token e invalida qualquer refresh ainda em andamento. */
export function encerrarSessao(): void {
  geracaoSessao += 1;
  refreshEmAndamento = null;
  accessToken = null;
}

/** Chamado pelo AuthContext para reagir quando o refresh falha de vez. */
export function registrarPerdaDeSessao(fn: (() => void) | null): void {
  aoPerderSessao = fn;
}

export const api = axios.create({ baseURL: "/api", withCredentials: true });

api.interceptors.request.use((config) => {
  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`;
  }
  return config;
});

/** Renova o access token. Deduplicada: chamadas concorrentes compartilham a promise. */
export function renovarToken(): Promise<string> {
  if (!refreshEmAndamento) {
    const geracao = geracaoSessao;
    const promessa = axios
      .post<TokenOut>(`${api.defaults.baseURL ?? "/api"}/auth/refresh`, null, {
        withCredentials: true,
      })
      .then((r) => {
        if (geracao !== geracaoSessao) throw new SessaoExpirada(); // houve logout no meio
        definirToken(r.data.access_token);
        return r.data.access_token;
      })
      .finally(() => {
        if (refreshEmAndamento === promessa) refreshEmAndamento = null;
      });
    refreshEmAndamento = promessa;
  }
  return refreshEmAndamento;
}

type ConfigComRetentativa = InternalAxiosRequestConfig & { _tentouRefresh?: boolean };

const ROTAS_SEM_REFRESH = ["/auth/login", "/auth/refresh", "/auth/logout"];

api.interceptors.response.use(
  (resposta) => resposta,
  async (erro: AxiosError) => {
    const config = erro.config as ConfigComRetentativa | undefined;
    const url = config?.url ?? "";
    const podeRenovar =
      erro.response?.status === 401 &&
      config !== undefined &&
      !config._tentouRefresh &&
      !ROTAS_SEM_REFRESH.some((r) => url.includes(r));

    if (!podeRenovar) {
      return Promise.reject(erro);
    }

    config._tentouRefresh = true;
    try {
      const novo = await renovarToken();
      config.headers.Authorization = `Bearer ${novo}`;
      return api.request(config);
    } catch {
      definirToken(null);
      aoPerderSessao?.();
      return Promise.reject(new SessaoExpirada());
    }
  },
);

/** Mensagem legivel de um erro da API, para notificacoes. */
export function mensagemDeErro(erro: unknown, padrao = "erro inesperado"): string {
  if (erro instanceof SessaoExpirada) return erro.message;
  if (axios.isAxiosError(erro)) {
    const detail = (erro.response?.data as { detail?: unknown } | undefined)?.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "mensagem" in detail) {
      return String((detail as { mensagem: unknown }).mensagem);
    }
    if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg);
    if (erro.response?.status) return `${padrao} (HTTP ${erro.response.status})`;
  }
  return padrao;
}
