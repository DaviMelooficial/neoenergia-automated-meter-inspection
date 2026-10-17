import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { LeituraOut } from "../api/servicos";
import { PainelLeitura } from "./PainelLeitura";

type Historico = LeituraOut["historico"][number];

function entrada(status: string, detalhe: Record<string, unknown> | null): Historico {
  return { status, ator: "modelo:heuristica@v1", confianca: null, detalhe, criado_em: "" };
}

type Inferencia = LeituraOut["inferencias"][number];

function leitura(historico: Historico[], inferencias: Inferencia[] = []): LeituraOut {
  return {
    id: 1,
    lote: "PSP_EXTRATLEITIMPL_200526_0352",
    linha_csv: 2,
    medidores: ["3101"],
    posicao_lida: 1001,
    nota_codigo: null,
    status_atual: "fila_humana",
    foto_principal: null,
    reservado_por: null,
    reservado_ate: null,
    criado_em: new Date().toISOString(),
    nota: null,
    fotos: [],
    historico,
    inferencias,
    quarentena: [],
    revisoes: [],
  };
}

describe("PainelLeitura", () => {
  it("mostra o motivo da entrada mais recente na fila, nao da primeira", () => {
    render(
      <MantineProvider>
        <PainelLeitura
          leitura={leitura([
            entrada("recebida", null),
            entrada("triagem_qualidade", null),
            entrada("fila_humana", { politica: "politica@v0", decisao: "humano" }),
            entrada("triagem_qualidade", { motivo: "recalcular_decisao" }),
            entrada("rejeitada_qualidade", { politica: "politica@v1", decisao: "rejeitar" }),
            entrada("triagem_qualidade", { motivo: "recalcular_decisao" }),
            entrada("fila_humana", { politica: "politica@v2", decisao: "humano" }),
          ])}
        />
      </MantineProvider>,
    );
    expect(screen.getByText(/Na fila por: humano · politica@v2/)).toBeInTheDocument();
    expect(screen.queryByText(/politica@v0/)).not.toBeInTheDocument();
  });

  it("modelo treinado: mostra ilegivel como o score que decide e os defeitos como informativos", () => {
    const saida = { ilegivel: 0.97, fora_de_foco: 0.8, glare: 0.1, cena: { digital: 0.9 } };
    render(
      <MantineProvider>
        <PainelLeitura
          leitura={leitura(
            [],
            [{ modelo: "qualidade@v1", saida, latencia_ms: 5, criado_em: "" }],
          )}
        />
      </MantineProvider>,
    );
    expect(screen.getByText("ilegível (decide)")).toBeInTheDocument();
    expect(screen.getByText("97%")).toBeInTheDocument();
    expect(screen.getByTestId("defeitos-informativos")).toBeInTheDocument();
  });

  it("heuristica: sem barra de ilegivel nem aviso de defeitos informativos", () => {
    render(
      <MantineProvider>
        <PainelLeitura
          leitura={leitura(
            [],
            [{ modelo: "heuristica@v1", saida: { fora_de_foco: 0.9 }, latencia_ms: 3, criado_em: "" }],
          )}
        />
      </MantineProvider>,
    );
    expect(screen.queryByText("ilegível (decide)")).not.toBeInTheDocument();
    expect(screen.queryByTestId("defeitos-informativos")).not.toBeInTheDocument();
    expect(screen.getByText("90%")).toBeInTheDocument();
  });
});
