/**
 * O teste mais importante do frontend: a corrida de multiplos 401.
 * Varias requisicoes falham ao mesmo tempo; so UMA chamada a /auth/refresh
 * pode acontecer, e todas precisam ser repetidas com o token novo.
 */
import axios, { type AxiosRequestConfig } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  api,
  definirToken,
  encerrarSessao,
  obterToken,
  registrarPerdaDeSessao,
  renovarToken,
  SessaoExpirada,
} from "./client";

function respostaHttp(status: number, data: unknown, config: AxiosRequestConfig) {
  return { status, data, statusText: "", headers: {}, config: config as never };
}

describe("interceptor de refresh", () => {
  let chamadasRefresh = 0;
  let requisicoes: string[] = [];
  const adapterOriginal = api.defaults.adapter;

  beforeEach(() => {
    chamadasRefresh = 0;
    requisicoes = [];
    definirToken("antigo");

    // Adapter falso da instancia `api`: 401 com o token antigo, 200 com o novo.
    api.defaults.adapter = async (config) => {
      const auth = String(config.headers?.Authorization ?? "");
      requisicoes.push(`${config.url}:${auth}`);
      if (auth === "Bearer novo") return respostaHttp(200, { ok: config.url }, config);
      const erro = new axios.AxiosError("Unauthorized", "401", config as never, null, respostaHttp(401, { detail: "token invalido" }, config) as never);
      throw erro;
    };

    // /auth/refresh e chamado pela instancia global do axios.
    vi.spyOn(axios, "post").mockImplementation(async () => {
      chamadasRefresh += 1;
      await new Promise((r) => setTimeout(r, 20)); // simula latencia da rede
      return respostaHttp(200, { access_token: "novo", token_type: "bearer", expires_in: 900 }, {});
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    registrarPerdaDeSessao(null);
    definirToken(null);
    api.defaults.adapter = adapterOriginal;
  });

  it("deduplica o refresh quando varias requisicoes recebem 401 ao mesmo tempo", async () => {
    const respostas = await Promise.all([api.get("/lotes"), api.get("/fila-humana"), api.get("/auth/me")]);

    expect(chamadasRefresh).toBe(1);
    expect(obterToken()).toBe("novo");
    expect(respostas.map((r) => r.data.ok)).toEqual(["/lotes", "/fila-humana", "/auth/me"]);
    // Cada rota foi tentada com o token antigo e repetida com o novo.
    expect(requisicoes.filter((r) => r.endsWith("Bearer antigo"))).toHaveLength(3);
    expect(requisicoes.filter((r) => r.endsWith("Bearer novo"))).toHaveLength(3);
  });

  it("nao entra em laco quando a repeticao tambem falha", async () => {
    api.defaults.adapter = async (config) => {
      requisicoes.push(String(config.url));
      throw new axios.AxiosError("Unauthorized", "401", config as never, null, respostaHttp(401, {}, config) as never);
    };
    await expect(api.get("/lotes")).rejects.toBeInstanceOf(axios.AxiosError);
    expect(chamadasRefresh).toBe(1);
    expect(requisicoes).toEqual(["/lotes", "/lotes"]); // original + uma unica repeticao
  });

  it("limpa a sessao e avisa quando o refresh falha", async () => {
    vi.mocked(axios.post).mockRejectedValue(new Error("refresh invalido"));
    const perdeu = vi.fn();
    registrarPerdaDeSessao(perdeu);

    await expect(api.get("/lotes")).rejects.toBeInstanceOf(SessaoExpirada);
    expect(perdeu).toHaveBeenCalledTimes(1);
    expect(obterToken()).toBeNull();
  });

  it("um refresh em voo durante o logout nao reinstala token", async () => {
    const pendente = renovarToken(); // dispara o refresh (20ms de latencia simulada)
    encerrarSessao(); // usuario clicou em Sair antes da resposta chegar
    await expect(pendente).rejects.toBeInstanceOf(SessaoExpirada);
    expect(obterToken()).toBeNull();
    // A proxima renovacao e uma promise nova, da geracao atual.
    await expect(renovarToken()).resolves.toBe("novo");
    expect(chamadasRefresh).toBe(2);
  });

  it("nao tenta refresh no logout", async () => {
    await expect(api.post("/auth/logout")).rejects.toBeInstanceOf(axios.AxiosError);
    expect(chamadasRefresh).toBe(0);
  });

  it("nao tenta refresh no proprio login", async () => {
    await expect(api.post("/auth/login", { email: "a@b.com.br", senha: "x" })).rejects.toBeInstanceOf(
      axios.AxiosError,
    );
    expect(chamadasRefresh).toBe(0);
  });

  it("renovarToken compartilha a promise entre chamadores concorrentes", async () => {
    const [a, b, c] = await Promise.all([renovarToken(), renovarToken(), renovarToken()]);
    expect(chamadasRefresh).toBe(1);
    expect(a).toBe("novo");
    expect(b).toBe("novo");
    expect(c).toBe("novo");
  });
});
