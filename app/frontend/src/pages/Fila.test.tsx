/**
 * A tela do aceite do M4: 20 decisoes so pelo teclado, motivo obrigatorio na
 * reprovacao, fila vazia e erro ao pular. A API e mockada; o fluxo e real.
 */
import { MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AxiosError, type AxiosResponse } from "axios";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { LeituraOut } from "../api/servicos";

vi.mock("../api/servicos", async (importar) => {
  const real = await importar<typeof import("../api/servicos")>();
  return {
    ...real,
    fila: { proxima: vi.fn(), decidir: vi.fn(), liberar: vi.fn(), listar: vi.fn() },
    operacao: { ...real.operacao, fotoBlob: vi.fn(async () => "blob:mock") },
  };
});

import { fila } from "../api/servicos";
import { Fila } from "./Fila";

const filaMock = vi.mocked(fila);

function leitura(id: number): LeituraOut {
  return {
    id,
    lote: "PSP_EXTRATLEITIMPL_200526_0352",
    linha_csv: id,
    medidores: [`31${id}`],
    posicao_lida: 1000 + id,
    nota_codigo: null,
    status_atual: "fila_humana",
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
    reservado_por: 1,
    reservado_ate: null,
    criado_em: new Date().toISOString(),
    nota: null,
    fotos: [],
    historico: [],
    inferencias: [],
    quarentena: [],
    revisoes: [],
  };
}

function erro404() {
  return new AxiosError("fila vazia", "404", undefined, null, {
    status: 404,
    data: { detail: "fila vazia" },
  } as AxiosResponse);
}

function renderizar() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <Notifications />
      <QueryClientProvider client={client}>
        <Fila />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

const tecla = (key: string) => fireEvent.keyDown(window, { key });

describe("Fila", () => {
  let proximoId: number;

  beforeEach(() => {
    vi.clearAllMocks();
    proximoId = 1;
    filaMock.proxima.mockImplementation(async () => leitura(proximoId++));
    filaMock.decidir.mockImplementation(async (id, dados) => ({
      id,
      usuario_id: 1,
      decisao: dados.decisao,
      motivo: dados.motivo ?? null,
      observacao: dados.observacao ?? null,
      criado_em: new Date().toISOString(),
    }));
    filaMock.liberar.mockResolvedValue({} as never);
    filaMock.listar.mockResolvedValue({ itens: [], total: 0, pagina: 1, por_pagina: 4 });
  });

  it("20 decisoes so pelo teclado, alternando aprovar e reprovar com motivo", async () => {
    renderizar();
    await screen.findByText("leitura #1");

    for (let i = 1; i <= 20; i++) {
      if (i % 2 === 1) {
        tecla("a");
      } else {
        tecla("2"); // glare
        tecla("r");
      }
      await screen.findByText(`leitura #${i + 1}`);
    }

    expect(filaMock.decidir).toHaveBeenCalledTimes(20);
    const ids = filaMock.decidir.mock.calls.map(([id]) => id);
    expect(new Set(ids).size).toBe(20);
    expect(filaMock.decidir.mock.calls[0][1]).toEqual({
      decisao: "aprovada",
      motivo: null,
      observacao: null,
    });
    expect(filaMock.decidir.mock.calls[1][1]).toEqual({
      decisao: "reprovada",
      motivo: "glare",
      observacao: null,
    });
    expect(screen.getByText("20", { selector: "b" })).toBeInTheDocument();
  });

  it("R sem motivo nao decide e avisa; motivo e limpo apos cada item", async () => {
    renderizar();
    await screen.findByText("leitura #1");
    tecla("r");
    await screen.findByText(/escolha o motivo/i);
    expect(filaMock.decidir).not.toHaveBeenCalled();

    tecla("5");
    tecla("r");
    await screen.findByText("leitura #2");
    expect(filaMock.decidir).toHaveBeenCalledWith(1, {
      decisao: "reprovada",
      motivo: "display_apagado",
      observacao: null,
    });

    // O motivo nao vaza para o proximo item.
    tecla("r");
    await waitFor(() => expect(screen.getAllByText(/escolha o motivo/i).length).toBeGreaterThan(0));
    expect(filaMock.decidir).toHaveBeenCalledTimes(1);
  });

  it("'outro' exige observacao", async () => {
    renderizar();
    await screen.findByText("leitura #1");
    tecla("6");
    tecla("r");
    await screen.findByText(/descreva o motivo/i);
    expect(filaMock.decidir).not.toHaveBeenCalled();
  });

  it("espaco pula: libera a reserva e exclui o item nas proximas chamadas", async () => {
    renderizar();
    await screen.findByText("leitura #1");
    tecla(" ");
    await screen.findByText("leitura #2");
    expect(filaMock.liberar).toHaveBeenCalledWith(1);
    expect(filaMock.proxima).toHaveBeenLastCalledWith([1]);
    expect(filaMock.decidir).not.toHaveBeenCalled();
  });

  it("falha ao pular mostra erro e nao exclui o item", async () => {
    filaMock.liberar.mockRejectedValueOnce(new Error("rede"));
    renderizar();
    await screen.findByText("leitura #1");
    tecla(" ");
    await screen.findByText(/não foi possível pular/i);
    expect(screen.getByText("leitura #1")).toBeInTheDocument();
    tecla(" ");
    await screen.findByText("leitura #2");
    expect(filaMock.proxima).toHaveBeenLastCalledWith([1]);
  });

  it("404 na proxima mostra fila vazia", async () => {
    filaMock.proxima.mockRejectedValue(erro404());
    renderizar();
    await screen.findByText("Fila vazia");
    expect(screen.queryByTestId("leitura-atual")).not.toBeInTheDocument();
  });

  it("409 ao decidir avisa e carrega o proximo", async () => {
    filaMock.decidir.mockRejectedValueOnce(
      new AxiosError("conflito", "409", undefined, null, { status: 409, data: {} } as AxiosResponse),
    );
    renderizar();
    await screen.findByText("leitura #1");
    tecla("a");
    await screen.findByText("leitura #2");
    await screen.findByText(/já foi decidido/i);
    expect(screen.getByText("0", { selector: "b" })).toBeInTheDocument(); // nao contou
  });
});
