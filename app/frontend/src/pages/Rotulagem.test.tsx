/**
 * Tela de rotulagem do M6: rotulo inteiro pelo teclado, regras do esquema v2,
 * rodada vazia e painel de kappa com o gate. A API e mockada; o fluxo e real.
 */
import { MantineProvider } from "@mantine/core";
import { Notifications, notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AxiosError, type AxiosResponse } from "axios";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ORDEM_PAPEL, type ConsolidacaoOut, type FotoRotulagemOut, type Papel } from "../api/servicos";
import { AuthContext, type EstadoAuth } from "../auth/AuthContext";

vi.mock("../api/servicos", async (importar) => {
  const real = await importar<typeof import("../api/servicos")>();
  return {
    ...real,
    rotulagem: { proxima: vi.fn(), gravar: vi.fn(), consolidacao: vi.fn() },
    operacao: { ...real.operacao, fotoBlob: vi.fn(async () => "blob:mock") },
  };
});

import { rotulagem } from "../api/servicos";
import { Rotulagem } from "./Rotulagem";

const mock = vi.mocked(rotulagem);

function foto(id: number, restantes = 10): FotoRotulagemOut {
  return { foto_id: id, url: `/api/fotos/${id}/arquivo`, largura: 360, altura: 480, rodada: "a", restantes };
}

function erro404() {
  return new AxiosError("vazio", "404", undefined, null, {
    status: 404,
    data: { detail: "nada a rotular nesta rodada" },
  } as AxiosResponse);
}

const CONSOLIDACAO: ConsolidacaoOut = {
  rodadas: ["lab02"],
  limiar: 0.6,
  rotuladores: ["x", "y"],
  rotulos: 8,
  fotos: 4,
  fotos_dupla_rotulagem: 4,
  fotos_com_empate: 1,
  por_classe: [
    { classe: "cena", kappa: 0.744, n: 225, passa: true },
    { classe: "tampa_suja", kappa: 0.148, n: 225, passa: false },
    { classe: "display_apagado", kappa: null, n: 0, passa: false },
  ],
  pares: [{ a: "x", b: "y", n: 4, kappa: { cena: 0.744, tampa_suja: 0.148, display_apagado: null } }],
};

function renderizar(papel: Papel = "supervisor") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const auth: EstadoAuth = {
    usuario: {
      id: 7,
      nome: "Rotuladora",
      email: "r@teste.com.br",
      papel,
      ativo: true,
      precisa_trocar_senha: false,
      criado_em: new Date().toISOString(),
    },
    carregando: false,
    login: vi.fn(),
    logout: vi.fn(),
    recarregar: vi.fn(),
    temPapel: (m) => ORDEM_PAPEL[papel] >= ORDEM_PAPEL[m],
  };
  return render(
    <MantineProvider>
      <Notifications />
      <AuthContext.Provider value={auth}>
        <QueryClientProvider client={client}>
          <Rotulagem />
        </QueryClientProvider>
      </AuthContext.Provider>
    </MantineProvider>,
  );
}

const tecla = (key: string) => fireEvent.keyDown(window, { key });

describe("Rotulagem", () => {
  let proximo: number;

  beforeEach(() => {
    vi.clearAllMocks();
    notifications.clean(); // o store de notificacoes e global entre testes
    proximo = 1;
    mock.proxima.mockImplementation(async () => foto(proximo++));
    mock.gravar.mockImplementation(async (id, dados) => ({
      id,
      foto_id: id,
      rodada: dados.rodada,
      cena: dados.cena,
      defeitos: dados.defeitos ?? [],
      legivel: dados.legivel,
      gravado_em: new Date().toISOString(),
    }));
    mock.consolidacao.mockResolvedValue(CONSOLIDACAO);
  });

  it("mostra as definicoes operacionais do esquema v2", async () => {
    renderizar();
    await screen.findByTestId("foto-atual");
    expect(screen.getByText(/O mostrador define a cena/)).toBeInTheDocument();
    expect(screen.getByText(/dá para contar os segmentos de cada dígito/)).toBeInTheDocument();
    expect(screen.getByText(/sem chutar nenhum/)).toBeInTheDocument();
  });

  it("rotula pelo teclado e grava com Enter", async () => {
    renderizar();
    await screen.findByText("foto #1");
    tecla("1");
    tecla("q");
    tecla("w");
    tecla("w"); // desmarca
    tecla("s");
    tecla("Enter");
    await screen.findByText("foto #2");
    expect(mock.gravar).toHaveBeenCalledTimes(1);
    const [id, dados] = mock.gravar.mock.calls[0];
    expect(id).toBe(1);
    expect(dados).toMatchObject({ rodada: "a", cena: "digital", defeitos: ["reflexo"], legivel: true });
    expect(screen.getByText(/gravadas nesta sessão/)).toHaveTextContent("1");
  });

  it("Enter sem cena ou sem legivel nao grava", async () => {
    renderizar();
    await screen.findByText("foto #1");
    tecla("Enter");
    tecla("2");
    tecla("Enter");
    expect((await screen.findAllByText(/Escolha a cena/)).length).toBeGreaterThan(0);
    expect(mock.gravar).not.toHaveBeenCalled();
  });

  it("aplica as regras do esquema: nao_medidor zera defeitos, display apagado impoe nao legivel", async () => {
    renderizar();
    await screen.findByText("foto #1");
    tecla("q");
    tecla("4"); // nao_medidor: defeitos zerados, legivel = nao
    tecla("e"); // ignorado
    tecla("s"); // ignorado: legivel imposto
    tecla("Enter");
    await screen.findByText("foto #2");
    expect(mock.gravar.mock.calls[0][1]).toMatchObject({ cena: "nao_medidor", defeitos: [], legivel: false });

    tecla("1");
    tecla("t"); // display_apagado
    tecla("s"); // ignorado
    tecla("Enter");
    await screen.findByText("foto #3");
    expect(mock.gravar.mock.calls[1][1]).toMatchObject({
      cena: "digital",
      defeitos: ["display_apagado"],
      legivel: false,
    });
  });

  it("trocar de ocorrencia para digital apaga o legivel imposto", async () => {
    renderizar();
    await screen.findByText("foto #1");
    tecla("3");
    tecla("1");
    tecla("Enter");
    expect((await screen.findAllByText(/Escolha a cena/)).length).toBeGreaterThan(0);
    expect(mock.gravar).not.toHaveBeenCalled();
    tecla("n");
    tecla("Enter");
    await screen.findByText("foto #2");
    expect(mock.gravar.mock.calls[0][1]).toMatchObject({ cena: "digital", legivel: false });
  });

  it("rodada sem foto disponivel mostra aviso e troca de rodada pede outra", async () => {
    mock.proxima.mockRejectedValueOnce(erro404()).mockRejectedValueOnce(erro404());
    renderizar();
    expect(await screen.findByText("Nada a rotular")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Rodada B"));
    await waitFor(() => expect(mock.proxima).toHaveBeenLastCalledWith("b"));
  });

  it("409 ao gravar avisa e carrega a proxima", async () => {
    mock.gravar.mockRejectedValueOnce(
      new AxiosError("conflito", "409", undefined, null, { status: 409, data: {} } as AxiosResponse),
    );
    renderizar();
    await screen.findByText("foto #1");
    tecla("1");
    tecla("s");
    tecla("Enter");
    await screen.findByText(/não está mais disponível/);
    await screen.findByText("foto #2");
  });

  it("tempo na foto acima do teto do servidor vai nulo, e o rotulo grava", async () => {
    const agora = vi.spyOn(Date, "now").mockReturnValue(1_000_000);
    try {
      renderizar();
      await screen.findByText("foto #1");
      agora.mockReturnValue(1_000_000 + 12_340);
      tecla("1");
      tecla("s");
      tecla("Enter");
      await screen.findByText("foto #2");
      expect(mock.gravar.mock.calls[0][1].segundos_na_foto).toBe(12.3);

      agora.mockReturnValue(1_000_000 + 12_340 + 3_700_000); // aba esquecida por 1 h
      tecla("1");
      tecla("s");
      tecla("Enter");
      await screen.findByText("foto #3");
      expect(mock.gravar.mock.calls[1][1].segundos_na_foto).toBeNull();
    } finally {
      agora.mockRestore();
    }
  });

  it("supervisor so ve o kappa do lab02: rodadas cegas ficam fora do painel", async () => {
    renderizar("supervisor");
    await screen.findByText("foto #1");
    fireEvent.click(screen.getByRole("tab", { name: "Kappa por classe" }));
    await screen.findByTestId("kappa-cena");
    expect(screen.queryByText("Rodadas A + B")).not.toBeInTheDocument();
    expect(screen.queryByText("A + B + desempate")).not.toBeInTheDocument();
    expect(mock.consolidacao).toHaveBeenCalledTimes(1);
    expect(mock.consolidacao).toHaveBeenCalledWith(["lab02"]);
  });

  it("painel de kappa sinaliza o gate de 0,6 por classe (admin ve as rodadas cegas)", async () => {
    renderizar("admin");
    await screen.findByText("foto #1");
    fireEvent.click(screen.getByRole("tab", { name: "Kappa por classe" }));
    const cena = await screen.findByTestId("kappa-cena");
    expect(within(cena).getByText("0,74")).toBeInTheDocument();
    expect(within(cena).getByText(/passa \(≥ 0,60\)/)).toBeInTheDocument();
    expect(within(screen.getByTestId("kappa-tampa_suja")).getByText(/não passa/)).toBeInTheDocument();
    expect(within(screen.getByTestId("kappa-display_apagado")).getByText("sem dado")).toBeInTheDocument();
    expect(mock.consolidacao).toHaveBeenCalledWith(["lab02"]);
    fireEvent.click(screen.getByText("Rodadas A + B"));
    await waitFor(() => expect(mock.consolidacao).toHaveBeenLastCalledWith(["a", "b"]));
  });
});
