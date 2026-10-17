/** Tela da auditoria amostral: decide pelo teclado, sem ver a decisao anterior. */
import { MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AxiosError, type AxiosResponse } from "axios";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ItemAuditoria } from "../api/servicos";

vi.mock("../api/servicos", async (importar) => {
  const real = await importar<typeof import("../api/servicos")>();
  return {
    ...real,
    auditoria: { proxima: vi.fn(), decidir: vi.fn(), liberar: vi.fn() },
    operacao: { ...real.operacao, fotoBlob: vi.fn(async () => "blob:mock") },
  };
});

import { auditoria } from "../api/servicos";
import { Auditoria } from "./Auditoria";

const a = vi.mocked(auditoria);

function item(id: number, restantes = 2): ItemAuditoria {
  return {
    auditoria_id: id,
    lote: "PSP_EXTRATLEITIMPL_990126_0004",
    linha_csv: 10 + id,
    medidores: [`77${id}`],
    posicao_lida: 4000 + id,
    nota_codigo: null,
    nota: null,
    foto_principal: {
      id,
      nome_arquivo: `f${id}.jpg`,
      sequencia: 0,
      principal: true,
      largura: 360,
      altura: 480,
      bytes: 1,
      url: `/api/fotos/${id}/arquivo`,
    },
    fotos: [],
    quarentena: [],
    reservado_ate: null,
    restantes,
  };
}

function erro(status: number, detail: string) {
  return new AxiosError(detail, String(status), undefined, null, {
    status,
    data: { detail },
  } as AxiosResponse);
}

function renderizar() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <Notifications />
      <QueryClientProvider client={client}>
        <Auditoria />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

const tecla = (key: string) => fireEvent.keyDown(window, { key });

describe("Auditoria", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    a.liberar.mockResolvedValue({} as never);
    a.decidir.mockImplementation(async (id, dados) => ({
      auditoria_id: id,
      decisao: dados.decisao,
      motivo: dados.motivo ?? null,
      observacao: null,
      decidida_em: new Date().toISOString(),
    }));
  });

  it("decide pelo teclado e avisa que a decisao anterior nao e mostrada", async () => {
    a.proxima.mockResolvedValueOnce(item(1)).mockResolvedValueOnce(item(2, 1));
    renderizar();
    await screen.findByText("auditoria #1");
    expect(screen.getByText(/A decisão anterior não é mostrada/)).toBeInTheDocument();
    expect(screen.queryByText(/aprovada_humano|reprovada_humano/)).not.toBeInTheDocument();
    tecla("3"); // tampa suja
    tecla("r");
    await screen.findByText("auditoria #2");
    expect(a.decidir).toHaveBeenCalledWith(1, {
      decisao: "reprovada",
      motivo: "tampa_suja",
      observacao: null,
    });
  });

  it("sem pendencias para o usuario: mensagem de vazio", async () => {
    a.proxima.mockRejectedValue(erro(404, "nenhuma auditoria pendente para voce"));
    renderizar();
    expect(await screen.findByText("Nenhuma auditoria pendente para você")).toBeInTheDocument();
  });

  it("recusa unica do servidor (404) avisa e carrega a proxima", async () => {
    a.proxima.mockResolvedValueOnce(item(1)).mockResolvedValueOnce(item(5));
    a.decidir.mockRejectedValueOnce(erro(404, "auditoria indisponivel"));
    renderizar();
    await screen.findByText("auditoria #1");
    tecla("a");
    expect(await screen.findByText("auditoria indisponivel")).toBeInTheDocument();
    await screen.findByText("auditoria #5");
  });

  it("papel sem auditoria (403) mostra o motivo", async () => {
    a.proxima.mockRejectedValue(erro(403, "auditoria amostral e feita so pelo papel auditor"));
    renderizar();
    expect(await screen.findByText("auditoria amostral e feita so pelo papel auditor")).toBeInTheDocument();
  });

  it("pular devolve a reserva", async () => {
    a.proxima.mockResolvedValueOnce(item(1));
    renderizar();
    await screen.findByText("auditoria #1");
    fireEvent.click(screen.getByTestId("btn-pular"));
    await waitFor(() => expect(a.liberar).toHaveBeenCalledWith(1));
    expect(await screen.findByText("Auditoria devolvida")).toBeInTheDocument();
  });
});
